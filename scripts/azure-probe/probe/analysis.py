"""Pure analysis over what the probes record. No SDK, no network.

Two of these are prototypes of logic the real adapters will need, kept here
first so the measurements and the unit tests pin them down before anything
touches tts/ or audio/:

- `spans_from_visemes`: Azure gives each viseme a start only; the end is the
  next viseme's start and the last one runs to the end of the audio. This is
  the `sample_counts` the tts engine contract wants (tts/app/speech.py).
- `gather_transcript`: continuous recognition yields several `recognized`
  pieces for one utterance (Azure segments on its own pauses). The final text
  is their concatenation, with the last partial as fallback when stopping cut
  the tail before it was finalised.
"""

from collections import Counter
from dataclasses import dataclass


# Azure's viseme inventory (0..21), as documented for en-US. The ko-KR voices
# reuse the same IDs; which ID each Korean vowel lands on is what the tts probe
# measures. Recorded here so the histogram can be read without the docs open.
viseme_names = {
    0: "silence",
    1: "æ ə ʌ",
    2: "ɑ",
    3: "ɔ",
    4: "ɛ ʊ",
    5: "ɝ",
    6: "j i ɪ",
    7: "w u",
    8: "o",
    9: "aʊ",
    10: "ɔɪ",
    11: "aɪ",
    12: "h",
    13: "ɹ",
    14: "l",
    15: "s z",
    16: "ʃ tʃ dʒ ʒ",
    17: "ð",
    18: "f v",
    19: "d t n θ",
    20: "k g ŋ",
    21: "p b m",
}

ticks_per_millisecond = 10_000


@dataclass(frozen=True)
class viseme_event:
    viseme_id: int
    offset_ms: float


@dataclass(frozen=True)
class viseme_span:
    viseme_id: int
    start_ms: float
    end_ms: float


def ticks_to_ms(ticks: int) -> float:
    return ticks / ticks_per_millisecond


def spans_from_visemes(events: list[viseme_event], duration_ms: float) -> list[viseme_span]:
    """Contiguous spans tiling [0, duration_ms].

    A leading gap before the first event becomes a silence span so the tiling
    starts at zero, which is what the engine contract's alignment check wants.
    Events are sorted because the SDK delivers them on a callback thread.
    """
    ordered = sorted(events, key=lambda event: event.offset_ms)

    if len(ordered) == 0:
        return [viseme_span(viseme_id=0, start_ms=0.0, end_ms=duration_ms)]

    leading = (
        [viseme_span(viseme_id=0, start_ms=0.0, end_ms=ordered[0].offset_ms)]
        if ordered[0].offset_ms > 0.0
        else []
    )
    ends = [event.offset_ms for event in ordered[1:]] + [duration_ms]

    return leading + [
        viseme_span(viseme_id=event.viseme_id, start_ms=event.offset_ms, end_ms=end)
        for event, end in zip(ordered, ends)
    ]


def viseme_histogram(events: list[viseme_event]) -> Counter[int]:
    return Counter(event.viseme_id for event in events)


def viseme_time_share(spans: list[viseme_span]) -> dict[int, float]:
    """Fraction of the audio each viseme ID holds. Time, not count: a vowel
    that fires once but holds for 200 ms is the one the lips show."""
    total = sum(span.end_ms - span.start_ms for span in spans)
    held: Counter[int] = Counter()

    for span in spans:
        held[span.viseme_id] += span.end_ms - span.start_ms

    return {viseme_id: held[viseme_id] / total for viseme_id in sorted(held)} if total > 0 else {}


def format_time_share(share: dict[int, float]) -> str:
    return "  ".join(
        f"{viseme_id}({viseme_names[viseme_id]}) {fraction:.0%}"
        for viseme_id, fraction in sorted(share.items(), key=lambda item: -item[1])
        if fraction >= 0.05
    )


@dataclass(frozen=True)
class partial:
    at_seconds: float
    text: str


def gather_transcript(recognized: list[str], partials: list[partial]) -> str:
    """Final text from one streaming session.

    `recognized` pieces are final; joined with a space because Azure already
    punctuates each piece. When stopping left nothing finalised, the last
    partial is the best text we have.
    """
    final = " ".join(piece for piece in recognized if piece != "")

    if final != "":
        return final

    return partials[-1].text if partials else ""
