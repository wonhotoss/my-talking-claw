"""Decides where an utterance ends, from a stream of voice-activity verdicts.

Pure state over frame durations, so it is tested without audio. The loop feeds
one verdict per captured frame after the wake word fired and asks `verdict()`
until it is not `recording`.
"""

from dataclasses import dataclass
from typing import Literal

endpoint_verdict = Literal["recording", "done", "no_speech", "too_long"]


@dataclass(frozen=True)
class endpoint_limits:
    # Trailing silence that closes an utterance once speech has been heard.
    trailing_silence_ms: int
    # Nothing voiced for this long after the wake word: the user went quiet.
    no_speech_timeout_ms: int
    # Hard cap, so a radio left on cannot record forever.
    max_utterance_ms: int
    # A single voiced frame is a click; speech has to last this long to count.
    min_speech_ms: int


class endpointer:
    def __init__(self, limits: endpoint_limits) -> None:
        self.limits = limits
        self.elapsed_ms = 0
        self.speech_ms = 0
        self.silence_after_speech_ms = 0
        # Where the voiced part lies, so the recording can be trimmed before STT:
        # whisper on seconds of near-silence is both slow (temperature fallback)
        # and prone to hallucinate (day-8, day-9 live: 6s clip with 0.7s of
        # speech took 53s).
        self.first_voiced_ms: int | None = None
        self.last_voiced_ms = 0

    @property
    def heard_speech(self) -> bool:
        return self.speech_ms >= self.limits.min_speech_ms

    def feed(self, voiced: bool, frame_ms: int) -> None:
        self.elapsed_ms += frame_ms

        if voiced:
            self.speech_ms += frame_ms
            self.silence_after_speech_ms = 0
            self.last_voiced_ms = self.elapsed_ms

            if self.first_voiced_ms is None:
                self.first_voiced_ms = self.elapsed_ms - frame_ms
        elif self.heard_speech:
            self.silence_after_speech_ms += frame_ms

    def verdict(self) -> endpoint_verdict:
        if self.elapsed_ms >= self.limits.max_utterance_ms:
            return "too_long" if not self.heard_speech else "done"

        if self.heard_speech and self.silence_after_speech_ms >= self.limits.trailing_silence_ms:
            return "done"

        if not self.heard_speech and self.elapsed_ms >= self.limits.no_speech_timeout_ms:
            return "no_speech"

        return "recording"


def voiced_span_ms(points: endpointer, padding_ms: int) -> tuple[int, int]:
    """[start, end) in ms of the part worth transcribing, padded on both sides."""
    if points.first_voiced_ms is None:
        return 0, points.elapsed_ms

    return max(points.first_voiced_ms - padding_ms, 0), min(points.last_voiced_ms + padding_ms, points.elapsed_ms)
