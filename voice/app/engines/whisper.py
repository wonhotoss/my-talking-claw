"""faster-whisper on this host: the day-8/9 engine, kept for manual fallback."""

import os

from app.stt import transcription_result


class whisper_engine:
    """On-device speech-to-text backed by faster-whisper.

    Configuration comes from the environment so the service can be moved to a
    more capable machine without code changes:

    - WHISPER_MODEL         (default base; set medium/large-v3 for higher quality)
    - WHISPER_DEVICE        (default cpu; cuda on a GPU machine)
    - WHISPER_COMPUTE_TYPE  (default int8; float16 on cuda)
    - WHISPER_LANGUAGE      (default ko; fallback when a request omits language)
    - WHISPER_BEAM_SIZE     (default 5, faster-whisper's own; 1 is ~25-35% faster
                             on RPi4 — day-8/day-9 measurements)
    """

    def __init__(self) -> None:
        self.model_name = os.environ.get("WHISPER_MODEL", "base")
        self.device = os.environ.get("WHISPER_DEVICE", "cpu")
        self.compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
        self.default_language = os.environ.get("WHISPER_LANGUAGE", "ko")
        # VAD off by default: it tends to drop short/quiet utterances entirely,
        # yielding empty transcriptions (the /transcribe 422). Enable with
        # WHISPER_VAD=true if silence hallucinations become a problem.
        self.vad_filter = os.environ.get("WHISPER_VAD", "false").lower() == "true"
        self.beam_size = int(os.environ.get("WHISPER_BEAM_SIZE", "5"))
        # Loading the model is expensive, so it is cached lazily on first use.
        # This is the performance-critical exception to the no-cached-state rule.
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.model_name,
                device=self.device,
                compute_type=self.compute_type,
            )

        return self._model

    def transcribe(self, audio_path: str, language: str) -> transcription_result:
        segments, info = self.model.transcribe(
            audio_path,
            language=language,
            vad_filter=self.vad_filter,
            beam_size=self.beam_size,
        )

        text = "".join(segment.text for segment in segments).strip()

        return transcription_result(
            text=text,
            language=info.language,
            duration_seconds=info.duration,
        )
