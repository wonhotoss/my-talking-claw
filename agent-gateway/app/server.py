import os
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.claude_agent import claude_agent


class webhook_request(BaseModel):
    message: str


class webhook_response(BaseModel):
    reply: str


class pair_response(BaseModel):
    token: str


class health_response(BaseModel):
    status: str
    model: str


app = FastAPI(
    title="My Talking Claw Agent Gateway",
    version="0.1.0",
)

agent = claude_agent()
issued_tokens: set[str] = set()
pairing_code = os.environ.get("AGENT_PAIRING_CODE", "000000")


@app.get("/health", response_model=health_response)
def get_health() -> health_response:
    return health_response(status="ok", model=agent.model)


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


@app.post("/webhook", response_model=webhook_response)
async def post_webhook(
    request: webhook_request,
    _: None = Depends(require_token),
) -> webhook_response:
    message = request.message.strip()

    if message == "":
        raise HTTPException(status_code=400, detail="message must not be empty")

    try:
        reply = await agent.respond(message)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"agent failure: {error}")

    return webhook_response(reply=reply)
