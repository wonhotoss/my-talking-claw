import asyncio
import os
from collections.abc import AsyncIterator

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import events
from app.turns import bus, new_turn_id, runner


# How long the stream stays quiet before a keepalive.
heartbeat_seconds = 15.0

push_token = os.environ.get("PUSH_TOKEN", "")


class turn_request(BaseModel):
    text: str
    # Client-generated: a barge-in during the trigger round trip still needs an
    # id to cancel, and the client must be able to tell its own turn from an
    # unsolicited one.
    turn_id: str


class turn_accepted(BaseModel):
    turn_id: str


class cancel_response(BaseModel):
    cancelled: bool


class push_request(BaseModel):
    source: events.turn_source
    # Exactly one of these must be set. `utterances` is spoken verbatim with no
    # agent hop; `prompt` runs a full unsolicited agent turn.
    prompt: str | None
    utterances: list[str] | None


class health_response(BaseModel):
    status: str


app = FastAPI(
    title="My Talking Claw Backend",
    version="0.2.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=health_response)
def get_health() -> health_response:
    return health_response(status="ok")


def replay_for(last_event_id: str | None) -> list[tuple[int, events.stream_event]] | None:
    """Events to replay, or None when the client should start from a hello.

    A client with no Last-Event-ID gets no replay at all. Treating a fresh page
    load as "replay from 0" would make the device re-speak the previous turn on
    every reload.
    """
    if last_event_id is None:
        return None

    try:
        last_seq = int(last_event_id)
    except ValueError:
        return None

    return bus.since(last_seq)


async def event_stream(last_event_id: str | None) -> AsyncIterator[str]:
    # Subscribed before the replay window is read, so nothing published in
    # between is lost. That can duplicate an event across replay and live; the
    # client drops anything it has already seen by lastEventId.
    with bus.subscribe() as queue:
        replay = replay_for(last_event_id)

        yield events.sse_retry_frame(events.reconnect_delay_ms)

        if replay is None:
            # Covers what replay cannot: a server restart, or a Last-Event-ID
            # older than the window. This frame is the *sole* authority on turn
            # liveness for a (re)connecting client - active_turn tells it whether
            # to show 생각하는 중 or 대기 중, and its absence means "keep whatever
            # you had". That is why a mid-turn blip, which replays instead, must
            # not make the client forget the turn it is in the middle of.
            yield events.sse_connection_frame(
                events.stream_hello_event(
                    kind="stream_hello",
                    protocol_version=events.protocol_version,
                    active_turn=runner.active,
                )
            )
        else:
            for seq, event in replay:
                yield events.sse_frame(seq, event)

        while True:
            try:
                seq, event = await asyncio.wait_for(queue.get(), heartbeat_seconds)
            except asyncio.TimeoutError:
                yield events.sse_comment("ping")
                continue

            yield events.sse_frame(seq, event)


@app.get("/api/events")
async def get_events(last_event_id: str | None = Header(default=None)) -> StreamingResponse:
    return StreamingResponse(
        event_stream(last_event_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            # A no-op through the vite dev proxy; required the day this sits
            # behind nginx on the Pi.
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/api/turns", status_code=202, response_model=turn_accepted)
async def post_turn(request: turn_request) -> turn_accepted:
    text = request.text.strip()
    turn_id = request.turn_id.strip()

    if text == "":
        raise HTTPException(status_code=400, detail="text must not be empty")

    if turn_id == "":
        raise HTTPException(status_code=400, detail="turn_id must not be empty")

    # 202 as soon as the turn is registered; everything else arrives on the stream.
    await runner.start_prompt("user", text, turn_id)

    return turn_accepted(turn_id=turn_id)


@app.post("/api/turns/{turn_id}/cancel", status_code=202, response_model=cancel_response)
async def post_turn_cancel(turn_id: str) -> cancel_response:
    return cancel_response(cancelled=await runner.cancel(turn_id))


def require_push_token(x_push_token: str | None = Header(default=None)) -> None:
    if push_token == "":
        # Loud rather than silently accepting or silently rejecting.
        raise HTTPException(
            status_code=503,
            detail="PUSH_TOKEN is not configured, so /api/push is disabled",
        )

    if x_push_token != push_token:
        raise HTTPException(status_code=401, detail="invalid push token")


@app.post("/api/push", status_code=202, response_model=turn_accepted)
async def post_push(
    request: push_request,
    _: None = Depends(require_push_token),
) -> turn_accepted:
    """Make the device speak without any pending client request.

    Split from /api/turns because the auth boundary differs, not because the
    payload does: /api/turns is the local UI on the same origin, this lets the
    agent - or anything else on the LAN holding the token - start a turn.
    """
    prompt = "" if request.prompt is None else request.prompt.strip()
    utterances = [] if request.utterances is None else request.utterances

    if (prompt != "") == (utterances != []):
        raise HTTPException(
            status_code=400,
            detail="exactly one of prompt or utterances must be set",
        )

    if utterances != []:
        try:
            turn_id = await runner.start_utterances(request.source, utterances)
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error))

        return turn_accepted(turn_id=turn_id)

    return turn_accepted(
        turn_id=await runner.start_prompt(request.source, prompt, new_turn_id())
    )
