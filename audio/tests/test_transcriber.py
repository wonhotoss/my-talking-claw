"""The two engines behind the transcriber seam, without audio or network.

The Azure session is driven by calling its SDK callbacks directly with
stand-in events; the stream and recognizer are fakes that record what was
asked of them.
"""

import threading
from types import SimpleNamespace

import numpy as np
import pytest

from app.device_client import no_speech_error
from app.pcm import read_wav
from app.transcriber import azure_session, transcript, whisper_transcriber


class fake_client:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def transcribe(self, wav: bytes, language: str) -> str:
        self.calls.append((wav, language))

        return "텍스트"


def test_whisper_sends_the_finished_recording_as_wav_and_ignores_frames() -> None:
    client = fake_client()
    session = whisper_transcriber(client, 16000, "ko").start()
    session.feed(np.zeros(1280, dtype=np.int16))
    recording = np.full(3200, 7, dtype=np.int16)

    assert session.finish(recording) == "텍스트"

    wav, language = client.calls[0]
    samples, rate = read_wav(wav)
    assert (rate, language) == (16000, "ko")
    assert np.array_equal(samples, recording)

    # Nothing to tear down.
    session.abort()


# --- transcript ---------------------------------------------------------------


def test_transcript_joins_finalised_pieces() -> None:
    assert transcript(["오늘 날씨", "", "어때?"], ["오늘"]) == "오늘 날씨 어때?"


def test_transcript_falls_back_to_the_last_partial() -> None:
    assert transcript([], ["오늘", "오늘 날씨"]) == "오늘 날씨"
    assert transcript([""], ["오늘 날씨"]) == "오늘 날씨"
    assert transcript([], []) == ""


# --- azure session ------------------------------------------------------------


class fake_stream:
    def __init__(self) -> None:
        self.written: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.written.append(data)

    def close(self) -> None:
        self.closed = True


class fake_recognizer:
    def __init__(self) -> None:
        self.stopped = 0
        self.stopped_async = 0

    def stop_continuous_recognition(self) -> None:
        self.stopped += 1

    def stop_continuous_recognition_async(self) -> None:
        self.stopped_async += 1


def result_event(text: str) -> SimpleNamespace:
    return SimpleNamespace(result=SimpleNamespace(text=text))


def canceled_event(reason, details: str) -> SimpleNamespace:
    return SimpleNamespace(cancellation_details=SimpleNamespace(reason=reason, error_details=details))


def end_of_stream():
    import azure.cognitiveservices.speech as speechsdk

    return speechsdk.CancellationReason.EndOfStream


def make_session() -> tuple[azure_session, fake_stream, fake_recognizer]:
    stream = fake_stream()
    recognizer = fake_recognizer()

    return azure_session(recognizer, stream, 1.0), stream, recognizer


def test_frames_are_written_as_little_endian_int16_bytes() -> None:
    session, stream, _ = make_session()
    frame = np.array([1, -2, 300], dtype=np.int16)

    session.feed(frame)

    assert stream.written == [frame.astype("<i2").tobytes()]


def test_finish_closes_the_stream_waits_for_the_end_and_joins_the_text() -> None:
    session, stream, recognizer = make_session()
    session.on_recognizing(result_event("지금"))
    session.on_recognized(result_event("지금 기기 온도"))
    session.on_recognized(result_event("측정 부탁해."))
    session.on_canceled(canceled_event(end_of_stream(), ""))
    session.on_session_stopped(SimpleNamespace())

    assert session.finish(np.zeros(0, dtype=np.int16)) == "지금 기기 온도 측정 부탁해."
    assert stream.closed
    assert recognizer.stopped == 1


def test_the_end_may_arrive_after_finish_started_waiting() -> None:
    session, _, _ = make_session()

    def late_end() -> None:
        session.on_recognized(result_event("늦게"))
        session.on_canceled(canceled_event(end_of_stream(), ""))

    threading.Timer(0.05, late_end).start()

    assert session.finish(np.zeros(0, dtype=np.int16)) == "늦게"


def test_nothing_recognised_is_no_speech() -> None:
    session, _, _ = make_session()
    session.on_recognized(result_event(""))
    session.on_canceled(canceled_event(end_of_stream(), ""))

    with pytest.raises(no_speech_error):
        session.finish(np.zeros(0, dtype=np.int16))


def test_a_service_error_is_fatal_for_the_turn() -> None:
    import azure.cognitiveservices.speech as speechsdk

    session, _, _ = make_session()
    session.on_canceled(canceled_event(speechsdk.CancellationReason.Error, "WebSocket upgrade failed"))

    with pytest.raises(RuntimeError, match="WebSocket upgrade failed"):
        session.finish(np.zeros(0, dtype=np.int16))


def test_a_session_that_never_ends_times_out_and_is_stopped() -> None:
    stream = fake_stream()
    recognizer = fake_recognizer()
    session = azure_session(recognizer, stream, 0.05)

    with pytest.raises(RuntimeError, match="did not end"):
        session.finish(np.zeros(0, dtype=np.int16))

    assert recognizer.stopped_async == 1


def test_abort_closes_and_stops_without_waiting() -> None:
    session, stream, recognizer = make_session()

    session.abort()

    assert stream.closed
    assert recognizer.stopped_async == 1
