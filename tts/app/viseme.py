"""Pure jamo -> viseme mapping and speech timeline shaping.

Deliberately free of torch and melo so it imports (and unit-tests) on any host,
including the Windows dev box. numpy is used only for the RMS envelope.

A viseme is a visible lip posture, not a phoneme: only aperture, rounding and
spread are observable. Tongue position is not, which is why several distinct
Korean vowels share one viseme.
"""

from dataclasses import dataclass
from itertools import accumulate

import numpy


viseme_silence = "x"
viseme_neutral = "n"
viseme_closed = "m"


# Korean jungseong, U+1161..U+1175. Diphthongs and y/w-glides take their
# nucleus: the onglide is short and the nucleus holds the shape for most of the
# token, so rendering the glide would leave the mouth in the wrong posture for
# the audible majority of the vowel.
vowel_visemes = {
    "ᅡ": "a",  # /a/   open, unrounded
    "ᅢ": "e",  # /ɛ/   mid, spread (merged with ᅦ in Seoul Korean)
    "ᅣ": "a",  # /ja/
    "ᅤ": "e",  # /jɛ/
    "ᅥ": "a",  # /ʌ/   mid-open, unrounded -> shares the open unrounded shape
    "ᅦ": "e",  # /e/   mid, spread
    "ᅧ": "a",  # /jʌ/
    "ᅨ": "e",  # /je/
    "ᅩ": "o",  # /o/   mid-close, rounded
    "ᅪ": "a",  # /wa/
    "ᅫ": "e",  # /wɛ/
    "ᅬ": "e",  # [we] in modern Seoul speech; conservative /ø/ would be "o"
    "ᅭ": "o",  # /jo/
    "ᅮ": "u",  # /u/   close, rounded and protruded
    "ᅯ": "a",  # /wʌ/
    "ᅰ": "e",  # /we/
    "ᅱ": "i",  # [wi] in modern Seoul speech; conservative /y/ would be "u"
    "ᅲ": "u",  # /ju/
    "ᅳ": "i",  # /ɯ/   close and UNROUNDED - the common Korean lip-sync trap.
    #                  Mapping it to "u" purses the lips on every 은/는/를,
    #                  which is most of the language.
    "ᅴ": "i",  # /ɰi/
    "ᅵ": "i",  # /i/   close, spread
}

# Lips shut regardless of the surrounding vowels: the four labial onsets, plus
# the two labial codas that survive MeloTTS's coda neutralisation (ᆸ already
# covers final ㅍ).
closed_symbols = frozenset({"ᄆ", "ᄇ", "ᄈ", "ᄑ", "ᆷ", "ᆸ"})

# Genuinely mouth-at-rest: pad/blank, the explicit pause token, and the
# punctuation and bracket literals that reach the model as raw text.
silence_symbols = frozenset(
    {
        "_",
        "SP",
        "!",
        "?",
        "…",
        ",",
        ".",
        "'",
        "-",
        "¿",
        "¡",
        "(",
        ")",
        "[",
        "]",
        "/",
        "\\",
        "^",
        ":",
        "*",
        '"',
    }
)

# No visible labial gesture of their own, so they borrow the nearest vowel's
# shape. Anything outside every table behaves identically (see resolve_visemes)
# - this set exists so the inventory-coverage test is meaningful and so the
# intent is recorded. "~" is a Korean chat lengthener and must not close the
# mouth mid-word on 안녕~.
transparent_symbols = frozenset(
    {
        "ᄀ",
        "ᄁ",
        "ᄂ",
        "ᄃ",
        "ᄄ",
        "ᄅ",
        "ᄉ",
        "ᄊ",
        "ᄋ",
        "ᄌ",
        "ᄍ",
        "ᄎ",
        "ᄏ",
        "ᄐ",
        "ᄒ",
        "ᆨ",
        "ᆫ",
        "ᆮ",
        "ᆯ",
        "ᆼ",
        "ㄸ",
        "~",
        "UNK",
    }
)


@dataclass(frozen=True)
class viseme_span:
    start: float
    end: float
    viseme: str


def nearest_source(flags: list[bool]) -> list[int | None]:
    """For each position, the index of the nearest True flag; ties go forward.

    Resolving ties forward models anticipatory co-articulation: the mouth
    reaches for the next vowel while the intervening consonant is still being
    released. Codas still take their own syllable's vowel, because that one is
    strictly nearer than the following syllable's.
    """
    count = len(flags)

    previous: list[int | None] = [None] * count
    latest: int | None = None

    for index in range(count):
        previous[index] = latest

        if flags[index]:
            latest = index

    following: list[int | None] = [None] * count
    earliest: int | None = None

    for index in reversed(range(count)):
        following[index] = earliest

        if flags[index]:
            earliest = index

    def pick(index: int) -> int | None:
        before = previous[index]
        after = following[index]

        if before is None:
            return after

        if after is None:
            return before

        return after if after - index <= index - before else before

    return [pick(index) for index in range(count)]


def resolve_visemes(symbols: list[str], blank_indices: frozenset[int]) -> list[str]:
    """Map model tokens to visemes.

    Two passes of the same nearest-neighbour rule:
      1. anything that is not a vowel, not bilabial and not silence takes the
         viseme of the nearest vowel (co-articulation);
      2. the interspersed blanks then take the nearest already-resolved token,
         so a blank never shows up as a spurious closed mouth.

    Blanks are identified by index, never by symbol: blank id 0 decodes to "_",
    which is indistinguishable from the real leading/trailing pad tokens.

    An unmapped symbol is treated as transparent rather than raising. The model
    carries a multilingual symbol union, and the timeline is a cosmetic overlay
    - failing a whole utterance over one unknown mouth shape would be the worse
    outcome. Coverage of the Korean inventory is pinned by a test instead.
    """
    is_vowel = [
        index not in blank_indices and symbol in vowel_visemes
        for index, symbol in enumerate(symbols)
    ]
    vowel_source = nearest_source(is_vowel)

    def resolve(index: int, symbol: str) -> str:
        if symbol in vowel_visemes:
            return vowel_visemes[symbol]

        if symbol in closed_symbols:
            return viseme_closed

        if symbol in silence_symbols:
            return viseme_silence

        source = vowel_source[index]

        return viseme_neutral if source is None else vowel_visemes[symbols[source]]

    resolved: list[str | None] = [
        None if index in blank_indices else resolve(index, symbol)
        for index, symbol in enumerate(symbols)
    ]
    blank_source = nearest_source([value is not None for value in resolved])

    def fill(index: int, value: str | None) -> str:
        if value is not None:
            return value

        source = blank_source[index]

        return viseme_neutral if source is None else str(resolved[source])

    return [fill(index, value) for index, value in enumerate(resolved)]


def build_spans(
    symbols: list[str],
    frame_counts: list[int],
    blank_indices: frozenset[int],
    seconds_per_frame: float,
    offset_seconds: float,
) -> list[viseme_span]:
    """One span per model token, contiguous, offset into the global timeline.

    Boundaries are derived from a cumulative *frame* count and multiplied once,
    so there is no float drift across a long utterance.
    """
    if len(symbols) != len(frame_counts):
        raise ValueError(
            f"symbol/frame count mismatch: {len(symbols)} vs {len(frame_counts)}"
        )

    visemes = resolve_visemes(symbols, blank_indices)
    boundaries = list(accumulate(frame_counts, initial=0))

    return [
        viseme_span(
            start=offset_seconds + boundaries[index] * seconds_per_frame,
            end=offset_seconds + boundaries[index + 1] * seconds_per_frame,
            viseme=visemes[index],
        )
        for index in range(len(symbols))
    ]


def snap_contiguous(spans: list[viseme_span]) -> list[viseme_span]:
    """Pull each span's start onto the previous span's end.

    Boundaries within one sentence piece are derived from a frame count while
    the piece-to-piece joins are derived from a sample count, so the two can
    disagree in the last ulp. The client walks the timeline by comparing
    against `end`, so make the contract exact rather than nearly exact.
    """
    return [
        span if index == 0 else viseme_span(
            start=spans[index - 1].end, end=span.end, viseme=span.viseme
        )
        for index, span in enumerate(spans)
    ]


def coalesce(spans: list[viseme_span]) -> list[viseme_span]:
    """Merge adjacent spans that share a viseme, keeping exact boundaries."""
    merged: list[viseme_span] = []

    for span in spans:
        if len(merged) > 0 and merged[-1].viseme == span.viseme:
            merged[-1] = viseme_span(
                start=merged[-1].start, end=span.end, viseme=span.viseme
            )
        else:
            merged.append(span)

    return merged


def absorb_shortest(spans: list[viseme_span]) -> list[viseme_span]:
    """Drop the shortest span, extending its longer neighbour over the gap.

    Donating to the *longer* neighbour biases toward preserving structure
    rather than smearing a whole phrase into one shape.
    """

    def duration_of(span: viseme_span) -> float:
        return span.end - span.start

    index = min(range(len(spans)), key=lambda position: duration_of(spans[position]))
    before = duration_of(spans[index - 1]) if index > 0 else -1.0
    after = duration_of(spans[index + 1]) if index + 1 < len(spans) else -1.0

    if before >= after:
        widened = viseme_span(
            start=spans[index - 1].start,
            end=spans[index].end,
            viseme=spans[index - 1].viseme,
        )
        rebuilt = spans[: index - 1] + [widened] + spans[index + 1 :]
    else:
        widened = viseme_span(
            start=spans[index].start,
            end=spans[index + 1].end,
            viseme=spans[index + 1].viseme,
        )
        rebuilt = spans[:index] + [widened] + spans[index + 2 :]

    return coalesce(rebuilt)


def merge_spans(
    spans: list[viseme_span],
    minimum_duration_seconds: float,
    total_duration_seconds: float,
) -> list[viseme_span]:
    """Collapse runs, absorb sub-threshold spans, snap the end to the audio.

    Contiguity is structural: spans are only ever merged by widening a
    neighbour over the removed interval, so span[i].end stays span[i + 1].start
    for the whole pipeline.
    """
    if len(spans) == 0:
        return []

    merged = coalesce(snap_contiguous(spans))

    while (
        len(merged) > 1
        and min(span.end - span.start for span in merged) < minimum_duration_seconds
    ):
        merged = absorb_shortest(merged)

    # A gap here means the caller's frame accounting is wrong, which would ship
    # a timeline that silently drifts against the audio. Crash instead.
    if abs(merged[-1].end - total_duration_seconds) > 1e-6:
        raise ValueError(
            f"viseme timeline ends at {merged[-1].end:.6f}s "
            f"but the audio is {total_duration_seconds:.6f}s"
        )

    merged[-1] = viseme_span(
        start=merged[-1].start,
        end=total_duration_seconds,
        viseme=merged[-1].viseme,
    )

    return merged


def rms_envelope(
    samples: numpy.ndarray, sample_rate: int, envelope_hz: int
) -> list[float]:
    """Peak-normalised 0..1 RMS loudness at envelope_hz, as plain floats.

    Peak-normalising per utterance means a quiet sentence still drives a
    full-range mouth. Absolute loudness is not comparable across responses,
    which nothing here needs.
    """
    window = max(1, round(sample_rate / envelope_hz))
    count = -(-len(samples) // window)

    padded = numpy.zeros(count * window, dtype=numpy.float64)
    padded[: len(samples)] = samples

    levels = numpy.sqrt(numpy.mean(numpy.square(padded.reshape(count, window)), axis=1))
    peak = float(levels.max())

    if peak <= 0.0:
        return [0.0] * count

    # 3 decimals is well past what a cartoon mouth can show and roughly thirds
    # the JSON size: ~6 bytes per sample instead of ~19.
    return [round(value, 3) for value in (levels / peak).tolist()]
