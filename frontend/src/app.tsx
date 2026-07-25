import { useEffect, useRef, useState } from "react";
import { Mic, Square, Volume2, VolumeX } from "lucide-react";

type message_response = {
  text: string;
};

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
type turn_state = "idle" | "recording" | "processing";

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

  if (!response.ok) {
    throw new Error(`STT 서버 오류 ${response.status}`);
  }

  const data = (await response.json()) as { text: string };

  return data.text;
}

export function app_shell() {
  const media_stream_ref = useRef<MediaStream | undefined>(undefined);
  const media_recorder_ref = useRef<MediaRecorder | undefined>(undefined);
  const audio_chunks_ref = useRef<Blob[]>([]);
  const entry_id_ref = useRef(0);
  const timeline_end_ref = useRef<HTMLDivElement | null>(null);
  const speech_primed_ref = useRef(false);
  const speech_started_ref = useRef(false);

  const [timeline, set_timeline] = useState<timeline_entry[]>([]);
  const [permission, set_permission] = useState<mic_permission>("pending");
  const [turn, set_turn] = useState<turn_state>("idle");
  const [is_speaking, set_is_speaking] = useState(false);

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
    if ("speechSynthesis" in window) {
      window.speechSynthesis.cancel();
    }

    set_is_speaking(false);
  };

  // iOS (WebKit, incl. Chrome iOS) ignores speechSynthesis.speak() unless the
  // engine was first activated inside a user gesture. The agent reply is spoken
  // several awaits after the tap, so the engine is primed here — synchronously
  // inside the tap handler — with a silent utterance.
  const prime_speech = () => {
    if (speech_primed_ref.current || !("speechSynthesis" in window)) {
      return;
    }

    const unlock = new SpeechSynthesisUtterance(" ");
    unlock.volume = 0;
    window.speechSynthesis.speak(unlock);
    speech_primed_ref.current = true;
  };

  const speak_text = (text: string) => {
    if (!("speechSynthesis" in window)) {
      log_error("이 브라우저는 TTS를 지원하지 않습니다.");
      return;
    }

    window.speechSynthesis.cancel();
    speech_started_ref.current = false;

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "ko-KR";
    utterance.onstart = () => {
      speech_started_ref.current = true;
      set_is_speaking(true);
      log_system("재생 시작");
    };
    utterance.onend = () => {
      set_is_speaking(false);
      log_system("재생 완료");
    };
    utterance.onerror = (event) => {
      set_is_speaking(false);
      log_error(`TTS 재생 오류: ${event.error}`);
    };

    window.speechSynthesis.speak(utterance);

    // Surface the silent-no-op case so the timeline shows why nothing played.
    window.setTimeout(() => {
      if (!speech_started_ref.current) {
        log_error("TTS가 시작되지 않음 (iOS 제스처/오디오 세션 제약 가능성)");
      }
    }, 1500);
  };

  const acquire_microphone = async (): Promise<MediaStream | undefined> => {
    if (media_stream_ref.current) {
      return media_stream_ref.current;
    }

    if (!navigator.mediaDevices?.getUserMedia) {
      set_permission("denied");
      log_error("마이크를 사용할 수 없습니다. HTTPS/보안 컨텍스트를 확인하세요.");
      return undefined;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      // The stream is kept open for the whole session so day 3 VAD can listen
      // continuously without re-prompting.
      media_stream_ref.current = stream;
      set_permission("granted");
      log_system("마이크 준비됨");
      return stream;
    } catch (error) {
      set_permission("denied");
      log_error(`마이크 권한 실패: ${error instanceof Error ? error.name : "unknown"}`);
      return undefined;
    }
  };

  const run_turn = async (blob: Blob) => {
    set_turn("processing");

    if (blob.size === 0) {
      log_error("녹음된 오디오가 없습니다.");
      set_turn("idle");
      return;
    }

    try {
      log_system("음성 전사 요청");
      const text = await transcribe_audio(blob);
      add_user(text);

      log_system("에이전트 요청");
      const data = await send_message(text);
      add_agent(data.text);

      log_system("TTS 재생");
      speak_text(data.text);
    } catch (error) {
      log_error(error instanceof Error ? error.message : "처리 실패");
    } finally {
      set_turn("idle");
    }
  };

  const start_turn_recording = (stream: MediaStream) => {
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
      void run_turn(blob);
    };

    recorder.start();
    set_turn("recording");
    log_system("녹음 시작");
  };

  const handle_mic_click = async () => {
    // Must run synchronously inside the tap gesture to unlock iOS TTS.
    prime_speech();

    if (turn === "recording") {
      media_recorder_ref.current?.stop();
      return;
    }

    const stream = await acquire_microphone();

    if (!stream) {
      return;
    }

    stop_speaking();
    start_turn_recording(stream);
  };

  useEffect(() => {
    void acquire_microphone();
  }, []);

  useEffect(() => {
    timeline_end_ref.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [timeline]);

  return (
    <main className="app_shell">
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
          <div className={`speaker_lamp ${is_speaking ? "on" : ""}`}>
            {is_speaking ? <Volume2 size={18} /> : <VolumeX size={18} />}
            <span>{is_speaking ? "재생 중" : "대기"}</span>
          </div>
        </div>
      </header>

      <section className="timeline" aria-label="대화 및 로그 타임라인">
        {timeline.length === 0 && (
          <p className="timeline_empty">마이크 버튼을 눌러 대화를 시작하세요.</p>
        )}
        {timeline.map((entry) => (
          <div key={entry.id} className={`entry entry_${entry.kind}`}>
            {entry.kind === "system" ? (
              <span className={`system_line ${entry.level === "error" ? "system_error" : ""}`}>
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
          className={`mic_button ${turn === "recording" ? "recording" : ""}`}
          type="button"
          onClick={() => void handle_mic_click()}
          disabled={turn === "processing"}
          aria-label={turn === "recording" ? "녹음 정지" : "말하기 시작"}
        >
          {turn === "recording" ? <Square size={30} /> : <Mic size={30} />}
          <span>
            {turn === "recording" ? "정지" : turn === "processing" ? "처리 중" : "말하기"}
          </span>
        </button>
      </footer>
    </main>
  );
}
