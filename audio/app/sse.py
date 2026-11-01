"""Server-sent events parsing for the backend's /api/events stream.

Only what the backend emits: `id:` and `data:` lines, blank-line delimited,
comments for keepalive. Mirrors event_stream.ts.
"""

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass


@dataclass(frozen=True)
class sse_message:
    # 0 for connection-scoped frames (stream_hello carries no id).
    seq: int
    event: dict


def parse_sse(lines: Iterable[str]) -> Iterator[sse_message]:
    seq = 0
    data: list[str] = []

    for line in lines:
        line = line.rstrip("\r\n")

        if line == "":
            if data:
                yield sse_message(seq=seq, event=json.loads("\n".join(data)))

            seq = 0
            data = []
            continue

        if line.startswith(":"):
            continue

        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value

        if field == "id":
            seq = int(value)
        elif field == "data":
            data.append(value)
