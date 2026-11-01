"""Wake word detection behind one small interface.

Today's implementation is openWakeWord with one of its stock English models
("hey jarvis") as a placeholder: there is no off-the-shelf Korean "누구야"
(day-9.md "열린 결정"). Swapping in Porcupine or a custom openWakeWord model
is one more class here, nothing elsewhere.
"""

import os
from typing import Protocol

import numpy as np


class wake_word_detector(Protocol):
    def feed(self, frame: np.ndarray) -> float:
        """Confidence 0..1 that this frame completes a wake word.

        The threshold lives in the loop, which knows whether the speaker is on
        (the mic then also hears our own voice, and the bar may need to differ).
        """
        ...

    def best(self) -> str:
        """Which wake word the last feed() scored highest, for the log."""
        ...

    def reset(self) -> None: ...


class openwakeword_detector:
    """Several stock models at once: day-9 live, a Korean speaker's "hey jarvis"
    scored anywhere from 0.0 to 0.65, so more than one phrase is on offer until a
    real Korean wake word exists."""

    def __init__(self, model_names: list[str]) -> None:
        import openwakeword
        from openwakeword.model import Model

        self.model_names = model_names
        self.last_scores: dict[str, float] = {name: 0.0 for name in model_names}

        models_dir = os.path.join(os.path.dirname(openwakeword.__file__), "resources", "models")
        missing = [
            name for name in model_names
            if not os.path.exists(os.path.join(models_dir, f"{name}_v0.1.onnx"))
        ]
        if missing:
            # One-time download (~1.3MB each, plus the shared feature models).
            openwakeword.utils.download_models(missing)

        # A copy: openWakeWord rewrites the list it is given, name -> file path.
        self.model = Model(wakeword_models=list(model_names), inference_framework="onnx")

    def feed(self, frame: np.ndarray) -> float:
        scores = self.model.predict(frame)
        self.last_scores = {name: float(scores[name]) for name in self.model_names}

        return max(self.last_scores.values())

    def best(self) -> str:
        return max(self.last_scores, key=lambda name: self.last_scores[name])

    def reset(self) -> None:
        self.model.reset()
