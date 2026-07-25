import os
from dataclasses import dataclass

from faster_whisper import WhisperModel


@dataclass(frozen=True)
class transcription_result:
    text: str
    language: str
    duration_seconds: float


class stt_engine:
    """On-device speech-to-text backed by faster-whisper.

    Configuration comes from the environment so the service can be moved to a
    more capable machine without code changes:

    - WHISPER_MODEL         (default base; set medium/large-v3 for higher quality)
    - WHISPER_DEVICE        (default cpu; cuda on a GPU machine)
    - WHISPER_COMPUTE_TYPE  (default int8; float16 on cuda)
    - WHISPER_LANGUAGE      (default ko; fallback when a request omits language)
    """

    def __init__(self) -> None:
        self.model_name = os.environ.get("WHISPER_MODEL", "base")
        self.device = os.environ.get("WHISPER_DEVICE", "cpu")
        self.compute_type = os.environ.get("WHISPER_COMPUTE_TYPE", "int8")
        self.default_language = os.environ.get("WHISPER_LANGUAGE", "ko")
        # Loading the model is expensive, so it is cached lazily on first use.
        # This is the performance-critical exception to the no-cached-state rule.
        self._model: WhisperModel | None = None

    @property
    def model(self) -> WhisperModel:
        if self._model is None:
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
            vad_filter=True,
        )

        text = "".join(segment.text for segment in segments).strip()

        return transcription_result(
            text=text,
            language=info.language,
            duration_seconds=info.duration,
        )
