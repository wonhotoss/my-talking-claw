"""Microphone frames from PortAudio, as a plain iterator of int16 mono arrays.

A callback fills a short queue; the consumer (the loop) pulls frames at its own
pace. The queue is short on purpose: while the loop is busy transcribing, it is
not listening, and stale frames must not be replayed when it comes back -
`flush()` drops whatever piled up.
"""

import queue
from collections.abc import Iterator
from typing import Protocol

import numpy as np
import sounddevice as sd


class frame_source(Protocol):
    dropped: int

    def frames(self) -> Iterator[np.ndarray]: ...

    def flush(self) -> None: ...


class microphone:
    def __init__(self, device: int | str | None, sample_rate: int, frame_samples: int) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.pending: queue.Queue[np.ndarray] = queue.Queue(maxsize=8)
        self.dropped = 0

    def flush(self) -> None:
        while True:
            try:
                self.pending.get_nowait()
            except queue.Empty:
                return

    def frames(self) -> Iterator[np.ndarray]:
        def on_audio(indata: np.ndarray, frame_count: int, time_info, status) -> None:
            try:
                self.pending.put_nowait(indata[:, 0].copy())
            except queue.Full:
                self.dropped += 1

        with sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.frame_samples,
            device=self.device,
            callback=on_audio,
        ):
            while True:
                yield self.pending.get()
