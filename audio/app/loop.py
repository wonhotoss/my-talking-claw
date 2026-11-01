"""The device's audio loop: listens, triggers turns, speaks, and reports.

Three threads, each a thin wrapper over a synchronous method so the behaviour
is testable without audio hardware or a network:

  listen()      capture -> wake word -> endpoint -> STT -> POST /api/turns
  on_event()    SSE from the backend -> utterance queue / barge-in bookkeeping
  speak_next()  queue -> /tts/speak -> sink, bracketed by speech_started/ended

"Newest intent wins", exactly as the browser enforced it: any turn other than
the one we adopted clears the speaker, and our own turn is adopted before its
POST so it is left alone.
"""

import os
import sys
import threading
import time
import uuid
from collections import deque
from collections.abc import Callable, Iterator
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass

import numpy as np

from app.capture import frame_source
from app.device_client import device_client, no_speech_error, spoken_reply
from app.endpointer import endpoint_limits, endpointer, voiced_span_ms
from app.pcm import apply_gain, dbfs, resample, tone, wav_bytes
from app.player import playback_sink
from app.vad import voice_detector
from app.wake_word import wake_word_detector


@dataclass(frozen=True)
class loop_settings:
    sample_rate: int
    frame_samples: int
    language: str
    playback_gain_db: float
    limits: endpoint_limits
    # Where to keep each recorded utterance as a WAV, for tuning the endpointer
    # and comparing STT settings offline. None keeps nothing.
    dump_dir: str | None
    # Day-9 first live turn: the user missed the on-screen "듣고 있어요" and said
    # nothing. A chime right after the wake word is the cue that needs no screen.
    wake_chime: bool
    wake_threshold: float
    # While we are talking the mic hears us too (no AEC, day-9), so the wake word
    # scores lower; a separate bar for barge-in. The peak score seen during each
    # stretch of speech is logged so this can be tuned from the log alone.
    wake_threshold_while_speaking: float
    # Every this many seconds, one log line with the mic level and the peak wake
    # score: tells a dead microphone from a deaf detector. 0 turns it off.
    diagnostics_seconds: float


@dataclass(frozen=True)
class queued_utterance:
    turn_id: str
    seq: int
    text: str
    synthesis: Future


# Kept on either side of the voiced span, so a soft first syllable survives.
trim_padding_ms = 300


def chime(sample_rate: int) -> np.ndarray:
    """The "I'm listening" cue. A USB DAC swallows its first ~100ms after the
    stream opens (day-9: a bare 120ms tone was inaudible), hence the lead-in."""
    lead_in = np.zeros(int(0.15 * sample_rate), dtype=np.int16)

    return np.concatenate([lead_in, tone(880, 0.2, sample_rate, -12.0)])


def log(message: str) -> None:
    print(f"[audio] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


class audio_loop:
    def __init__(
        self,
        client: device_client,
        source: frame_source,
        wake: wake_word_detector,
        make_vad: Callable[[], voice_detector],
        sink: playback_sink,
        settings: loop_settings,
    ) -> None:
        self.client = client
        self.source = source
        self.wake = wake
        self.make_vad = make_vad
        self.sink = sink
        self.settings = settings

        self.lock = threading.Lock()
        self.accepted_turn: str | None = None
        self.queue: deque[queued_utterance] = deque()
        self.queue_ready = threading.Condition(self.lock)
        self.stop_playback = threading.Event()
        self.synthesizer = ThreadPoolExecutor(max_workers=1)
        self.phase = "idle"
        self.speaking = False
        self.last_seq = 0
        # For the latency line in the log: when our turn was posted.
        self.turn_posted_at = 0.0

    # --- reporting -------------------------------------------------------

    def report(self, event: dict) -> None:
        try:
            self.client.publish(event)
        except Exception as error:  # noqa: BLE001 - reporting must not take the loop down
            log(f"publish failed: {error}")

    def set_phase(self, phase: str) -> None:
        if phase == self.phase:
            return

        self.phase = phase
        self.report({"kind": "device_state", "phase": phase})

    def resting_phase(self) -> str:
        return "speaking" if self.speaking else "listening"

    # --- speaker side ----------------------------------------------------

    def adopt_turn(self, turn_id: str) -> None:
        with self.lock:
            self.accepted_turn = turn_id

    def clear_speaker(self) -> None:
        with self.lock:
            for item in self.queue:
                item.synthesis.cancel()

            self.queue.clear()
            self.stop_playback.set()

    def on_event(self, event: dict) -> None:
        kind = event["kind"]

        if kind == "stream_hello":
            active = event["active_turn"]
            self.adopt_turn(None if active is None else active["turn_id"])
            # A viewer that reconnected alongside us has no idea what we are doing.
            self.report({"kind": "device_state", "phase": self.phase})
            return

        if kind == "turn_started":
            if event["turn_id"] != self.accepted_turn:
                self.clear_speaker()
                self.adopt_turn(event["turn_id"])

            return

        if kind == "utterance":
            if event["turn_id"] != self.accepted_turn:
                log(f"dropped an utterance from turn {event['turn_id']}")
                return

            with self.queue_ready:
                self.queue.append(
                    queued_utterance(
                        turn_id=event["turn_id"],
                        seq=event["seq"],
                        text=event["text"],
                        synthesis=self.synthesizer.submit(self.client.speak, event["text"]),
                    )
                )
                self.queue_ready.notify()

            return

        if kind == "turn_cancelling" and event["turn_id"] == self.accepted_turn:
            self.clear_speaker()

    def prepare(self, spoken: spoken_reply) -> np.ndarray:
        return apply_gain(
            resample(spoken.samples, spoken.sample_rate, self.sink.sample_rate),
            self.settings.playback_gain_db,
        )

    def speak_next(self) -> None:
        with self.queue_ready:
            while not self.queue:
                self.queue_ready.wait()

            item = self.queue.popleft()
            self.stop_playback.clear()

        try:
            spoken = item.synthesis.result()
        except Exception as error:  # noqa: BLE001 - one bad sentence must not silence the rest
            log(f"synthesis failed for seq {item.seq}: {error}")
            return

        if self.stop_playback.is_set() or item.turn_id != self.accepted_turn:
            return

        if self.turn_posted_at > 0:
            log(f"first sound {time.time() - self.turn_posted_at:.2f}s after the turn was posted")
            self.turn_posted_at = 0.0

        self.speaking = True
        self.set_phase("speaking")
        self.report(
            {
                "kind": "speech_started",
                "turn_id": item.turn_id,
                "seq": item.seq,
                "started_at": time.time(),
                "track": spoken.track,
            }
        )
        finished = self.sink.play(self.prepare(spoken), self.stop_playback)
        self.report({"kind": "speech_ended", "turn_id": item.turn_id, "seq": item.seq})
        log(f"spoke seq {item.seq} ({spoken.track['duration']:.1f}s){'' if finished else ' - interrupted'}")

        with self.lock:
            self.speaking = bool(self.queue)

        if self.phase == "speaking" and not self.speaking:
            self.set_phase("listening")

    # --- microphone side -------------------------------------------------

    def record_utterance(self, frames: Iterator[np.ndarray]) -> tuple[str, np.ndarray]:
        """The verdict and the recording trimmed to its voiced span."""
        vad = self.make_vad()
        points = endpointer(self.settings.limits)
        frame_ms = self.settings.frame_samples * 1000 // self.settings.sample_rate
        recorded: list[np.ndarray] = []
        verdict = "no_speech"

        for frame in frames:
            recorded.append(frame)
            points.feed(vad.feed(frame), frame_ms)
            verdict = points.verdict()

            if verdict != "recording":
                break

        start_ms, end_ms = voiced_span_ms(points, trim_padding_ms)
        log(f"endpoint {verdict}: speech {points.speech_ms}ms of {points.elapsed_ms}ms, kept {start_ms}-{end_ms}ms")
        samples = np.concatenate(recorded) if recorded else np.zeros(0, dtype=np.int16)
        per_ms = self.settings.sample_rate // 1000

        return verdict, samples[start_ms * per_ms : end_ms * per_ms]

    def run_turn(self, recorded: np.ndarray) -> None:
        self.set_phase("transcribing")
        started = time.time()
        wav = wav_bytes(recorded, self.settings.sample_rate)

        if self.settings.dump_dir is not None:
            path = os.path.join(self.settings.dump_dir, time.strftime("utterance-%Y%m%d-%H%M%S.wav"))
            with open(path, "wb") as dump:
                dump.write(wav)

        try:
            text = self.client.transcribe(wav, self.settings.language)
        except no_speech_error:
            log(f"stt {time.time() - started:.2f}s: nothing recognised")
            return

        log(f"stt {time.time() - started:.2f}s: {text}")

        turn_id = str(uuid.uuid4())
        # Adopted before the POST so turn_started is not taken for a stranger's.
        self.adopt_turn(turn_id)
        self.turn_posted_at = time.time()

        try:
            self.client.start_turn(text, turn_id)
        except Exception as error:  # noqa: BLE001 - a backend blip loses one turn, not the device
            log(f"turn trigger failed: {error}")

    def barge_in(self) -> None:
        turn_id = self.accepted_turn
        self.clear_speaker()

        # Whether it is still thinking or already talking, the newest intent wins.
        # On a turn that already ended the backend answers cancelled=false; cheap.
        if turn_id is not None:
            log(f"barge-in: cancelling turn {turn_id}")

            try:
                self.client.cancel_turn(turn_id)
            except Exception as error:  # noqa: BLE001
                log(f"cancel failed: {error}")

    def wake_threshold(self) -> float:
        return (
            self.settings.wake_threshold_while_speaking
            if self.speaking
            else self.settings.wake_threshold
        )

    def listen(self, frames: Iterator[np.ndarray]) -> None:
        self.set_phase("listening")
        peak_while_speaking = 0.0
        diag_peak_score = 0.0
        diag_peak_word = ""
        diag_peak_level = -100.0
        diag_at = time.time()

        for frame in frames:
            score = self.wake.feed(frame)

            if self.settings.diagnostics_seconds > 0:
                if score > diag_peak_score:
                    diag_peak_score, diag_peak_word = score, self.wake.best()

                diag_peak_level = max(diag_peak_level, dbfs(frame))

                if time.time() - diag_at >= self.settings.diagnostics_seconds:
                    log(f"mic peak {diag_peak_level:.0f} dBFS, peak wake score {diag_peak_score:.2f} ({diag_peak_word}), dropped frames {self.source.dropped}")
                    diag_peak_score, diag_peak_word, diag_peak_level, diag_at = 0.0, "", -100.0, time.time()

            if self.speaking:
                peak_while_speaking = max(peak_while_speaking, score)
            elif peak_while_speaking > 0:
                log(f"peak wake score while speaking: {peak_while_speaking:.2f}")
                peak_while_speaking = 0.0

            if score < self.wake_threshold():
                continue

            log(f"wake word {self.wake.best()} (score {score:.2f}{', while speaking' if self.speaking else ''})")
            self.wake.reset()
            self.barge_in()
            self.set_phase("recording")

            if self.settings.wake_chime:
                self.sink.play(chime(self.sink.sample_rate), threading.Event())

            verdict, recorded = self.record_utterance(frames)

            if verdict in ("done", "too_long"):
                self.run_turn(recorded)

            # Frames that piled up while we were busy are stale by definition.
            self.source.flush()
            self.wake.reset()
            self.set_phase(self.resting_phase())

    # --- threads ---------------------------------------------------------

    def follow_events(self) -> None:
        while True:
            try:
                for message in self.client.events(self.last_seq):
                    if message.seq > 0:
                        self.last_seq = message.seq

                    self.on_event(message.event)
            except Exception as error:  # noqa: BLE001 - the backend restarting must not kill the device
                log(f"event stream dropped: {error}; reconnecting")
                time.sleep(2.0)

    def speak_forever(self) -> None:
        while True:
            self.speak_next()

    def run(self) -> None:
        threading.Thread(target=self.follow_events, name="events", daemon=True).start()
        threading.Thread(target=self.speak_forever, name="speaker", daemon=True).start()
        self.listen(self.source.frames())
