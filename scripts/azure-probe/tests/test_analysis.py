from collections import Counter

from probe.analysis import (
    gather_transcript,
    partial,
    spans_from_visemes,
    ticks_to_ms,
    viseme_event,
    viseme_histogram,
    viseme_span,
    viseme_time_share,
)


def test_ticks_are_100ns() -> None:
    assert ticks_to_ms(10_000) == 1.0
    assert ticks_to_ms(1_250_000) == 125.0


def test_spans_tile_the_audio_with_end_from_next_start() -> None:
    events = [viseme_event(0, 0.0), viseme_event(19, 50.0), viseme_event(1, 120.0)]

    assert spans_from_visemes(events, 300.0) == [
        viseme_span(0, 0.0, 50.0),
        viseme_span(19, 50.0, 120.0),
        viseme_span(1, 120.0, 300.0),
    ]


def test_spans_sort_callback_order_and_fill_a_leading_gap_with_silence() -> None:
    events = [viseme_event(1, 120.0), viseme_event(19, 50.0)]

    assert spans_from_visemes(events, 200.0) == [
        viseme_span(0, 0.0, 50.0),
        viseme_span(19, 50.0, 120.0),
        viseme_span(1, 120.0, 200.0),
    ]


def test_no_events_is_one_silent_span() -> None:
    assert spans_from_visemes([], 80.0) == [viseme_span(0, 0.0, 80.0)]


def test_histogram_and_time_share() -> None:
    events = [viseme_event(0, 0.0), viseme_event(1, 100.0), viseme_event(21, 300.0), viseme_event(1, 350.0)]

    assert viseme_histogram(events) == Counter({0: 1, 1: 2, 21: 1})

    share = viseme_time_share(spans_from_visemes(events, 400.0))

    assert share == {0: 0.25, 1: 0.625, 21: 0.125}


def test_transcript_joins_recognized_pieces() -> None:
    assert gather_transcript(["오늘 날씨", "", "어때?"], []) == "오늘 날씨 어때?"


def test_transcript_falls_back_to_last_partial() -> None:
    partials = [partial(0.5, "오늘"), partial(1.2, "오늘 날씨")]

    assert gather_transcript([], partials) == "오늘 날씨"
    assert gather_transcript([""], partials) == "오늘 날씨"
    assert gather_transcript([], []) == ""
