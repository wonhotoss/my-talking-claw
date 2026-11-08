"""Azure Speech for the voice service: the phone's path to the cloud.

The phone records with MediaRecorder - webm/opus on Android, mp4 on iOS - and
Azure takes neither container directly. PyAV, which faster-whisper already
pulls in, decodes anything to 16 kHz mono PCM; the PCM then goes through a
push stream with continuous recognition, the same path the device loop uses
(audio/app/transcriber.py) minus the streaming-while-recording, since the
phone hands over the whole clip at once. Continuous rather than
recognize_once so a pause mid-utterance does not cut the text.

Measured on the probe (day-11): a 2.5 s clip pushed at once comes back in
1.4-2.9 s, against whisper base's 5-7 s on the RPi, and the RPi does no work.

`azure_session` is a copy of audio/app/transcriber.py's; the two services are
separate uv projects. A third copy means it is time for a package. The SDK
and PyAV are imported lazily so this module loads on any host.

Environment:

- AZURE_SPEECH_KEY            required
- AZURE_SPEECH_REGION         required, e.g. koreacentral
- AZURE_STT_LANGUAGE          default ko-KR; a request's bare `language` (the
                              phone sends "ko") is mapped through language_tags
- AZURE_STT_TIMEOUT_SECONDS   default 10: wait for the final text after the clip
"""

import os
import threading

from app.stt import transcription_result

sample_rate = 16000
# The phone (and the whisper path) speak whisper's bare codes.
language_tags = {"ko": "ko-KR", "en": "en-US", "ja": "ja-JP"}


def language_tag(language: str) -> str:
    # An unknown bare code raises KeyError on its own; that is the message we want.
    return language if "-" in language else language_tags[language]


def transcript(recognized: list[str], partials: list[str]) -> str:
    final = " ".join(piece for piece in recognized if piece != "")

    if final != "":
        return final

    return partials[-1] if partials else ""


def decode_pcm(audio_path: str, rate: int) -> bytes:
    """Any container PyAV can open -> 16-bit mono PCM at `rate`."""
    import av

    resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
    chunks: list[bytes] = []

    with av.open(audio_path) as container:
        for frame in container.decode(audio=0):
            chunks += [piece.to_ndarray().tobytes() for piece in resampler.resample(frame)]

    # Flush what the resampler still holds.
    chunks += [piece.to_ndarray().tobytes() for piece in resampler.resample(None)]

    return b"".join(chunks)


class azure_session:
    """One clip on a push stream; see audio/app/transcriber.py for the why."""

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

        if details.reason != speechsdk.CancellationReason.EndOfStream:
            self.error = f"{details.reason} {details.error_details}"

        self.ended.set()

    def on_session_stopped(self, event) -> None:
        self.ended.set()

    def feed(self, pcm: bytes) -> None:
        self.stream.write(pcm)

    def finish(self) -> str:
        self.stream.close()

        if not self.ended.wait(self.timeout_seconds):
            self.recognizer.stop_continuous_recognition_async()
            raise RuntimeError(f"azure stt: the session did not end within {self.timeout_seconds}s of closing the stream")

        self.recognizer.stop_continuous_recognition()

        if self.error != "":
            raise RuntimeError(f"azure stt canceled: {self.error}")

        return transcript(self.recognized, self.partials)


class azure_engine:
    def __init__(self) -> None:
        self.key = os.environ["AZURE_SPEECH_KEY"]
        self.region = os.environ["AZURE_SPEECH_REGION"]
        self.default_language = os.environ.get("AZURE_STT_LANGUAGE", "ko-KR")
        self.timeout_seconds = float(os.environ.get("AZURE_STT_TIMEOUT_SECONDS", "10"))
        # /health's whisper-shaped fields, filled with what this engine has.
        self.model_name = "azure"
        self.device = self.region
        self.compute_type = "cloud"

    def open_session(self, language: str) -> azure_session:
        import azure.cognitiveservices.speech as speechsdk

        stream = speechsdk.audio.PushAudioInputStream(
            stream_format=speechsdk.audio.AudioStreamFormat(
                samples_per_second=sample_rate, bits_per_sample=16, channels=1
            )
        )
        config = speechsdk.SpeechConfig(subscription=self.key, region=self.region)
        config.speech_recognition_language = language
        recognizer = speechsdk.SpeechRecognizer(
            speech_config=config, audio_config=speechsdk.audio.AudioConfig(stream=stream)
        )
        session = azure_session(recognizer, stream, self.timeout_seconds)
        recognizer.recognizing.connect(session.on_recognizing)
        recognizer.recognized.connect(session.on_recognized)
        recognizer.canceled.connect(session.on_canceled)
        recognizer.session_stopped.connect(session.on_session_stopped)
        recognizer.start_continuous_recognition()

        return session

    def transcribe(self, audio_path: str, language: str) -> transcription_result:
        tag = language_tag(language)
        pcm = decode_pcm(audio_path, sample_rate)
        session = self.open_session(tag)
        session.feed(pcm)

        # An empty text is the server's 422, exactly as with whisper.
        return transcription_result(
            text=session.finish(),
            language=tag,
            duration_seconds=len(pcm) / (2 * sample_rate),
        )
