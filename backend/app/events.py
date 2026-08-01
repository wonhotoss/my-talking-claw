import asyncio
import collections
import contextlib
from collections.abc import Iterator
from typing import Literal

from pydantic import BaseModel


protocol_version = 1

# Ours rather than the browser's 3s default.
reconnect_delay_ms = 2000

# How far back a reconnecting client can replay through Last-Event-ID. This is
# not a cache of state held somewhere else - events are not persisted anywhere,
# so the buffer is the log itself.
#
# The reason to have it is not lost sentences: if the connection drops between
# the last utterance and turn_ended, a client with no replay stays stuck showing
# "말하는 중" forever, and a stuck kiosk is the worst failure here.
replay_window = 256

# A subscriber that falls this far behind is dropped and its stream closed. The
# browser reconnects with Last-Event-ID and replays. Loud, and self-healing -
# the alternative is letting one slow client stall the whole device.
subscriber_queue_size = 64


turn_source = Literal["user", "agent", "external"]
turn_end_reason = Literal["completed", "cancelled", "failed"]
notice_kind = Literal["tool_use", "progress"]


class turn_snapshot(BaseModel):
    turn_id: str
    source: turn_source
    trigger_text: str | None
    started_at: float


class stream_hello_event(BaseModel):
    """First event of a connection that could not be resumed from replay.

    Covers what replay cannot: a server restart, or a Last-Event-ID older than
    the window. active_turn is computed from the runner on each connect, so a
    fresh client immediately knows whether to show 생각하는 중 or 대기 중.
    """

    kind: Literal["stream_hello"]
    protocol_version: int
    active_turn: turn_snapshot | None


class turn_started_event(BaseModel):
    kind: Literal["turn_started"]
    turn_id: str
    source: turn_source
    # Carried so the kiosk sees what the phone user said.
    trigger_text: str | None
    started_at: float


class utterance_event(BaseModel):
    """One thing to say out loud. The client posts each to /tts/speak."""

    kind: Literal["utterance"]
    turn_id: str
    seq: int
    text: str


class notice_event(BaseModel):
    """Progress worth showing but never spoken.

    The utterance/notice split is the load-bearing line of this schema: the
    client's audio pipeline branches on exactly it. "조사해 보겠습니다" is
    something the agent actually said, so it is an utterance; a grey status line
    reading "WebSearch" is a notice.
    """

    kind: Literal["notice"]
    turn_id: str
    notice_kind: notice_kind
    text: str


class turn_cancelling_event(BaseModel):
    """Published before the subprocess dies, so the client stops audio now."""

    kind: Literal["turn_cancelling"]
    turn_id: str


class turn_ended_event(BaseModel):
    kind: Literal["turn_ended"]
    turn_id: str
    reason: turn_end_reason


class error_event(BaseModel):
    """Never ends a turn on its own; always followed by turn_ended{failed}."""

    kind: Literal["error"]
    turn_id: str | None
    message: str


stream_event = (
    stream_hello_event
    | turn_started_event
    | utterance_event
    | notice_event
    | turn_cancelling_event
    | turn_ended_event
    | error_event
)


def sse_comment(text: str) -> str:
    """A keepalive. EventSource ignores comments: no handler, no event id."""
    return f": {text}\n\n"


def sse_retry_frame(delay_ms: int) -> str:
    """Sets the browser's reconnect delay to ours rather than its 3s default."""
    return f"retry: {delay_ms}\n\n"


def sse_frame(seq: int, event: stream_event) -> str:
    """One SSE frame.

    The sequence number lives only in the `id:` line - one source of truth. The
    browser hands it back as MessageEvent.lastEventId and, on reconnect, as the
    Last-Event-ID request header. JSON escapes newlines, so a frame can never be
    split by the payload.

    Events are deliberately unnamed: EventSource.onmessage only fires for events
    without an `event:` field, so naming them would force one addEventListener
    per type and make a forgotten registration a silent no-op.
    """
    return f"id: {seq}\n" + sse_connection_frame(event)


def sse_connection_frame(event: stream_event) -> str:
    """A frame with no `id:` line, for connection-scoped events (stream_hello).

    It is not part of the log, so it must neither consume a sequence number nor
    overwrite the client's Last-Event-ID.
    """
    return f"data: {event.model_dump_json()}\n\n"


class event_bus:
    """Broadcast log of everything the device did, with a short replay window.

    The device is a singleton - one speaker, one face, one conversation - so
    there is one bus and N subscribers all seeing the same thing. The kiosk and
    the phone are two windows onto one device.
    """

    def __init__(self) -> None:
        self.next_seq = 1
        self.recent: collections.deque[tuple[int, stream_event]] = collections.deque(
            maxlen=replay_window
        )
        self.subscribers: set[asyncio.Queue[tuple[int, stream_event]]] = set()

    def publish(self, event: stream_event) -> int:
        """Fan out to every subscriber. Synchronous on purpose.

        put_nowait means this can be called from an `except CancelledError`
        block and from shutdown paths, where awaiting is not safe.
        """
        seq = self.next_seq
        self.next_seq += 1
        self.recent.append((seq, event))

        for queue in list(self.subscribers):
            try:
                queue.put_nowait((seq, event))
            except asyncio.QueueFull:
                self.subscribers.discard(queue)

        return seq

    def since(self, last_seq: int) -> list[tuple[int, stream_event]] | None:
        """Events after last_seq, or None when the window cannot serve it.

        None means "start fresh with a stream_hello" - either the client is
        behind the buffer, or it is ahead of us because the server restarted and
        the numbering went back to 1.
        """
        # Buffered sequence numbers are contiguous and end at next_seq - 1, so
        # how many the client is behind by is all that has to be compared.
        if last_seq >= self.next_seq or self.next_seq - last_seq - 1 > len(self.recent):
            return None

        return [entry for entry in self.recent if entry[0] > last_seq]

    @contextlib.contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[tuple[int, stream_event]]]:
        queue: asyncio.Queue[tuple[int, stream_event]] = asyncio.Queue(
            maxsize=subscriber_queue_size
        )
        self.subscribers.add(queue)

        try:
            yield queue
        finally:
            self.subscribers.discard(queue)
