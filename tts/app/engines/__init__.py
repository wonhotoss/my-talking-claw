"""Engine selection.

One image ships one engine - the dependency sets do not overlap (MeloTTS needs
torch + mecab-ko + unidic, Piper needs onnxruntime) and putting both in one
image would carry ~6GB of them. The adapters both import their model lazily, so
this module resolves cleanly in either image and picks by environment.

    TTS_ENGINE=melotts   (default) app/engines/melo.py
    TTS_ENGINE=piper               app/engines/piper.py
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

    raise RuntimeError(f"unknown TTS_ENGINE {name!r}; expected 'melotts' or 'piper'")
