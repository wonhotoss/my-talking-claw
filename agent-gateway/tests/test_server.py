import pytest
from fastapi.testclient import TestClient

from app import server


client = TestClient(server.app)


def pair_for_token() -> str:
    response = client.post("/pair", headers={"X-Pairing-Code": server.pairing_code})

    assert response.status_code == 200

    return response.json()["token"]


def test_get_health() -> None:
    response = client.get("/health")

    assert response.status_code == 200

    body = response.json()
    assert body["status"] == "ok"
    assert body["model"] == server.agent.model


def test_pair_rejects_bad_code() -> None:
    response = client.post("/pair", headers={"X-Pairing-Code": "definitely-wrong"})

    assert response.status_code == 401


def test_webhook_requires_bearer_token() -> None:
    response = client.post("/webhook", json={"message": "안녕"})

    assert response.status_code == 401


def test_webhook_rejects_unknown_token() -> None:
    response = client.post(
        "/webhook",
        json={"message": "안녕"},
        headers={"Authorization": "Bearer not-a-real-token"},
    )

    assert response.status_code == 401


def test_pair_then_webhook_returns_reply(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_respond(message: str) -> str:
        return f"응답: {message}"

    monkeypatch.setattr(server.agent, "respond", fake_respond)

    token = pair_for_token()

    response = client.post(
        "/webhook",
        json={"message": "오늘 날씨 어때"},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 200
    assert response.json() == {"reply": "응답: 오늘 날씨 어때"}


def test_webhook_rejects_empty_message(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_respond(message: str) -> str:
        return "should not be called"

    monkeypatch.setattr(server.agent, "respond", fake_respond)

    token = pair_for_token()

    response = client.post(
        "/webhook",
        json={"message": "   "},
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 400
