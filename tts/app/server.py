import base64
import threading

from fastapi import FastAPI, HTTPException, Response
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.engines import engine_for_environment


class synthesize_request(BaseModel):
    text: str


# Structurally identical to synthesize_request today, but a separate contract:
# a per-request speed or voice override belongs on /speak only.
class speak_request(BaseModel):
    text: str


class speak_viseme(BaseModel):
    start: float
    end: float
    viseme: str


class speak_segment(BaseModel):
    start: float
    end: float
    text: str


class speak_response(BaseModel):
    audio_base64: str
    media_type: str
    sample_rate: int
    duration: float
    visemes: list[speak_viseme]
    segments: list[speak_segment]
    envelope: list[float]
    envelope_hz: int


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

engine = engine_for_environment()

# One synthesis at a time. The engine's single onnxruntime session already uses
# every core, so concurrent requests gain no throughput and only share the CPU:
# on an RPi4, four sentences arriving together took the first one from 1.8s to
# 6.2s while the total stayed at ~11s. Serialising keeps first-sound latency at
# the single-request figure, and because the client fires its requests in
# utterance order, FIFO here is utterance order.
synthesis_lock = threading.Lock()


@app.get("/health", response_model=health_response)
def get_health() -> health_response:
    return health_response(status="ok", engine=engine.engine, language=engine.language)


@app.post("/synthesize")
def post_synthesize(request: synthesize_request) -> Response:
    text = request.text.strip()

    if text == "":
        raise HTTPException(status_code=400, detail="text must not be empty")

    try:
        with synthesis_lock:
            audio = engine.synthesize(text)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"tts failure: {error}")

    return Response(content=audio, media_type="audio/wav")


# Audio and its viseme timeline must come from one synthesis: the duration
# predictor is stochastic (noise_scale_w), so a second call would produce
# different phoneme durations and the timeline would not match the audio. That
# rules out a separate metadata endpoint, and the timeline is too large for a
# response header, so the WAV rides along base64-encoded (+33%, tens of ms on
# a LAN) in one round trip.
@app.post("/speak", response_model=speak_response)
def post_speak(request: speak_request) -> speak_response:
    text = request.text.strip()

    if text == "":
        raise HTTPException(status_code=400, detail="text must not be empty")

    try:
        with synthesis_lock:
            result = engine.speak(text)
    except Exception as error:
        raise HTTPException(status_code=502, detail=f"tts failure: {error}")

    return speak_response(
        audio_base64=base64.b64encode(result.audio).decode("ascii"),
        media_type="audio/wav",
        sample_rate=result.sample_rate,
        duration=result.duration,
        visemes=[
            speak_viseme(start=span.start, end=span.end, viseme=span.viseme)
            for span in result.visemes
        ],
        segments=[
            speak_segment(start=segment.start, end=segment.end, text=segment.text)
            for segment in result.segments
        ],
        envelope=result.envelope,
        envelope_hz=result.envelope_hz,
    )
