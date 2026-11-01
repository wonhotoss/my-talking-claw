import datetime
import json
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ResultMessage, SystemMessage, TextBlock, ThinkingBlock, ToolUseBlock

from app.sdk_agent import is_trusted, read_session_id, stamp, translate_message, trust_keys, write_session_id


def a_result(**overrides) -> ResultMessage:
    fields = dict(
        subtype="success",
        duration_ms=1000,
        duration_api_ms=900,
        is_error=False,
        num_turns=1,
        session_id="s-1",
        total_cost_usd=0.01,
    )
    fields.update(overrides)

    return ResultMessage(**fields)


def test_stamp_puts_the_clock_in_front_of_the_words() -> None:
    now = datetime.datetime(2026, 11, 1, 15, 4)

    assert stamp("10분 후에 불러줘", now) == "[지금 2026-11-01 (일) 15:04]\n10분 후에 불러줘"


def test_text_blocks_become_replies_and_tool_use_a_notice() -> None:
    message = AssistantMessage(
        content=[
            ThinkingBlock(thinking="...", signature="x"),
            TextBlock(text="  네, 알겠어요.  "),
            ToolUseBlock(id="t1", name="Write", input={}),
            TextBlock(text=""),
        ],
        model="claude-sonnet-5-5",
    )

    lines = translate_message(message)

    assert [(line.type, line.text, line.kind) for line in lines] == [
        ("reply", "네, 알겠어요.", None),
        ("notice", "Write", "tool_use"),
    ]


def test_a_successful_result_is_done_and_a_failed_one_an_error() -> None:
    assert [line.type for line in translate_message(a_result())] == ["done"]

    errors = translate_message(a_result(subtype="error_max_turns", is_error=True))

    assert [(line.type, line.text) for line in errors] == [("error", "claude: error_max_turns")]


def test_system_messages_are_not_forwarded() -> None:
    assert translate_message(SystemMessage(subtype="init", data={})) == []


def test_session_id_round_trips_through_the_home(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"

    assert read_session_id(session_file) is None

    write_session_id(session_file, "abc")

    assert read_session_id(session_file) == "abc"
    assert json.loads(session_file.read_text()) == {"session_id": "abc"}


def test_trust_is_read_from_the_projects_table_with_forward_slashes() -> None:
    home = Path("C:/Users/me/claw-home") if Path("C:/").exists() else Path("/home/me/claw-home")
    key = trust_keys(home)[0]

    assert "\\" not in key
    assert is_trusted({"projects": {key: {"hasTrustDialogAccepted": True}}}, home)
    assert not is_trusted({"projects": {key: {"hasTrustDialogAccepted": False}}}, home)
    assert not is_trusted({}, home)
