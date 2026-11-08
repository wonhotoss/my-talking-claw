"""Probe: Azure TTS round trip, voices, and the ko-KR viseme-ID mapping.

    uv run python -m probe.tts --voices ko-KR-SunHiNeural ko-KR-InJoonNeural ko-KR-HyunsuMultilingualNeural
    uv run python -m probe.tts --voices ko-KR-SunHiNeural --format raw24k --out out/tts-24k

One SpeechSynthesizer per voice, kept for the whole run (the adapter will keep
one for the process), optionally with the Connection pre-opened. Each sentence
is synthesized once; the probe records the round trip, the audio length, every
viseme_received event (ID + offset) and every word boundary, and writes the
audio as WAV next to a JSON of all of it under --out/<voice>/.

Those JSON files are the fixtures the tts/app/engines/azure.py unit tests will
replay - no network in the test suite.

The vowel lines print their viseme time share. Read it row by row: the ID that
dominates a row and changes between rows is where that Korean vowel lands.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk

from probe.analysis import (
    format_time_share,
    spans_from_visemes,
    ticks_to_ms,
    viseme_event,
    viseme_time_share,
)
from probe.common import dump_json, log, pcm_seconds, settings_from_environment, stopwatch, table, write_wav
from probe.sentences import by_length, by_vowel, sample

output_formats = {
    "raw16k": (speechsdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm, 16000),
    "raw24k": (speechsdk.SpeechSynthesisOutputFormat.Raw24Khz16BitMonoPcm, 24000),
}


@dataclass(frozen=True)
class word_boundary:
    text: str
    offset_ms: float
    duration_ms: float


@dataclass(frozen=True)
class synthesis_record:
    voice: str
    label: str
    text: str
    chars: int
    round_trip_seconds: float
    audio_seconds: float
    sample_rate: int
    visemes: list[viseme_event]
    word_boundaries: list[word_boundary]
    wav: str
    # Non-empty when the service failed this sentence. A stall is a measurement
    # (first RPi run: "Timeout while synthesizing, RTF 2.0, frame interval
    # 5023ms" on the long sentence), so it is recorded and the run goes on.
    error: str


@dataclass(frozen=True)
class synthesis_outcome:
    elapsed_seconds: float
    pcm: bytes
    error: str


class voice_session:
    """One synthesizer, reused; the event lists are reset per sentence."""

    def __init__(self, settings_key: str, region: str, voice: str, output_format: speechsdk.SpeechSynthesisOutputFormat, preconnect: bool) -> None:
        config = speechsdk.SpeechConfig(subscription=settings_key, region=region)
        config.speech_synthesis_voice_name = voice
        config.set_speech_synthesis_output_format(output_format)
        # audio_config=None: the audio comes back in the result instead of a speaker.
        self.synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
        self.visemes: list[viseme_event] = []
        self.word_boundaries: list[word_boundary] = []
        self.synthesizer.viseme_received.connect(self.on_viseme)
        self.synthesizer.synthesis_word_boundary.connect(self.on_word_boundary)

        if preconnect:
            clock = stopwatch()
            speechsdk.Connection.from_speech_synthesizer(self.synthesizer).open(True)
            log(f"{voice}: pre-opened connection in {clock.lap():.2f}s")

    def on_viseme(self, event: speechsdk.SpeechSynthesisVisemeEventArgs) -> None:
        self.visemes.append(viseme_event(viseme_id=event.viseme_id, offset_ms=ticks_to_ms(event.audio_offset)))

    def on_word_boundary(self, event: speechsdk.SpeechSynthesisWordBoundaryEventArgs) -> None:
        self.word_boundaries.append(
            word_boundary(
                text=event.text,
                offset_ms=ticks_to_ms(event.audio_offset),
                duration_ms=event.duration.total_seconds() * 1000,
            )
        )

    def speak(self, text: str) -> synthesis_outcome:
        self.visemes = []
        self.word_boundaries = []
        clock = stopwatch()
        result = self.synthesizer.speak_text_async(text).get()
        elapsed = clock.lap()

        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            details = result.cancellation_details

            return synthesis_outcome(elapsed_seconds=elapsed, pcm=b"", error=f"{details.reason} {details.error_details}")

        return synthesis_outcome(elapsed_seconds=elapsed, pcm=result.audio_data, error="")


def run_voice(session: voice_session, voice: str, samples: list[sample], rate: int, out: Path) -> list[synthesis_record]:
    records: list[synthesis_record] = []

    for index, item in enumerate(samples):
        outcome = session.speak(item.text)
        wav = out / voice / f"{index:02d}-{item.label}.wav"

        if outcome.error == "":
            write_wav(wav, outcome.pcm, rate)

        record = synthesis_record(
            voice=voice,
            label=item.label,
            text=item.text,
            chars=len(item.text),
            round_trip_seconds=outcome.elapsed_seconds,
            audio_seconds=pcm_seconds(outcome.pcm, rate),
            sample_rate=rate,
            visemes=list(session.visemes),
            word_boundaries=list(session.word_boundaries),
            wav=str(wav) if outcome.error == "" else "",
            error=outcome.error,
        )
        dump_json(wav.with_suffix(".json"), record)
        records.append(record)

        if outcome.error == "":
            log(f"{voice} {item.label}: {outcome.elapsed_seconds:.2f}s round trip, {record.audio_seconds:.1f}s audio, {len(record.visemes)} visemes, {len(record.word_boundaries)} words")
        else:
            log(f"{voice} {item.label}: FAILED after {outcome.elapsed_seconds:.2f}s: {outcome.error}")

    return records


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--voices", nargs="+", required=True, help="e.g. ko-KR-SunHiNeural ko-KR-InJoonNeural")
    parser.add_argument("--format", choices=sorted(output_formats), default="raw16k")
    parser.add_argument("--preconnect", action="store_true", help="open the Connection before the first sentence")
    parser.add_argument("--out", type=Path, default=Path("out/tts"))
    arguments = parser.parse_args(argv)

    settings = settings_from_environment()
    output_format, rate = output_formats[arguments.format]
    log(f"voices {arguments.voices}, format {arguments.format}, region {settings.region}")

    latency_rows: list[synthesis_record] = []
    vowel_rows: list[synthesis_record] = []

    for voice in arguments.voices:
        session = voice_session(settings.key, settings.region, voice, output_format, arguments.preconnect)
        latency_rows += run_voice(session, voice, by_length, rate, arguments.out)
        vowel_rows += run_voice(session, voice, by_vowel, rate, arguments.out)

    print("\n## round trip by sentence length (first row of each voice includes the first-connection cost)\n")
    print(
        table(
            [
                [
                    row.voice,
                    row.label,
                    str(row.chars),
                    f"{row.round_trip_seconds:.2f}",
                    f"{row.audio_seconds:.1f}",
                    str(len(row.visemes)),
                    row.error[:60],
                ]
                for row in latency_rows
            ],
            ["voice", "len", "chars", "round trip s", "audio s", "visemes", "error"],
        )
    )

    print("\n## viseme time share per isolated vowel (dominant IDs; the one that changes between rows is the vowel)\n")

    for row in vowel_rows:
        if row.error != "":
            print(f"{row.voice:34} {row.label:4} FAILED: {row.error[:80]}")
            continue

        spans = spans_from_visemes(row.visemes, row.audio_seconds * 1000)
        print(f"{row.voice:34} {row.label:4} {format_time_share(viseme_time_share(spans))}")

    dump_json(arguments.out / "summary.json", {"latency": latency_rows, "vowels": vowel_rows})
    log(f"wrote {arguments.out}")


if __name__ == "__main__":
    main(sys.argv[1:])
