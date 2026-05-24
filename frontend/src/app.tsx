import { FormEvent, useMemo, useRef, useState } from "react";
import {
  Mic,
  MicOff,
  Send,
  Square,
  Volume2,
  Wifi,
  WifiOff,
} from "lucide-react";

type message_response = {
  text: string;
};

type api_state = "idle" | "sending" | "ready" | "error";
type mic_state = "idle" | "requesting" | "recording" | "ready" | "error";
type speech_state = "idle" | "listening" | "speaking";

const api_base_url = import.meta.env.VITE_API_BASE_URL || "";

function get_speech_recognition_constructor(): speech_recognition_constructor | undefined {
  return window.SpeechRecognition || window.webkitSpeechRecognition;
}

function get_audio_context_constructor() {
  return window.AudioContext || window.webkitAudioContext;
}

function get_supported_recorder_mime_types() {
  if (!("MediaRecorder" in window)) {
    return [];
  }

  const mime_types = [
    "audio/mp4;codecs=mp4a.40.2",
    "audio/mp4",
    "video/mp4",
    "audio/webm;codecs=opus",
    "audio/webm",
  ];

  return mime_types.filter((mime_type) => MediaRecorder.isTypeSupported(mime_type));
}

function get_speech_support() {
  const recognition_source = window.SpeechRecognition
    ? "SpeechRecognition"
    : window.webkitSpeechRecognition
      ? "webkitSpeechRecognition"
      : "none";

  return {
    has_audio_context: Boolean(get_audio_context_constructor()),
    has_media_devices: Boolean(navigator.mediaDevices?.getUserMedia),
    has_media_recorder: "MediaRecorder" in window,
    has_speech_recognition: Boolean(window.SpeechRecognition),
    has_webkit_speech_recognition: Boolean(window.webkitSpeechRecognition),
    host: window.location.host,
    protocol: window.location.protocol,
    recognition_source,
    secure_context: window.isSecureContext,
    stt: Boolean(get_speech_recognition_constructor()),
    supported_recorder_mime_types: get_supported_recorder_mime_types(),
    tts: "speechSynthesis" in window,
    user_agent: navigator.userAgent,
  };
}

function format_boolean(value: boolean) {
  return value ? "yes" : "no";
}

async function send_message(text: string): Promise<message_response> {
  const response = await fetch(`${api_base_url}/api/message`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ text }),
  });

  if (!response.ok) {
    throw new Error(`backend returned ${response.status}`);
  }

  return response.json() as Promise<message_response>;
}

export function app_shell() {
  const speech_support = useMemo(get_speech_support, []);
  const recognition_ref = useRef<speech_recognition | undefined>(undefined);
  const animation_frame_ref = useRef<number | undefined>(undefined);
  const audio_context_ref = useRef<AudioContext | undefined>(undefined);
  const audio_source_ref = useRef<MediaStreamAudioSourceNode | undefined>(undefined);
  const media_recorder_ref = useRef<MediaRecorder | undefined>(undefined);
  const media_stream_ref = useRef<MediaStream | undefined>(undefined);
  const audio_chunks_ref = useRef<Blob[]>([]);
  const requested_recorder_stop_ref = useRef(false);
  const [speech_state, set_speech_state] = useState<speech_state>("idle");
  const [mic_state, set_mic_state] = useState<mic_state>("idle");
  const [api_state, set_api_state] = useState<api_state>("idle");
  const [user_text, set_user_text] = useState("");
  const [agent_text, set_agent_text] = useState("");
  const [audio_url, set_audio_url] = useState("");
  const [mic_events, set_mic_events] = useState<string[]>([]);
  const [mic_level, set_mic_level] = useState(0);
  const [recorded_size, set_recorded_size] = useState(0);
  const [recorded_type, set_recorded_type] = useState("");
  const [stt_events, set_stt_events] = useState<string[]>([]);
  const [stt_error_text, set_stt_error_text] = useState("");
  const [status_text, set_status_text] = useState("대기");

  const can_send = user_text.trim() !== "" && api_state !== "sending";
  const is_listening = speech_state === "listening";
  const is_recording = mic_state === "recording";
  const is_requesting_mic = mic_state === "requesting";
  const is_speaking = speech_state === "speaking";
  const stt_runtime_aborted = stt_error_text === "aborted";
  const stt_label = speech_support.stt
    ? stt_runtime_aborted
      ? "중단"
      : "가능"
    : "미지원";
  const route_to_microphone_test =
    stt_runtime_aborted && speech_support.has_media_devices;
  const use_microphone_button = !speech_support.stt || route_to_microphone_test;
  const show_speech_diagnostics =
    !speech_support.stt || stt_error_text !== "" || stt_events.length > 0;
  const show_mic_panel =
    !speech_support.stt ||
    route_to_microphone_test ||
    mic_state !== "idle" ||
    mic_events.length > 0;

  const append_mic_event = (message: string) => {
    const timestamp = new Date().toLocaleTimeString();

    set_mic_events((current_mic_events) =>
      [`${timestamp} ${message}`, ...current_mic_events].slice(0, 8),
    );
  };

  const append_stt_event = (message: string) => {
    const timestamp = new Date().toLocaleTimeString();

    set_stt_events((current_stt_events) =>
      [`${timestamp} ${message}`, ...current_stt_events].slice(0, 8),
    );
  };

  const submit_text = async (text: string) => {
    const cleaned_text = text.trim();

    if (cleaned_text === "") {
      throw new Error("text must not be empty");
    }

    set_api_state("sending");
    set_status_text("전송 중");

    try {
      const data = await send_message(cleaned_text);
      set_agent_text(data.text);
      set_api_state("ready");
      set_status_text("응답 수신");
      speak_text(data.text);
    } catch (error) {
      set_api_state("error");
      set_status_text(error instanceof Error ? error.message : "전송 실패");
    }
  };

  const speak_text = (text: string) => {
    if (!speech_support.tts) {
      set_status_text("TTS 미지원");
      return;
    }

    window.speechSynthesis.cancel();

    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "ko-KR";
    utterance.onend = () => {
      set_speech_state("idle");
      set_status_text("대기");
    };
    utterance.onerror = () => {
      set_speech_state("idle");
      set_status_text("TTS 오류");
    };

    set_speech_state("speaking");
    set_status_text("재생 중");
    window.speechSynthesis.speak(utterance);
  };

  const start_listening = () => {
    const speech_recognition_constructor = get_speech_recognition_constructor();

    if (!speech_recognition_constructor) {
      set_status_text("STT 미지원");
      return;
    }

    const recognition = new speech_recognition_constructor();
    recognition.lang = "ko-KR";
    recognition.continuous = false;
    recognition.interimResults = true;
    set_stt_error_text("");
    set_stt_events([]);

    ["start", "audiostart", "soundstart", "speechstart", "speechend", "soundend", "audioend", "nomatch"].forEach(
      (event_name) => {
        recognition.addEventListener(event_name, () => {
          append_stt_event(event_name);
        });
      },
    );

    recognition.onresult = (event) => {
      const text = Array.from(event.results)
        .map((result) => result[0].transcript)
        .join("");

      set_user_text(text);

      const last_result = event.results[event.results.length - 1];
      append_stt_event(`result: final=${last_result.isFinal ? "yes" : "no"}`);

      if (last_result.isFinal) {
        void submit_text(text);
      }
    };

    recognition.onerror = (event) => {
      set_speech_state("idle");
      set_stt_error_text(event.error);
      append_stt_event(`error: ${event.error}`);

      if (event.error === "aborted" && speech_support.has_media_devices) {
        set_status_text("STT 중단, 마이크 확인");
        void start_microphone_test();
        return;
      }

      set_status_text(
        event.error === "not-allowed" ? "STT 권한 차단" : `STT 오류: ${event.error}`,
      );
    };

    recognition.onend = () => {
      append_stt_event("end");
      set_speech_state((current_speech_state) =>
        current_speech_state === "listening" ? "idle" : current_speech_state,
      );
    };

    recognition_ref.current = recognition;
    window.speechSynthesis.cancel();
    set_speech_state("listening");
    set_status_text("듣는 중");
    recognition.start();
  };

  const stop_audio_meter = () => {
    if (animation_frame_ref.current !== undefined) {
      cancelAnimationFrame(animation_frame_ref.current);
      animation_frame_ref.current = undefined;
    }

    audio_source_ref.current?.disconnect();
    audio_source_ref.current = undefined;

    void audio_context_ref.current?.close();
    audio_context_ref.current = undefined;
    set_mic_level(0);
  };

  const release_microphone = () => {
    stop_audio_meter();
    media_stream_ref.current?.getTracks().forEach((track) => track.stop());
    media_stream_ref.current = undefined;
  };

  const finish_microphone_test = (message: string, next_mic_state: mic_state) => {
    release_microphone();
    media_recorder_ref.current = undefined;
    set_mic_state(next_mic_state);
    set_status_text(message);
    append_mic_event(message);
  };

  const start_audio_meter = async (stream: MediaStream) => {
    const audio_context_constructor = get_audio_context_constructor();

    if (!audio_context_constructor) {
      append_mic_event("AudioContext 미지원");
      return;
    }

    const audio_context = new audio_context_constructor();
    await audio_context.resume();

    const analyser = audio_context.createAnalyser();
    analyser.fftSize = 512;

    const source = audio_context.createMediaStreamSource(stream);
    const samples = new Uint8Array(analyser.fftSize);

    source.connect(analyser);
    audio_context_ref.current = audio_context;
    audio_source_ref.current = source;

    const update_level = () => {
      analyser.getByteTimeDomainData(samples);

      const squared_sum = samples.reduce((sum, sample) => {
        const centered_sample = sample - 128;

        return sum + centered_sample * centered_sample;
      }, 0);
      const root_mean_square = Math.sqrt(squared_sum / samples.length) / 128;

      set_mic_level(Math.min(100, Math.round(root_mean_square * 220)));
      animation_frame_ref.current = requestAnimationFrame(update_level);
    };

    update_level();
    append_mic_event("audio meter started");
  };

  const start_media_recorder = (stream: MediaStream) => {
    if (!("MediaRecorder" in window)) {
      append_mic_event("MediaRecorder unavailable");
      return;
    }

    try {
      const recorder = new MediaRecorder(stream);

      requested_recorder_stop_ref.current = false;
      audio_chunks_ref.current = [];
      media_recorder_ref.current = recorder;

      recorder.onstart = () => {
        set_recorded_size(0);
        set_recorded_type(recorder.mimeType || "unknown");
        append_mic_event(`recorder started: ${recorder.mimeType || "unknown"}`);
      };

      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) {
          audio_chunks_ref.current = [...audio_chunks_ref.current, event.data];
          set_recorded_size(
            audio_chunks_ref.current.reduce((sum, chunk) => sum + chunk.size, 0),
          );
          append_mic_event(`dataavailable: ${event.data.size} bytes`);
        }
      };

      recorder.onstop = () => {
        const blob = new Blob(audio_chunks_ref.current, {
          type: recorder.mimeType,
        });

        if (blob.size > 0) {
          if (audio_url !== "") {
            URL.revokeObjectURL(audio_url);
          }

          set_audio_url(URL.createObjectURL(blob));
          set_recorded_size(blob.size);
          set_recorded_type(blob.type || recorder.mimeType || "unknown");
        }

        append_mic_event(`recorder stopped: ${blob.size} bytes`);

        if (requested_recorder_stop_ref.current) {
          finish_microphone_test("녹음 완료", blob.size > 0 ? "ready" : "idle");
          return;
        }

        set_status_text("마이크 입력 중");
        append_mic_event("recorder stopped unexpectedly");
      };

      recorder.onerror = (event) => {
        append_mic_event(`recorder error: ${event.error.name}`);
        set_status_text(`녹음 오류: ${event.error.name}`);
      };

      recorder.start();
    } catch (error) {
      append_mic_event(error instanceof Error ? error.message : "recorder start failed");
    }
  };

  const start_microphone_test = async () => {
    if (!navigator.mediaDevices?.getUserMedia) {
      set_mic_state("error");
      set_status_text("HTTPS 필요");
      append_mic_event("mediaDevices unavailable");
      return;
    }

    set_mic_state("requesting");
    set_status_text("마이크 요청");
    append_mic_event("getUserMedia requested");
    window.speechSynthesis.cancel();

    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const [audio_track] = stream.getAudioTracks();

      if (audio_url !== "") {
        URL.revokeObjectURL(audio_url);
        set_audio_url("");
      }

      media_stream_ref.current = stream;
      set_recorded_size(0);
      set_recorded_type("");
      set_mic_state("recording");
      set_status_text("마이크 입력 중");
      append_mic_event(
        `stream granted: ${audio_track?.label || "audio track"}, ${audio_track?.readyState || "unknown"}`,
      );

      if (audio_track) {
        audio_track.onended = () => {
          finish_microphone_test("마이크 트랙 종료", "error");
        };

        audio_track.onmute = () => {
          append_mic_event("audio track muted");
        };

        audio_track.onunmute = () => {
          append_mic_event("audio track unmuted");
        };
      }

      await start_audio_meter(stream);
      start_media_recorder(stream);
    } catch (error) {
      set_mic_state("error");
      set_status_text(error instanceof Error ? error.message : "마이크 실패");
      append_mic_event(error instanceof Error ? error.name : "getUserMedia failed");
      release_microphone();
    }
  };

  const stop_microphone_test = () => {
    const recorder = media_recorder_ref.current;

    requested_recorder_stop_ref.current = true;

    if (recorder?.state === "recording") {
      recorder.stop();
      set_status_text("녹음 저장");
      append_mic_event("recorder stop requested");
      return;
    }

    finish_microphone_test(
      audio_url !== "" ? "녹음 완료" : "마이크 종료",
      audio_url !== "" ? "ready" : "idle",
    );
  };

  const clear_microphone_test = () => {
    if (audio_url !== "") {
      URL.revokeObjectURL(audio_url);
      set_audio_url("");
    }

    set_recorded_size(0);
    set_recorded_type("");
    set_mic_events([]);
    set_mic_state("idle");
    set_status_text("대기");
  };

  const stop_listening = () => {
    recognition_ref.current?.stop();
    set_speech_state("idle");
    set_status_text("대기");
  };

  const stop_speaking = () => {
    window.speechSynthesis.cancel();
    set_speech_state("idle");
    set_status_text("대기");
  };

  const submit_form = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    void submit_text(user_text);
  };

  return (
    <main className="app_shell">
      <header className="top_bar">
        <div>
          <p className="eyebrow">My Talking Claw</p>
          <h1>음성 콘솔</h1>
        </div>
        <div className={`status_pill status_${api_state}`}>
          {api_state === "error" ? <WifiOff size={18} /> : <Wifi size={18} />}
          <span>{status_text}</span>
        </div>
      </header>

      <section className="voice_panel" aria-label="음성 대화">
        <div className="support_grid">
          <span className={speech_support.stt && !stt_runtime_aborted ? "support_ok" : "support_bad"}>
            STT {stt_label}
          </span>
          <span className={speech_support.tts ? "support_ok" : "support_bad"}>
            TTS {speech_support.tts ? "가능" : "미지원"}
          </span>
        </div>

        {show_speech_diagnostics && (
          <details className="diagnostics_panel" open>
            <summary>STT 진단</summary>
            <dl>
              <div>
                <dt>SpeechRecognition</dt>
                <dd>{format_boolean(speech_support.has_speech_recognition)}</dd>
              </div>
              <div>
                <dt>webkitSpeechRecognition</dt>
                <dd>{format_boolean(speech_support.has_webkit_speech_recognition)}</dd>
              </div>
              <div>
                <dt>recognition source</dt>
                <dd>{speech_support.recognition_source}</dd>
              </div>
              <div>
                <dt>secureContext</dt>
                <dd>{format_boolean(speech_support.secure_context)}</dd>
              </div>
              <div>
                <dt>mediaDevices</dt>
                <dd>{format_boolean(speech_support.has_media_devices)}</dd>
              </div>
              <div>
                <dt>MediaRecorder</dt>
                <dd>{format_boolean(speech_support.has_media_recorder)}</dd>
              </div>
              <div>
                <dt>AudioContext</dt>
                <dd>{format_boolean(speech_support.has_audio_context)}</dd>
              </div>
              <div>
                <dt>record types</dt>
                <dd>
                  {speech_support.supported_recorder_mime_types.join(", ") || "none"}
                </dd>
              </div>
              <div>
                <dt>last STT error</dt>
                <dd>{stt_error_text || "none"}</dd>
              </div>
              <div>
                <dt>origin</dt>
                <dd>{`${speech_support.protocol}//${speech_support.host}`}</dd>
              </div>
              <div>
                <dt>userAgent</dt>
                <dd>{speech_support.user_agent}</dd>
              </div>
            </dl>
            {stt_events.length > 0 && (
              <ol className="event_log">
                {stt_events.map((stt_event) => (
                  <li key={stt_event}>{stt_event}</li>
                ))}
              </ol>
            )}
          </details>
        )}

        <div className="primary_controls">
          <button
            className={`round_button ${is_listening || is_recording ? "active" : ""}`}
            type="button"
            onClick={
              use_microphone_button
                ? is_recording
                  ? stop_microphone_test
                  : start_microphone_test
                : is_listening
                  ? stop_listening
                  : start_listening
            }
            aria-label={
              use_microphone_button
                ? is_recording
                  ? "마이크 녹음 중지"
                  : "마이크 녹음 시작"
                : is_listening
                  ? "음성 인식 중지"
                  : "음성 인식 시작"
            }
            title={
              use_microphone_button
                ? is_recording
                  ? "마이크 녹음 중지"
                  : "마이크 녹음 시작"
                : is_listening
                  ? "음성 인식 중지"
                  : "음성 인식 시작"
            }
            disabled={api_state === "sending" || is_speaking || is_requesting_mic}
          >
            {use_microphone_button ? (
              is_recording ? (
                <MicOff size={34} />
              ) : (
                <Mic size={34} />
              )
            ) : is_listening ? (
              <MicOff size={34} />
            ) : (
              <Mic size={34} />
            )}
          </button>
          <button
            className="icon_button"
            type="button"
            onClick={stop_speaking}
            aria-label="음성 재생 중지"
            title="음성 재생 중지"
            disabled={!is_speaking}
          >
            <Square size={22} />
          </button>
        </div>

        <form className="message_form" onSubmit={submit_form}>
          <label htmlFor="user_text">사용자</label>
          <textarea
            id="user_text"
            value={user_text}
            onChange={(event) => set_user_text(event.target.value)}
            rows={4}
            spellCheck="false"
          />
          <button className="send_button" type="submit" disabled={!can_send}>
            <Send size={19} />
            <span>{api_state === "sending" ? "전송 중" : "전송"}</span>
          </button>
        </form>

        {show_mic_panel && (
          <section className="mic_panel" aria-label="마이크 진단">
            <div className="response_header">
              <span>마이크</span>
              <button
                className="icon_button"
                type="button"
                onClick={clear_microphone_test}
                aria-label="마이크 진단 초기화"
                title="마이크 진단 초기화"
                disabled={is_recording || is_requesting_mic}
              >
                <Square size={19} />
              </button>
            </div>
            <div className="level_meter" aria-label="마이크 입력 레벨">
              <span style={{ width: `${mic_level}%` }} />
            </div>
            <dl className="mic_metrics">
              <div>
                <dt>state</dt>
                <dd>{mic_state}</dd>
              </div>
              <div>
                <dt>level</dt>
                <dd>{mic_level}</dd>
              </div>
              <div>
                <dt>recorded</dt>
                <dd>{recorded_size > 0 ? `${recorded_size} bytes` : "none"}</dd>
              </div>
              <div>
                <dt>type</dt>
                <dd>{recorded_type || "none"}</dd>
              </div>
            </dl>
            {mic_events.length > 0 && (
              <ol className="event_log">
                {mic_events.map((mic_event) => (
                  <li key={mic_event}>{mic_event}</li>
                ))}
              </ol>
            )}
          </section>
        )}

        {audio_url !== "" && (
          <section className="response_area" aria-label="마이크 녹음 확인">
            <div className="response_header">
              <span>마이크</span>
            </div>
            <audio className="audio_preview" controls src={audio_url} />
          </section>
        )}

        <section className="response_area" aria-label="에이전트 응답">
          <div className="response_header">
            <span>에이전트</span>
            <button
              className="icon_button"
              type="button"
              onClick={() => speak_text(agent_text)}
              aria-label="응답 다시 재생"
              title="응답 다시 재생"
              disabled={agent_text.trim() === "" || !speech_support.tts}
            >
              <Volume2 size={21} />
            </button>
          </div>
          <p>{agent_text || "응답 대기"}</p>
        </section>
      </section>
    </main>
  );
}
