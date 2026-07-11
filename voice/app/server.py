import os
import tempfile

from fastapi import FastAPI, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.stt import stt_engine


class health_response(BaseModel):
    status: str
    model: str
    device: str
    compute_type: str


class transcribe_response(BaseModel):
    text: str
    language: str
    duration_seconds: float


app = FastAPI(
    title="My Talking Claw Voice",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = stt_engine()


@app.get("/health", response_model=health_response)
def get_health() -> health_response:
    return health_response(
        status="ok",
        model=engine.model_name,
        device=engine.device,
        compute_type=engine.compute_type,
    )


@app.post("/transcribe", response_model=transcribe_response)
async def post_transcribe(
    file: UploadFile,
    language: str | None = Form(None),
) -> transcribe_response:
    audio_bytes = await file.read()

    if len(audio_bytes) == 0:
        raise HTTPException(status_code=400, detail="audio file must not be empty")

    # An absent or blank language field means "use the service default".
    resolved_language = language or engine.default_language

    # faster-whisper decodes via PyAV, which needs a real seekable file for
    # container formats like mp4/m4a, so the upload is staged on disk.
    temp = tempfile.NamedTemporaryFile(delete=False)
    try:
        temp.write(audio_bytes)
        temp.close()
        result = engine.transcribe(temp.name, resolved_language)
    finally:
        os.unlink(temp.name)

    if result.text == "":
        raise HTTPException(status_code=422, detail="no speech recognized")

    return transcribe_response(
        text=result.text,
        language=result.language,
        duration_seconds=result.duration_seconds,
    )
