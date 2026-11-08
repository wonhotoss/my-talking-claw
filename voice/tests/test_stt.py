import pytest

from app.engines.whisper import whisper_engine
from app.stt import engine_for_environment


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
    engine = whisper_engine()
    model = fake_model()
    engine._model = model

    engine.transcribe("clip.wav", language="ko")

    assert engine.beam_size == 1
    assert model.calls[0]["beam_size"] == 1


def test_beam_size_defaults_to_faster_whisper_default(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("WHISPER_BEAM_SIZE", raising=False)

    assert whisper_engine().beam_size == 5


def test_whisper_is_the_default_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("STT_ENGINE", raising=False)

    assert isinstance(engine_for_environment(), whisper_engine)


def test_azure_is_selected_by_name(monkeypatch: pytest.MonkeyPatch) -> None:
    from app.engines.azure import azure_engine

    monkeypatch.setenv("STT_ENGINE", "azure")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "k")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "koreacentral")

    assert isinstance(engine_for_environment(), azure_engine)


def test_an_unknown_engine_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("STT_ENGINE", "vosk")

    with pytest.raises(RuntimeError, match="vosk"):
        engine_for_environment()
