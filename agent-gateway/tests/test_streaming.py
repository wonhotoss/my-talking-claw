import asyncio
from collections.abc import AsyncIterator

from claude_agent_sdk import AssistantMessage, ResultMessage, StreamEvent, TextBlock, ToolUseBlock

from app.sdk_agent import relay, turn_translator, wait_notice


def delta(text: str, parent: str | None = None) -> StreamEvent:
    return StreamEvent(
        uuid="u",
        session_id="s",
        event={"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}},
        parent_tool_use_id=parent,
    )


def block_stop() -> StreamEvent:
    return StreamEvent(uuid="u", session_id="s", event={"type": "content_block_stop", "index": 0})


def done() -> ResultMessage:
    return ResultMessage(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False, num_turns=1, session_id="s"
    )


def lines_of(messages: list[object]) -> list[tuple[str, str]]:
    translator = turn_translator()

    return [(line.type, line.text) for message in messages for line in translator.on(message)]


def test_sentences_go_out_as_deltas_complete_them_and_the_full_message_adds_only_notices() -> None:
    messages = [
        delta("지금은 "),
        delta("다섯 시예요. 바깥은 "),
        delta("춥고요."),
        block_stop(),
        AssistantMessage(
            content=[TextBlock(text="지금은 다섯 시예요. 바깥은 춥고요."), ToolUseBlock(id="t", name="WebSearch", input={})],
            model="m",
        ),
        done(),
    ]

    assert lines_of(messages) == [
        ("reply", "지금은 다섯 시예요."),
        ("reply", "바깥은 춥고요."),
        ("notice", "WebSearch"),
        ("done", ""),
    ]


def test_an_unstreamed_message_is_spoken_from_its_blocks() -> None:
    messages = [AssistantMessage(content=[TextBlock(text="네.")], model="m"), done()]

    assert lines_of(messages) == [("reply", "네."), ("done", "")]


def test_streaming_state_resets_between_assistant_messages() -> None:
    messages = [
        delta("찾아볼게요."),
        block_stop(),
        AssistantMessage(content=[TextBlock(text="찾아볼게요."), ToolUseBlock(id="t", name="WebFetch", input={})], model="m"),
        AssistantMessage(content=[TextBlock(text="비가 와요.")], model="m"),
    ]

    assert lines_of(messages) == [("reply", "찾아볼게요."), ("notice", "WebFetch"), ("reply", "비가 와요.")]


def test_subagent_deltas_are_ignored() -> None:
    assert lines_of([delta("내부 작업. ", parent="tool_1"), block_stop()]) == []


async def slow(messages: list[tuple[float, object]]) -> AsyncIterator[object]:
    for seconds, message in messages:
        await asyncio.sleep(seconds)
        yield message


async def collect(messages: AsyncIterator[object], notice: wait_notice | None) -> list[tuple[str, str]]:
    return [(line.type, line.text) async for line in relay(messages, turn_translator(), notice)]


def test_the_wait_notice_fills_a_long_silence_once() -> None:
    notice = wait_notice(after_seconds=0.05, text="잠깐만요.")
    messages = [(0.2, delta("답이에요. ")), (0.0, block_stop()), (0.0, done())]

    assert asyncio.run(collect(slow(messages), notice)) == [
        ("reply", "잠깐만요."),
        ("reply", "답이에요."),
        ("done", ""),
    ]


def test_no_notice_when_the_first_sentence_is_quick_or_none_is_configured() -> None:
    notice = wait_notice(after_seconds=0.5, text="잠깐만요.")
    quick = [(0.0, delta("바로요. ")), (0.0, block_stop()), (0.0, done())]

    assert asyncio.run(collect(slow(quick), notice)) == [("reply", "바로요."), ("done", "")]
    assert asyncio.run(collect(slow([(0.1, done())]), None)) == [("done", "")]
