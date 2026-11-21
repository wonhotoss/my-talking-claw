"""Records the family voice checklist (voice_checklist.py), one row at a time.

This is the terminal version; the studio app (voice_studio.py, launched by
scripts/rpi-record.sh) shows the prompts on the RPi screen and shares
`recording_device` below.

Run it on the RPi with the audio loop stopped (both want the ReSpeaker), from
a terminal the operator can see; the person being recorded only listens for
the beeps:

  ready cue (low beep)  -> speak now
  chime (the loop's)    -> wake_command only: "딸기님" was heard, ask the question

Same microphone, rate, channel and VAD as the loop, so the data matches what
the device will really hear. After each take: its length and peak level, then
Enter keeps it (and fills the row), p plays it back, r redoes, s skips the row
for now, q quits. Rerunning resumes at the first row without a file.

  uv run python -m app.voice_recorder ~/claw-data/voices/checklist.csv [--person child] [--session 1]

Takes are saved next to the checklist as <person>/<kind>/<id>_<conditions>.wav,
so a file is self-describing even without the CSV.
"""

import argparse
import os
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass

import numpy as np

from app import voice_checklist as checklist
from app.capture import microphone
from app.endpointer import endpoint_limits, endpointer, voiced_span_ms
from app.loop import chime, trim_padding_ms
from app.main import capture_frame_samples, capture_rate, device_from_env, env
from app.pcm import apply_gain, dbfs, resample, tone, wav_bytes
from app.player import portaudio_sink
from app.vad import silero_vad, voice_detector

# "딸기님" is ~0.6 s; a short trailing silence ends it, so the chime comes about
# as soon after the word as the real detector's would.
wake_limits = endpoint_limits(trailing_silence_ms=500, no_speech_timeout_ms=5000, max_utterance_ms=4000, min_speech_ms=200)
# The loop's own defaults (main.py), for the question.
command_limits = endpoint_limits(trailing_silence_ms=700, no_speech_timeout_ms=5000, max_utterance_ms=15000, min_speech_ms=300)
free_max_seconds = 120
ambient_max_seconds = checklist.ambient_minutes * 60

frame_ms = capture_frame_samples * 1000 // capture_rate

# Above this the take is clipped (day-11: the chime alone drove the mic to -0 dBFS).
clipping_dbfs = -1.0
# Below this the speech is probably lost in the noise floor.
too_quiet_dbfs = -40.0


@dataclass(frozen=True)
class take:
    # None when it went wrong; `problem` says what.
    wake: np.ndarray | None
    command: np.ndarray | None
    problem: str | None


def ready_cue(sample_rate: int) -> np.ndarray:
    """Lower than the chime so the two are not confused; the same lead-in, which
    the USB DAC swallows (loop.chime)."""
    lead_in = np.zeros(int(0.15 * sample_rate), dtype=np.int16)

    return np.concatenate([lead_in, tone(523, 0.15, sample_rate, -12.0)])


def record_until_endpoint(frames: Iterator[np.ndarray], vad: voice_detector,
                          limits: endpoint_limits) -> tuple[str, np.ndarray]:
    """The verdict and the take trimmed to its voiced span, as loop.record_utterance."""
    points = endpointer(limits)
    recorded: list[np.ndarray] = []
    verdict = "no_speech"

    for frame in frames:
        recorded.append(frame)
        points.feed(vad.feed(frame), frame_ms)
        verdict = points.verdict()

        if verdict != "recording":
            break

    start_ms, end_ms = voiced_span_ms(points, trim_padding_ms)
    per_ms = capture_rate // 1000

    return verdict, np.concatenate(recorded)[start_ms * per_ms : end_ms * per_ms]


def record_until_stopped(frames: Iterator[np.ndarray], stop: threading.Event, max_seconds: int) -> np.ndarray:
    """Untrimmed: free talk and the house sound are kept whole."""
    recorded: list[np.ndarray] = []

    for frame in frames:
        recorded.append(frame)

        if stop.is_set() or len(recorded) * frame_ms >= max_seconds * 1000:
            break

    return np.concatenate(recorded)


def take_wake(frames: Iterator[np.ndarray], make_vad: Callable[[], voice_detector]) -> take:
    verdict, wake = record_until_endpoint(frames, make_vad(), wake_limits)

    if verdict != "done":
        return take(None, None, f"딸기님이 안 들렸어요 ({verdict})")

    return take(wake, None, None)


def take_wake_command(frames: Iterator[np.ndarray], make_vad: Callable[[], voice_detector],
                      play_chime: Callable[[], None]) -> take:
    """"딸기님", the chime, the question - as the loop runs. `play_chime` also
    flushes what the microphone heard of the chime itself."""
    first = take_wake(frames, make_vad)

    if first.problem is not None:
        return first

    play_chime()
    verdict, command = record_until_endpoint(frames, make_vad(), command_limits)

    if verdict != "done":
        # Usually the question ran on straight after "딸기님", before the chime.
        return take(first.wake, None, f"차임 뒤 질문이 안 들렸어요 ({verdict}) - 딸기님, 차임, 그다음 질문")

    return take(first.wake, command, None)


def file_stem(item: dict[str, str]) -> str:
    conditions = "-".join(
        value for value in (item["distance"], item["loudness"], item["intonation"], item["background"]) if value
    )

    return os.path.join(item["person"], item["kind"], f"{item['id']}_{conditions}")


def describe(item: dict[str, str]) -> str:
    parts = [
        checklist.people.get(item["person"], "집"),
        checklist.kinds[item["kind"]],
        checklist.distances.get(item["distance"], ""),
        checklist.loudnesses.get(item["loudness"], ""),
        checklist.intonations.get(item["intonation"], ""),
        checklist.backgrounds[item["background"]],
        f"{item['session']}차",
        item["split"],
    ]

    return " · ".join(part for part in parts if part)


def level_report(samples: np.ndarray) -> tuple[float, str]:
    peak = float(np.max(np.abs(samples.astype(np.int32)))) if len(samples) > 0 else 0.0
    peak_dbfs = 20 * np.log10(max(peak, 1.0) / 32768)
    warnings = [
        *(["포화 - 더 작게 또는 더 멀리"] if peak_dbfs > clipping_dbfs else []),
        *(["너무 작음 - 다시 해 볼 만함"] if peak_dbfs < too_quiet_dbfs else []),
    ]
    text = f"{len(samples) / capture_rate:.1f}s, 피크 {peak_dbfs:.1f} dBFS, RMS {dbfs(samples):.1f} dBFS"

    return peak_dbfs, " / ".join([text, *warnings])


def pending(rows: list[dict[str, str]], person: str | None, session: str | None) -> list[dict[str, str]]:
    return [
        item for item in rows
        if not item["file"]
        and (person is None or item["person"] == person)
        and (session is None or item["session"] == session)
    ]


def save(root: str, item: dict[str, str], recorded: take | np.ndarray) -> dict[str, str]:
    """Writes the WAV(s) and returns the row with its recorder columns filled."""
    stem = file_stem(item)
    os.makedirs(os.path.join(root, os.path.dirname(stem)), exist_ok=True)

    def write(name: str, samples: np.ndarray) -> str:
        with open(os.path.join(root, name), "wb") as target:
            target.write(wav_bytes(samples, capture_rate))

        return name.replace(os.sep, "/")

    if isinstance(recorded, take):
        main_file = write(f"{stem}_wake.wav", recorded.wake)
        command_file = write(f"{stem}_command.wav", recorded.command) if recorded.command is not None else ""
        whole = recorded.wake if recorded.command is None else np.concatenate([recorded.wake, recorded.command])
    else:
        main_file = write(f"{stem}.wav", recorded)
        command_file = ""
        whole = recorded

    peak_dbfs, _ = level_report(whole)

    return {
        **item,
        "file": main_file,
        "command_file": command_file,
        "seconds": f"{len(whole) / capture_rate:.2f}",
        "peak_dbfs": f"{peak_dbfs:.1f}",
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def whole_take(recorded: take | np.ndarray) -> np.ndarray:
    if not isinstance(recorded, take):
        return recorded

    return np.concatenate([part for part in (recorded.wake, recorded.command) if part is not None])


def stops_on_request(item: dict[str, str]) -> bool:
    """Free talk and the house sound end when the operator says so; the wake
    kinds end at the endpoint."""
    return item["kind"] in ("free", "ambient")


class recording_device:
    """The ReSpeaker side, shared by this CLI and the studio app (voice_studio.py):
    one capture stream for the whole sitting, the cues, one take per row."""

    def __init__(self) -> None:
        self.source = microphone(device_from_env("AUDIO_INPUT_DEVICE"), capture_rate, capture_frame_samples)
        self.sink = portaudio_sink(
            device_from_env("AUDIO_OUTPUT_DEVICE"),
            int(env("AUDIO_OUTPUT_RATE", "16000")),
            int(env("AUDIO_OUTPUT_CHANNELS", "2")),
        )
        self.playback_gain_db = float(env("AUDIO_PLAYBACK_GAIN_DB", "-12"))
        vad_threshold = float(env("VAD_THRESHOLD", "0.5"))
        self.make_vad = lambda: silero_vad(vad_threshold)
        # Level of the last frame pulled, for a live meter; it only moves while recording.
        self.live_dbfs = -90.0
        self.frames = self.metered()

    def metered(self) -> Iterator[np.ndarray]:
        for frame in self.source.frames():
            self.live_dbfs = dbfs(frame)
            yield frame

    def play(self, samples: np.ndarray) -> None:
        self.sink.play(resample(samples, capture_rate, self.sink.sample_rate), threading.Event())

    def cue(self, samples: np.ndarray) -> None:
        """The speaker sits next to the microphone: drop what it heard of the cue."""
        self.play(samples)
        self.source.flush()

    def playback(self, recorded: take | np.ndarray) -> None:
        parts = [recorded.wake, recorded.command] if isinstance(recorded, take) else [recorded]

        for part in parts:
            if part is not None:
                self.play(apply_gain(part, self.playback_gain_db))

    def record(self, item: dict[str, str], stop: threading.Event,
               announce: Callable[[str], None]) -> tuple[take | np.ndarray, int]:
        """The take and how many frames were lost during it. `announce` hears
        each cue as it happens ("말하세요", "질문하세요"); `stop` ends free talk
        and the house sound."""
        self.source.flush()
        self.cue(ready_cue(self.sink.sample_rate))
        # The queue overflows while a menu waits (nobody pulls frames); only
        # drops during the take itself mean lost audio.
        dropped_before = self.source.dropped
        recorded = self.record_kind(item, stop, announce)

        return recorded, self.source.dropped - dropped_before

    def record_kind(self, item: dict[str, str], stop: threading.Event,
                    announce: Callable[[str], None]) -> take | np.ndarray:
        if item["kind"] == "wake":
            announce("말하세요")
            return take_wake(self.frames, self.make_vad)

        if item["kind"] == "wake_command":
            def chime_then_ask() -> None:
                self.cue(chime(self.sink.sample_rate))
                announce("질문하세요")

            announce("말하세요")
            return take_wake_command(self.frames, self.make_vad, chime_then_ask)

        max_seconds = ambient_max_seconds if item["kind"] == "ambient" else free_max_seconds
        announce(f"녹음 중 - 끝나면 끝 (최대 {max_seconds // 60}분)")

        return record_until_stopped(self.frames, stop, max_seconds)


class session_runner:
    """The terminal version: the operator reads the prompts and passes them on."""

    def __init__(self, path: str, person: str | None, session: str | None) -> None:
        self.path = path
        self.root = os.path.dirname(path)
        self.person = person
        self.session = session
        self.device = recording_device()

    def record(self, item: dict[str, str]) -> take | np.ndarray:
        stop = threading.Event()
        waiter = threading.Thread(target=lambda: (input(), stop.set()), daemon=True)

        if stops_on_request(item):
            waiter.start()

        recorded, lost = self.device.record(item, stop, lambda text: print(f"  {text}"))

        if stops_on_request(item) and not stop.is_set():
            print("  시간이 다 됐어요 - Enter를 누르세요")
            waiter.join()

        if lost > 0:
            print(f"  ! 녹음 중 프레임 {lost}개 유실 - 다시 하는 게 좋아요")

        return recorded

    def run(self) -> None:
        rows = checklist.read_rows(self.path)
        print(checklist.summary(rows))
        to_do = pending(rows, self.person, self.session)

        for position, item in enumerate(to_do, start=1):
            print(f"\n[{position}/{len(to_do)}] {item['id']}  {describe(item)}")
            print(f"  말할 것: {item['prompt']}")

            answer = input("  Enter=녹음, s=건너뛰기, q=끝 > ").strip()

            while answer not in ("s", "q"):
                recorded = self.record(item)

                if isinstance(recorded, take) and recorded.problem is not None:
                    print(f"  ! {recorded.problem}")
                    answer = input("  Enter=다시, s=건너뛰기, q=끝 > ").strip()
                else:
                    print(f"  {level_report(whole_take(recorded))[1]}")
                    answer = input("  Enter=저장, p=듣기, r=다시, s=건너뛰기, q=끝 > ").strip()

                    while answer == "p":
                        self.device.playback(recorded)
                        answer = input("  Enter=저장, p=듣기, r=다시, s=건너뛰기, q=끝 > ").strip()

                    if answer == "":
                        saved = save(self.root, item, recorded)
                        rows = [saved if other["id"] == item["id"] else other for other in rows]
                        checklist.write_rows(self.path, rows)
                        print(f"  저장: {saved['file']}")
                        break

            if answer == "q":
                break

        print(f"\n{checklist.summary(rows)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="가족 목소리 체크리스트 녹음")
    parser.add_argument("checklist", help="voice_checklist.py가 만든 CSV")
    parser.add_argument("--person", choices=list(checklist.people) + ["house"], help="이 사람의 줄만")
    parser.add_argument("--session", choices=["1", "2"], help="이 회차의 줄만")
    arguments = parser.parse_args()

    session_runner(os.path.expanduser(arguments.checklist), arguments.person, arguments.session).run()


if __name__ == "__main__":
    main()
