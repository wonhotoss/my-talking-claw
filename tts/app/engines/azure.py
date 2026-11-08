"""Azure Speech (neural ko-KR voices) engine adapter.

Unlike the two local engines, Azure hands back no phonemes: one synthesis
delivers the PCM plus a stream of viseme_received events, each a (viseme ID,
start offset). The IDs become the piece's symbols and the gaps between the
offsets its sample counts, so speech.build_speech and the shaping in
app/viseme.py apply unchanged - including the alignment check, which is what
would catch an offset drifting past the audio.

Measured 2026-11-08 (day-11.md, Azure probe): over 42 syntheses the offsets
were monotonic, none lay beyond the audio, the first event was silence at
0-50 ms and the last event was silence too, so the trailing pause is covered.
The event stream is deterministic for a given voice and text, which is what
lets tests/fixtures/azure replay real ones without the network.

One SpeechSynthesizer lives for the process, created on first use: the
connection is the cost (first request 0.3-0.5 s, later ones 0.1-0.3 s). The
server serialises requests with its lock, so the per-request event list needs
no further guarding. The SDK is imported lazily so this module, like the
other adapters, loads on a host where it is not installed.

Environment (shared knobs are documented on speech.speech_engine; TTS_SPEED
is sent as the SSML prosody rate):

- AZURE_SPEECH_KEY     required
- AZURE_SPEECH_REGION  required, e.g. koreacentral
- AZURE_TTS_VOICE      (default ko-KR-SunHiNeural; InJoon and
                        HyunsuMultilingual measured equally well)
"""

import os
from collections.abc import Iterable
from dataclasses import dataclass
from xml.sax.saxutils import escape

import numpy

from app import speech, viseme


default_voice = "ko-KR-SunHiNeural"
# Raw16Khz16BitMonoPcm: the device sink's rate, so nothing is resampled on the
# way to the ReSpeaker. Neural voices are 24 kHz natively; the probe heard no
# difference worth the 1.5x bytes on a 16 kHz speaker.
output_sample_rate = 16000
# Azure reports audio offsets in 100 ns ticks.
ticks_per_second = 10_000_000


@dataclass(frozen=True)
class viseme_event:
    viseme_id: int
    offset_ticks: int


def ssml(text: str, voice: str, speed: float, language: str) -> str:
    return (
        f'<speak version="1.0" xmlns="http://www.w3.org/2001/10/synthesis" xml:lang="{language}">'
        f'<voice name="{voice}"><prosody rate="{speed:.2f}">{escape(text)}</prosody></voice>'
        "</speak>"
    )


def pcm_to_samples(pcm: bytes) -> numpy.ndarray:
    """16-bit little-endian mono PCM to float32 in [-1, 1)."""
    return numpy.frombuffer(pcm, dtype="<i2").astype(numpy.float32) / 32768.0


def alignment(
    events: list[viseme_event], total_samples: int, sample_rate: int
) -> tuple[list[str], list[int]]:
    """Symbols and sample counts that tile exactly `total_samples`.

    Each event only says when its posture starts, so it ends where the next
    one starts and the last runs to the end of the audio. A gap before the
    first event becomes silence so the tiling starts at zero. Events are
    sorted because the SDK raises them on its own thread.
    """
    ordered = sorted(events, key=lambda event: event.offset_ticks)

    if len(ordered) == 0:
        return [viseme.viseme_silence_id], [total_samples]

    starts = [round(event.offset_ticks * sample_rate / ticks_per_second) for event in ordered]

    if starts[-1] > total_samples:
        # A posture that starts after the audio ends is a timeline the face
        # would walk off the end of. build_speech would reject the sum anyway;
        # say what actually happened.
        raise RuntimeError(
            f"viseme offset {starts[-1]} samples lies beyond the audio ({total_samples})"
        )

    leading = [(viseme.viseme_silence_id, starts[0])] if starts[0] > 0 else []
    ends = starts[1:] + [total_samples]
    pairs = leading + [
        (str(event.viseme_id), end - start)
        for event, start, end in zip(ordered, starts, ends)
    ]

    return [symbol for symbol, _ in pairs], [count for _, count in pairs]


class azure_engine(speech.speech_engine):
    engine = "azure"
    language = "ko-KR"
    sample_rate = output_sample_rate
    viseme_table = viseme.azure_visemes
    # The last viseme event is silence and runs to the end of the audio, so
    # the utterance already ends in a pause.
    silence_seconds = 0.0

    def __init__(self) -> None:
        super().__init__()
        # A missing key raises KeyError on its own; that is the message we want.
        self.key = os.environ["AZURE_SPEECH_KEY"]
        self.region = os.environ["AZURE_SPEECH_REGION"]
        self.voice = os.environ.get("AZURE_TTS_VOICE", default_voice)
        # The synthesizer holds the connection, which is the expensive part.
        # This is the performance-critical exception to the no-cached-state rule.
        self._synthesizer = None
        self.events: list[viseme_event] = []

    @property
    def synthesizer(self):
        if self._synthesizer is None:
            import azure.cognitiveservices.speech as speechsdk

            config = speechsdk.SpeechConfig(subscription=self.key, region=self.region)
            config.set_speech_synthesis_output_format(
                speechsdk.SpeechSynthesisOutputFormat.Raw16Khz16BitMonoPcm
            )
            # audio_config=None: the PCM comes back in the result, not a speaker.
            synthesizer = speechsdk.SpeechSynthesizer(speech_config=config, audio_config=None)
            synthesizer.viseme_received.connect(
                lambda event: self.events.append(
                    viseme_event(viseme_id=event.viseme_id, offset_ticks=event.audio_offset)
                )
            )
            self._synthesizer = synthesizer

        return self._synthesizer

    def warm_up(self) -> None:
        # Measured on the dev box: the first /speak after start took 1.4 s
        # (SDK import, synthesizer, TLS + websocket), the next ones 0.2 s.
        # Opening the connection here moves that 1.2 s off the first turn.
        import azure.cognitiveservices.speech as speechsdk

        speechsdk.Connection.from_speech_synthesizer(self.synthesizer).open(True)

    def synthesize_events(self, text: str) -> tuple[bytes, list[viseme_event]]:
        """One round trip: the PCM and every viseme event it raised."""
        import azure.cognitiveservices.speech as speechsdk

        self.events = []
        result = self.synthesizer.speak_ssml_async(
            ssml(text, self.voice, self.speed, self.language)
        ).get()

        if result.reason != speechsdk.ResultReason.SynthesizingAudioCompleted:
            details = result.cancellation_details
            raise RuntimeError(
                f"azure synthesis {result.reason.name}: {details.reason} {details.error_details}"
            )

        return result.audio_data, list(self.events)

    def pieces(self, text: str) -> Iterable[speech.speech_piece]:
        # One piece per request. The backend already hands over one sentence
        # per /speak, so the piece is the sentence and the subtitle segment is
        # the whole text.
        pcm, events = self.synthesize_events(text)
        samples = pcm_to_samples(pcm)
        symbols, sample_counts = alignment(events, len(samples), self.sample_rate)

        yield speech.speech_piece(
            text=text,
            samples=samples,
            symbols=symbols,
            sample_counts=sample_counts,
        )
