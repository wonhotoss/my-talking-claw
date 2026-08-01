import json
from pathlib import Path

import pytest

from app.claude_agent import agent_line, claude_agent, translate_line


# Captured from `claude -p ... --output-format stream-json --verbose` (CLI
# 2.1.220). stream_error_result.jsonl is the one constructed fixture: it is a
# real result line with is_error/subtype flipped, since a genuine failure was
# not reproducible on demand.
fixtures = Path(__file__).parent / "fixtures"


def load_lines(name: str) -> list[dict]:
    text = (fixtures / name).read_text(encoding="utf-8")

    return [json.loads(line) for line in text.splitlines() if line.strip() != ""]


def translate_all(name: str) -> list[agent_line]:
    return [line for payload in load_lines(name) for line in translate_line(payload)]


def test_single_reply_yields_one_reply_then_done() -> None:
    lines = translate_all("stream_single_reply.jsonl")

    assert [line.type for line in lines] == ["reply", "done"]
    assert lines[0].text == "안녕하세요! 무엇을 도와드릴까요?"


def test_multi_turn_yields_replies_and_notices_in_order() -> None:
    lines = translate_all("stream_multi_turn.jsonl")

    assert [line.type for line in lines] == ["reply", "notice", "reply", "done"]
    assert lines[0].text == "probe 디렉터리를 찾아서 파일 개수를 세어볼게."
    assert lines[1].kind == "tool_use"
    assert lines[1].text == "Bash"
    assert lines[2].text == "probe 디렉터리에는 파일이 2개 있어."


def test_result_text_is_not_spoken_twice() -> None:
    # The result line repeats the final assistant text verbatim. Forwarding both
    # makes the device say its last sentence twice.
    lines = translate_all("stream_multi_turn.jsonl")
    replies = [line.text for line in lines if line.type == "reply"]

    assert replies.count("probe 디렉터리에는 파일이 2개 있어.") == 1


def test_thinking_blocks_are_never_spoken() -> None:
    thinking = [
        block
        for payload in load_lines("stream_multi_turn.jsonl")
        if payload.get("type") == "assistant"
        for block in payload["message"]["content"]
        if block["type"] == "thinking"
    ]

    assert thinking != []

    lines = translate_all("stream_multi_turn.jsonl")

    assert all(line.text != "" for line in lines if line.type == "reply")
    assert len([line for line in lines if line.type == "reply"]) == 2


def test_error_result_yields_error_and_no_done() -> None:
    lines = translate_all("stream_error_result.jsonl")

    assert [line.type for line in lines] == ["reply", "error"]
    assert "error_during_execution" in lines[1].text


def test_uninteresting_line_types_yield_nothing() -> None:
    dropped = [
        {"type": "system", "subtype": "init"},
        {"type": "system", "subtype": "thinking_tokens"},
        {"type": "rate_limit_event", "rate_limit_info": {}},
        {"type": "user", "message": {"role": "user", "content": []}},
        {"type": "some_type_the_cli_added_later"},
    ]

    assert [line for payload in dropped for line in translate_line(payload)] == []


def test_first_turn_creates_the_session_then_resumes_it() -> None:
    agent = claude_agent()

    first = agent.build_argv("안녕")

    assert "--session-id" in first
    assert first[first.index("--session-id") + 1] == agent.session_id
    assert "--resume" not in first
    # Required by the CLI: stream-json in print mode refuses to run without it.
    assert "--verbose" in first

    agent.session_created = True
    second = agent.build_argv("또 안녕")

    assert "--resume" in second
    assert second[second.index("--resume") + 1] == agent.session_id
    assert "--session-id" not in second


def test_empty_allowed_tools_is_not_passed() -> None:
    agent = claude_agent()
    agent.allowed_tools = ""

    assert "--allowedTools" not in agent.build_argv("안녕")

    agent.allowed_tools = "Read"

    assert "--allowedTools" in agent.build_argv("안녕")


def test_the_push_channel_is_absent_unless_both_settings_are_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("DEVICE_PUSH_URL", raising=False)
    monkeypatch.delenv("DEVICE_PUSH_TOKEN", raising=False)

    assert "X-Push-Token" not in claude_agent().system_prompt

    monkeypatch.setenv("DEVICE_PUSH_URL", "http://127.0.0.1:8000/api/push")

    # A url with no token would produce a command that always gets a 401.
    assert "X-Push-Token" not in claude_agent().system_prompt


def test_the_push_channel_is_described_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DEVICE_PUSH_URL", "http://127.0.0.1:8000/api/push")
    monkeypatch.setenv("DEVICE_PUSH_TOKEN", "secret")

    prompt = claude_agent().system_prompt

    assert "http://127.0.0.1:8000/api/push" in prompt
    assert "X-Push-Token: secret" in prompt
    # The persona survives; the channel is appended, not substituted.
    assert claude_agent.default_system_prompt in prompt
    # The known rough edge has to be in the prompt, because the contract does not
    # prevent it: pushing during your own turn cancels that turn.
    assert "취소된다" in prompt
