"""The transcription contract and engine selection for the voice service.

    STT_ENGINE=whisper  (default) app/engines/whisper.py - faster-whisper on this host
    STT_ENGINE=azure              app/engines/azure.py   - Azure Speech (needs AZURE_SPEECH_*)

The device loop (audio/) picks its engine with the same variable, so the phone
- which transcribes through this service - and the device switch together and
fall back together. Switching is manual: env + restart, no runtime fallback,
by decision (뇌중심-로드맵.md "비용·전제"). Both engines import their model or
SDK lazily, so this module loads on any host.
"""

import os
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class transcription_result:
    text: str
    language: str
    duration_seconds: float


class stt_engine(Protocol):
    # Reported by /health. Named after whisper's knobs; the cloud engine fills
    # them with what it has (voice, region, "cloud") so the contract holds.
    model_name: str
    device: str
    compute_type: str
    default_language: str

    def transcribe(self, audio_path: str, language: str) -> transcription_result: ...


def engine_for_environment() -> stt_engine:
    name = os.environ.get("STT_ENGINE", "whisper")

    if name == "whisper":
        from app.engines.whisper import whisper_engine

        return whisper_engine()

    if name == "azure":
        from app.engines.azure import azure_engine

        return azure_engine()

    raise RuntimeError(f"unknown STT_ENGINE {name!r}; expected 'whisper' or 'azure'")
