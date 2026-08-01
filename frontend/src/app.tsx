import { createElement, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Mic, Square, Volume2, VolumeX } from "lucide-react";

import { face_view } from "./face_view";
import { pick_mood } from "./face_shapes";
import {
  api_base_url,
  read_stream_status,
  subscribe_events,
  subscribe_stream_status,
} from "./event_stream";
import type { server_event } from "./event_stream";
import { create_speech_queue } from "./speech_queue";
import type { speech_phase, speech_queue } from "./speech_queue";
import type { speech_track } from "./speech_track";

type layout_kind = "console" | "face";

type timeline_kind = "user" | "agent" | "notice" | "system";
type timeline_level = "info" | "error";
type timeline_entry = {
  id: number;
  kind: timeline_kind;
  text: string;
  level: timeline_level;
  time: string;
};
type mic_permission = "pending" | "granted" | "denied";

// The old single activity_state conflated three things that are now genuinely
// concurrent: the agent can still be thinking while audio plays, and an
// unsolicited utterance can start from rest. Split into what the mic is doing,
// what the speaker is doing, and whether a turn is live - and compute the label.
type mic_phase = "idle" | "recording" | "transcribing";

const denied_label = "마이크 권한이 없어요";
const recording_label = "듣고 있어요";
const transcribing_label = "받아쓰는 중";
const thinking_label = "생각하는 중";
const synthesizing_label = "목소리 만드는 중";
const speaking_label = "말하는 중";
const waiting_label = "대기 중";
const alarm_label = "알림이 왔어요";
const blocked_label = "탭하면 들려드릴게요";
const offline_label = "서버 연결 끊김";

// offline_label sits below turn_label so a momentary reconnect does not nag in
// the middle of a turn.
function pick_status_label(
  permission: mic_permission,
  mic: mic_phase,
  speech: speech_phase,
  turn_label: string,
  stream: "connecting" | "open" | "closed",
): string {
  return permission === "denied"
    ? denied_label
    : mic === "recording"
      ? recording_label
      : mic === "transcribing"
        ? transcribing_label
        : speech === "blocked"
          ? blocked_label
          : speech === "speaking"
            ? speaking_label
            : speech === "synthesizing"
              ? synthesizing_label
              : turn_label !== ""
                ? turn_label
                : stream === "closed"
                  ? offline_label
                  : waiting_label;
}

// The mic is only locked out while the recording is being transcribed. It stays
// live for the whole rest of a turn, because interrupting is the point: the
// server owns the turn and can be told to abandon it.
function is_busy(mic: mic_phase): boolean {
  return mic === "transcribing";
}

// The console is a debug log, and turns can now start with nobody watching (a
// scheduled announcement on an unattended kiosk), so it is capped rather than
// grown forever - every append copies the array, and on #/face it is never even
// rendered.
const max_timeline_entries = 300;

// Returns as soon as the turn is registered. The reply does not come back here
// at all - it arrives on the event stream, possibly as several utterances
// seconds apart.
async function trigger_turn(text: string, turn_id: string): Promise<void> {
  const response = await fetch(`${api_base_url}/api/turns`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text, turn_id }),
  });

  if (!response.ok) {
    throw new Error(`에이전트 서버 오류 ${response.status}`);
  }
}

async function cancel_turn(turn_id: string): Promise<void> {
  const response = await fetch(`${api_base_url}/api/turns/${turn_id}/cancel`, {
    method: "POST",
  });

  if (!response.ok) {
    throw new Error(`턴 취소 오류 ${response.status}`);
  }
}

// The voice service is a separate deployable server reached through a relative
// path so its location is controlled by the dev proxy / reverse proxy target
// only. The browser sets the multipart boundary, so no Content-Type is set.
async function transcribe_audio(blob: Blob): Promise<string> {
  const form_data = new FormData();
  form_data.append("file", blob, "recording");
  form_data.append("language", "ko");

  const response = await fetch("/voice/transcribe", {
    method: "POST",
    body: form_data,
  });

  if (response.status === 422) {
    throw new Error("음성이 인식되지 않았어요. 조금 또렷하게 다시 말해주세요.");
  }

  if (!response.ok) {
    throw new Error(`STT 서버 오류 ${response.status}`);
  }

  const data = (await response.json()) as { text: string };

  return data.text;
}

// The face view lives at #/face so a Raspberry Pi can boot Chromium straight
// into it (chromium --kiosk https://.../#/face). The hash is subscribed to
// rather than mirrored into state — the URL is already the source of truth.
function subscribe_hash(on_change: () => void): () => void {
  window.addEventListener("hashchange", on_change);

  return () => window.removeEventListener("hashchange", on_change);
}

function read_hash(): string {
  return window.location.hash;
}

// Builds a short silent WAV blob URL, played once inside a tap gesture to
// unlock <audio> playback on iOS (which blocks any play() that is not
// gesture-initiated — the agent reply plays several awaits after the tap).
function make_silent_wav_url(): string {
  const sample_rate = 8000;
  const num_samples = 800;
  const buffer = new ArrayBuffer(44 + num_samples);
  const view = new DataView(buffer);
  const write_string = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) {
      view.setUint8(offset + i, text.charCodeAt(i));
    }
  };

  write_string(0, "RIFF");
  view.setUint32(4, 36 + num_samples, true);
  write_string(8, "WAVE");
  write_string(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sample_rate, true);
  view.setUint32(28, sample_rate, true);
  view.setUint16(32, 1, true);
  view.setUint16(34, 8, true);
  write_string(36, "data");
  view.setUint32(40, num_samples, true);
  for (let i = 0; i < num_samples; i += 1) {
    view.setUint8(44 + i, 128);
  }

  return URL.createObjectURL(new Blob([buffer], { type: "audio/wav" }));
}

export function app_shell() {
  const media_stream_ref = useRef<MediaStream | undefined>(undefined);
  const media_recorder_ref = useRef<MediaRecorder | undefined>(undefined);
  const audio_chunks_ref = useRef<Blob[]>([]);
  const entry_id_ref = useRef(0);
  const timeline_end_ref = useRef<HTMLDivElement | null>(null);
  const audio_ref = useRef<HTMLAudioElement | null>(null);
  // A ref, not state: the face's animation loop reads it every frame, and
  // going through state would re-render the whole shell mid-utterance. The
  // speech queue owns writing it now, including freeing each object URL.
  const speech_track_ref = useRef<speech_track | null>(null);
  const silent_url_ref = useRef<string | null>(null);
  const audio_unlocked_ref = useRef(false);
  const queue_ref = useRef<speech_queue | null>(null);
  // The identity gate for incoming events, read outside render. A single ref is
  // enough instead of a set of cancelled turns, because the server runs one turn
  // at a time: after a barge-in this is null, so every straggling utterance from
  // the abandoned turn is dropped.
  const accepted_turn_ref = useRef<string | null>(null);

  const [timeline, set_timeline] = useState<timeline_entry[]>([]);
  const [permission, set_permission] = useState<mic_permission>("pending");
  const [mic_phase, set_mic_phase] = useState<mic_phase>("idle");
  const [speech, set_speech] = useState<speech_phase>("idle");
  // Not a mirror of accepted_turn_ref: one is an id used to filter events and
  // address a cancel, the other is the server's own status prose. Neither is
  // derivable from the other. "" means no live turn.
  const [turn_label, set_turn_label] = useState("");

  const append_entry = (kind: timeline_kind, text: string, level: timeline_level) => {
    entry_id_ref.current += 1;

    const entry: timeline_entry = {
      id: entry_id_ref.current,
      kind,
      text,
      level,
      time: new Date().toLocaleTimeString(),
    };

    set_timeline((current) => [...current, entry].slice(-max_timeline_entries));
  };

  const log_system = (text: string) => append_entry("system", text, "info");
  const log_error = (text: string) => append_entry("system", text, "error");
  const add_user = (text: string) => append_entry("user", text, "info");
  const add_agent = (text: string) => append_entry("agent", text, "info");
  const add_notice = (text: string) => append_entry("notice", text, "info");

  // Turn liveness is two values written as a pair everywhere, so the pairing
  // lives here instead of being re-established by hand at every call site.
  // `adopt` and `finish` are the whole vocabulary: finishing leaves whatever is
  // queued playing, which is exactly why the state was split off from playback.
  const adopt_turn = (turn_id: string, label: string) => {
    accepted_turn_ref.current = turn_id;
    set_turn_label(label);
  };

  const finish_turn = () => {
    accepted_turn_ref.current = null;
    set_turn_label("");
  };

  // Play a short silent clip on the real audio element, inside the tap gesture,
  // so iOS permits the later (post-fetch) TTS playback.
  const unlock_audio = () => {
    const audio = audio_ref.current;

    if (audio === null || audio_unlocked_ref.current) {
      return;
    }

    // An unsolicited utterance can play before any tap has happened (desktop, or
    // a Pi kiosk started with --autoplay-policy=no-user-gesture-required). If it
    // is already making sound the element is de facto unlocked, and swapping in
    // the silent WAV underneath a live end-waiter would strand the queue.
    if (!audio.paused) {
      audio_unlocked_ref.current = true;
      return;
    }

    if (silent_url_ref.current === null) {
      silent_url_ref.current = make_silent_wav_url();
    }

    audio.src = silent_url_ref.current;
    audio.muted = true;
    void audio
      .play()
      .then(() => {
        audio.pause();
        audio.currentTime = 0;
      })
      .catch(() => undefined)
      .finally(() => {
        audio.muted = false;
      });

    audio_unlocked_ref.current = true;
  };

  // Invariant that makes the one-shot effect below safe: this handler and the
  // speech queue read refs and call setters. They never read React state.
  const handle_server_event = (event: server_event, queue: speech_queue) => {
    switch (event.kind) {
      case "stream_hello":
        // The server sends this exactly when it could not resume us, so it is
        // the one authority on turn liveness across a gap. A plain reconnect
        // replays instead, and must leave the turn we are mid-way through alone.
        if (event.active_turn === null) {
          finish_turn();
        } else {
          adopt_turn(event.active_turn.turn_id, thinking_label);
        }

        log_system(`스트림 연결 (v${event.protocol_version})`);
        return;

      case "turn_started":
        // "The newest intent wins" has to be enforced here, not only in the
        // runner: a turn's utterances are published long before the device has
        // finished saying them, and a verbatim announcement is published all at
        // once, so the server sees that turn as over while seconds of its audio
        // are still queued. Any turn that is not the one we already adopted
        // therefore clears the speaker. Our own turn is adopted before its POST,
        // so it matches and is left alone.
        if (event.turn_id !== accepted_turn_ref.current) {
          queue.cancel();
        }

        adopt_turn(event.turn_id, event.source === "user" ? thinking_label : alarm_label);
        log_system(`턴 시작 (${event.source})`);

        if (event.source !== "user" && event.trigger_text !== null) {
          add_user(event.trigger_text);
        }

        return;

      case "notice":
        if (event.turn_id !== accepted_turn_ref.current) {
          return;
        }

        set_turn_label(event.text);
        add_notice(event.text);
        return;

      case "utterance":
        if (event.turn_id !== accepted_turn_ref.current) {
          log_system("지난 턴의 발화를 버렸습니다");
          return;
        }

        add_agent(event.text);
        queue.push(event.text);
        return;

      case "turn_cancelling":
        if (event.turn_id !== accepted_turn_ref.current) {
          return;
        }

        // The one case that also drops the audio: the turn was abandoned, so
        // what is queued is no longer wanted.
        finish_turn();
        queue.cancel();
        return;

      case "turn_ended":
        if (event.turn_id !== accepted_turn_ref.current) {
          return;
        }

        // Clears the label, not the queue: when the turn ends server-side the
        // last utterances are usually still playing. That separation is exactly
        // why the state was split.
        finish_turn();
        log_system(`턴 종료 (${event.reason})`);
        return;

      case "error":
        log_error(event.message);

        if (event.turn_id === accepted_turn_ref.current) {
          finish_turn();
        }

        return;
    }
  };

  const acquire_microphone = async (): Promise<MediaStream | undefined> => {
    if (!navigator.mediaDevices?.getUserMedia) {
      set_permission("denied");
      log_error("마이크를 사용할 수 없습니다. HTTPS/보안 컨텍스트를 확인하세요.");
      return undefined;
    }

    try {
      // A fresh stream is acquired per recording and released afterwards.
      // Holding one open across turns makes iOS mute/end the mic track once TTS
      // audio plays (audio-session conflict), which produced empty recordings
      // after the first couple of turns.
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      set_permission("granted");
      return stream;
    } catch (error) {
      set_permission("denied");
      log_error(`마이크 권한 실패: ${error instanceof Error ? error.name : "unknown"}`);
      return undefined;
    }
  };

  const release_microphone = () => {
    media_stream_ref.current?.getTracks().forEach((track) => track.stop());
    media_stream_ref.current = undefined;
  };

  const run_turn = async (blob: Blob) => {
    if (blob.size === 0) {
      log_error("녹음된 오디오가 없습니다.");
      set_mic_phase("idle");
      return;
    }

    // Client-generated so a barge-in during the trigger round trip still has an
    // id to cancel, and so our own turn is distinguishable from a scheduled one.
    const turn_id = crypto.randomUUID();

    try {
      set_mic_phase("transcribing");
      log_system("음성 전사 요청");
      const text = await transcribe_audio(blob);
      add_user(text);

      // Adopted before the POST: turn_started can land first, and it must not
      // be filtered out as belonging to somebody else's turn.
      adopt_turn(turn_id, thinking_label);
      log_system("턴 트리거");
      await trigger_turn(text, turn_id);
    } catch (error) {
      finish_turn();
      log_error(error instanceof Error ? error.message : "처리 실패");
    } finally {
      set_mic_phase("idle");
    }
  };

  const start_turn_recording = (stream: MediaStream) => {
    media_stream_ref.current = stream;
    audio_chunks_ref.current = [];

    const recorder = new MediaRecorder(stream);
    media_recorder_ref.current = recorder;

    recorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        audio_chunks_ref.current = [...audio_chunks_ref.current, event.data];
      }
    };

    recorder.onstop = () => {
      const blob = new Blob(audio_chunks_ref.current, { type: recorder.mimeType });
      media_recorder_ref.current = undefined;
      // Release the mic so the next TTS playback doesn't fight the capture
      // session, and the next turn starts from a clean stream.
      release_microphone();
      queue_ref.current?.set_hold(false);
      void run_turn(blob);
    };

    recorder.start();
    set_mic_phase("recording");
    log_system("녹음 시작");
  };

  const handle_mic_click = async () => {
    const queue = queue_ref.current;

    if (queue === null) {
      throw new Error("speech queue was not created");
    }

    // Must run synchronously inside the tap gesture to unlock iOS audio.
    unlock_audio();

    // A blocked utterance is waiting for exactly this gesture. Play it rather
    // than dropping it, and do not start recording.
    if (speech === "blocked") {
      queue.resume();
      return;
    }

    if (mic_phase === "recording") {
      media_recorder_ref.current?.stop();
      return;
    }

    // Stop any playback first so iOS frees the audio session before we grab
    // the mic — otherwise the fresh stream can start muted for a moment. The
    // hold goes on before the permission await, not after the recorder starts:
    // an alarm arriving in that window would otherwise be synthesised and played
    // straight over the capture session (the day-4 iOS conflict).
    queue.cancel();
    queue.set_hold(true);

    const turn_id = accepted_turn_ref.current;
    finish_turn();

    if (turn_id !== null) {
      log_system("턴 취소 요청");
      void cancel_turn(turn_id).catch((error) =>
        log_error(error instanceof Error ? error.message : "턴 취소 실패"),
      );
    }

    const stream = await acquire_microphone();

    if (!stream) {
      queue.set_hold(false);
      return;
    }

    start_turn_recording(stream);
  };

  useEffect(() => {
    const queue = create_speech_queue({
      audio: audio_ref,
      track: speech_track_ref,
      on_phase: set_speech,
      on_error: log_error,
    });
    queue_ref.current = queue;

    const unsubscribe = subscribe_events({
      on_event: (event) => handle_server_event(event, queue),
      on_error: log_error,
    });

    return () => {
      unsubscribe();
      queue.cancel();
      queue_ref.current = null;
    };
  }, []);

  useEffect(() => {
    // Prompt for mic permission on load, then release the track. Each recording
    // acquires its own fresh stream (permission is remembered — no re-prompt).
    void (async () => {
      const stream = await acquire_microphone();

      if (stream) {
        stream.getTracks().forEach((track) => track.stop());
        log_system("마이크 준비됨");
      }
    })();
  }, []);

  useEffect(() => {
    timeline_end_ref.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [timeline]);

  const hash = useSyncExternalStore(subscribe_hash, read_hash);
  const stream = useSyncExternalStore(subscribe_stream_status, read_stream_status);
  const layout: layout_kind = hash === "#/face" ? "face" : "console";
  const mood = pick_mood(permission, mic_phase, speech, turn_label !== "");
  const status_label = pick_status_label(permission, mic_phase, speech, turn_label, stream);

  // The console body is wrapped in a single fragment so that both layouts
  // render exactly two children, [branch, audio]. React reconciles by
  // position: if the child count differed, the <audio> element would be
  // destroyed and recreated on every view switch, and since audio_unlocked_ref
  // would still say "true" it would never be unlocked again — permanently
  // silent TTS on iOS. Do not flatten this, and do not add a sibling here:
  // every new console element belongs inside the fragment.
  return (
    <main className={layout === "face" ? "face_root" : "app_shell"}>
      {layout === "face" ? (
        createElement(face_view, {
          mood,
          status_label,
          audio_ref,
          speech_track_ref,
          speech_pending: speech !== "idle",
          on_tap: () => void handle_mic_click(),
          on_exit: () => {
            window.location.hash = "";
          },
        })
      ) : (
        <>
          <header className="top_bar">
            <div>
              <p className="eyebrow">My Talking Claw</p>
              <h1>음성 콘솔</h1>
            </div>
            <div className="header_status">
              <span
                className={`perm_dot perm_${permission}`}
                title={`마이크 권한: ${permission}`}
              />
              <span
                className={`stream_dot stream_${stream}`}
                title={`이벤트 스트림: ${stream}`}
              />
              {turn_label !== "" && <span className="turn_chip">{turn_label}</span>}
              <div className={`speaker_lamp ${speech === "speaking" ? "on" : ""}`}>
                {speech === "speaking" ? <Volume2 size={18} /> : <VolumeX size={18} />}
                <span>{status_label}</span>
              </div>
              <a className="face_link" href="#/face">
                얼굴
              </a>
            </div>
          </header>

          <section className="timeline" aria-label="대화 및 로그 타임라인">
            {timeline.length === 0 && (
              <p className="timeline_empty">마이크 버튼을 눌러 대화를 시작하세요.</p>
            )}
            {timeline.map((entry) => (
              <div key={entry.id} className={`entry entry_${entry.kind}`}>
                {entry.kind === "system" ? (
                  <span
                    className={`system_line ${entry.level === "error" ? "system_error" : ""}`}
                  >
                    <span className="entry_time">{entry.time}</span>
                    <span>{entry.text}</span>
                  </span>
                ) : entry.kind === "notice" ? (
                  <span className="notice_line">
                    <span className="entry_time">{entry.time}</span>
                    <span>{entry.text}</span>
                  </span>
                ) : (
                  <div className="bubble">
                    <span className="entry_who">
                      {entry.kind === "user" ? "나" : "에이전트"}
                    </span>
                    <p>{entry.text}</p>
                  </div>
                )}
              </div>
            ))}
            <div ref={timeline_end_ref} />
          </section>

          <footer className="mic_bar">
            <button
              className={`mic_button ${mic_phase === "recording" ? "recording" : ""}`}
              type="button"
              onClick={() => void handle_mic_click()}
              disabled={is_busy(mic_phase)}
              aria-label={mic_phase === "recording" ? "녹음 정지" : "말하기 시작"}
            >
              {mic_phase === "recording" ? <Square size={30} /> : <Mic size={30} />}
              <span>
                {mic_phase === "recording"
                  ? "정지"
                  : is_busy(mic_phase)
                    ? "처리 중"
                    : "말하기"}
              </span>
            </button>
          </footer>
        </>
      )}

      {/* No React event handlers: the speech queue is the single owner of
          playback lifecycle. Still the second and last child of <main>. */}
      <audio ref={audio_ref} hidden />
    </main>
  );
}
