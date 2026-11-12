import asyncio
import datetime
import json
from pathlib import Path

import httpx
import pytest

from app.scheduler import parse_entry, push_body, scheduler

seoul = datetime.timezone(datetime.timedelta(hours=9))


def test_a_naive_time_is_local_and_say_or_prompt_is_required() -> None:
    entry = parse_entry(Path("x.json"), '{"at": "2026-11-01T15:14:00", "say": "지났어요"}')

    assert entry.at.tzinfo is not None
    assert entry.at.replace(tzinfo=None) == datetime.datetime(2026, 11, 1, 15, 14)
    assert (entry.say, entry.prompt) == ("지났어요", None)

    with pytest.raises(ValueError):
        parse_entry(Path("x.json"), '{"at": "2026-11-01T15:14:00"}')

    with pytest.raises(ValueError):
        parse_entry(Path("x.json"), '{"at": "2026-11-01T15:14:00", "say": "a", "prompt": "b"}')


def test_push_bodies_match_the_backend_contract() -> None:
    say = parse_entry(Path("x"), '{"at": "2026-11-01T15:14:00+09:00", "say": "지났어요"}')
    prompt = parse_entry(Path("x"), '{"at": "2026-11-01T15:14:00+09:00", "prompt": "알려줘"}')

    assert push_body(say) == {"source": "agent", "prompt": None, "utterances": ["지났어요"]}
    assert push_body(prompt) == {"source": "agent", "prompt": "알려줘", "utterances": None}


recorded: list[str] = []


def make_scheduler(home: Path, handler) -> scheduler:
    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))

    return scheduler(home, "http://backend/api/push", "tok", http, recorded.append)


def test_every_move_is_recorded(tmp_path: Path) -> None:
    recorded.clear()
    write(tmp_path, "a.json", {"at": "2026-11-01T15:14:00+09:00", "say": "지났어요"})
    write(tmp_path, "b.json", {"at": "not a time", "say": "x"})

    asyncio.run(make_scheduler(tmp_path, lambda request: httpx.Response(202)).tick(
        datetime.datetime(2026, 11, 1, 15, 14, 1, tzinfo=seoul)
    ))

    assert recorded == ["스케줄러: a.json → done/", "스케줄러: b.json → failed/"]


def write(home: Path, name: str, payload: dict) -> Path:
    (home / "schedule").mkdir(exist_ok=True)
    path = home / "schedule" / name
    path.write_text(json.dumps(payload), encoding="utf-8")

    return path


def test_due_entries_are_pushed_and_moved_to_done(tmp_path: Path) -> None:
    sent: list[tuple[str, dict]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append((request.headers["X-Push-Token"], json.loads(request.content)))

        return httpx.Response(202, json={"turn_id": "t_1"})

    write(tmp_path, "a.json", {"at": "2026-11-01T15:14:00+09:00", "say": "지났어요"})
    write(tmp_path, "b.json", {"at": "2026-11-01T16:00:00+09:00", "say": "아직"})

    asyncio.run(make_scheduler(tmp_path, handler).tick(datetime.datetime(2026, 11, 1, 15, 14, 1, tzinfo=seoul)))

    assert sent == [("tok", {"source": "agent", "prompt": None, "utterances": ["지났어요"]})]
    assert (tmp_path / "schedule" / "done" / "a.json").exists()
    assert (tmp_path / "schedule" / "b.json").exists()


def test_an_unreadable_file_is_moved_to_failed_without_a_push(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must not push")

    path = tmp_path / "schedule"
    path.mkdir()
    (path / "bad.json").write_text("{not json", encoding="utf-8")
    write(tmp_path, "nokey.json", {"at": "2026-11-01T15:14:00+09:00"})

    asyncio.run(make_scheduler(tmp_path, handler).tick(datetime.datetime(2026, 11, 1, 15, 14, 1, tzinfo=seoul)))

    assert sorted(p.name for p in (path / "failed").iterdir()) == ["bad.json", "nokey.json"]


def test_a_failed_push_keeps_the_file_for_the_next_tick(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    write(tmp_path, "a.json", {"at": "2026-11-01T15:14:00+09:00", "say": "지났어요"})

    asyncio.run(make_scheduler(tmp_path, handler).tick(datetime.datetime(2026, 11, 1, 15, 14, 1, tzinfo=seoul)))

    assert (tmp_path / "schedule" / "a.json").exists()


def test_a_missing_schedule_directory_is_fine(tmp_path: Path) -> None:
    asyncio.run(make_scheduler(tmp_path, lambda request: httpx.Response(202)).tick(
        datetime.datetime(2026, 11, 1, tzinfo=seoul))
    )
