"""Voice activity per captured frame, from the silero model openWakeWord ships.

Silero wants 30ms (480-sample) windows and the capture frame is 80ms, so the
detector re-chunks internally; a frame is "voiced" when any window in it is.
"""

from typing import Protocol

import numpy as np


class voice_detector(Protocol):
    def feed(self, frame: np.ndarray) -> bool: ...


silero_window_samples = 480


class silero_vad:
    def __init__(self, threshold: float) -> None:
        from openwakeword.vad import VAD

        self.threshold = threshold
        self.model = VAD()
        self.carry = np.zeros(0, dtype=np.int16)

    def feed(self, frame: np.ndarray) -> bool:
        samples = np.concatenate([self.carry, frame.astype(np.int16)])
        usable = len(samples) - len(samples) % silero_window_samples
        self.carry = samples[usable:]

        if usable == 0:
            return False

        probabilities = np.asarray(
            self.model.predict(samples[:usable], frame_size=silero_window_samples)
        )

        return float(np.max(probabilities)) >= self.threshold
