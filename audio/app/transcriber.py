"""Who turns the recording into text: the seam between the loop and an engine.

Like `playback_sink` in player.py, one Protocol so the loop never knows which
engine it has and the tests can run with a fake. Two engines:

  whisper_transcriber   the voice service on this host (POST /transcribe with
                        the finished, trimmed WAV) - what day 9 shipped
  azure_transcriber     Azure Speech, streaming: frames go to the service
                        while the user is still talking, so the text is ready
                        when the endpointer says the utterance is over
                        (day-11 probe: final text -0.13..+0.24 s after the
                        last frame, against whisper's 6.8 s)

The shape is streaming-first, because that is where the win is:

  session = transcriber.start()      at the wake word, before the chime
  session.feed(frame)                every captured frame while recording
  text = session.finish(recording)   at the endpoint; raises no_speech_error
  session.abort()                    the endpointer gave up (no speech)

`finish` takes the trimmed recording for the whole-clip engine, which
transcribes that and ignores what was fed; the streaming engine ignores the
argument and finalises what it already sent. Both shapes fit one call site.

Switching engines is manual (STT_ENGINE + restart); there is no runtime
fallback by decision (뇌중심-로드맵.md "비용·전제"). The Azure SDK is imported
lazily so the module loads where it is not installed.

azure_session is duplicated in voice/app/engines/azure.py for the phone's
path; the two services are separate uv projects. A third copy means it is time
for a package.
"""

import sys
import threading
import time
from typing import Protocol

import numpy as np

from app.device_client import device_client, no_speech_error
from app.pcm import wav_bytes


def log(message: str) -> None:
    print(f"[audio] {time.strftime('%H:%M:%S')} {message}", file=sys.stderr, flush=True)


class transcription_session(Protocol):
    def feed(self, frame: np.ndarray) -> None: ...

    def finish(self, recording: np.ndarray) -> str: ...

    def abort(self) -> None: ...


class transcriber(Protocol):
    def start(self) -> transcription_session: ...


# --- whisper: the voice service, whole clip -----------------------------------


class whisper_session:
    def __init__(self, client: device_client, sample_rate: int, language: str) -> None:
        self.client = client
        self.sample_rate = sample_rate
        self.language = language

    def feed(self, frame: np.ndarray) -> None:
        # The clip is sent whole at the end; nothing to do per frame.
        pass

    def finish(self, recording: np.ndarray) -> str:
        return self.client.transcribe(wav_bytes(recording, self.sample_rate), self.language)

    def abort(self) -> None:
        pass


class whisper_transcriber:
    def __init__(self, client: device_client, sample_rate: int, language: str) -> None:
        self.client = client
        self.sample_rate = sample_rate
        self.language = language

    def start(self) -> whisper_session:
        return whisper_session(self.client, self.sample_rate, self.language)


# --- azure: streaming while recording -----------------------------------------


def transcript(recognized: list[str], partials: list[str]) -> str:
    """The final text of one session.

    Continuous recognition yields one `recognized` piece per pause the service
    hears; Azure punctuates each, so a space joins them. When nothing was
    finalised the last partial is the best text there is (the probe never saw
    this once the stream was closed properly, but the fallback is cheap).
    """
    final = " ".join(piece for piece in recognized if piece != "")

    if final != "":
        return final

    return partials[-1] if partials else ""


class azure_session:
    """One utterance on a push stream. Callbacks arrive on the SDK's thread.

    Ending it is `stream.close()` and then waiting: the SDK tells the service
    the audio is over, the service finalises what is pending, and the session
    ends by itself (canceled/EndOfStream, then session_stopped). Calling
    stop_continuous_recognition() first aborts the pending result - the probe
    lost the text on two of three runs that way.
    """

    def __init__(self, recognizer, stream, timeout_seconds: float) -> None:
        self.recognizer = recognizer
        self.stream = stream
        self.timeout_seconds = timeout_seconds
        self.recognized: list[str] = []
        self.partials: list[str] = []
        self.error = ""
        self.ended = threading.Event()

    def on_recognizing(self, event) -> None:
        self.partials.append(event.result.text)

    def on_recognized(self, event) -> None:
        self.recognized.append(event.result.text)

    def on_canceled(self, event) -> None:
        import azure.cognitiveservices.speech as speechsdk

        details = event.cancellation_details

        # EndOfStream is the normal end of a closed stream, not a failure.
        if details.reason != speechsdk.CancellationReason.EndOfStream:
            self.error = f"{details.reason} {details.error_details}"

        self.ended.set()

    def on_session_stopped(self, event) -> None:
        self.ended.set()

    def feed(self, frame: np.ndarray) -> None:
        # int16 mono at the capture rate - exactly the stream's declared format.
        self.stream.write(frame.tobytes())

    def finish(self, recording: np.ndarray) -> str:
        self.stream.close()

        if not self.ended.wait(self.timeout_seconds):
            self.recognizer.stop_continuous_recognition_async()
            raise RuntimeError(f"azure stt: the session did not end within {self.timeout_seconds}s of closing the stream")

        # Cleanup only; every result has arrived.
        self.recognizer.stop_continuous_recognition()

        if self.error != "":
            raise RuntimeError(f"azure stt canceled: {self.error}")

        # What the service actually said, for the day the text is wrong: a
        # `recognized` of "" is its NoMatch, partials show how far it got.
        last_partial = f" last {self.partials[-1]!r}" if self.partials else ""
        log(f"azure stt: recognized {self.recognized!r}, partials {len(self.partials)}{last_partial}")
        text = transcript(self.recognized, self.partials)

        if text == "":
            raise no_speech_error()

        return text

    def abort(self) -> None:
        self.stream.close()
        # Not waited on: the loop is about to listen again.
        self.recognizer.stop_continuous_recognition_async()


class azure_transcriber:
    def __init__(self, key: str, region: str, language: str, sample_rate: int, timeout_seconds: float) -> None:
        self.key = key
        self.region = region
        # BCP-47 (ko-KR), unlike whisper's bare code.
        self.language = language
        self.sample_rate = sample_rate
        self.timeout_seconds = timeout_seconds

    def start(self) -> azure_session:
        import azure.cognitiveservices.speech as speechsdk

        stream = speechsdk.audio.PushAudioInputStream(
            stream_format=speechsdk.audio.AudioStreamFormat(
                samples_per_second=self.sample_rate, bits_per_sample=16, channels=1
            )
        )
        config = speechsdk.SpeechConfig(subscription=self.key, region=self.region)
        config.speech_recognition_language = self.language
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=config, audio_config=speechsdk.audio.AudioConfig(stream=stream)
        )
        session = azure_session(recognizer, stream, self.timeout_seconds)
        recognizer.recognizing.connect(session.on_recognizing)
        recognizer.recognized.connect(session.on_recognized)
        recognizer.canceled.connect(session.on_canceled)
        recognizer.session_stopped.connect(session.on_session_stopped)
        # Returns at once; the connection comes up under the first frames
        # (probe: 0.00 s here, first partial 1.2-2.8 s into the speech).
        recognizer.start_continuous_recognition()

        return session
