"""Piper (ko_KR-kss-medium) engine adapter.

Piper is a VITS model too, so it can produce the same per-phoneme alignment
MeloTTS does - but only from a patched voice: `piper.patch_voice_with_alignment`
marks the graph's w_ceil tensor as an output, and PiperVoice then fills
AudioChunk.phoneme_alignments. The image does that patch at build time; an
unpatched model yields alignments of None and this adapter refuses to run.

piper is imported lazily on first use, so this module imports cleanly on hosts
where it is not installed (the Windows dev box, and the MeloTTS image).

Environment (shared knobs are documented on speech.speech_engine):

- PIPER_MODEL   (default /voices/ko_KR-kss-medium-aligned.onnx)
- PIPER_CONFIG  (default: the model path with -aligned stripped, + .json;
                 piper writes the config next to the *unpatched* model)
"""

import os
import re
from collections.abc import Iterable

import numpy

from app import speech, viseme


default_model = "/voices/ko_KR-kss-medium-aligned.onnx"

# Piper's own sentence splitting happens inside espeak and never comes back out
# as text - AudioChunk carries phonemes, not the sentence it came from. Subtitle
# segments need that text, so the split happens here instead, on sentence-final
# punctuation with the delimiter kept. A piece may still be split further by
# espeak; its chunks are concatenated back into the one piece we know the text
# of, so the mapping holds either way.
sentence_pattern = re.compile(r"(?<=[.!?…])\s+")


class piper_engine(speech.speech_engine):
    """On-device Korean text-to-speech backed by Piper.

    The voice is single-speaker Korean, so `language` is fixed rather than
    configurable: the espeak-ng IPA viseme table in app/viseme.py only describes
    Korean, and the model cannot speak anything else anyway.
    """

    engine = "piper"
    language = "KR"
    viseme_table = viseme.korean_ipa
    # espeak already ends every sentence with its own pause (the EOS symbol
    # carries ~180ms), so unlike MeloTTS there is nothing to pad.
    silence_seconds = 0.0

    def __init__(self) -> None:
        super().__init__()
        self.model_path = os.environ.get("PIPER_MODEL", default_model)
        self.config_path = os.environ.get(
            "PIPER_CONFIG", self.model_path.replace("-aligned.onnx", ".onnx") + ".json"
        )
        # Loading the model is expensive, so it is cached lazily on first use.
        self._voice = None

    @property
    def voice(self):
        if self._voice is None:
            from piper.voice import PiperVoice

            self._voice = PiperVoice.load(
                self.model_path,
                config_path=self.config_path,
                use_cuda=self.device == "cuda",
                include_alignments=True,
            )

        return self._voice

    @property
    def sample_rate(self) -> int:  # type: ignore[override]
        return int(self.voice.config.sample_rate)

    @property
    def synthesis_config(self):
        from piper.config import SynthesisConfig

        return SynthesisConfig(
            # length_scale < 1 is faster, matching melo's 1/speed convention.
            length_scale=1.0 / self.speed,
            # OFF deliberately. Piper's default rescales every sentence to full
            # range independently, which makes loudness jump between sentences
            # and detaches the RMS envelope - the value the face uses to drive
            # mouth opening - from what is actually audible. viseme.rms_envelope
            # already peak-normalises once, per utterance.
            normalize_audio=False,
        )

    def pieces(self, text: str) -> Iterable[speech.speech_piece]:
        config = self.synthesis_config
        texts = [piece for piece in sentence_pattern.split(text.strip()) if piece != ""]

        if len(texts) == 0:
            raise RuntimeError(f"sentence split produced no pieces for {text!r}")

        for piece in texts:
            blocks: list[numpy.ndarray] = []
            symbols: list[str] = []
            sample_counts: list[int] = []

            for chunk in self.voice.synthesize(
                piece, syn_config=config, include_alignments=True
            ):
                # PiperVoice returns None when its phoneme-id walk fails to line
                # up, and separately when the voice was never patched. Either
                # way there is no timeline, and a silently missing one is worse
                # than a failed request.
                if chunk.phoneme_alignments is None:
                    raise RuntimeError(
                        "piper returned no phoneme alignments - is the voice "
                        f"patched? ({self.model_path})"
                    )

                blocks.append(chunk.audio_float_array)
                symbols += [
                    alignment.phoneme for alignment in chunk.phoneme_alignments
                ]
                sample_counts += [
                    int(alignment.num_samples) for alignment in chunk.phoneme_alignments
                ]

            if len(blocks) == 0:
                continue

            yield speech.speech_piece(
                text=piece,
                samples=numpy.concatenate(blocks).astype(numpy.float32),
                symbols=symbols,
                sample_counts=sample_counts,
            )
