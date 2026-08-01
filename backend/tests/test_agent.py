import asyncio
import json
from collections.abc import AsyncIterator

import pytest

from app.agent import agent_chunk, agent_client, decode_line, ndjson_media_type


class fake_response:
    """Just enough of httpx.Response for agent_client._decode."""

    def __init__(self, content_type: str, lines: list[str], body: str) -> None:
        self.headers = {"content-type": content_type}
        self.lines = lines
        self.body = body

    async def aiter_lines(self) -> AsyncIterator[str]:
        for line in self.lines:
            yield line

    async def aread(self) -> bytes:
        return self.body.encode()


def decode(response: fake_response) -> list[agent_chunk]:
    async def scenario() -> list[agent_chunk]:
        return [chunk async for chunk in agent_client()._decode(response)]

    return asyncio.run(scenario())


def test_decode_line_maps_each_gateway_line_type() -> None:
    assert decode_line('{"type":"reply","text":"안녕","kind":null}') == agent_chunk(
        type="reply", text="안녕", notice_kind=None
    )
    assert decode_line('{"type":"notice","text":"Bash","kind":"tool_use"}') == agent_chunk(
        type="notice", text="Bash", notice_kind="tool_use"
    )
    assert decode_line('{"type":"done","text":"","kind":null}').type == "done"
    assert decode_line('{"type":"error","text":"터졌다","kind":null}').type == "error"


def test_decode_line_rejects_an_unknown_line_type() -> None:
    # An unknown line *type* would lose content, so it raises - unlike an unknown
    # notice label, which is cosmetic and degrades.
    with pytest.raises(RuntimeError):
        decode_line('{"type":"something_new","text":"","kind":null}')


def test_an_unknown_notice_label_degrades_at_the_trust_boundary() -> None:
    chunk = decode_line('{"type":"notice","text":"무언가","kind":"something_new"}')

    assert chunk.notice_kind == "progress"
    assert chunk.text == "무언가"
    assert decode_line('{"type":"notice","text":"x","kind":null}').notice_kind == "progress"


def test_ndjson_response_is_decoded_line_by_line() -> None:
    chunks = decode(
        fake_response(
            ndjson_media_type,
            [
                json.dumps({"type": "reply", "text": "조사해 보겠습니다.", "kind": None}),
                "",
                json.dumps({"type": "notice", "text": "WebSearch", "kind": "tool_use"}),
                json.dumps({"type": "reply", "text": "결론은 이렇습니다.", "kind": None}),
                json.dumps({"type": "done", "text": "", "kind": None}),
            ],
            "",
        )
    )

    assert [chunk.type for chunk in chunks] == ["reply", "notice", "reply", "done"]
    assert chunks[1].notice_kind == "tool_use"


def test_a_non_streaming_gateway_still_yields_a_reply_and_a_done() -> None:
    # A real nullclaw that never implements the extension answers with the old
    # single-reply body. The caller must not be able to tell the difference apart
    # from the timing of the first sentence.
    chunks = decode(
        fake_response("application/json", [], json.dumps({"reply": "통째로 온 답변입니다."}))
    )

    assert [chunk.type for chunk in chunks] == ["reply", "done"]
    assert chunks[0].text == "통째로 온 답변입니다."


def test_a_streaming_gateway_never_synthesises_an_extra_done() -> None:
    # The done comes from the gateway on this path; inventing a second one would
    # let a truncated stream read as a clean end.
    chunks = decode(
        fake_response(
            ndjson_media_type,
            [json.dumps({"type": "reply", "text": "잘렸다.", "kind": None})],
            "",
        )
    )

    assert [chunk.type for chunk in chunks] == ["reply"]
