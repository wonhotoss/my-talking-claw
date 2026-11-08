"""The Azure engine without the network: decoding through real PyAV, the
session through its callbacks with stand-in events."""

import struct
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.engines import azure


def write_wav(path: Path, rate: int, channels: int, seconds: float) -> None:
    count = int(rate * seconds)

    with wave.open(str(path), "wb") as target:
        target.setnchannels(channels)
        target.setsampwidth(2)
        target.setframerate(rate)
        target.writeframes(struct.pack(f"<{count * channels}h", *([1000] * (count * channels))))


def test_language_tags_map_the_phones_bare_code_and_pass_full_tags_through() -> None:
    assert azure.language_tag("ko") == "ko-KR"
    assert azure.language_tag("en-GB") == "en-GB"

    with pytest.raises(KeyError):
        azure.language_tag("xx")


def test_decode_passes_16k_mono_through(tmp_path: Path) -> None:
    write_wav(tmp_path / "clip.wav", 16000, 1, 0.5)

    pcm = azure.decode_pcm(str(tmp_path / "clip.wav"), 16000)

    assert len(pcm) == 16000 * 2 // 2


def test_decode_resamples_and_downmixes(tmp_path: Path) -> None:
    write_wav(tmp_path / "clip.wav", 48000, 2, 0.5)

    pcm = azure.decode_pcm(str(tmp_path / "clip.wav"), 16000)

    # 0.5 s at 16 kHz mono = 8000 samples; the resampler's edge handling may
    # shift the count by a few frames.
    assert abs(len(pcm) // 2 - 8000) < 64


def test_transcript_joins_pieces_and_falls_back_to_the_last_partial() -> None:
    assert azure.transcript(["오늘 날씨", "어때?"], []) == "오늘 날씨 어때?"
    assert azure.transcript([""], ["오늘", "오늘 날씨"]) == "오늘 날씨"
    assert azure.transcript([], []) == ""


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


def test_session_feeds_closes_waits_and_joins() -> None:
    import azure.cognitiveservices.speech as speechsdk

    stream = fake_stream()
    recognizer = fake_recognizer()
    session = azure.azure_session(recognizer, stream, 1.0)
    session.feed(b"\x00\x01")
    session.on_recognized(result_event("안녕하세요."))
    session.on_canceled(canceled_event(speechsdk.CancellationReason.EndOfStream, ""))

    assert session.finish() == "안녕하세요."
    assert stream.written == [b"\x00\x01"]
    assert stream.closed
    assert recognizer.stopped == 1


def test_session_reports_a_service_error() -> None:
    import azure.cognitiveservices.speech as speechsdk

    session = azure.azure_session(fake_recognizer(), fake_stream(), 1.0)
    session.on_canceled(canceled_event(speechsdk.CancellationReason.Error, "401"))

    with pytest.raises(RuntimeError, match="401"):
        session.finish()


def test_session_times_out_when_the_service_never_ends() -> None:
    recognizer = fake_recognizer()
    session = azure.azure_session(recognizer, fake_stream(), 0.05)

    with pytest.raises(RuntimeError, match="did not end"):
        session.finish()

    assert recognizer.stopped_async == 1


def test_engine_transcribes_a_file_through_a_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("AZURE_SPEECH_KEY", "k")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "koreacentral")
    write_wav(tmp_path / "clip.wav", 16000, 1, 1.0)
    engine = azure.azure_engine()
    opened: list[str] = []

    class scripted_session:
        def __init__(self) -> None:
            self.fed: list[bytes] = []

        def feed(self, pcm: bytes) -> None:
            self.fed.append(pcm)

        def finish(self) -> str:
            return "네." if len(self.fed[0]) == 32000 else "?"

    def open_session(language: str) -> scripted_session:
        opened.append(language)

        return scripted_session()

    monkeypatch.setattr(engine, "open_session", open_session)

    result = engine.transcribe(str(tmp_path / "clip.wav"), "ko")

    assert (result.text, result.language, result.duration_seconds) == ("네.", "ko-KR", 1.0)
    assert opened == ["ko-KR"]
    assert (engine.model_name, engine.device, engine.compute_type, engine.default_language) == (
        "azure", "koreacentral", "cloud", "ko-KR",
    )
