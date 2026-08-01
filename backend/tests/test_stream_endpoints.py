import asyncio
import json
from collections.abc import AsyncIterator

import pytest
from fastapi.testclient import TestClient

from app import events, server
from app.events import event_bus


client = TestClient(server.app)


def an_utterance(text: str) -> events.utterance_event:
    return events.utterance_event(kind="utterance", turn_id="t_1", seq=0, text=text)


def payload_of(frame: str) -> dict:
    return json.loads(frame.split("data: ", 1)[1].strip())


async def take(stream: AsyncIterator[str], count: int) -> list[str]:
    frames = []

    async for frame in stream:
        frames.append(frame)

        if len(frames) == count:
            break

    await stream.aclose()

    return frames


def fresh_bus(monkeypatch: pytest.MonkeyPatch) -> event_bus:
    bus = event_bus()
    monkeypatch.setattr(server, "bus", bus)

    return bus


def test_a_fresh_connection_gets_a_hello_and_no_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = fresh_bus(monkeypatch)
    bus.publish(an_utterance("지난 턴에서 말한 것."))

    frames = asyncio.run(take(server.event_stream(None), 2))

    assert frames[0] == f"retry: {events.reconnect_delay_ms}\n\n"

    hello = payload_of(frames[1])
    assert hello["kind"] == "stream_hello"
    assert hello["protocol_version"] == events.protocol_version
    assert hello["active_turn"] is None

    # A page reload must not re-speak the previous turn.
    assert len(frames) == 2


def test_the_hello_frame_carries_no_event_id(monkeypatch: pytest.MonkeyPatch) -> None:
    fresh_bus(monkeypatch)

    frames = asyncio.run(take(server.event_stream(None), 2))

    # Connection-scoped, so it must not consume a sequence number nor overwrite
    # the client's Last-Event-ID.
    assert "id:" not in frames[1]


def test_reconnect_replays_only_newer_events_without_a_hello(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = fresh_bus(monkeypatch)
    bus.publish(an_utterance("첫째."))
    bus.publish(an_utterance("둘째."))
    bus.publish(an_utterance("셋째."))

    frames = asyncio.run(take(server.event_stream("1"), 3))

    assert [payload_of(frame)["kind"] for frame in frames[1:]] == ["utterance", "utterance"]
    assert [payload_of(frame)["text"] for frame in frames[1:]] == ["둘째.", "셋째."]
    assert frames[1].startswith("id: 2\n")
    assert frames[2].startswith("id: 3\n")


def test_reconnect_beyond_the_window_falls_back_to_a_hello(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = fresh_bus(monkeypatch)

    for index in range(events.replay_window + 10):
        bus.publish(an_utterance(f"{index}."))

    frames = asyncio.run(take(server.event_stream("1"), 2))

    assert payload_of(frames[1])["kind"] == "stream_hello"


def test_a_malformed_last_event_id_falls_back_to_a_hello(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bus = fresh_bus(monkeypatch)
    bus.publish(an_utterance("첫째."))

    frames = asyncio.run(take(server.event_stream("not-a-number"), 2))

    assert payload_of(frames[1])["kind"] == "stream_hello"


def test_live_events_reach_a_connected_stream(monkeypatch: pytest.MonkeyPatch) -> None:
    bus = fresh_bus(monkeypatch)

    async def scenario() -> list[str]:
        stream = server.event_stream(None)
        frames = await take_then_publish(stream, bus)

        return frames

    async def take_then_publish(stream: AsyncIterator[str], bus: event_bus) -> list[str]:
        frames = [await stream.__anext__(), await stream.__anext__()]
        bus.publish(an_utterance("방금 말한 것."))
        frames.append(await stream.__anext__())
        await stream.aclose()

        return frames

    frames = asyncio.run(scenario())

    assert payload_of(frames[2])["text"] == "방금 말한 것."


def test_the_stream_route_sets_no_buffering_headers() -> None:
    # Called directly rather than over TestClient: the stream never ends on its
    # own, so an HTTP-level test would just block on the heartbeat.
    async def scenario() -> tuple[str | None, dict[str, str]]:
        response = await server.get_events(None)
        await response.body_iterator.aclose()

        return response.media_type, dict(response.headers)

    media_type, headers = asyncio.run(scenario())

    assert media_type == "text/event-stream"
    assert headers["cache-control"] == "no-cache"
    assert headers["x-accel-buffering"] == "no"


def test_post_turn_accepts_and_returns_the_client_turn_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[tuple[str, str, str]] = []

    async def fake_start_prompt(source: str, text: str, turn_id: str) -> str:
        seen.append((source, text, turn_id))

        return turn_id

    monkeypatch.setattr(server.runner, "start_prompt", fake_start_prompt)

    response = client.post("/api/turns", json={"text": " 안녕 ", "turn_id": "t_client_1"})

    assert response.status_code == 202
    assert response.json() == {"turn_id": "t_client_1"}
    assert seen == [("user", "안녕", "t_client_1")]


def test_post_turn_rejects_empty_text_and_empty_turn_id() -> None:
    assert client.post("/api/turns", json={"text": "  ", "turn_id": "t_1"}).status_code == 400
    assert client.post("/api/turns", json={"text": "안녕", "turn_id": " "}).status_code == 400


def test_post_cancel_reports_whether_it_matched(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_cancel(turn_id: str) -> bool:
        return turn_id == "t_live"

    monkeypatch.setattr(server.runner, "cancel", fake_cancel)

    assert client.post("/api/turns/t_live/cancel").json() == {"cancelled": True}
    assert client.post("/api/turns/t_gone/cancel").json() == {"cancelled": False}


def test_push_is_disabled_loudly_when_no_token_is_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "push_token", "")

    response = client.post("/api/push", json={"source": "agent", "prompt": None, "utterances": ["안녕"]})

    assert response.status_code == 503
    assert "PUSH_TOKEN" in response.json()["detail"]


def test_push_rejects_a_wrong_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "push_token", "secret")

    response = client.post(
        "/api/push",
        json={"source": "agent", "prompt": None, "utterances": ["안녕"]},
        headers={"X-Push-Token": "nope"},
    )

    assert response.status_code == 401


def test_push_requires_exactly_one_of_prompt_or_utterances(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "push_token", "secret")
    headers = {"X-Push-Token": "secret"}

    neither = client.post(
        "/api/push",
        json={"source": "agent", "prompt": None, "utterances": None},
        headers=headers,
    )
    both = client.post(
        "/api/push",
        json={"source": "agent", "prompt": "말해줘", "utterances": ["안녕"]},
        headers=headers,
    )

    assert neither.status_code == 400
    assert both.status_code == 400


def test_push_with_utterances_speaks_verbatim(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(server, "push_token", "secret")
    seen: list[tuple[str, list[str]]] = []

    async def fake_start_utterances(source: str, texts: list[str]) -> str:
        seen.append((source, texts))

        return "t_pushed"

    monkeypatch.setattr(server.runner, "start_utterances", fake_start_utterances)

    response = client.post(
        "/api/push",
        json={"source": "agent", "prompt": None, "utterances": ["일곱 시예요."]},
        headers={"X-Push-Token": "secret"},
    )

    assert response.status_code == 202
    assert response.json() == {"turn_id": "t_pushed"}
    assert seen == [("agent", ["일곱 시예요."])]


def test_push_with_a_prompt_runs_an_unsolicited_agent_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(server, "push_token", "secret")
    seen: list[tuple[str, str]] = []

    async def fake_start_prompt(source: str, text: str, turn_id: str) -> str:
        seen.append((source, text))

        return turn_id

    monkeypatch.setattr(server.runner, "start_prompt", fake_start_prompt)

    response = client.post(
        "/api/push",
        json={"source": "agent", "prompt": "7시라고 알려줘", "utterances": None},
        headers={"X-Push-Token": "secret"},
    )

    assert response.status_code == 202
    # Server-generated: an unsolicited turn has no client to name it.
    assert response.json()["turn_id"].startswith("t_")
    assert seen == [("agent", "7시라고 알려줘")]
