"""The home's history against a real git in a temporary directory."""

import subprocess
from pathlib import Path

from app import home_history


def log_lines(home: Path) -> list[str]:
    return subprocess.run(
        ["git", "-C", str(home), "log", "--format=%an|%s"], capture_output=True, text=True, encoding="utf-8", check=True
    ).stdout.splitlines()


def make_home(tmp_path: Path) -> Path:
    (tmp_path / "CLAUDE.md").write_text("규칙\n", encoding="utf-8")
    (tmp_path / "session.json").write_text('{"session_id": "s1"}', encoding="utf-8")

    return tmp_path


def test_ensure_initialises_and_snapshots_as_the_host(tmp_path: Path) -> None:
    home = make_home(tmp_path)

    home_history.ensure(home)

    assert home_history.is_repository(home)
    assert log_lines(home) == ["claw-host|기동 스냅샷: 게이트웨이가 꺼져 있던 동안의 변경(관리자 편집 등)"]


def test_session_json_is_never_recorded(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    home_history.ensure(home)

    (home / "session.json").write_text('{"session_id": "s2"}', encoding="utf-8")

    assert home_history.record(home, "턴: 아무것도 안 바꿈", "brain") == []
    assert len(log_lines(home)) == 1


def test_a_turn_that_changed_files_is_one_commit_with_its_words(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    home_history.ensure(home)

    (home / "CLAUDE.md").write_text("규칙\n새 규칙\n", encoding="utf-8")
    (home / "schedule").mkdir()
    (home / "schedule" / "a.json").write_text("{}", encoding="utf-8")

    paths = home_history.record(home, "턴: 앞으로는\n더 짧게   말해", "brain")

    assert sorted(paths) == ["CLAUDE.md", "schedule/a.json"]
    assert log_lines(home)[0] == "claw-brain|턴: 앞으로는 더 짧게 말해"


def test_ensure_on_an_existing_history_only_snapshots(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    home_history.ensure(home)
    (home / "memory.md").write_text("- 기억\n", encoding="utf-8")

    home_history.ensure(home)

    assert [line.split("|")[0] for line in log_lines(home)] == ["claw-host", "claw-host"]


def test_long_subjects_are_cut(tmp_path: Path) -> None:
    text = "가" * 200

    assert len(home_history.subject(text)) == home_history.subject_limit
    assert home_history.subject(text).endswith("…")


def test_record_quietly_survives_a_broken_repository(tmp_path: Path) -> None:
    home = make_home(tmp_path)
    home_history.ensure(home)
    (home / "CLAUDE.md").write_text("바뀜\n", encoding="utf-8")
    # A lock left behind by a crashed git makes the commit fail.
    (home / ".git" / "index.lock").write_text("", encoding="utf-8")

    assert home_history.record_quietly(home, "턴: x", "brain") == []
