import base64

import pytest
from fastapi.testclient import TestClient

from app import server
from app.speech import speech_result, speech_segment
from app.viseme import viseme_span


client = TestClient(server.app)

# A tiny but valid WAV header + silence, returned by the mocked engine.
fake_wav = b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00\x00}\x00\x00\x00}\x00\x00\x01\x00\x08\x00data\x00\x00\x00\x00"

fake_speech = speech_result(
    audio=fake_wav,
    sample_rate=44100,
    duration=1.0,
    visemes=[
        viseme_span(start=0.0, end=0.6, viseme="a"),
        viseme_span(start=0.6, end=1.0, viseme="x"),
    ],
    segments=[speech_segment(start=0.0, end=1.0, text="안녕하세요")],
    envelope=[0.0, 1.0, 0.25],
    envelope_hz=50,
)


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


def test_post_speak_returns_audio_and_timeline(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_speak(text: str) -> speech_result:
        assert text == "안녕하세요"
        return fake_speech

    monkeypatch.setattr(server.engine, "speak", fake_speak)

    response = client.post("/speak", json={"text": "안녕하세요"})

    assert response.status_code == 200

    body = response.json()
    assert base64.b64decode(body["audio_base64"]) == fake_wav
    assert body["media_type"] == "audio/wav"
    assert body["sample_rate"] == 44100
    assert body["duration"] == 1.0
    assert body["visemes"] == [
        {"start": 0.0, "end": 0.6, "viseme": "a"},
        {"start": 0.6, "end": 1.0, "viseme": "x"},
    ]
    assert body["segments"] == [{"start": 0.0, "end": 1.0, "text": "안녕하세요"}]
    assert body["envelope"] == [0.0, 1.0, 0.25]
    assert body["envelope_hz"] == 50


def test_post_speak_rejects_empty_text() -> None:
    response = client.post("/speak", json={"text": "   "})

    assert response.status_code == 400
    assert response.json() == {"detail": "text must not be empty"}


def test_post_speak_maps_engine_failure_to_502(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_speak(text: str) -> speech_result:
        raise RuntimeError("melotts alignment mismatch")

    monkeypatch.setattr(server.engine, "speak", fake_speak)

    response = client.post("/speak", json={"text": "안녕"})

    assert response.status_code == 502
    assert "melotts alignment mismatch" in response.json()["detail"]
