import asyncio
import contextlib
import dataclasses
import json
import os
import secrets
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Response
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

from app import home_history
from app.claude_agent import agent_line, claude_agent
from app.scheduler import scheduler


# Proposed extension to nullclaw's webhook contract: a client that sends
# `Accept: application/x-ndjson` may get a chunked body of one JSON object per
# line instead of a single reply. A gateway that does not implement it ignores
# the header and answers exactly as before, and the client branches on the
# response Content-Type - so there is no negotiation, no version field and no
# configuration. Sentence segmentation is deliberately not part of the contract;
# a non-streaming gateway loses only the timing of the first sentence.
ndjson_media_type = "application/x-ndjson"


class webhook_request(BaseModel):
    message: str


class webhook_response(BaseModel):
    reply: str


class pair_response(BaseModel):
    token: str


class health_response(BaseModel):
    status: str
    model: str


# Which brain hosts the session. "cli" spawns `claude -p` per turn (day-7);
# "sdk" keeps one Agent SDK session resident in a BRAIN_HOME (day-10).
brain_kind = os.environ.get("AGENT_BRAIN", "cli")

# How often the brain's schedule/ directory is checked, in seconds.
schedule_poll_seconds = float(os.environ.get("SCHEDULE_POLL_SECONDS", "5"))


def make_agent():
    if brain_kind == "sdk":
        from app.sdk_agent import sdk_agent

        return sdk_agent()

    if brain_kind == "cli":
        return claude_agent()

    raise ValueError(f"AGENT_BRAIN must be sdk or cli, got {brain_kind!r}")


@contextlib.asynccontextmanager
async def lifespan(_: FastAPI):
    # The scheduler only makes sense with a home to read and a device to push to.
    push_url = os.environ.get("DEVICE_PUSH_URL", "")
    push_token = os.environ.get("DEVICE_PUSH_TOKEN", "")

    if brain_kind == "sdk" and push_url != "" and push_token != "":
        home = Path(os.environ["BRAIN_HOME"]).resolve()

        async with httpx.AsyncClient() as http:
            task = asyncio.create_task(
                scheduler(
                    home,
                    push_url,
                    push_token,
                    http,
                    lambda summary: home_history.record_quietly(home, summary, "scheduler"),
                ).run(schedule_poll_seconds)
            )

            try:
                yield
            finally:
                task.cancel()
    else:
        yield


app = FastAPI(
    title="My Talking Claw Agent Gateway",
    version="0.3.0",
    lifespan=lifespan,
)

agent = make_agent()
issued_tokens: set[str] = set()
pairing_code = os.environ.get("AGENT_PAIRING_CODE", "000000")


@app.get("/health", response_model=health_response)
def get_health() -> health_response:
    return health_response(status="ok", model=agent.model or "settings.json")


@app.post("/pair", response_model=pair_response)
def post_pair(x_pairing_code: str | None = Header(default=None)) -> pair_response:
    if x_pairing_code != pairing_code:
        raise HTTPException(status_code=401, detail="invalid pairing code")

    token = secrets.token_urlsafe(24)
    issued_tokens.add(token)
    return pair_response(token=token)


def require_token(authorization: str | None = Header(default=None)) -> None:
    prefix = "Bearer "

    if authorization is None or not authorization.startswith(prefix):
        raise HTTPException(status_code=401, detail="missing bearer token")

    if authorization[len(prefix):] not in issued_tokens:
        raise HTTPException(status_code=401, detail="invalid bearer token")


def encode_line(line: agent_line) -> bytes:
    # From the dataclass rather than a hand-written dict: the dataclass *is* the
    # declaration of the wire shape, so a field added there cannot be silently
    # dropped on the way out.
    return (json.dumps(dataclasses.asdict(line), ensure_ascii=False) + "\n").encode()


async def stream_lines(message: str) -> AsyncIterator[bytes]:
    try:
        async for line in agent.stream(message):
            yield encode_line(line)
    except Exception as error:
        # The 200 is already on the wire, so a failure has to arrive as a line.
        # CancelledError is a BaseException and deliberately not caught here: a
        # disconnected client must propagate so claude_agent kills the subprocess.
        #
        # The class name is included because the messages that matter most are
        # empty: `NotImplementedError()` - which is what create_subprocess_exec
        # raises on a Windows SelectorEventLoop - reads as "agent failure: " and
        # says nothing at all.
        yield encode_line(
            agent_line(
                type="error",
                text=f"agent failure: {type(error).__name__}: {error}",
                kind=None,
            )
        )


# No response_model: this endpoint has two body shapes.
@app.post("/webhook")
async def post_webhook(
    request: webhook_request,
    accept: str | None = Header(default=None),
    _: None = Depends(require_token),
) -> Response:
    message = request.message.strip()

    if message == "":
        raise HTTPException(status_code=400, detail="message must not be empty")

    if accept is not None and ndjson_media_type in accept:
        return StreamingResponse(stream_lines(message), media_type=ndjson_media_type)

    try:
        reply = await agent.respond(message)
    except Exception as error:
        raise HTTPException(
            status_code=502, detail=f"agent failure: {type(error).__name__}: {error}"
        )

    return JSONResponse(content=webhook_response(reply=reply).model_dump())
