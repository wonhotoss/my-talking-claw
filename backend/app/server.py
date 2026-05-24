from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel


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


def build_mock_agent_response(text: str) -> str:
    cleaned_text = text.strip()

    if cleaned_text == "":
        raise HTTPException(status_code=400, detail="text must not be empty")

    return f"임시 에이전트 응답입니다. 입력한 내용은 '{cleaned_text}'입니다."


@app.get("/health", response_model=health_response)
def get_health() -> health_response:
    return health_response(status="ok")


@app.post("/api/message", response_model=message_response)
def post_message(request: message_request) -> message_response:
    return message_response(text=build_mock_agent_response(request.text))
