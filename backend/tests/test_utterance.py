from app.utterance import split_utterances


def test_splits_on_sentence_final_punctuation_keeping_it() -> None:
    text = "조사해 보겠습니다. 사실이 발견되었습니다! 결론은 이렇습니다?"

    assert split_utterances(text) == [
        "조사해 보겠습니다.",
        "사실이 발견되었습니다!",
        "결론은 이렇습니다?",
    ]


def test_text_without_sentence_final_punctuation_stays_whole() -> None:
    # Cutting on a guess would be worse than one long utterance.
    assert split_utterances("일곱 시예요 일어날 시간입니다") == ["일곱 시예요 일어날 시간입니다"]


def test_ellipsis_is_a_boundary() -> None:
    assert split_utterances("잠깐만요… 찾았습니다.") == ["잠깐만요…", "찾았습니다."]


def test_blank_and_whitespace_only_input_yields_nothing() -> None:
    # The caller turns this into a failed turn rather than a silent one.
    assert split_utterances("") == []
    assert split_utterances("   \n  ") == []


def test_runs_of_whitespace_between_sentences_do_not_make_empty_pieces() -> None:
    assert split_utterances("첫째.\n\n   둘째.") == ["첫째.", "둘째."]


def test_trailing_punctuation_does_not_make_a_trailing_empty_piece() -> None:
    assert split_utterances("끝났습니다.") == ["끝났습니다."]
