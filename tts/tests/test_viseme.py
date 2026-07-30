import numpy
import pytest

from app import viseme


# Every symbol MeloTTS's Korean g2p can emit: 19 choseong, 21 jungseong, the 7
# neutralised jongseong, the stray compatibility jamo the model's symbol table
# carries, and the pad/pause/punctuation tokens. resolve_visemes deliberately
# does not raise on unknown symbols, so this list is where the "cover the whole
# inventory" guarantee actually lives.
korean_inventory = (
    list("ᄀᄁᄂᄃᄄᄅᄆᄇᄈᄉᄊᄋᄌᄍᄎᄏᄐᄑᄒ")
    + list("ᅡᅢᅣᅤᅥᅦᅧᅨᅩᅪᅫᅬᅭᅮᅯᅰᅱᅲᅳᅴᅵ")
    + list("ᆨᆫᆮᆯᆷᆸᆼ")
    + ["ㄸ", "_", "SP", "UNK", "!", "?", "…", ",", ".", "'", "-", "~"]
)


def resolve(symbols: list[str]) -> list[str]:
    return viseme.resolve_visemes(symbols, frozenset())


def test_korean_inventory_is_fully_covered() -> None:
    tables = (
        set(viseme.vowel_visemes)
        | viseme.closed_symbols
        | viseme.silence_symbols
        | viseme.transparent_symbols
    )

    assert set(korean_inventory) <= tables


@pytest.mark.parametrize(
    "jamo,expected",
    [
        ("ᅡ", "a"),
        ("ᅥ", "a"),
        ("ᅦ", "e"),
        ("ᅢ", "e"),
        ("ᅵ", "i"),
        ("ᅳ", "i"),
        ("ᅩ", "o"),
        ("ᅮ", "u"),
        ("ᅲ", "u"),
        ("ᅱ", "i"),
        ("ᅬ", "e"),
        ("ᅪ", "a"),
        ("ᅴ", "i"),
    ],
)
def test_vowels_map_by_lip_shape(jamo: str, expected: str) -> None:
    assert resolve([jamo]) == [expected]


def test_bilabials_close_the_mouth() -> None:
    # 감 -> ᄀ ᅡ ᆷ
    assert resolve(["ᄀ", "ᅡ", "ᆷ"]) == ["a", "a", "m"]


def test_consonants_take_the_nearest_vowel() -> None:
    # 한글 -> ᄒ ᅡ ᆫ ᄀ ᅳ ᆯ. The coda ᆫ must keep its own syllable's vowel
    # rather than jumping ahead to the next one.
    assert resolve(["ᄒ", "ᅡ", "ᆫ", "ᄀ", "ᅳ", "ᆯ"]) == ["a", "a", "a", "i", "i", "i"]


def test_equidistant_consonants_look_forward() -> None:
    assert resolve(["ᅡ", "ᄂ", "ᅵ"]) == ["a", "i", "i"]


def test_null_onset_is_transparent() -> None:
    assert resolve(["ᄋ", "ᅩ"]) == ["o", "o"]


def test_lengthener_does_not_close_the_mouth() -> None:
    # 안녕~
    assert resolve(["ᅧ", "ᆼ", "~"]) == ["a", "a", "a"]


def test_unmapped_symbol_is_transparent_not_fatal() -> None:
    assert resolve(["zh", "ᅡ"]) == ["a", "a"]


def test_no_vowel_falls_back_to_neutral() -> None:
    assert resolve(["ᄂ", "ᄉ"]) == ["n", "n"]


def test_blanks_inherit_their_neighbours() -> None:
    # 하, fully interspersed the way add_blank does it: blanks at even indices.
    # Blank id 0 decodes to "_", so they can only be told apart by position.
    symbols = ["_", "_", "_", "ᄒ", "_", "ᅡ", "_", "_", "_"]
    blanks = frozenset(range(0, len(symbols), 2))

    assert viseme.resolve_visemes(symbols, blanks) == [
        "x",
        "x",
        "a",
        "a",
        "a",
        "a",
        "x",
        "x",
        "x",
    ]


def test_build_spans_is_contiguous_and_frame_aligned() -> None:
    spans = viseme.build_spans(["ᄀ", "ᅡ", "ᆷ"], [2, 5, 3], frozenset(), 0.01, 1.0)

    assert [span.viseme for span in spans] == ["a", "a", "m"]
    assert spans[0].start == 1.0
    assert spans[0].end == pytest.approx(1.02)
    assert spans[1].start == spans[0].end
    assert spans[-1].end == pytest.approx(1.1)


def test_build_spans_rejects_a_length_mismatch() -> None:
    with pytest.raises(ValueError):
        viseme.build_spans(["ᅡ"], [1, 1], frozenset(), 0.01, 0.0)


def test_merge_collapses_runs_and_absorbs_flicker() -> None:
    spans = [
        viseme.viseme_span(start=0.00, end=0.05, viseme="x"),
        viseme.viseme_span(start=0.05, end=0.10, viseme="a"),
        viseme.viseme_span(start=0.10, end=0.11, viseme="m"),  # 10ms flicker
        viseme.viseme_span(start=0.11, end=0.20, viseme="a"),
    ]

    merged = viseme.merge_spans(spans, 0.04, 0.20)

    assert [span.viseme for span in merged] == ["x", "a"]
    assert merged[0].start == 0.0
    assert merged[-1].end == 0.20
    assert all(
        merged[index].end == merged[index + 1].start for index in range(len(merged) - 1)
    )


def test_merge_snaps_sub_ulp_joins() -> None:
    # The piece-to-piece join is computed from a sample count while the spans
    # inside a piece come from a frame count, so the two can disagree in the
    # last ulp. The client compares against `end`, so the join must be exact.
    spans = [
        viseme.viseme_span(start=0.0, end=0.1, viseme="a"),
        viseme.viseme_span(start=0.1 + 1e-16, end=0.2, viseme="x"),
    ]

    merged = viseme.merge_spans(spans, 0.04, 0.2)

    assert merged[0].end == merged[1].start


def test_merge_keeps_a_single_short_span() -> None:
    spans = [viseme.viseme_span(start=0.0, end=0.005, viseme="a")]

    assert viseme.merge_spans(spans, 0.04, 0.005) == spans


def test_merge_rejects_a_timeline_that_misses_the_audio() -> None:
    spans = [viseme.viseme_span(start=0.0, end=1.0, viseme="a")]

    with pytest.raises(ValueError):
        viseme.merge_spans(spans, 0.04, 2.0)


def test_rms_envelope_is_peak_normalised() -> None:
    times = numpy.arange(44100, dtype=numpy.float32) / 44100.0
    samples = (0.3 * numpy.sin(2 * numpy.pi * 220.0 * times)).astype(numpy.float32)

    envelope = viseme.rms_envelope(samples, 44100, 50)

    assert len(envelope) == 50
    assert max(envelope) == 1.0
    assert all(0.0 <= value <= 1.0 for value in envelope)


def test_rms_envelope_of_silence_is_all_zero() -> None:
    envelope = viseme.rms_envelope(numpy.zeros(4410, dtype=numpy.float32), 44100, 50)

    assert envelope == [0.0] * 5
