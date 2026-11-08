"""Shared plumbing for the probes: credentials, PCM/WAV helpers, timing, output.

Deliberately free of the Azure SDK so the pure parts import on any host and
the unit tests need no key. The SDK is imported only inside the probe scripts.
"""

import json
import os
import struct
import sys
import time
import wave
from collections.abc import Iterator
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path


# What the device captures (audio/app/main.py) and what Azure expects by
# default: one and the same, which is the whole reason streaming needs no
# resampling. Pinned here so a probe fed a different WAV fails loudly.
sample_rate = 16000
sample_width_bytes = 2
channels = 1
# The loop's capture frame: 1280 samples = 80 ms at 16 kHz.
frame_samples = 1280
frame_bytes = frame_samples * sample_width_bytes
frame_seconds = frame_samples / sample_rate


@dataclass(frozen=True)
class azure_settings:
    key: str
    region: str


def settings_from_environment() -> azure_settings:
    # A missing variable raises KeyError on its own; that is the message we want.
    return azure_settings(
        key=os.environ["AZURE_SPEECH_KEY"],
        region=os.environ["AZURE_SPEECH_REGION"],
    )


def log(message: str) -> None:
    print(f"[probe] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


def read_pcm(path: Path) -> bytes:
    """16 kHz / 16-bit / mono PCM bytes from a WAV, or a crash explaining why not."""
    with wave.open(str(path), "rb") as source:
        actual = (source.getframerate(), source.getsampwidth(), source.getnchannels())
        expected = (sample_rate, sample_width_bytes, channels)

        if actual != expected:
            raise ValueError(f"{path}: (rate, width, channels) is {actual}, expected {expected}")

        return source.readframes(source.getnframes())


def write_wav(path: Path, pcm: bytes, rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)

    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(sample_width_bytes)
        target.setframerate(rate)
        target.writeframes(pcm)


def pcm_seconds(pcm: bytes, rate: int) -> float:
    return len(pcm) / (rate * sample_width_bytes * channels)


def frames_of(pcm: bytes, size: int) -> Iterator[bytes]:
    """Consecutive slices of `size` bytes; the last one may be short."""
    return (pcm[offset : offset + size] for offset in range(0, len(pcm), size))


def peak_dbfs(pcm: bytes) -> float:
    """Peak level of 16-bit PCM, for a quick 'is this clip audible' column."""
    if len(pcm) == 0:
        return -100.0

    count = len(pcm) // sample_width_bytes
    samples = struct.unpack(f"<{count}h", pcm[: count * sample_width_bytes])
    peak = max(abs(value) for value in samples)

    if peak == 0:
        return -100.0

    import math

    return 20 * math.log10(peak / 32768)


def dump_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(_plain(payload), ensure_ascii=False, indent=2), encoding="utf-8")


def _plain(value: object) -> object:
    if is_dataclass(value) and not isinstance(value, type):
        return _plain(asdict(value))

    if isinstance(value, dict):
        return {key: _plain(item) for key, item in value.items()}

    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]

    return value


class stopwatch:
    """Monotonic seconds since construction; `lap()` reads without resetting."""

    def __init__(self) -> None:
        self.started = time.monotonic()

    def lap(self) -> float:
        return time.monotonic() - self.started


def table(rows: list[list[str]], headers: list[str]) -> str:
    """A fixed-width text table that pastes cleanly into a day-N.md code block."""
    widths = [
        max(len(headers[column]), *(len(row[column]) for row in rows)) if rows else len(headers[column])
        for column in range(len(headers))
    ]

    def line(cells: list[str]) -> str:
        return "  ".join(cell.ljust(widths[column]) for column, cell in enumerate(cells))

    return "\n".join([line(headers), line(["-" * width for width in widths]), *(line(row) for row in rows)])
