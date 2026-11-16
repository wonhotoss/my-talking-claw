from collections import Counter

import numpy as np

from app import voice_checklist as checklist
from app import voice_recorder as recorder

frame_samples = recorder.capture_frame_samples


class level_vad:
    """Voiced when the frame is not silent: the takes are scripted as frames."""

    def feed(self, frame: np.ndarray) -> bool:
        return bool(np.any(frame != 0))


def frames_from(pattern: str) -> list[np.ndarray]:
    return [np.full(frame_samples, 1000 if mark == "v" else 0, dtype=np.int16) for mark in pattern]


def test_every_person_gets_the_same_rows() -> None:
    rows = checklist.build_rows()
    shapes = {
        person: sorted(
            (item["kind"], item["distance"], item["loudness"], item["intonation"], item["background"],
             item["session"], item["split"], item["prompt"])
            for item in rows if item["person"] == person
        )
        for person in checklist.people
    }

    assert shapes["dad"] == shapes["mom"] == shapes["child"]
    assert Counter(item["kind"] for item in rows if item["person"] == "child") == {
        "wake": 54, "wake_command": 18, "free": 4,
    }
    assert Counter(item["kind"] for item in rows if item["person"] == "house") == {"ambient": 6}


def test_ids_are_unique() -> None:
    ids = [item["id"] for item in checklist.build_rows()]

    assert len(ids) == len(set(ids))


def test_wake_takes_cover_every_condition_and_hold_out_one_each() -> None:
    wake = [item for item in checklist.build_rows() if item["person"] == "dad" and item["kind"] == "wake"]
    per_condition = Counter((item["distance"], item["loudness"], item["background"]) for item in wake)
    tests_per_condition = Counter(
        (item["distance"], item["loudness"], item["background"]) for item in wake if item["split"] == "test"
    )

    assert set(per_condition.values()) == {3}
    assert len(per_condition) == 18
    assert set(tests_per_condition.values()) == {1}
    assert len(tests_per_condition) == 18
    assert Counter(item["intonation"] for item in wake if item["split"] == "test") == {
        "calling": 6, "asking": 6, "flat": 6,
    }
    assert Counter(item["session"] for item in wake) == {"1": 27, "2": 27}


def test_a_session_is_not_confounded_with_background_or_intonation() -> None:
    rows = [item for item in checklist.build_rows() if item["person"] == "dad"]
    wake_pairs = Counter(
        (item["session"], item["background"], item["intonation"]) for item in rows if item["kind"] == "wake"
    )
    command_backgrounds = Counter(
        (item["session"], item["background"]) for item in rows if item["kind"] == "wake_command"
    )

    # Every (background, intonation) pair is recorded on both days, 4 or 5 times each.
    assert len(wake_pairs) == 2 * len(checklist.backgrounds) * len(checklist.intonations)
    assert set(wake_pairs.values()) == {4, 5}
    assert set(command_backgrounds.values()) == {4, 5}
    assert Counter(item["session"] for item in rows if item["kind"] == "wake_command") == {"1": 9, "2": 9}


def test_each_question_is_asked_once_per_person() -> None:
    commands = [item["prompt"] for item in checklist.build_rows() if item["person"] == "mom" and item["kind"] == "wake_command"]

    assert len(commands) == len(set(commands)) == len(checklist.commands)


def test_rows_survive_a_round_trip_through_the_csv(tmp_path) -> None:
    path = str(tmp_path / "checklist.csv")
    rows = checklist.build_rows()
    checklist.write_rows(path, rows)

    assert checklist.read_rows(path) == rows


def test_pending_skips_recorded_rows_and_filters() -> None:
    rows = checklist.build_rows()
    first_child = next(item for item in rows if item["person"] == "child")
    rows = [{**item, "file": "x.wav"} if item is first_child else item for item in rows]
    child_rows = recorder.pending(rows, "child", "1")

    assert first_child["id"] not in [item["id"] for item in child_rows]
    assert {(item["person"], item["session"]) for item in child_rows} == {("child", "1")}
    assert len(recorder.pending(rows, None, None)) == len(rows) - 1


def test_wake_take_is_trimmed_to_the_word() -> None:
    # 0.4 s silence, 0.64 s word, then the 0.5 s trailing silence that ends it.
    result = recorder.take_wake(iter(frames_from("....." + "v" * 8 + "." * 7 + "v" * 20)), level_vad)

    assert result.problem is None
    voiced = int(np.count_nonzero(result.wake)) / recorder.capture_rate

    assert voiced == 8 * frame_samples / recorder.capture_rate


def test_wake_command_plays_the_chime_between_the_two_parts() -> None:
    chimes: list[int] = []
    frames = iter(frames_from("vvvvvvvv" + "......." + "...." + "v" * 20 + "." * 10))
    result = recorder.take_wake_command(frames, level_vad, lambda: chimes.append(1))

    assert result.problem is None
    assert chimes == [1]
    assert np.count_nonzero(result.wake) == 8 * frame_samples
    assert np.count_nonzero(result.command) == 20 * frame_samples


def test_a_question_said_before_the_chime_is_a_problem() -> None:
    # Word and question in one breath, then nothing after the chime.
    frames = iter(frames_from("v" * 30 + "." * 7 + "." * 80))
    result = recorder.take_wake_command(frames, level_vad, lambda: None)

    assert result.command is None
    assert result.problem is not None


def test_saved_take_fills_its_row(tmp_path) -> None:
    item = next(item for item in checklist.build_rows() if item["kind"] == "wake_command")
    done = recorder.save(str(tmp_path), item, recorder.take(
        np.full(8000, 100, dtype=np.int16), np.full(16000, 100, dtype=np.int16), None
    ))

    assert done["file"].endswith("_wake.wav")
    assert done["command_file"].endswith("_command.wav")
    assert done["seconds"] == "1.50"
    assert (tmp_path / done["file"]).exists()
    assert (tmp_path / done["command_file"]).exists()
    assert done["file"].startswith(f"{item['person']}/wake_command/{item['id']}_")
