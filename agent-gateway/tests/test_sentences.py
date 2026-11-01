from app.sentences import sentence_splitter


def feed_all(pieces: list[str]) -> tuple[list[str], str | None]:
    splitter = sentence_splitter()
    out: list[str] = []

    for piece in pieces:
        out += splitter.feed(piece)

    return out, splitter.flush()


def test_a_sentence_is_emitted_as_soon_as_its_terminator_and_a_space_arrive() -> None:
    assert feed_all(["지금은 오후 ", "다섯 시", "예요. 바깥", "은 춥고요."]) == (
        ["지금은 오후 다섯 시예요."],
        "바깥은 춥고요.",
    )


def test_a_terminator_at_the_end_of_the_buffer_waits_for_more_text() -> None:
    splitter = sentence_splitter()

    assert splitter.feed("정말요?") == []
    assert splitter.feed("! 네.") == ["정말요?!"]
    assert splitter.flush() == "네."


def test_decimals_and_versions_are_not_boundaries() -> None:
    assert feed_all(["기온은 3.5도예요. 버전은 v2.0 입니다."]) == (
        ["기온은 3.5도예요."],
        "버전은 v2.0 입니다.",
    )


def test_newlines_count_as_whitespace_and_empty_pieces_are_dropped() -> None:
    assert feed_all(["첫째.\n\n둘째!\n", "셋째"]) == (["첫째.", "둘째!"], "셋째")


def test_flush_on_empty_is_none() -> None:
    assert sentence_splitter().flush() is None
