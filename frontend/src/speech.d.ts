interface speech_recognition_event extends Event {
  readonly results: SpeechRecognitionResultList;
}

interface speech_recognition_error_event extends Event {
  readonly error: string;
}

interface speech_recognition extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: speech_recognition_event) => void) | undefined;
  onerror: ((event: speech_recognition_error_event) => void) | undefined;
  onend: (() => void) | undefined;
  start: () => void;
  stop: () => void;
}

interface speech_recognition_constructor {
  new (): speech_recognition;
}

interface Window {
  SpeechRecognition?: speech_recognition_constructor;
  webkitAudioContext?: typeof AudioContext;
  webkitSpeechRecognition?: speech_recognition_constructor;
}
