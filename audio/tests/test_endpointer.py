from app.endpointer import endpoint_limits, endpointer

limits = endpoint_limits(
    trailing_silence_ms=700,
    no_speech_timeout_ms=5000,
    max_utterance_ms=15000,
    min_speech_ms=300,
)


def feed(points: endpointer, pattern: str, frame_ms: int) -> str:
    for mark in pattern:
        points.feed(mark == "v", frame_ms)

    return points.verdict()


def test_speech_followed_by_trailing_silence_is_done() -> None:
    points = endpointer(limits)

    # 400ms voiced, then 700ms silence.
    assert feed(points, "vvvvv" + "........." , 80) == "done"


def test_silence_before_any_speech_does_not_close_the_utterance() -> None:
    points = endpointer(limits)

    assert feed(points, "." * 20, 80) == "recording"


def test_nothing_voiced_for_the_timeout_is_no_speech() -> None:
    points = endpointer(limits)

    assert feed(points, "." * 63, 80) == "no_speech"


def test_a_single_click_does_not_count_as_speech() -> None:
    points = endpointer(limits)

    # One voiced frame (80ms) then a long silence: the silence timer never
    # starts because speech was never established.
    assert feed(points, "v" + "." * 20, 80) == "recording"


def test_speech_resets_the_silence_timer() -> None:
    points = endpointer(limits)

    # 400ms speech, 480ms gap, speech again: a pause inside a sentence.
    assert feed(points, "vvvvv" + "......" + "vv", 80) == "recording"


def test_the_hard_cap_closes_a_long_utterance() -> None:
    points = endpointer(limits)

    assert feed(points, "v" * 188, 80) == "done"


def test_the_hard_cap_without_speech_is_too_long() -> None:
    points = endpointer(limits)

    # Only possible when no_speech_timeout is above max_utterance; checked anyway
    # so the ordering of the two caps is pinned.
    wide = endpointer(endpoint_limits(700, 99999, 1000, 300))

    assert feed(wide, "." * 13, 80) == "too_long"
    assert feed(points, "", 80) == "recording"


def test_the_voiced_span_is_tracked_and_padded() -> None:
    from app.endpointer import voiced_span_ms

    points = endpointer(limits)
    # 10 silent frames (800ms), 5 voiced (400ms), 9 silent.
    feed(points, "." * 10 + "v" * 5 + "." * 9, 80)

    assert points.first_voiced_ms == 800
    assert points.last_voiced_ms == 1200
    assert voiced_span_ms(points, 300) == (500, 1500)


def test_the_voiced_span_of_silence_is_everything() -> None:
    from app.endpointer import voiced_span_ms

    points = endpointer(limits)
    feed(points, "." * 5, 80)

    assert voiced_span_ms(points, 300) == (0, 400)
