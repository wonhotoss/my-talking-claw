"""The engine-independent half: whatever an engine yields, the timeline holds.

These run without either engine installed - that is the point of the seam.
"""

import io
import wave

import numpy
import pytest

from app import speech, viseme


def tone(samples: int) -> numpy.ndarray:
    times = numpy.arange(samples, dtype=numpy.float32) / 100.0
    return (0.5 * numpy.sin(2 * numpy.pi * 5.0 * times)).astype(numpy.float32)


def piece(text: str, symbols: list[str], counts: list[int]) -> speech.speech_piece:
    return speech.speech_piece(
        text=text, samples=tone(sum(counts)), symbols=symbols, sample_counts=counts
    )


def build(pieces: list[speech.speech_piece], silence: float = 0.0):
    return speech.build_speech(
        pieces,
        sample_rate=100,
        table=viseme.korean_jamo,
        silence_seconds=silence,
        viseme_minimum_seconds=0.04,
        envelope_hz=50,
    )


def test_timeline_tiles_the_audio() -> None:
    result = build(
        [
            piece("가", ["ᄀ", "ᅡ"], [10, 30]),
            piece("몸", ["ᄆ", "ᅩ", "ᆷ"], [20, 30, 20]),
        ]
    )

    assert result.duration == pytest.approx(1.1)
    assert result.visemes[0].start == 0.0
    assert result.visemes[-1].end == result.duration
    assert all(
        result.visemes[index].end == result.visemes[index + 1].start
        for index in range(len(result.visemes) - 1)
    )


def test_segments_tile_the_timeline_and_keep_their_text() -> None:
    result = build(
        [piece("가", ["ᅡ"], [40]), piece("나", ["ᅡ"], [60])],
        silence=0.1,
    )

    assert [segment.text for segment in result.segments] == ["가", "나"]
    assert result.segments[0].start == 0.0
    assert result.segments[0].end == result.segments[1].start
    assert result.segments[-1].end == pytest.approx(result.duration)


def test_silence_padding_is_added_between_pieces() -> None:
    padded = build([piece("가", ["ᅡ"], [40])], silence=0.1)
    bare = build([piece("가", ["ᅡ"], [40])], silence=0.0)

    # 40 samples at 100Hz is 0.4s; the padding adds exactly 0.1s more.
    assert bare.duration == pytest.approx(0.4)
    assert padded.duration == pytest.approx(0.5)
    assert padded.visemes[-1].viseme == viseme.viseme_silence


def test_alignment_that_misses_the_audio_is_fatal() -> None:
    # An engine claiming 30 samples of phonemes over 40 samples of audio would
    # ship a timeline that silently drifts. Crash instead.
    broken = speech.speech_piece(
        text="가", samples=tone(40), symbols=["ᅡ"], sample_counts=[30]
    )

    with pytest.raises(RuntimeError):
        build([broken])


def test_no_pieces_is_fatal() -> None:
    with pytest.raises(RuntimeError):
        build([])


def test_blank_indices_are_plumbed_through() -> None:
    # MeloTTS interleaves blanks; Piper does not. Blank id 0 decodes to "_",
    # which is indistinguishable from a real pad token, so position is the only
    # thing that separates them - and it has to survive the trip through here.
    def resolved(blanks: frozenset[int]) -> list[str]:
        result = speech.build_speech(
            [
                speech.speech_piece(
                    text="하",
                    samples=tone(50),
                    symbols=["_", "ᄒ", "_", "ᅡ", "_"],
                    sample_counts=[10, 10, 10, 10, 10],
                    blank_indices=blanks,
                )
            ],
            sample_rate=100,
            table=viseme.korean_jamo,
            silence_seconds=0.0,
            viseme_minimum_seconds=0.0,
            envelope_hz=50,
        )

        return [span.viseme for span in result.visemes]

    # Marked as blanks they inherit their neighbours and collapse into the
    # vowel; left unmarked the same "_" tokens are pads and rest the mouth.
    assert resolved(frozenset({0, 2, 4})) == ["a"]
    assert resolved(frozenset()) == ["x", "a", "x", "a", "x"]


def test_wav_is_mono_16bit_at_the_engine_rate() -> None:
    result = build([piece("가", ["ᅡ"], [40])])

    with wave.open(io.BytesIO(result.audio)) as handle:
        assert handle.getnchannels() == 1
        assert handle.getsampwidth() == 2
        assert handle.getframerate() == 100
        assert handle.getnframes() == 40


def test_envelope_covers_the_whole_utterance() -> None:
    result = build([piece("가", ["ᅡ"], [100])])

    assert result.envelope_hz == 50
    assert len(result.envelope) == 50
    assert max(result.envelope) == 1.0
