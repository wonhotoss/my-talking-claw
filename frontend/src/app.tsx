import { createElement, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Mic, Square, Volume2, VolumeX } from "lucide-react";

import { face_view } from "./face_view";
import { pick_mood } from "./face_shapes";
import { fetch_spoken_reply } from "./speech_track";
import type { speech_track } from "./speech_track";

type message_response = {
  text: string;
};

type layout_kind = "console" | "face";

type timeline_kind = "user" | "agent" | "system";
type timeline_level = "info" | "error";
type timeline_entry = {
  id: number;
  kind: timeline_kind;
  text: string;
  level: timeline_level;
  time: string;
};
type mic_permission = "pending" | "granted" | "denied";

// One state for the whole turn, finer than the old idle/recording/processing.
// "processing" hid the longest phase of all: TTS synthesis runs after run_turn
// has already returned, so the face used to fall back to idle for the several
// seconds it takes to make the audio.
type activity_state =
  | "waiting"
  | "recording"
  | "transcribing"
  | "thinking"
  | "synthesizing"
  | "speaking";

const activity_labels: Record<activity_state, string> = {
  waiting: "대기 중",
  recording: "듣고 있어요",
  transcribing: "받아쓰는 중",
  thinking: "생각하는 중",
  synthesizing: "목소리 만드는 중",
  speaking: "말하는 중",
};

// The mic is only locked out while a turn is mid-flight. Synthesis and playback
// stay tappable so a reply can be cut off.
function is_busy(activity: activity_state): boolean {
  return activity === "transcribing" || activity === "thinking";
}

const api_base_url = import.meta.env.VITE_API_BASE_URL || "";

async function send_message(text: string): Promise<message_response> {
  const response = await fetch(`${api_base_url}/api/message`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text }),
  });

  if (!response.ok) {
    throw new Error(`에이전트 서버 오류 ${response.status}`);
  }

  return response.json() as Promise<message_response>;
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
  const tts_url_ref = useRef<string | null>(null);
  // A ref, not state: the face's animation loop reads it every frame, and
  // going through state would re-render the whole shell mid-utterance.
  const speech_track_ref = useRef<speech_track | null>(null);
  const silent_url_ref = useRef<string | null>(null);
  const audio_unlocked_ref = useRef(false);
  const audio_unlocking_ref = useRef(false);

  const [timeline, set_timeline] = useState<timeline_entry[]>([]);
  const [permission, set_permission] = useState<mic_permission>("pending");
  const [activity, set_activity] = useState<activity_state>("waiting");

  const append_entry = (kind: timeline_kind, text: string, level: timeline_level) => {
    entry_id_ref.current += 1;

    const entry: timeline_entry = {
      id: entry_id_ref.current,
      kind,
      text,
      level,
      time: new Date().toLocaleTimeString(),
    };

    set_timeline((current) => [...current, entry]);
  };

  const log_system = (text: string) => append_entry("system", text, "info");
  const log_error = (text: string) => append_entry("system", text, "error");
  const add_user = (text: string) => append_entry("user", text, "info");
  const add_agent = (text: string) => append_entry("agent", text, "info");

  const stop_speaking = () => {
    const audio = audio_ref.current;

    if (audio) {
      audio.pause();
      audio.currentTime = 0;
    }

    speech_track_ref.current = null;
    set_activity("waiting");
  };

  // Play a short silent clip on the real audio element, inside the tap gesture,
  // so iOS permits the later (post-fetch) TTS playback. The element's play/end
  // handlers are suppressed during this unlock via audio_unlocking_ref.
  const unlock_audio = () => {
    const audio = audio_ref.current;

    if (audio === null || audio_unlocked_ref.current) {
      return;
    }

    if (silent_url_ref.current === null) {
      silent_url_ref.current = make_silent_wav_url();
    }

    audio_unlocking_ref.current = true;
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
        audio_unlocking_ref.current = false;
      });

    audio_unlocked_ref.current = true;
  };

  // TTS runs on the standalone TTS service (relative /tts, proxied), so the
  // voice is ours and not tied to the phone's built-in speech engine.
  const speak_text = async (text: string) => {
    const audio = audio_ref.current;

    if (audio === null) {
      return;
    }

    // Owned here rather than in run_turn: speak_text is deliberately not
    // awaited, so run_turn returns while the audio is still being made.
    set_activity("synthesizing");

    try {
      // /speak returns the WAV together with its viseme timeline, from one
      // synthesis — the two cannot be fetched separately because the duration
      // predictor is stochastic and would give the face the wrong timings.
      const spoken = await fetch_spoken_reply(text);

      if (tts_url_ref.current !== null) {
        URL.revokeObjectURL(tts_url_ref.current);
      }

      tts_url_ref.current = spoken.audio_url;
      speech_track_ref.current = spoken.track;
      audio.src = spoken.audio_url;
      await audio.play();
    } catch (error) {
      speech_track_ref.current = null;
      set_activity("waiting");
      log_error(error instanceof Error ? error.message : "TTS 재생 실패");
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
      set_activity("waiting");
      return;
    }

    try {
      set_activity("transcribing");
      log_system("음성 전사 요청");
      const text = await transcribe_audio(blob);
      add_user(text);

      set_activity("thinking");
      log_system("에이전트 요청");
      const data = await send_message(text);
      add_agent(data.text);

      log_system("TTS 재생");
      // Not awaited on purpose, so a long synthesis does not block the UI.
      // speak_text carries the activity state from here on.
      void speak_text(data.text);
    } catch (error) {
      log_error(error instanceof Error ? error.message : "처리 실패");
      set_activity("waiting");
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
      void run_turn(blob);
    };

    recorder.start();
    set_activity("recording");
    log_system("녹음 시작");
  };

  const handle_mic_click = async () => {
    // Must run synchronously inside the tap gesture to unlock iOS audio.
    unlock_audio();

    if (activity === "recording") {
      media_recorder_ref.current?.stop();
      return;
    }

    // Stop any TTS playback first so iOS frees the audio session before we grab
    // the mic — otherwise the fresh stream can start muted for a moment.
    stop_speaking();

    const stream = await acquire_microphone();

    if (!stream) {
      return;
    }

    start_turn_recording(stream);
  };

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
  const layout: layout_kind = hash === "#/face" ? "face" : "console";
  const mood = pick_mood(permission, activity);
  const status_label =
    permission === "denied" ? "마이크 권한이 없어요" : activity_labels[activity];

  // The console body is wrapped in a single fragment so that both layouts
  // render exactly two children, [branch, audio]. React reconciles by
  // position: if the child count differed, the <audio> element would be
  // destroyed and recreated on every view switch, and since audio_unlocked_ref
  // would still say "true" it would never be unlocked again — permanently
  // silent TTS on iOS. Do not flatten this.
  return (
    <main className={layout === "face" ? "face_root" : "app_shell"}>
      {layout === "face" ? (
        createElement(face_view, {
          mood,
          status_label,
          audio_ref,
          speech_track_ref,
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
              <div className={`speaker_lamp ${activity === "speaking" ? "on" : ""}`}>
                {activity === "speaking" ? <Volume2 size={18} /> : <VolumeX size={18} />}
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
              className={`mic_button ${activity === "recording" ? "recording" : ""}`}
              type="button"
              onClick={() => void handle_mic_click()}
              disabled={is_busy(activity)}
              aria-label={activity === "recording" ? "녹음 정지" : "말하기 시작"}
            >
              {activity === "recording" ? <Square size={30} /> : <Mic size={30} />}
              <span>
                {activity === "recording"
                  ? "정지"
                  : is_busy(activity)
                    ? "처리 중"
                    : "말하기"}
              </span>
            </button>
          </footer>
        </>
      )}

      <audio
        ref={audio_ref}
        hidden
        onPlay={() => {
          if (audio_unlocking_ref.current) {
            return;
          }

          set_activity("speaking");
          log_system("재생 시작");
        }}
        onEnded={() => {
          if (audio_unlocking_ref.current) {
            return;
          }

          set_activity("waiting");
          log_system("재생 완료");
        }}
        onError={() => {
          if (audio_unlocking_ref.current) {
            return;
          }

          set_activity("waiting");
          log_error("TTS 재생 오류");
        }}
      />
    </main>
  );
}
