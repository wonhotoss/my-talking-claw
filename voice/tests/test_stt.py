import pytest

from app.stt import stt_engine


class fake_info:
    language = "ko"
    duration = 1.0


class fake_model:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def transcribe(self, audio_path: str, **kwargs):
        self.calls.append(kwargs)
        return iter([]), fake_info()


def test_beam_size_comes_from_env_and_reaches_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("WHISPER_BEAM_SIZE", "1")
    engine = stt_engine()
    model = fake_model()
    engine._model = model

    engine.transcribe("clip.wav", language="ko")

    assert engine.beam_size == 1
    assert model.calls[0]["beam_size"] == 1


def test_beam_size_defaults_to_faster_whisper_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WHISPER_BEAM_SIZE", raising=False)

    assert stt_engine().beam_size == 5
