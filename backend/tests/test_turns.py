import asyncio
from collections.abc import AsyncIterator

import pytest

from app import events
from app.agent import agent_chunk
from app.events import event_bus
from app.turns import turn_runner


def reply(text: str) -> agent_chunk:
    return agent_chunk(type="reply", text=text, notice_kind=None)


def notice(text: str) -> agent_chunk:
    return agent_chunk(type="notice", text=text, notice_kind="tool_use")


def done() -> agent_chunk:
    return agent_chunk(type="done", text="", notice_kind=None)


class fake_agent:
    """Stands in for agent_client. `blocks` makes a turn hang so it can be preempted.

    Blocking turns never end on their own, so a test that starts one must always
    cancel it - otherwise it waits out the whole turn budget.
    """

    def __init__(self, chunks: list[agent_chunk], blocks: bool) -> None:
        self.chunks = chunks
        self.blocks = blocks
        self.calls = 0

    async def stream(self, message: str) -> AsyncIterator[agent_chunk]:
        self.calls += 1

        for chunk in self.chunks:
            yield chunk

        if self.blocks:
            await asyncio.Event().wait()


def kinds_of(bus: event_bus) -> list[str]:
    return [event.kind for _, event in bus.recent]


def events_of(bus: event_bus, kind: str) -> list[events.stream_event]:
    return [event for _, event in bus.recent if event.kind == kind]


def run_turn(chunks: list[agent_chunk]) -> event_bus:
    async def scenario() -> event_bus:
        bus = event_bus()
        runner = turn_runner(bus, fake_agent(chunks, blocks=False))
        await runner.start_prompt("user", "안녕", "t_1")
        await asyncio.gather(runner.task, return_exceptions=True)

        return bus

    return asyncio.run(scenario())


def test_a_turn_publishes_started_utterances_and_ended() -> None:
    bus = run_turn([reply("조사해 보겠습니다. 결론은 이렇습니다."), done()])

    assert kinds_of(bus) == ["turn_started", "utterance", "utterance", "turn_ended"]

    spoken = events_of(bus, "utterance")
    assert [event.text for event in spoken] == ["조사해 보겠습니다.", "결론은 이렇습니다."]
    # seq is 0-based and contiguous per turn.
    assert [event.seq for event in spoken] == [0, 1]
    assert events_of(bus, "turn_ended")[0].reason == "completed"


def test_utterance_seq_keeps_counting_across_separate_replies() -> None:
    # The multi-turn shape: three assistant blocks arriving seconds apart.
    bus = run_turn(
        [reply("조사해 보겠습니다."), reply("사실이 발견되었습니다."), reply("결론은 이렇습니다."), done()]
    )

    assert [event.seq for event in events_of(bus, "utterance")] == [0, 1, 2]


def test_notices_are_published_but_are_not_utterances() -> None:
    bus = run_turn([reply("찾아볼게."), notice("WebSearch"), reply("찾았어."), done()])

    assert kinds_of(bus) == [
        "turn_started",
        "utterance",
        "notice",
        "utterance",
        "turn_ended",
    ]

    published = events_of(bus, "notice")[0]
    assert published.notice_kind == "tool_use"
    assert published.text == "WebSearch"


def test_the_notice_label_is_published_as_received() -> None:
    # Narrowing happens at the gateway trust boundary (agent.decode_notice_kind),
    # so by the time a chunk reaches the runner there is nothing left to decide.
    bus = run_turn(
        [agent_chunk(type="notice", text="무언가", notice_kind="progress"), reply("끝."), done()]
    )

    assert events_of(bus, "notice")[0].notice_kind == "progress"
    assert events_of(bus, "turn_ended")[0].reason == "completed"


def test_error_chunk_fails_the_turn_after_an_error_event() -> None:
    bus = run_turn([reply("시작합니다."), agent_chunk(type="error", text="터졌다", notice_kind=None)])

    assert kinds_of(bus) == ["turn_started", "utterance", "error", "turn_ended"]
    # An error never ends a turn on its own.
    assert events_of(bus, "turn_ended")[0].reason == "failed"
    assert events_of(bus, "error")[0].message == "터졌다"


def test_stream_ending_without_done_is_a_failure_not_a_success() -> None:
    bus = run_turn([reply("잘렸다.")])

    assert events_of(bus, "turn_ended")[0].reason == "failed"
    assert "done" in events_of(bus, "error")[0].message


def test_empty_agent_reply_fails_rather_than_producing_a_silent_turn() -> None:
    bus = run_turn([done()])

    assert events_of(bus, "utterance") == []
    assert events_of(bus, "turn_ended")[0].reason == "failed"


def test_every_turn_started_gets_exactly_one_turn_ended() -> None:
    for chunks in (
        [reply("정상."), done()],
        [reply("잘림.")],
        [done()],
        [agent_chunk(type="error", text="실패", notice_kind=None)],
    ):
        bus = run_turn(chunks)

        assert kinds_of(bus).count("turn_started") == 1
        assert kinds_of(bus).count("turn_ended") == 1


def test_a_new_trigger_preempts_the_active_turn() -> None:
    async def scenario() -> event_bus:
        bus = event_bus()
        runner = turn_runner(bus, fake_agent([reply("첫 턴.")], blocks=True))

        await runner.start_prompt("user", "첫 질문", "t_1")
        # start_prompt awaits the previous turn's death before starting.
        await runner.start_prompt("user", "둘째 질문", "t_2")
        # The second turn blocks too, so it has to be cleaned up explicitly.
        await runner.cancel("t_2")

        return bus

    bus = asyncio.run(scenario())

    assert kinds_of(bus).count("turn_cancelling") == 2
    assert [event.turn_id for event in events_of(bus, "turn_started")] == ["t_1", "t_2"]

    ended = events_of(bus, "turn_ended")
    assert [event.turn_id for event in ended] == ["t_1", "t_2"]
    assert [event.reason for event in ended] == ["cancelled", "cancelled"]


def test_a_turn_cancelled_before_the_loop_ran_it_still_ends() -> None:
    # A task cancelled before its first step never executes its body, so the
    # finally that publishes turn_ended would be skipped and the client would sit
    # at 생각하는 중 forever.
    async def scenario() -> event_bus:
        bus = event_bus()
        runner = turn_runner(bus, fake_agent([reply("시작도 못 했다.")], blocks=True))

        await runner.start_prompt("user", "질문", "t_1")
        await runner.cancel("t_1")

        return bus

    bus = asyncio.run(scenario())

    assert kinds_of(bus).count("turn_ended") == 1
    assert events_of(bus, "turn_ended")[0].reason == "cancelled"


def test_cancel_only_matches_the_active_turn() -> None:
    async def scenario() -> tuple[bool, bool, event_bus]:
        bus = event_bus()
        runner = turn_runner(bus, fake_agent([reply("도는 중.")], blocks=True))

        await runner.start_prompt("user", "질문", "t_1")

        wrong = await runner.cancel("t_nope")
        right = await runner.cancel("t_1")

        return wrong, right, bus

    wrong, right, bus = asyncio.run(scenario())

    assert wrong is False
    assert right is True
    assert events_of(bus, "turn_ended")[0].reason == "cancelled"


def test_cancelling_when_nothing_is_running_is_not_an_error() -> None:
    async def scenario() -> bool:
        runner = turn_runner(event_bus(), fake_agent([], blocks=False))

        return await runner.cancel("t_1")

    assert asyncio.run(scenario()) is False


def test_active_is_none_once_the_turn_finishes() -> None:
    async def scenario() -> tuple[bool, bool]:
        runner = turn_runner(event_bus(), fake_agent([reply("끝."), done()], blocks=True))

        await runner.start_prompt("user", "질문", "t_1")
        during = runner.active is not None

        await runner.cancel("t_1")

        return during, runner.active is None

    during, after = asyncio.run(scenario())

    assert during is True
    assert after is True


def test_a_turn_over_the_budget_fails() -> None:
    async def scenario() -> event_bus:
        bus = event_bus()
        runner = turn_runner(bus, fake_agent([reply("느리다.")], blocks=True))
        runner.timeout_seconds = 0.05

        await runner.start_prompt("user", "질문", "t_1")
        await asyncio.gather(runner.task, return_exceptions=True)

        return bus

    bus = asyncio.run(scenario())

    assert events_of(bus, "turn_ended")[0].reason == "failed"
    assert "초를 넘었" in events_of(bus, "error")[0].message


def test_verbatim_utterances_never_reach_the_agent() -> None:
    async def scenario() -> tuple[event_bus, int]:
        bus = event_bus()
        agent = fake_agent([], blocks=False)
        runner = turn_runner(bus, agent)

        await runner.start_utterances("agent", ["일곱 시예요. 일어날 시간입니다."])
        # Verbatim turns run as a task like any other, so they have to be awaited.
        await asyncio.gather(runner.task, return_exceptions=True)

        return bus, agent.calls

    bus, calls = asyncio.run(scenario())

    # No subprocess, no tokens: this is the scheduled-wake-up path.
    assert calls == 0
    assert kinds_of(bus) == ["turn_started", "utterance", "utterance", "turn_ended"]
    assert [event.text for event in events_of(bus, "utterance")] == [
        "일곱 시예요.",
        "일어날 시간입니다.",
    ]
    assert events_of(bus, "turn_started")[0].source == "agent"
    assert events_of(bus, "turn_started")[0].trigger_text is None
    assert events_of(bus, "turn_ended")[0].reason == "completed"


def test_a_verbatim_turn_is_preemptible_like_any_other() -> None:
    # It has no subprocess, but it still has to be cancellable and visible in
    # `active` - otherwise the "newest intent wins" policy would silently not
    # apply to the one path an autonomous agent uses.
    async def scenario() -> tuple[event_bus, bool]:
        bus = event_bus()
        runner = turn_runner(bus, fake_agent([], blocks=False))

        turn_id = await runner.start_utterances("agent", ["첫째. 둘째. 셋째."])
        was_active = runner.active is not None
        cancelled = await runner.cancel(turn_id)

        assert cancelled is True

        return bus, was_active

    bus, was_active = asyncio.run(scenario())

    assert was_active is True
    assert "turn_cancelling" in kinds_of(bus)
    assert kinds_of(bus).count("turn_ended") == 1
    assert events_of(bus, "turn_ended")[0].reason == "cancelled"


def test_blank_verbatim_utterances_are_rejected_before_a_turn_starts() -> None:
    async def scenario() -> event_bus:
        bus = event_bus()
        runner = turn_runner(bus, fake_agent([], blocks=False))

        with pytest.raises(ValueError):
            await runner.start_utterances("agent", ["   ", ""])

        return bus

    # No half-started turn is left behind.
    assert kinds_of(asyncio.run(scenario())) == []
