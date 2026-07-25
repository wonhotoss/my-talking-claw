from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.tts_engine import tts_engine


class synthesize_request(BaseModel):
    text: str


class health_response(BaseModel):
    status: str
    engine: str
    language: str


app = FastAPI(
    title="My Talking Claw TTS",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = tts_engine()


@app.get("/health", response_model=health_response)
def get_health() -> health_response:
    return health_response(status="ok", engine=engine.engine, language=engine.language)


@app.post("/synthesize")
def post_synthesize(request: synthesize_request) -> Response:
    text = request.text.strip()

    if text == "":
        raise HTTPException(status_code=400, detail="text must not be empty")

    try:
        audio = engine.synthesize(text)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"tts failure: {error}")

    return Response(content=audio, media_type="audio/wav")
