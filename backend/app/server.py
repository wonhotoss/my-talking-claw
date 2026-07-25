from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.agent import agent


class message_request(BaseModel):
    text: str


class message_response(BaseModel):
    text: str


class health_response(BaseModel):
    status: str


app = FastAPI(
    title="My Talking Claw Backend",
    version="0.1.0",
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


@app.post("/api/message", response_model=message_response)
async def post_message(request: message_request) -> message_response:
    text = request.text.strip()

    if text == "":
        raise HTTPException(status_code=400, detail="text must not be empty")

    try:
        reply = await agent.respond(text)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"agent gateway failure: {error}")

    return message_response(text=reply)
