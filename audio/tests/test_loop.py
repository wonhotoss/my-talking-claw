"""The loop's behaviour with every piece of hardware and network faked."""

import threading
from collections.abc import Iterator

import numpy as np

from app.device_client import no_speech_error, spoken_reply
from app.endpointer import endpoint_limits
from app.loop import audio_loop, loop_settings

settings = loop_settings(
    sample_rate=16000,
    frame_samples=1280,
    language="ko",
    playback_gain_db=-12.0,
    dump_dir=None,
    wake_chime=False,
    wake_threshold=0.5,
    wake_threshold_while_speaking=0.3,
    diagnostics_seconds=0.0,
    limits=endpoint_limits(
        trailing_silence_ms=700,
        no_speech_timeout_ms=5000,
        max_utterance_ms=15000,
        min_speech_ms=300,
    ),
)

a_track = {"duration": 0.5, "visemes": [], "segments": [], "envelope": [], "envelope_hz": 50}


class fake_client:
    def __init__(self) -> None:
        self.calls: list[tuple] = []
        self.transcription = "안녕"
        self.published: list[dict] = []

    def transcribe(self, wav: bytes, language: str) -> str:
        self.calls.append(("transcribe", len(wav), language))

        if self.transcription == "":
            raise no_speech_error()

        return self.transcription

    def start_turn(self, text: str, turn_id: str) -> None:
        self.calls.append(("start_turn", text, turn_id))

    def cancel_turn(self, turn_id: str) -> None:
        self.calls.append(("cancel_turn", turn_id))

    def speak(self, text: str) -> spoken_reply:
        self.calls.append(("speak", text))

        return spoken_reply(samples=np.zeros(22050, dtype=np.int16), sample_rate=22050, track=a_track)

    def publish(self, event: dict) -> None:
        self.published.append(event)


class fake_source:
    def __init__(self) -> None:
        self.flushed = 0
        self.dropped = 0

    def frames(self) -> Iterator[np.ndarray]:
        return iter([])

    def flush(self) -> None:
        self.flushed += 1


class fake_wake:
    def __init__(self, fire_on: set[int]) -> None:
        self.fire_on = fire_on
        self.seen = 0
        self.resets = 0

    def feed(self, frame: np.ndarray) -> float:
        self.seen += 1

        return 0.9 if self.seen in self.fire_on else 0.0

    def best(self) -> str:
        return "fake"

    def reset(self) -> None:
        self.resets += 1


class fake_vad:
    """Voiced for frames whose first sample is non-zero."""

    def feed(self, frame: np.ndarray) -> bool:
        return frame[0] != 0


class fake_sink:
    sample_rate = 16000

    def __init__(self) -> None:
        self.played: list[int] = []
        self.release = threading.Event()
        self.release.set()

    def play(self, samples: np.ndarray, stop: threading.Event) -> bool:
        self.played.append(len(samples))
        self.release.wait()

        return not stop.is_set()


def silent(count: int) -> list[np.ndarray]:
    return [np.zeros(1280, dtype=np.int16) for _ in range(count)]


def voiced(count: int) -> list[np.ndarray]:
    return [np.full(1280, 1000, dtype=np.int16) for _ in range(count)]


def make_loop(wake: fake_wake) -> tuple[audio_loop, fake_client, fake_sink]:
    client = fake_client()
    sink = fake_sink()
    loop = audio_loop(client, fake_source(), wake, fake_vad, sink, settings)

    return loop, client, sink


def test_wake_word_then_speech_then_silence_posts_a_turn() -> None:
    loop, client, _ = make_loop(fake_wake({3}))

    # 3 frames of nothing, wake fires on the third, 5 voiced, 9 silent (720ms).
    loop.listen(iter(silent(3) + voiced(5) + silent(9)))

    kinds = [call[0] for call in client.calls]
    assert kinds == ["transcribe", "start_turn"]
    assert client.calls[1][1] == "안녕"
    assert loop.accepted_turn == client.calls[1][2]
    # Trimmed to the voiced span: speech starts at 0ms, ends at 400ms, plus
    # 300ms padding after it = 700ms = 11200 samples * 2 bytes + 44-byte header.
    assert client.calls[0][1] == 11200 * 2 + 44
    assert [event["phase"] for event in client.published] == [
        "listening",
        "recording",
        "transcribing",
        "listening",
    ]


def test_nothing_recognised_does_not_post_a_turn() -> None:
    loop, client, _ = make_loop(fake_wake({1}))
    client.transcription = ""

    loop.listen(iter(silent(1) + voiced(5) + silent(9)))

    assert [call[0] for call in client.calls] == ["transcribe"]
    assert loop.accepted_turn is None


def test_silence_after_the_wake_word_times_out_without_stt() -> None:
    loop, client, _ = make_loop(fake_wake({1}))

    loop.listen(iter(silent(1) + silent(63)))

    assert client.calls == []
    assert [event["phase"] for event in client.published][-1] == "listening"


def test_wake_word_during_a_turn_cancels_it_and_clears_the_queue() -> None:
    loop, client, _ = make_loop(fake_wake({1}))
    loop.on_event({"kind": "turn_started", "turn_id": "t_1", "source": "agent", "trigger_text": None, "started_at": 0})
    loop.on_event({"kind": "utterance", "turn_id": "t_1", "seq": 0, "text": "첫 문장"})

    loop.listen(iter(silent(1) + voiced(5) + silent(9)))

    assert ("cancel_turn", "t_1") in client.calls
    assert len(loop.queue) == 0
    assert loop.stop_playback.is_set()


def test_utterances_of_the_adopted_turn_are_spoken_in_order_with_events() -> None:
    loop, client, sink = make_loop(fake_wake(set()))
    loop.on_event({"kind": "turn_started", "turn_id": "t_1", "source": "user", "trigger_text": "안녕", "started_at": 0})
    loop.on_event({"kind": "utterance", "turn_id": "t_1", "seq": 0, "text": "하나"})
    loop.on_event({"kind": "utterance", "turn_id": "t_1", "seq": 1, "text": "둘"})

    loop.speak_next()
    loop.speak_next()

    assert [call for call in client.calls if call[0] == "speak"] == [("speak", "하나"), ("speak", "둘")]
    # 22050 samples at 22050Hz resampled to the sink's 16000Hz.
    assert sink.played == [16000, 16000]
    assert [event["kind"] for event in client.published] == [
        "device_state",
        "speech_started",
        "speech_ended",
        "speech_started",
        "speech_ended",
        "device_state",
    ]
    assert client.published[1]["track"] == a_track
    assert client.published[1]["seq"] == 0
    assert client.published[-1]["phase"] == "listening"


def test_a_strangers_utterance_is_dropped() -> None:
    loop, client, _ = make_loop(fake_wake(set()))
    loop.on_event({"kind": "turn_started", "turn_id": "t_1", "source": "user", "trigger_text": None, "started_at": 0})

    loop.on_event({"kind": "utterance", "turn_id": "t_other", "seq": 0, "text": "남의 말"})

    assert len(loop.queue) == 0
    assert [call for call in client.calls if call[0] == "speak"] == []


def test_a_new_turn_preempts_the_queued_one() -> None:
    loop, _, _ = make_loop(fake_wake(set()))
    loop.on_event({"kind": "turn_started", "turn_id": "t_1", "source": "user", "trigger_text": None, "started_at": 0})
    loop.on_event({"kind": "utterance", "turn_id": "t_1", "seq": 0, "text": "하나"})

    loop.on_event({"kind": "turn_started", "turn_id": "t_2", "source": "agent", "trigger_text": None, "started_at": 1})

    assert len(loop.queue) == 0
    assert loop.accepted_turn == "t_2"


def test_turn_cancelling_stops_playback() -> None:
    loop, _, _ = make_loop(fake_wake(set()))
    loop.on_event({"kind": "turn_started", "turn_id": "t_1", "source": "user", "trigger_text": None, "started_at": 0})
    loop.on_event({"kind": "utterance", "turn_id": "t_1", "seq": 0, "text": "하나"})

    loop.on_event({"kind": "turn_cancelling", "turn_id": "t_1"})

    assert len(loop.queue) == 0
    assert loop.stop_playback.is_set()


def test_hello_adopts_the_active_turn_and_restates_the_phase() -> None:
    loop, client, _ = make_loop(fake_wake(set()))

    loop.on_event(
        {
            "kind": "stream_hello",
            "protocol_version": 1,
            "active_turn": {"turn_id": "t_9", "source": "agent", "trigger_text": None, "started_at": 0},
        }
    )

    assert loop.accepted_turn == "t_9"
    assert client.published == [{"kind": "device_state", "phase": "idle"}]


def test_recordings_are_kept_when_a_dump_dir_is_set(tmp_path) -> None:
    import dataclasses
    import os

    loop, _, _ = make_loop(fake_wake({1}))
    loop.settings = dataclasses.replace(settings, dump_dir=str(tmp_path))

    loop.listen(iter(silent(1) + voiced(5) + silent(9)))

    kept = os.listdir(tmp_path)
    assert len(kept) == 1 and kept[0].startswith("utterance-") and kept[0].endswith(".wav")


def test_the_wake_chime_plays_before_recording_when_enabled() -> None:
    import dataclasses

    loop, _, sink = make_loop(fake_wake({1}))
    loop.settings = dataclasses.replace(settings, wake_chime=True)

    loop.listen(iter(silent(1) + voiced(5) + silent(9)))

    # 150ms lead-in + 200ms tone at the sink's 16kHz.
    assert sink.played == [5600]


class scored_wake:
    def __init__(self, scores: list[float]) -> None:
        self.scores = iter(scores)

    def feed(self, frame: np.ndarray) -> float:
        return next(self.scores, 0.0)

    def best(self) -> str:
        return "scored"

    def reset(self) -> None:
        pass


def test_a_lower_bar_applies_while_the_device_is_speaking() -> None:
    client = fake_client()
    loop = audio_loop(client, fake_source(), scored_wake([0.4, 0.4]), fake_vad, fake_sink(), settings)

    # Not speaking: 0.4 is below the 0.5 bar, nothing happens.
    loop.listen(iter(silent(1)))
    assert all(call[0] != "cancel_turn" for call in client.calls)

    # Speaking: the same 0.4 clears the 0.3 bar and barges in.
    loop.adopt_turn("t_1")
    loop.speaking = True
    loop.listen(iter(silent(1) + voiced(5) + silent(9)))
    assert ("cancel_turn", "t_1") in client.calls
