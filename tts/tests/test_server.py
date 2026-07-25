import pytest
from fastapi.testclient import TestClient

from app import server


client = TestClient(server.app)

# A tiny but valid WAV header + silence, returned by the mocked engine.
fake_wav = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x00}\x00\x00\x00}\x00\x00\x01\x00\x08\x00data\x00\x00\x00\x00"


def test_get_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["engine"] == server.engine.engine
    assert body["language"] == server.engine.language


def test_post_synthesize_returns_wav(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_synthesize(text: str) -> bytes:
        assert text == "안녕하세요"
        return fake_wav

    monkeypatch.setattr(server.engine, "synthesize", fake_synthesize)

    response = client.post("/synthesize", json={"text": "안녕하세요"})

    assert response.status_code == 200
    assert response.headers["content-type"] == "audio/wav"
    assert response.content == fake_wav


def test_post_synthesize_rejects_empty_text() -> None:
    response = client.post("/synthesize", json={"text": "   "})

    assert response.status_code == 400
    assert response.json() == {"detail": "text must not be empty"}
