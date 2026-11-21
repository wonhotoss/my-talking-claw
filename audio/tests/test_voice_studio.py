import threading

import numpy as np

from app import voice_checklist as checklist
from app.voice_recorder import take
from app.voice_studio import studio


class fake_device:
    """Hands back scripted takes and remembers what the studio asked for."""

    def __init__(self, results: list[take | np.ndarray]) -> None:
        self.results = results
        self.live_dbfs = -30.0
        self.recorded_ids: list[str] = []
        self.played = 0

    def record(self, item: dict[str, str], stop: threading.Event, announce) -> tuple[take | np.ndarray, int]:
        announce("말하세요")
        self.recorded_ids.append(item["id"])
        return self.results.pop(0), 0

    def playback(self, recorded: take | np.ndarray) -> None:
        self.played += 1


def good_wake() -> take:
    return take(np.full(8000, 3000, dtype=np.int16), None, None)


def make_studio(tmp_path, results: list[take | np.ndarray]) -> tuple[studio, fake_device]:
    path = str(tmp_path / "checklist.csv")
    checklist.write_rows(path, checklist.build_rows())
    device = fake_device(results)

    return studio(path, device), device


def test_starts_on_the_block_chooser(tmp_path) -> None:
    app, _ = make_studio(tmp_path, [])
    state = app.state()

    assert state["phase"] == "choose"
    assert state["item"] is None
    assert {block["label"] for block in state["blocks"]} >= {"1차 아빠", "2차 어린이", "1차 집"}


def test_choosing_a_block_shows_its_first_row(tmp_path) -> None:
    app, _ = make_studio(tmp_path, [])
    app.process({"action": "choose", "session": "1", "person": "child"})
    state = app.state()
    first = next(item for item in checklist.build_rows() if item["session"] == "1" and item["person"] == "child")

    assert state["phase"] == "prompt"
    assert state["item"]["id"] == first["id"]
    assert state["item"]["prompt"] == ["딸기님"]
    assert state["block"] == {"label": "1차 어린이", "done": 0, "total": 38}


def test_a_kept_take_fills_the_csv_and_moves_on(tmp_path) -> None:
    app, device = make_studio(tmp_path, [good_wake()])
    app.process({"action": "choose", "session": "1", "person": "dad"})
    first = app.state()["item"]["id"]

    app.process({"action": "record"})
    assert app.state()["phase"] == "review"

    app.process({"action": "keep"})
    saved = next(item for item in checklist.read_rows(app.path) if item["id"] == first)

    assert saved["file"].endswith("_wake.wav")
    assert (tmp_path / saved["file"]).exists()
    assert app.state()["item"]["id"] != first
    assert app.state()["block"]["done"] == 1
    assert device.recorded_ids == [first]


def test_a_problem_take_offers_a_retry_of_the_same_row(tmp_path) -> None:
    app, device = make_studio(tmp_path, [take(None, None, "딸기님이 안 들렸어요 (no_speech)"), good_wake()])
    app.process({"action": "choose", "session": "1", "person": "mom"})
    first = app.state()["item"]["id"]

    app.process({"action": "record"})
    assert app.state()["phase"] == "problem"
    assert "안 들렸어요" in app.state()["message"]

    app.process({"action": "record"})

    assert app.state()["phase"] == "review"
    assert device.recorded_ids == [first, first]


def test_skip_leaves_the_row_to_do_but_shows_the_next(tmp_path) -> None:
    app, _ = make_studio(tmp_path, [])
    app.process({"action": "choose", "session": "2", "person": "dad"})
    first = app.state()["item"]["id"]
    app.process({"action": "skip"})

    assert app.state()["item"]["id"] != first
    assert all(not item["file"] for item in checklist.read_rows(app.path))

    # A new sitting of the same block starts at the skipped row again.
    app.process({"action": "home"})
    app.process({"action": "choose", "session": "2", "person": "dad"})

    assert app.state()["item"]["id"] == first


def test_actions_that_make_no_sense_now_are_dropped(tmp_path) -> None:
    app, device = make_studio(tmp_path, [])
    app.process({"action": "keep"})
    app.process({"action": "record"})

    assert app.state()["phase"] == "choose"
    assert device.recorded_ids == []


def test_a_block_with_nothing_left_is_finished(tmp_path) -> None:
    app, _ = make_studio(tmp_path, [])
    rows = [{**item, "file": "x.wav"} if item["person"] == "house" else item for item in app.rows]
    app.rows = rows
    app.process({"action": "choose", "session": "1", "person": "house"})

    assert app.state()["phase"] == "finished"


def test_wake_command_prompt_splits_word_and_question(tmp_path) -> None:
    app, _ = make_studio(tmp_path, [])
    command = next(item for item in app.rows if item["kind"] == "wake_command")
    app.rows = [item for item in app.rows if item["id"] == command["id"]]
    app.process({"action": "choose", "session": command["session"], "person": command["person"]})

    assert app.state()["item"]["prompt"] == ["딸기님", command["prompt"].split(" ... ")[1]]
