"""Probe: Azure STT accuracy on recordings the device already made.

    uv run python -m probe.stt_file ~/claw-recordings --language ko-KR
    uv run python -m probe.stt_file ~/claw-recordings --language ko-KR --whisper-url http://127.0.0.1:8100

One `recognize_once` per WAV. This is the cheapest question to answer first:
does Azure hear the ReSpeaker better than whisper base beam 1 (the two
"측정 -> 제조/특정" misreads in HANDOFF.md are in that directory). With
--whisper-url the local voice service transcribes the same file so the two
texts sit side by side; without it only Azure runs.

Latency here is clip-length bound (the file is sent as a whole) and is NOT
the A-2 figure - see stt_stream.py for that.
"""

import argparse
import sys
from dataclasses import dataclass
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk
import httpx

from probe.common import (
    dump_json,
    log,
    pcm_seconds,
    peak_dbfs,
    read_pcm,
    sample_rate,
    settings_from_environment,
    stopwatch,
    table,
)


@dataclass(frozen=True)
class file_result:
    name: str
    clip_seconds: float
    peak_dbfs: float
    azure_seconds: float
    azure_reason: str
    azure_text: str
    whisper_text: str


def recognize_file(config: speechsdk.SpeechConfig, path: Path) -> tuple[float, str, str]:
    audio = speechsdk.audio.AudioConfig(filename=str(path))
    recognizer = speechsdk.SpeechRecognizer(speech_config=config, audio_config=audio)
    clock = stopwatch()
    result = recognizer.recognize_once()
    elapsed = clock.lap()

    if result.reason == speechsdk.ResultReason.Canceled:
        details = result.cancellation_details
        # Auth and quota problems surface here; the probe should not quietly
        # print 22 empty rows.
        raise RuntimeError(f"{path.name}: canceled: {details.reason} {details.error_details}")

    return elapsed, result.reason.name, result.text


def whisper_text(client: httpx.Client, base_url: str, path: Path, language: str) -> str:
    response = client.post(
        f"{base_url}/transcribe",
        files={"file": (path.name, path.read_bytes(), "audio/wav")},
        # The voice service wants whisper's bare code, not a BCP-47 tag.
        data={"language": language.split("-")[0]},
        timeout=120.0,
    )

    if response.status_code == 422:
        return "(no speech)"

    response.raise_for_status()

    return response.json()["text"]


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("directory", type=Path, help="directory of 16 kHz mono WAVs (the loop's AUDIO_DUMP_DIR)")
    parser.add_argument("--language", required=True, help="BCP-47, e.g. ko-KR")
    parser.add_argument("--whisper-url", default="", help="voice service base URL for a side-by-side column")
    parser.add_argument("--out", type=Path, default=Path("out/stt_file.json"), help="where the rows are dumped")
    arguments = parser.parse_args(argv)

    settings = settings_from_environment()
    config = speechsdk.SpeechConfig(subscription=settings.key, region=settings.region)
    config.speech_recognition_language = arguments.language

    paths = sorted(arguments.directory.glob("*.wav"))

    if len(paths) == 0:
        raise FileNotFoundError(f"no *.wav under {arguments.directory}")

    log(f"{len(paths)} files, region {settings.region}, language {arguments.language}")
    rows: list[file_result] = []

    with httpx.Client() as http:
        for path in paths:
            pcm = read_pcm(path)
            elapsed, reason, text = recognize_file(config, path)
            whisper = whisper_text(http, arguments.whisper_url, path, arguments.language) if arguments.whisper_url else ""
            rows.append(
                file_result(
                    name=path.name,
                    clip_seconds=pcm_seconds(pcm, sample_rate),
                    peak_dbfs=peak_dbfs(pcm),
                    azure_seconds=elapsed,
                    azure_reason=reason,
                    azure_text=text,
                    whisper_text=whisper,
                )
            )
            log(f"{path.name}: {elapsed:.2f}s {reason} {text!r}")

    headers = ["file", "clip s", "peak dBFS", "azure s", "reason", "azure text"] + (["whisper text"] if arguments.whisper_url else [])
    print(
        table(
            [
                [
                    row.name,
                    f"{row.clip_seconds:.1f}",
                    f"{row.peak_dbfs:.0f}",
                    f"{row.azure_seconds:.2f}",
                    row.azure_reason,
                    row.azure_text,
                ]
                + ([row.whisper_text] if arguments.whisper_url else [])
                for row in rows
            ],
            headers,
        )
    )
    dump_json(arguments.out, rows)
    log(f"wrote {arguments.out}")


if __name__ == "__main__":
    main(sys.argv[1:])
