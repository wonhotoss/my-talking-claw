import asyncio
import os
import time
import uuid
from collections.abc import Awaitable, Callable

from app import events
from app.agent import agent, agent_client
from app.events import event_bus, turn_snapshot, turn_source
from app.utterance import split_utterances


def new_turn_id() -> str:
    return f"t_{uuid.uuid4().hex[:12]}"


class turn_runner:
    """Owns the one turn the device is doing.

    Policy: a new trigger cancels the active turn and starts. The newest intent
    wins, which is how a voice device behaves, and it makes barge-in fall out for
    free. There is deliberately no queue - ordering, staleness and depth limits
    are a feature of their own.

    Turns are owned by the device, not by a subscriber: if the event-stream
    client disconnects mid-turn the turn keeps running, because a kiosk refresh
    must not silently kill an announcement.

    Both kinds of turn - an agent prompt and a verbatim announcement - go through
    the same start/run/end path. A verbatim turn needs no subprocess, but it must
    still be preemptible, cancellable and visible in `active`, so it gets a task
    like anything else rather than a lifecycle of its own.
    """

    def __init__(self, bus: event_bus, agent: agent_client) -> None:
        self.bus = bus
        self.agent = agent
        # 300 rather than the httpx 120: agent-latency-notes records a
        # legitimately 76-second turn, and tool-using turns are longer still.
        self.timeout_seconds = float(os.environ.get("TURN_TIMEOUT_SECONDS", "300"))
        self.snapshot: turn_snapshot | None = None
        self.task: asyncio.Task[None] | None = None
        # Which turn already had its turn_ended published, so the two sides that
        # can close a turn cannot both do it.
        self.closed_turn_id: str | None = None

    @property
    def active(self) -> turn_snapshot | None:
        # Derived from the task rather than cleared by hand, so it cannot go
        # stale against it. Sound because snapshot and task are only ever
        # assigned together, in start().
        if self.task is None or self.task.done():
            return None

        return self.snapshot

    def publish_error(self, turn_id: str, message: str) -> None:
        self.bus.publish(events.error_event(kind="error", turn_id=turn_id, message=message))

    def end_turn(self, turn_id: str, reason: events.turn_end_reason) -> None:
        """Exactly one turn_ended per turn_started, whoever gets there first.

        Both run() and preempt() call this: a task cancelled before the loop ever
        ran it never executes its body at all, so its finally cannot fire and the
        canceller has to close the turn instead. Without that, the client would
        sit at 생각하는 중 forever.
        """
        if self.closed_turn_id == turn_id:
            return

        self.closed_turn_id = turn_id
        self.bus.publish(
            events.turn_ended_event(kind="turn_ended", turn_id=turn_id, reason=reason)
        )

    async def preempt(self) -> None:
        snapshot = self.active

        if snapshot is None:
            return

        # Announced before the subprocess dies, so the client stops audio now
        # instead of when the process finally goes away.
        self.bus.publish(
            events.turn_cancelling_event(kind="turn_cancelling", turn_id=snapshot.turn_id)
        )
        self.task.cancel()
        # Awaited to completion, not just cancelled: two `claude --resume <same
        # id>` processes write the same session transcript, and that corrupts
        # rather than raises.
        await asyncio.gather(self.task, return_exceptions=True)
        self.end_turn(snapshot.turn_id, "cancelled")

    async def start(
        self,
        source: turn_source,
        trigger_text: str | None,
        turn_id: str,
        body: Callable[[], Awaitable[None]],
    ) -> str:
        await self.preempt()

        snapshot = turn_snapshot(
            turn_id=turn_id,
            source=source,
            trigger_text=trigger_text,
            started_at=time.time(),
        )
        self.snapshot = snapshot
        self.closed_turn_id = None
        self.bus.publish(
            events.turn_started_event(
                kind="turn_started",
                turn_id=snapshot.turn_id,
                source=snapshot.source,
                trigger_text=snapshot.trigger_text,
                started_at=snapshot.started_at,
            )
        )
        # `body` is a factory, not a coroutine: a task that is cancelled before
        # its first step never awaits what it was given, and an un-awaited
        # coroutine is a warning plus a leak.
        self.task = asyncio.create_task(self.run(turn_id, body))

        return turn_id

    async def start_prompt(self, source: turn_source, text: str, turn_id: str) -> str:
        return await self.start(source, text, turn_id, lambda: self.pump(turn_id, text))

    async def start_utterances(self, source: turn_source, texts: list[str]) -> str:
        """Say these words verbatim. No agent hop, no subprocess, no tokens.

        This is the channel an autonomous agent uses to make the device speak
        when it decides to - the scheduled 7am wake-up arrives here.
        """
        pieces = [piece for text in texts for piece in split_utterances(text)]

        if pieces == []:
            raise ValueError("utterances must not be empty")

        turn_id = new_turn_id()

        return await self.start(source, None, turn_id, lambda: self.emit(turn_id, pieces))

    async def cancel(self, turn_id: str) -> bool:
        snapshot = self.active

        if snapshot is None or snapshot.turn_id != turn_id:
            return False

        await self.preempt()

        return True

    async def run(self, turn_id: str, body: Callable[[], Awaitable[None]]) -> None:
        reason: events.turn_end_reason = "failed"

        try:
            await asyncio.wait_for(body(), self.timeout_seconds)
            reason = "completed"
        except asyncio.CancelledError:
            reason = "cancelled"
            raise
        except asyncio.TimeoutError:
            self.publish_error(
                turn_id, f"에이전트 응답이 {int(self.timeout_seconds)}초를 넘었습니다."
            )
        except Exception as error:
            self.publish_error(turn_id, str(error))
        finally:
            self.end_turn(turn_id, reason)

    async def emit(self, turn_id: str, pieces: list[str]) -> None:
        for seq, piece in enumerate(pieces):
            self.bus.publish(
                events.utterance_event(
                    kind="utterance", turn_id=turn_id, seq=seq, text=piece
                )
            )

    async def pump(self, turn_id: str, text: str) -> None:
        seq = 0
        saw_done = False

        async for chunk in self.agent.stream(text):
            if chunk.type == "notice":
                self.bus.publish(
                    events.notice_event(
                        kind="notice",
                        turn_id=turn_id,
                        notice_kind=chunk.notice_kind,
                        text=chunk.text,
                    )
                )
                continue

            if chunk.type == "error":
                raise RuntimeError(chunk.text)

            if chunk.type == "done":
                saw_done = True
                continue

            # Splitting here, not in the gateway, is what makes client behaviour
            # identical whether or not the gateway streams: a whole-reply gateway
            # loses only the timing of the first sentence.
            for piece in split_utterances(chunk.text):
                self.bus.publish(
                    events.utterance_event(
                        kind="utterance", turn_id=turn_id, seq=seq, text=piece
                    )
                )
                seq += 1

        if not saw_done:
            raise RuntimeError("게이트웨이 응답이 done 없이 끊겼습니다.")

        if seq == 0:
            raise RuntimeError("에이전트가 빈 응답을 보냈습니다.")


bus = event_bus()
runner = turn_runner(bus, agent)
