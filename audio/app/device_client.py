"""HTTP to the three services the loop talks to.

Same contracts the browser used (README "이벤트 계약"), nothing new on the wire
except POST /api/device, which the loop alone speaks.
"""

import base64
from collections.abc import Iterator
from dataclasses import dataclass

import httpx
import numpy as np

from app.pcm import read_wav
from app.sse import parse_sse, sse_message


class no_speech_error(Exception):
    """The voice service heard nothing it could transcribe (its 422)."""


@dataclass(frozen=True)
class spoken_reply:
    samples: np.ndarray
    sample_rate: int
    # The tts service's timing fields verbatim; forwarded to viewers untouched.
    track: dict


class device_client:
    def __init__(
        self,
        http: httpx.Client,
        backend_url: str,
        voice_url: str,
        tts_url: str,
        push_token: str,
    ) -> None:
        self.http = http
        self.backend_url = backend_url
        self.voice_url = voice_url
        self.tts_url = tts_url
        self.push_token = push_token

    def transcribe(self, wav: bytes, language: str) -> str:
        response = self.http.post(
            f"{self.voice_url}/transcribe",
            files={"file": ("recording.wav", wav, "audio/wav")},
            data={"language": language},
            timeout=120.0,
        )

        if response.status_code == 422:
            raise no_speech_error()

        response.raise_for_status()

        return response.json()["text"]

    def start_turn(self, text: str, turn_id: str) -> None:
        self.http.post(
            f"{self.backend_url}/api/turns",
            json={"text": text, "turn_id": turn_id},
            timeout=10.0,
        ).raise_for_status()

    def cancel_turn(self, turn_id: str) -> None:
        self.http.post(f"{self.backend_url}/api/turns/{turn_id}/cancel", timeout=10.0).raise_for_status()

    def speak(self, text: str) -> spoken_reply:
        response = self.http.post(f"{self.tts_url}/speak", json={"text": text}, timeout=120.0)
        response.raise_for_status()
        body = response.json()
        samples, sample_rate = read_wav(base64.b64decode(body["audio_base64"]))

        return spoken_reply(
            samples=samples,
            sample_rate=sample_rate,
            track={key: body[key] for key in ("duration", "visemes", "segments", "envelope", "envelope_hz")},
        )

    def publish(self, event: dict) -> None:
        self.http.post(
            f"{self.backend_url}/api/device",
            json=event,
            headers={"X-Push-Token": self.push_token},
            timeout=10.0,
        ).raise_for_status()

    def events(self, last_seq: int) -> Iterator[sse_message]:
        headers = {"Last-Event-ID": str(last_seq)} if last_seq > 0 else {}

        with self.http.stream(
            "GET",
            f"{self.backend_url}/api/events",
            headers=headers,
            timeout=httpx.Timeout(10.0, read=None),
        ) as response:
            response.raise_for_status()
            yield from parse_sse(response.iter_lines())
