import asyncio
import json

from app import events


def an_utterance(text: str) -> events.utterance_event:
    return events.utterance_event(kind="utterance", turn_id="t_1", seq=0, text=text)


def test_sse_frame_puts_the_sequence_only_in_the_id_line() -> None:
    frame = events.sse_frame(412, an_utterance("결론은 이렇습니다."))

    assert frame.startswith("id: 412\ndata: {")
    assert frame.endswith("\n\n")

    body = json.loads(frame.split("data: ", 1)[1].strip())

    assert body == {
        "kind": "utterance",
        "turn_id": "t_1",
        "seq": 0,
        "text": "결론은 이렇습니다.",
    }
    # A duplicated sequence number in the payload would be a second source of truth.
    assert "id" not in body


def test_sse_frame_survives_newlines_in_the_payload() -> None:
    frame = events.sse_frame(1, an_utterance("첫 줄\n둘째 줄"))

    # JSON escapes the newline, so the frame stays exactly two lines plus the blank.
    assert frame.count("\n") == 3


def test_wire_keys_are_locked_per_event_kind() -> None:
    # The frontend hand-writes these same names. This test is what catches drift;
    # see event_stream.ts for the other half of the contract.
    samples: list[tuple[events.stream_event, set[str]]] = [
        (
            events.stream_hello_event(
                kind="stream_hello", protocol_version=1, active_turn=None
            ),
            {"kind", "protocol_version", "active_turn"},
        ),
        (
            events.turn_started_event(
                kind="turn_started",
                turn_id="t_1",
                source="user",
                trigger_text="안녕",
                started_at=1.0,
            ),
            {"kind", "turn_id", "source", "trigger_text", "started_at"},
        ),
        (an_utterance("안녕"), {"kind", "turn_id", "seq", "text"}),
        (
            events.notice_event(
                kind="notice", turn_id="t_1", notice_kind="tool_use", text="Bash"
            ),
            {"kind", "turn_id", "notice_kind", "text"},
        ),
        (
            events.turn_cancelling_event(kind="turn_cancelling", turn_id="t_1"),
            {"kind", "turn_id"},
        ),
        (
            events.turn_ended_event(kind="turn_ended", turn_id="t_1", reason="completed"),
            {"kind", "turn_id", "reason"},
        ),
        (
            events.error_event(kind="error", turn_id="t_1", message="터졌다"),
            {"kind", "turn_id", "message"},
        ),
    ]

    for event, expected in samples:
        assert set(json.loads(event.model_dump_json()).keys()) == expected


def test_turn_snapshot_keys_are_locked() -> None:
    snapshot = events.turn_snapshot(
        turn_id="t_1", source="agent", trigger_text=None, started_at=2.0
    )

    assert set(json.loads(snapshot.model_dump_json()).keys()) == {
        "turn_id",
        "source",
        "trigger_text",
        "started_at",
    }


def test_publish_numbers_events_from_one() -> None:
    bus = events.event_bus()

    assert bus.publish(an_utterance("첫째.")) == 1
    assert bus.publish(an_utterance("둘째.")) == 2


def test_since_returns_only_newer_events() -> None:
    bus = events.event_bus()
    bus.publish(an_utterance("첫째."))
    bus.publish(an_utterance("둘째."))
    bus.publish(an_utterance("셋째."))

    replayed = bus.since(1)

    assert replayed is not None
    assert [seq for seq, _ in replayed] == [2, 3]


def test_since_zero_on_a_fresh_bus_is_empty_not_a_gap() -> None:
    assert events.event_bus().since(0) == []


def test_since_is_empty_when_the_client_is_already_current() -> None:
    bus = events.event_bus()
    bus.publish(an_utterance("첫째."))

    assert bus.since(1) == []


def test_since_reports_a_gap_once_the_window_has_rolled_over() -> None:
    bus = events.event_bus()

    for index in range(events.replay_window + 10):
        bus.publish(an_utterance(f"{index}."))

    oldest = bus.recent[0][0]

    # The oldest buffered event can still be served by asking for the one before it.
    assert bus.since(oldest - 1) is not None
    # Anything older than that is gone, and the client must start from a hello.
    assert bus.since(oldest - 2) is None


def test_since_reports_a_gap_when_the_client_is_ahead_of_us() -> None:
    # A server restart puts numbering back to 1 while the browser still holds a
    # large Last-Event-ID.
    bus = events.event_bus()
    bus.publish(an_utterance("첫째."))

    assert bus.since(9999) is None


def test_subscriber_receives_published_events() -> None:
    async def scenario() -> list[str]:
        bus = events.event_bus()

        with bus.subscribe() as queue:
            bus.publish(an_utterance("첫째."))
            bus.publish(an_utterance("둘째."))

            return [queue.get_nowait()[1].text, queue.get_nowait()[1].text]

    assert asyncio.run(scenario()) == ["첫째.", "둘째."]


def test_unsubscribed_queue_stops_receiving() -> None:
    async def scenario() -> int:
        bus = events.event_bus()

        with bus.subscribe() as queue:
            pass

        bus.publish(an_utterance("아무도 안 듣는다."))

        return queue.qsize()

    assert asyncio.run(scenario()) == 0


def test_slow_subscriber_is_dropped_rather_than_stalling_the_device() -> None:
    async def scenario() -> tuple[int, int]:
        bus = events.event_bus()

        with bus.subscribe() as queue:
            for index in range(events.subscriber_queue_size + 5):
                bus.publish(an_utterance(f"{index}."))

            return len(bus.subscribers), queue.qsize()

    subscriber_count, queued = asyncio.run(scenario())

    assert subscriber_count == 0
    assert queued == events.subscriber_queue_size


def test_publishing_with_no_subscribers_still_numbers_and_buffers() -> None:
    bus = events.event_bus()
    bus.publish(an_utterance("혼자."))

    assert bus.next_seq == 2
    assert len(bus.recent) == 1
