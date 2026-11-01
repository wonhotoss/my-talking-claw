"""Microphone frames from PortAudio, as a plain iterator of int16 mono arrays.

A callback fills a short queue; the consumer (the loop) pulls frames at its own
pace. The queue is short on purpose: while the loop is busy transcribing, it is
not listening, and stale frames must not be replayed when it comes back -
`flush()` drops whatever piled up.

Day-9 live: the ReSpeaker dropped off USB and came back three seconds later.
PortAudio's stream kept a dead handle, its thread spun on ENODEV at 100% CPU,
and the loop sat in queue.get() forever - deaf, silently. So the iterator has a
watchdog: no frame for `stall_seconds` means the stream is reopened, after
re-initialising PortAudio so a re-enumerated device is found again.
"""

import queue
import sys
import time
from collections.abc import Iterator
from typing import Protocol

import numpy as np
import sounddevice as sd

stall_seconds = 2.0
reopen_retry_seconds = 2.0


class frame_source(Protocol):
    dropped: int

    def frames(self) -> Iterator[np.ndarray]: ...

    def flush(self) -> None: ...


def log(message: str) -> None:
    print(f"[audio] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


def refresh_devices() -> None:
    """PortAudio enumerates devices once; a device that came back on USB is
    only visible after a re-initialisation."""
    sd._terminate()
    sd._initialize()


class microphone:
    def __init__(self, device: int | str | None, sample_rate: int, frame_samples: int) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.frame_samples = frame_samples
        self.pending: queue.Queue[np.ndarray] = queue.Queue(maxsize=8)
        self.dropped = 0
        self.reopened = 0

    def flush(self) -> None:
        while True:
            try:
                self.pending.get_nowait()
            except queue.Empty:
                return

    def open_stream(self) -> sd.InputStream:
        def on_audio(indata: np.ndarray, frame_count: int, time_info, status) -> None:
            try:
                self.pending.put_nowait(indata[:, 0].copy())
            except queue.Full:
                self.dropped += 1

        return sd.InputStream(
            samplerate=self.sample_rate,
            channels=1,
            dtype="int16",
            blocksize=self.frame_samples,
            device=self.device,
            callback=on_audio,
        )

    def frames(self) -> Iterator[np.ndarray]:
        stream = self.open_stream()
        stream.start()

        while True:
            try:
                yield self.pending.get(timeout=stall_seconds)
                continue
            except queue.Empty:
                pass

            log(f"microphone stalled for {stall_seconds:.0f}s - reopening the capture stream")
            stream.abort(ignore_errors=True)
            stream.close(ignore_errors=True)
            self.flush()

            while True:
                try:
                    refresh_devices()
                    stream = self.open_stream()
                    stream.start()
                    self.reopened += 1
                    log("microphone reopened")
                    break
                except Exception as error:  # noqa: BLE001 - the device may still be re-enumerating
                    log(f"microphone reopen failed: {error}; retrying in {reopen_retry_seconds:.0f}s")
                    time.sleep(reopen_retry_seconds)
