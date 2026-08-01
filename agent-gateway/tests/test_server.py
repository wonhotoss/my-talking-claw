import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app import server
from app.claude_agent import agent_line


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


def ndjson_webhook(message: str) -> list[dict]:
    token = pair_for_token()

    response = client.post(
        "/webhook",
        json={"message": message},
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": server.ndjson_media_type,
        },
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith(server.ndjson_media_type)

    return [json.loads(line) for line in response.text.splitlines() if line.strip() != ""]


def test_webhook_streams_ndjson_when_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_stream(message: str) -> AsyncIterator[agent_line]:
        yield agent_line(type="reply", text="조사해 보겠습니다.", kind=None)
        yield agent_line(type="notice", text="WebSearch", kind="tool_use")
        yield agent_line(type="reply", text="결론은 이렇습니다.", kind=None)
        yield agent_line(type="done", text="", kind=None)

    monkeypatch.setattr(server.agent, "stream", fake_stream)

    lines = ndjson_webhook("조사해줘")

    assert [line["type"] for line in lines] == ["reply", "notice", "reply", "done"]
    assert lines[0]["text"] == "조사해 보겠습니다."
    assert lines[1]["kind"] == "tool_use"


def test_webhook_ndjson_reports_failure_as_a_line(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_stream(message: str) -> AsyncIterator[agent_line]:
        yield agent_line(type="reply", text="시작합니다.", kind=None)
        raise RuntimeError("claude died")

    monkeypatch.setattr(server.agent, "stream", fake_stream)

    lines = ndjson_webhook("조사해줘")

    # A 200 is already on the wire by then, so the failure cannot be a status code.
    assert [line["type"] for line in lines] == ["reply", "error"]
    assert "claude died" in lines[1]["text"]
    # The class name has to be there: the failures that matter most carry an
    # empty message. NotImplementedError() - what create_subprocess_exec raises
    # on a Windows SelectorEventLoop, which is what `uvicorn --reload` selects -
    # would otherwise read as "agent failure: " and say nothing.
    assert "RuntimeError" in lines[1]["text"]


def test_webhook_without_accept_header_keeps_the_json_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def fake_respond(message: str) -> str:
        return f"응답: {message}"

    monkeypatch.setattr(server.agent, "respond", fake_respond)

    token = pair_for_token()

    response = client.post(
        "/webhook",
        json={"message": "안녕"},
        headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    assert response.json() == {"reply": "응답: 안녕"}
