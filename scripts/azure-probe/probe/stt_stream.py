"""Probe: what streaming STT buys - the A-2 figure.

    uv run python -m probe.stt_stream recording.wav --language ko-KR
    uv run python -m probe.stt_stream recording.wav --language ko-KR --preconnect
    uv run python -m probe.stt_stream recording.wav --language ko-KR --pace burst

Replays a WAV into a PushAudioInputStream the way the loop would: one 80 ms
frame every 80 ms (--pace realtime), with continuous recognition already
running. When the last frame is in, the probe does what the endpointer's
`done` will do - stop recognition - and measures how long the final text takes
to arrive after that. That gap is the whole STT cost the user would feel.

Also recorded, each against the same clock (t=0 is the first frame pushed):
- how long `start_continuous_recognition` itself took (connection setup),
  with and without a Connection pre-opened beforehand;
- every `recognizing` partial, to see how far behind the audio they run;
- whether the tail arrived as `recognized` or only ever as a partial
  (the fallback case in analysis.gather_transcript).

--pace burst pushes the whole clip at once: the service's own processing
time with network, minus the pacing. The difference against realtime says
how much of the work really overlaps the speech.
"""

import argparse
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

import azure.cognitiveservices.speech as speechsdk

from probe.analysis import gather_transcript, partial
from probe.common import (
    channels,
    dump_json,
    frame_bytes,
    frame_seconds,
    frames_of,
    log,
    pcm_seconds,
    read_pcm,
    sample_rate,
    sample_width_bytes,
    settings_from_environment,
)


@dataclass
class session_log:
    """Everything the callbacks saw, stamped on the shared clock."""

    partials: list[partial] = field(default_factory=list)
    recognized: list[str] = field(default_factory=list)
    recognized_at: list[float] = field(default_factory=list)
    canceled: str = ""
    # When the service said the audio was over (canceled/EndOfStream): the
    # moment an adapter knows no more text is coming.
    ended_at: float = -1.0
    stopped: threading.Event = field(default_factory=threading.Event)


@dataclass(frozen=True)
class stream_result:
    file: str
    clip_seconds: float
    pace: str
    preconnect: bool
    start_seconds: float
    audio_end_seconds: float
    final_text_seconds: float
    tail_after_audio_end_seconds: float
    session_end_seconds: float
    session_end_after_audio_end_seconds: float
    first_partial_seconds: float
    partial_count: int
    recognized_count: int
    text: str
    tail_was_partial_only: bool


def run_session(
    config: speechsdk.SpeechConfig,
    pcm: bytes,
    pace: str,
    preconnect: bool,
) -> tuple[session_log, float, float, float, float]:
    """Returns the log and (start, audio_end, final_text, first_partial) seconds."""
    stream_format = speechsdk.audio.AudioStreamFormat(
        samples_per_second=sample_rate,
        bits_per_sample=sample_width_bytes * 8,
        channels=channels,
    )
    stream = speechsdk.audio.PushAudioInputStream(stream_format=stream_format)
    recognizer = speechsdk.SpeechRecognizer(
        speech_config=config,
        audio_config=speechsdk.audio.AudioConfig(stream=stream),
    )

    record = session_log()
    origin = time.monotonic()

    def since_origin() -> float:
        return time.monotonic() - origin

    def on_recognizing(event: speechsdk.SpeechRecognitionEventArgs) -> None:
        record.partials.append(partial(at_seconds=since_origin(), text=event.result.text))

    def on_recognized(event: speechsdk.SpeechRecognitionEventArgs) -> None:
        # NoMatch arrives through here too, with empty text; keep the stamp so
        # the row shows that the service answered, just with nothing.
        record.recognized.append(event.result.text)
        record.recognized_at.append(since_origin())

    def on_canceled(event: speechsdk.SpeechRecognitionCanceledEventArgs) -> None:
        details = event.cancellation_details

        # EndOfStream is not a failure: it is how the SDK reports that the
        # closed stream has been consumed and every result for it has fired.
        if details.reason == speechsdk.CancellationReason.EndOfStream:
            record.ended_at = since_origin()
        else:
            record.canceled = f"{details.reason} {details.error_details}"

        record.stopped.set()

    def on_stopped(event: speechsdk.SessionEventArgs) -> None:
        record.stopped.set()

    recognizer.recognizing.connect(on_recognizing)
    recognizer.recognized.connect(on_recognized)
    recognizer.canceled.connect(on_canceled)
    recognizer.session_stopped.connect(on_stopped)

    if preconnect:
        # What the loop would do at the wake word, before the user speaks.
        connection = speechsdk.Connection.from_recognizer(recognizer)
        connection.open(for_continuous_recognition=True)
        log(f"pre-opened connection in {since_origin():.2f}s")

    start_clock = time.monotonic()
    recognizer.start_continuous_recognition()
    start_seconds = time.monotonic() - start_clock

    # The clock the results are measured against starts with the first frame,
    # not with the connection: that is when the user starts talking.
    origin = time.monotonic()
    frames = list(frames_of(pcm, frame_bytes))

    if pace == "realtime":
        for index, frame in enumerate(frames):
            due = origin + index * frame_seconds
            delay = due - time.monotonic()

            if delay > 0:
                time.sleep(delay)

            stream.write(frame)
    else:
        for frame in frames:
            stream.write(frame)

    audio_end_seconds = since_origin()
    # The endpointer's "done": close the stream. The SDK tells the service the
    # audio is over, the service finalises what is pending, and the session
    # ends on its own (canceled/EndOfStream, then session_stopped). Calling
    # stop_continuous_recognition here instead aborts the pending results -
    # first run of this probe lost the text on two of three burst runs that way.
    stream.close()

    if not record.stopped.wait(timeout=15.0):
        raise RuntimeError("the session did not end within 15s of closing the stream")

    if record.canceled != "":
        raise RuntimeError(f"canceled: {record.canceled}")

    # Cleanup only; everything has already arrived.
    recognizer.stop_continuous_recognition()
    final_text_seconds = max(record.recognized_at, default=since_origin())
    first_partial_seconds = record.partials[0].at_seconds if record.partials else -1.0

    return record, start_seconds, audio_end_seconds, final_text_seconds, first_partial_seconds


def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("wav", type=Path, help="a 16 kHz mono recording (one utterance)")
    parser.add_argument("--language", required=True, help="BCP-47, e.g. ko-KR")
    parser.add_argument("--pace", choices=["realtime", "burst"], default="realtime")
    parser.add_argument("--preconnect", action="store_true", help="open the Connection before starting recognition")
    parser.add_argument("--repeat", type=int, default=1, help="run the session this many times (connection cache effects)")
    parser.add_argument("--out", type=Path, default=Path("out/stt_stream.json"))
    arguments = parser.parse_args(argv)

    settings = settings_from_environment()
    config = speechsdk.SpeechConfig(subscription=settings.key, region=settings.region)
    config.speech_recognition_language = arguments.language

    pcm = read_pcm(arguments.wav)
    clip = pcm_seconds(pcm, sample_rate)
    log(f"{arguments.wav.name}: {clip:.1f}s, pace {arguments.pace}, preconnect {arguments.preconnect}, region {settings.region}")

    rows: list[stream_result] = []

    for run in range(arguments.repeat):
        record, start_seconds, audio_end, final_at, first_partial = run_session(
            config, pcm, arguments.pace, arguments.preconnect
        )
        text = gather_transcript(record.recognized, record.partials)
        row = stream_result(
            file=arguments.wav.name,
            clip_seconds=clip,
            pace=arguments.pace,
            preconnect=arguments.preconnect,
            start_seconds=start_seconds,
            audio_end_seconds=audio_end,
            final_text_seconds=final_at,
            tail_after_audio_end_seconds=final_at - audio_end,
            session_end_seconds=record.ended_at,
            session_end_after_audio_end_seconds=record.ended_at - audio_end,
            first_partial_seconds=first_partial,
            partial_count=len(record.partials),
            recognized_count=len([piece for piece in record.recognized if piece != ""]),
            text=text,
            tail_was_partial_only=all(piece == "" for piece in record.recognized),
        )
        rows.append(row)

        print(f"run {run + 1}: start {row.start_seconds:.2f}s | audio end {row.audio_end_seconds:.2f}s | "
              f"final text {row.final_text_seconds:.2f}s | **tail {row.tail_after_audio_end_seconds:.2f}s** | "
              f"session end +{row.session_end_after_audio_end_seconds:.2f}s | "
              f"first partial {row.first_partial_seconds:.2f}s | partials {row.partial_count} | "
              f"recognized {row.recognized_count}{' (partial-only tail!)' if row.tail_was_partial_only else ''}")
        print(f"       text: {row.text}")

        for item in record.partials:
            print(f"       {item.at_seconds:6.2f}s  recognizing  {item.text}")

        for piece, at in zip(record.recognized, record.recognized_at):
            print(f"       {at:6.2f}s  recognized   {piece}")

    dump_json(arguments.out, rows)
    log(f"wrote {arguments.out}")


if __name__ == "__main__":
    main(sys.argv[1:])
