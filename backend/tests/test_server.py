import pytest
from fastapi.testclient import TestClient

from app import server


client = TestClient(server.app)


def test_get_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_post_message_returns_agent_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_respond(message: str) -> str:
        return f"에이전트 응답: {message}"

    monkeypatch.setattr(server.agent, "respond", fake_respond)

    response = client.post("/api/message", json={"text": "안녕"})

    assert response.status_code == 200
    assert response.json() == {"text": "에이전트 응답: 안녕"}


def test_post_message_rejects_empty_text() -> None:
    response = client.post("/api/message", json={"text": "   "})

    assert response.status_code == 400
    assert response.json() == {"detail": "text must not be empty"}
