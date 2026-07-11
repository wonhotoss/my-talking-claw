import pytest
from fastapi.testclient import TestClient

from app import server
from app.stt import transcription_result


client = TestClient(server.app)


def test_get_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["model"] == server.engine.model_name
    assert body["device"] == server.engine.device
    assert body["compute_type"] == server.engine.compute_type


def test_post_transcribe_returns_text(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, str] = {}

    def fake_transcribe(audio_path: str, language: str) -> transcription_result:
        captured["language"] = language
        return transcription_result(text="안녕하세요", language="ko", duration_seconds=1.5)

    monkeypatch.setattr(server.engine, "transcribe", fake_transcribe)

    response = client.post(
        "/transcribe",
        files={"file": ("clip.m4a", b"fake-audio-bytes", "audio/mp4")},
        data={"language": "ko"},
    )

    assert response.status_code == 200
    assert response.json() == {
        "text": "안녕하세요",
        "language": "ko",
        "duration_seconds": 1.5,
    }
    assert captured["language"] == "ko"


def test_post_transcribe_falls_back_to_default_language(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, str] = {}

    def fake_transcribe(audio_path: str, language: str) -> transcription_result:
        captured["language"] = language
        return transcription_result(text="네", language="ko", duration_seconds=0.5)

    monkeypatch.setattr(server.engine, "transcribe", fake_transcribe)

    response = client.post(
        "/transcribe",
        files={"file": ("clip.m4a", b"fake-audio-bytes", "audio/mp4")},
    )

    assert response.status_code == 200
    assert captured["language"] == server.engine.default_language


def test_post_transcribe_rejects_empty_file() -> None:
    response = client.post(
        "/transcribe",
        files={"file": ("empty.m4a", b"", "audio/mp4")},
        data={"language": "ko"},
    )

    assert response.status_code == 400
    assert response.json() == {"detail": "audio file must not be empty"}


def test_post_transcribe_rejects_silence(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_transcribe(audio_path: str, language: str) -> transcription_result:
        return transcription_result(text="", language="ko", duration_seconds=2.0)

    monkeypatch.setattr(server.engine, "transcribe", fake_transcribe)

    response = client.post(
        "/transcribe",
        files={"file": ("silence.m4a", b"fake-audio-bytes", "audio/mp4")},
        data={"language": "ko"},
    )

    assert response.status_code == 422
    assert response.json() == {"detail": "no speech recognized"}
