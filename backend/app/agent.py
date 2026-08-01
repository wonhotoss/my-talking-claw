import json
import os
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Literal

import httpx

from app.events import notice_kind


ndjson_media_type = "application/x-ndjson"

agent_chunk_type = Literal["reply", "notice", "done", "error"]


@dataclass(frozen=True)
class agent_chunk:
    """One piece of a turn as it comes back from the gateway.

    Structurally the same vocabulary the gateway emits, so there is one set of
    names across both hops. `done` is carried rather than implied: a stream that
    ends without it was truncated, and the caller must be able to tell.
    """

    type: agent_chunk_type
    text: str
    notice_kind: notice_kind | None


def decode_notice_kind(raw: str | None) -> notice_kind:
    # Narrowed here because this is the trust boundary for the gateway's wire
    # format - the layer that publishes events should have nothing left to
    # decide. The label is cosmetic and `text` carries the information, so an
    # unrecognised one degrades rather than failing the whole turn; an unknown
    # line *type* still raises below, because that would lose content.
    return "tool_use" if raw == "tool_use" else "progress"


def decode_line(line: str) -> agent_chunk:
    payload = json.loads(line)
    line_type = payload["type"]

    if line_type == "notice":
        return agent_chunk(
            type="notice",
            text=payload["text"],
            notice_kind=decode_notice_kind(payload["kind"]),
        )

    if line_type in ("reply", "done", "error"):
        return agent_chunk(type=line_type, text=payload["text"], notice_kind=None)

    raise RuntimeError(f"unknown gateway line type {line_type!r}")


class agent_client:
    """Client for the nullclaw-compatible agent gateway.

    Pairs once to obtain a bearer token, then posts user messages to /webhook.
    The same contract points at the stand-in gateway during development and at a
    real nullclaw gateway in production - only the URL changes. Config via
    environment:

    - AGENT_GATEWAY_URL   (default http://127.0.0.1:3000 - nullclaw's default)
    - AGENT_PAIRING_CODE  (default 000000; nullclaw's one-time startup code)
    - AGENT_BEARER_TOKEN  (optional; skip pairing when a token is provided)

    Streaming is requested with `Accept: application/x-ndjson` and detected from
    the *response* Content-Type. There is deliberately no capability probe and no
    cached capability flag: a gateway that has not implemented the extension
    answers with the old single-reply body and everything downstream is
    identical, because sentence splitting happens above this layer.
    """

    def __init__(self) -> None:
        self.gateway_url = os.environ.get("AGENT_GATEWAY_URL", "http://127.0.0.1:3000").rstrip("/")
        self.pairing_code = os.environ.get("AGENT_PAIRING_CODE", "000000")
        self._token = os.environ.get("AGENT_BEARER_TOKEN", "") or None

    async def _pair(self, client: httpx.AsyncClient) -> str:
        response = await client.post(
            f"{self.gateway_url}/pair",
            headers={"X-Pairing-Code": self.pairing_code},
        )
        response.raise_for_status()

        return response.json()["token"]

    async def _decode(self, response: httpx.Response) -> AsyncIterator[agent_chunk]:
        if ndjson_media_type in response.headers.get("content-type", ""):
            async for line in response.aiter_lines():
                if line.strip() != "":
                    yield decode_line(line)

            return

        # Non-streaming gateway: the whole turn is one reply. Synthesising the
        # `done` keeps both paths identical for the caller.
        body = json.loads(await response.aread())

        yield agent_chunk(type="reply", text=body["reply"], notice_kind=None)
        yield agent_chunk(type="done", text="", notice_kind=None)

    async def stream(self, message: str) -> AsyncIterator[agent_chunk]:
        # For a streaming response httpx applies the read timeout per chunk, so
        # this now usefully means "no line for 120s" rather than a total cap.
        async with httpx.AsyncClient(timeout=120.0) as client:
            if self._token is None:
                self._token = await self._pair(client)

            for attempt in (1, 2):
                async with client.stream(
                    "POST",
                    f"{self.gateway_url}/webhook",
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Accept": ndjson_media_type,
                    },
                    json={"message": message},
                ) as response:
                    # Checked before the body is touched, so the retry loses nothing.
                    if response.status_code == 401 and attempt == 1:
                        self._token = await self._pair(client)
                        continue

                    if response.status_code >= 400:
                        detail = (await response.aread()).decode(errors="replace")[:500]

                        raise RuntimeError(f"agent gateway {response.status_code}: {detail}")

                    async for chunk in self._decode(response):
                        yield chunk

                    return


agent = agent_client()
