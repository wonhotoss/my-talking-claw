from app.sse import parse_sse, sse_message


def test_frames_carry_their_id_and_parsed_data() -> None:
    lines = [
        "retry: 2000",
        "",
        ': ping',
        "",
        'data: {"kind":"stream_hello","protocol_version":1,"active_turn":null}',
        "",
        "id: 412",
        'data: {"kind":"utterance","turn_id":"t_7","seq":2,"text":"결론"}',
        "",
    ]

    assert list(parse_sse(lines)) == [
        sse_message(seq=0, event={"kind": "stream_hello", "protocol_version": 1, "active_turn": None}),
        sse_message(seq=412, event={"kind": "utterance", "turn_id": "t_7", "seq": 2, "text": "결론"}),
    ]


def test_multi_line_data_is_joined_with_newlines() -> None:
    lines = ["id: 1", 'data: {"kind":"notice",', 'data: "text":"a"}', ""]

    assert list(parse_sse(lines)) == [sse_message(seq=1, event={"kind": "notice", "text": "a"})]


def test_the_id_does_not_leak_into_the_next_frame() -> None:
    lines = ["id: 5", 'data: {"a":1}', "", 'data: {"b":2}', ""]

    assert [message.seq for message in parse_sse(lines)] == [5, 0]
