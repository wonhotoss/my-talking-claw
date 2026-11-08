"""Engine selection.

One image ships one engine - the dependency sets do not overlap (MeloTTS needs
torch + mecab-ko + unidic, Piper needs onnxruntime) and putting both in one
image would carry ~6GB of them. The adapters all import their model or SDK
lazily, so this module resolves cleanly in any image and picks by environment.

    TTS_ENGINE=melotts   (default) app/engines/melo.py
    TTS_ENGINE=piper               app/engines/piper.py
    TTS_ENGINE=azure               app/engines/azure.py   (cloud; needs AZURE_SPEECH_*)

Switching is manual (env + restart). There is no runtime fallback between the
cloud and the local engines by decision (뇌중심-로드맵.md "비용·전제").
"""

import os

from app import speech


def engine_for_environment() -> speech.speech_engine:
    name = os.environ.get("TTS_ENGINE", "melotts")

    if name == "melotts":
        from app.engines.melo import melo_engine

        return melo_engine()

    if name == "piper":
        from app.engines.piper import piper_engine

        return piper_engine()

    if name == "azure":
        from app.engines.azure import azure_engine

        return azure_engine()

    raise RuntimeError(f"unknown TTS_ENGINE {name!r}; expected 'melotts', 'piper' or 'azure'")
