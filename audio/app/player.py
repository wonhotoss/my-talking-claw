"""Where the device's voice comes out.

`playback_sink` is the one seam for a different speaker: today PortAudio into
the ReSpeaker (16kHz stereo, fixed by its USB firmware), later possibly a
network stream to an external device. Everything above it hands over 16-bit
mono PCM at the sink's rate and a stop flag.
"""

import threading
from typing import Protocol

import numpy as np
import sounddevice as sd

# Granularity of the stop check: a barge-in lands within this much audio.
chunk_samples = 1600


class playback_sink(Protocol):
    sample_rate: int

    def play(self, samples: np.ndarray, stop: threading.Event) -> bool:
        """Blocks until the samples are out or `stop` is set; True if it finished."""
        ...


class portaudio_sink:
    def __init__(self, device: int | str | None, sample_rate: int, channels: int) -> None:
        self.device = device
        self.sample_rate = sample_rate
        self.channels = channels

    def play(self, samples: np.ndarray, stop: threading.Event) -> bool:
        with sd.OutputStream(
            samplerate=self.sample_rate,
            channels=self.channels,
            dtype="int16",
            device=self.device,
        ) as stream:
            for offset in range(0, len(samples), chunk_samples):
                if stop.is_set():
                    return False

                chunk = samples[offset : offset + chunk_samples]
                stream.write(np.repeat(chunk[:, np.newaxis], self.channels, axis=1))

        return True
