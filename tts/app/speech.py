"""The engine-independent half of /speak: contract types and timeline assembly.

An engine's job is narrow - turn text into sentence pieces, each carrying its
audio, its phoneme symbols and how many audio samples each symbol occupies.
Everything after that (offsets, inter-piece silence, subtitle segments, WAV
encoding, viseme merging, the RMS envelope) is identical for every engine and
lives here, so a second engine cannot quietly grow a second /speak contract.

Samples are the shared alignment unit. Piper reports them directly; MeloTTS
reports mel frames that convert by an exact integer factor. Doing that
conversion inside each engine keeps the drift check where the engine-specific
knowledge is.

Deliberately free of torch, melo and piper: importable (and unit-testable) on
any host, the same property app/viseme.py has.
"""

import io
import os
from collections.abc import Iterable
from dataclasses import dataclass

import numpy

from app import viseme


@dataclass(frozen=True)
class speech_segment:
    """One sentence piece and the exact audio range it occupies.

    Engines already split text into pieces and synthesize them one at a time, so
    the sample offsets are known for free. This is what lets the face show
    subtitles that are correct at sentence boundaries.
    """

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class speech_result:
    audio: bytes
    sample_rate: int
    duration: float
    visemes: list[viseme.viseme_span]
    segments: list[speech_segment]
    envelope: list[float]
    envelope_hz: int


@dataclass(frozen=True)
class speech_piece:
    """One synthesized sentence piece and its alignment.

    `sample_counts` must sum to exactly len(samples); build_speech checks it.
    `blank_indices` marks positions that are model padding rather than real
    symbols - MeloTTS interleaves them, Piper does not.
    """

    text: str
    samples: numpy.ndarray
    symbols: list[str]
    sample_counts: list[int]
    blank_indices: frozenset[int] = frozenset()


def encode_wav(samples: numpy.ndarray, sample_rate: int) -> bytes:
    """Mono 16-bit PCM WAV, in memory.

    soundfile rather than the stdlib `wave` module because it is what day-4
    shipped: its float->PCM_16 conversion is the one the current audio was
    verified against, and matching it keeps the MeloTTS output byte-identical
    across this refactor.
    """
    import soundfile

    buffer = io.BytesIO()
    soundfile.write(buffer, samples, sample_rate, format="WAV")

    return buffer.getvalue()


def build_speech(
    pieces: Iterable[speech_piece],
    sample_rate: int,
    table: viseme.symbol_table,
    silence_seconds: float,
    viseme_minimum_seconds: float,
    envelope_hz: int,
) -> speech_result:
    """Stitch synthesized pieces into one audio stream and its timeline."""
    silence_samples = int(sample_rate * silence_seconds)

    blocks: list[numpy.ndarray] = []
    spans: list[viseme.viseme_span] = []
    segments: list[speech_segment] = []
    offset_samples = 0

    for piece in pieces:
        piece_start_samples = offset_samples
        total = sum(piece.sample_counts)

        # A mismatch would ship a timeline that silently drifts against the
        # audio, which is worse than no lip sync. Crash instead.
        if total != len(piece.samples):
            raise RuntimeError(
                f"alignment covers {total} samples "
                f"but the piece is {len(piece.samples)}"
            )

        spans += viseme.build_spans(
            piece.symbols,
            piece.sample_counts,
            piece.blank_indices,
            sample_rate,
            offset_samples / sample_rate,
            table,
        )
        blocks.append(piece.samples)
        offset_samples += len(piece.samples)

        if silence_samples > 0:
            spans.append(
                viseme.viseme_span(
                    start=offset_samples / sample_rate,
                    end=(offset_samples + silence_samples) / sample_rate,
                    viseme=viseme.viseme_silence,
                )
            )
            blocks.append(numpy.zeros(silence_samples, dtype=numpy.float32))
            offset_samples += silence_samples

        # The trailing silence belongs to the segment, so segments tile the
        # whole timeline and a subtitle stays up through the pause after its
        # sentence instead of blinking out.
        segments.append(
            speech_segment(
                start=piece_start_samples / sample_rate,
                end=offset_samples / sample_rate,
                text=piece.text,
            )
        )

    if len(blocks) == 0:
        raise RuntimeError("synthesis produced no audio")

    audio = numpy.concatenate(blocks).astype(numpy.float32)
    duration = len(audio) / sample_rate

    return speech_result(
        audio=encode_wav(audio, sample_rate),
        sample_rate=sample_rate,
        duration=duration,
        visemes=viseme.merge_spans(spans, viseme_minimum_seconds, duration),
        segments=segments,
        envelope=viseme.rms_envelope(audio, sample_rate, envelope_hz),
        envelope_hz=envelope_hz,
    )


class speech_engine:
    """Base for the concrete engines; owns everything that is not the model.

    Subclasses provide `engine`, `language`, `sample_rate`, `viseme_table`,
    `silence_seconds` and `pieces()`. They import their model lazily so this
    module tree loads on a host where neither engine is installed.

    Shared environment:

    - TTS_DEVICE             (default cpu; cuda on a GPU machine)
    - TTS_SPEED              (default 1.0)
    - TTS_VISEME_MIN_SECONDS (default 0.04; viseme spans shorter than this are
                              absorbed into a neighbour to stop visual flicker)
    - TTS_ENVELOPE_HZ        (default 50; RMS loudness samples per second)
    """

    engine = "unknown"
    language = "KR"
    sample_rate = 0
    viseme_table = viseme.korean_jamo
    # Padding appended after every piece. MeloTTS's own concat does this, so the
    # value is copied from upstream there; Piper's espeak already ends a
    # sentence with its own pause, so it uses 0.
    silence_seconds = 0.0

    def __init__(self) -> None:
        self.device = os.environ.get("TTS_DEVICE", "cpu")
        self.speed = float(os.environ.get("TTS_SPEED", "1.0"))
        self.viseme_minimum_seconds = float(
            os.environ.get("TTS_VISEME_MIN_SECONDS", "0.04")
        )
        self.envelope_hz = int(os.environ.get("TTS_ENVELOPE_HZ", "50"))

    def pieces(self, text: str) -> Iterable[speech_piece]:
        raise NotImplementedError

    def speak(self, text: str) -> speech_result:
        return build_speech(
            self.pieces(text),
            self.sample_rate,
            self.viseme_table,
            self.silence_seconds,
            self.viseme_minimum_seconds,
            self.envelope_hz,
        )

    def synthesize(self, text: str) -> bytes:
        return self.speak(text).audio
