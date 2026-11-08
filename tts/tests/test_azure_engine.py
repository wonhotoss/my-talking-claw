"""The Azure adapter without the network.

tests/fixtures/azure/*.json are real event streams recorded by
scripts/azure-probe (2026-11-08, day-11.md): the viseme IDs and offsets Azure
raised for a sentence, plus the audio length. Synthesis is deterministic for a
voice+text, so replaying them is replaying the service. The audio itself is
not needed for the timeline, so zeros of the recorded length stand in.
"""

import json
import struct
from pathlib import Path

import numpy
import pytest

from app import speech, viseme
from app.engines import azure, engine_for_environment

fixtures = Path(__file__).parent / "fixtures" / "azure"
fixture_names = ["sunhi-short.json", "sunhi-long.json", "sunhi-labials.json", "injoon-medium.json"]
rate = azure.output_sample_rate


def load(name: str) -> dict:
    return json.loads((fixtures / name).read_text(encoding="utf-8"))


def events_of(fixture: dict) -> list[azure.viseme_event]:
    return [
        azure.viseme_event(viseme_id=item["viseme_id"], offset_ticks=round(item["offset_ms"] * 10_000))
        for item in fixture["visemes"]
    ]


def total_samples_of(fixture: dict) -> int:
    return round(fixture["audio_seconds"] * rate)


def seconds(value: float) -> int:
    return round(value * azure.ticks_per_second)


def replay(fixture: dict) -> speech.speech_result:
    total = total_samples_of(fixture)
    symbols, counts = azure.alignment(events_of(fixture), total, rate)
    piece = speech.speech_piece(
        text=fixture["text"],
        samples=numpy.zeros(total, dtype=numpy.float32),
        symbols=symbols,
        sample_counts=counts,
    )

    return speech.build_speech([piece], rate, viseme.azure_visemes, 0.0, 0.02, 50)


def mouth_shapes(result: speech.speech_result) -> list[str]:
    """The non-silent postures in order, runs collapsed."""
    shapes: list[str] = []

    for span in result.visemes:
        if span.viseme != viseme.viseme_silence and (not shapes or shapes[-1] != span.viseme):
            shapes.append(span.viseme)

    return shapes


# --- alignment ----------------------------------------------------------------


def test_each_posture_ends_where_the_next_starts_and_the_last_at_the_audio() -> None:
    events = [azure.viseme_event(0, 0), azure.viseme_event(19, seconds(0.5)), azure.viseme_event(4, seconds(1.0))]

    assert azure.alignment(events, 24000, rate) == (["0", "19", "4"], [8000, 8000, 8000])


def test_a_gap_before_the_first_event_is_silence() -> None:
    events = [azure.viseme_event(2, seconds(0.05))]

    assert azure.alignment(events, 1600, rate) == (["0", "2"], [800, 800])


def test_callback_order_does_not_matter() -> None:
    events = [azure.viseme_event(4, seconds(1.0)), azure.viseme_event(19, seconds(0.5)), azure.viseme_event(0, 0)]

    assert azure.alignment(events, 24000, rate)[0] == ["0", "19", "4"]


def test_no_events_is_one_silent_span() -> None:
    assert azure.alignment([], 800, rate) == (["0"], [800])


def test_an_event_beyond_the_audio_is_fatal() -> None:
    with pytest.raises(RuntimeError, match="beyond the audio"):
        azure.alignment([azure.viseme_event(0, 0), azure.viseme_event(2, seconds(2.0))], 16000, rate)


@pytest.mark.parametrize("name", fixture_names)
def test_recorded_streams_tile_their_audio_exactly(name: str) -> None:
    fixture = load(name)
    symbols, counts = azure.alignment(events_of(fixture), total_samples_of(fixture), rate)

    assert len(symbols) == len(counts)
    assert sum(counts) == total_samples_of(fixture)
    assert min(counts) >= 0


# --- pcm / ssml ---------------------------------------------------------------


def test_pcm_scales_int16_to_unit_float() -> None:
    samples = azure.pcm_to_samples(struct.pack("<3h", 0, 16384, -32768))

    assert samples.dtype == numpy.float32
    assert samples.tolist() == [0.0, 0.5, -1.0]


def test_ssml_carries_voice_and_rate_and_escapes_the_text() -> None:
    document = azure.ssml("1 < 2 & 3", "ko-KR-SunHiNeural", 1.25, "ko-KR")

    assert '<voice name="ko-KR-SunHiNeural">' in document
    assert '<prosody rate="1.25">1 &lt; 2 &amp; 3</prosody>' in document
    assert 'xml:lang="ko-KR"' in document


# --- the whole timeline from recorded streams ---------------------------------


@pytest.mark.parametrize("name", fixture_names)
def test_recorded_streams_replay_through_build_speech(name: str) -> None:
    fixture = load(name)
    result = replay(fixture)
    duration = total_samples_of(fixture) / rate

    assert result.duration == pytest.approx(duration)
    assert result.visemes[0].start == 0.0
    assert result.visemes[-1].end == pytest.approx(duration)
    # Azure opens and closes every utterance with its silence posture.
    assert result.visemes[0].viseme == viseme.viseme_silence
    assert result.visemes[-1].viseme == viseme.viseme_silence
    assert [segment.text for segment in result.segments] == [fixture["text"]]


def test_the_short_sentence_moves_the_mouth_through_its_vowels() -> None:
    # 네, 알겠어요: ㅔ ㅏ ㅔ ㅓ ㅛ. The y-glide of 요 shows as a brief "i"
    # before the "o" - Azure raises ID 6 for ~130 ms ahead of the nucleus.
    assert mouth_shapes(replay(load("sunhi-short.json"))) == ["e", "a", "e", "a", "i", "o"]


def test_labials_close_the_mouth() -> None:
    result = replay(load("sunhi-labials.json"))

    assert viseme.viseme_closed in {span.viseme for span in result.visemes}


# --- the engine object --------------------------------------------------------


def configured(monkeypatch: pytest.MonkeyPatch) -> azure.azure_engine:
    monkeypatch.setenv("AZURE_SPEECH_KEY", "k")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "koreacentral")

    return azure.azure_engine()


def test_engine_reads_its_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_TTS_VOICE", raising=False)
    engine = configured(monkeypatch)

    assert (engine.engine, engine.language, engine.sample_rate) == ("azure", "ko-KR", 16000)
    assert engine.voice == azure.default_voice
    assert engine.region == "koreacentral"

    monkeypatch.setenv("AZURE_TTS_VOICE", "ko-KR-InJoonNeural")

    assert azure.azure_engine().voice == "ko-KR-InJoonNeural"


def test_local_engines_have_nothing_to_warm_up() -> None:
    # The base is a no-op; the server calls it for every engine at startup.
    speech.speech_engine().warm_up()


def test_a_missing_key_is_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_SPEECH_KEY", raising=False)
    monkeypatch.setenv("AZURE_SPEECH_REGION", "koreacentral")

    with pytest.raises(KeyError, match="AZURE_SPEECH_KEY"):
        azure.azure_engine()


def test_speak_builds_the_timeline_from_the_events(monkeypatch: pytest.MonkeyPatch) -> None:
    engine = configured(monkeypatch)
    pcm = struct.pack(f"<{rate}h", *([0] * rate))
    events = [azure.viseme_event(0, 0), azure.viseme_event(2, seconds(0.25)), azure.viseme_event(0, seconds(0.75))]
    monkeypatch.setattr(engine, "synthesize_events", lambda text: (pcm, events))

    result = engine.speak("아")

    assert result.duration == pytest.approx(1.0)
    assert result.sample_rate == rate
    assert [(span.viseme, span.start, span.end) for span in result.visemes] == [
        ("x", 0.0, 0.25),
        ("a", 0.25, 0.75),
        ("x", 0.75, 1.0),
    ]


def test_engine_selection_knows_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TTS_ENGINE", "azure")
    monkeypatch.setenv("AZURE_SPEECH_KEY", "k")
    monkeypatch.setenv("AZURE_SPEECH_REGION", "koreacentral")

    assert isinstance(engine_for_environment(), azure.azure_engine)

    monkeypatch.setenv("TTS_ENGINE", "cloud")

    with pytest.raises(RuntimeError, match="azure"):
        engine_for_environment()
