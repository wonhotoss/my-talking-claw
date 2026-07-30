import io
import os
import re
from dataclasses import dataclass

import numpy

from app import viseme


# melo.api.TTS.tts_to_file's own defaults. The previous implementation called
# that method, so pinning them here keeps the synthesized audio identical.
melo_sdp_ratio = 0.2
melo_noise_scale = 0.6
melo_noise_scale_w = 0.8
# melo.api.TTS.audio_numpy_concat pads *every* segment (the last one included)
# with this much silence. The expression below is copied verbatim so the sample
# count - and therefore the timeline - matches exactly.
melo_segment_silence_seconds = 0.05


@dataclass(frozen=True)
class speech_segment:
    """One sentence piece and the exact audio range it occupies.

    MeloTTS already splits the text into pieces and synthesizes them one at a
    time, so the sample offsets are known for free. This is what lets the face
    show subtitles that are correct at sentence boundaries.
    """

    start: float
    end: float
    text: str


@dataclass(frozen=True)
class speech_result:
    audio: bytes
    sample_rate: int
    duration: float
    visemes: list[viseme.viseme_span]
    segments: list[speech_segment]
    envelope: list[float]
    envelope_hz: int


@dataclass(frozen=True)
class piece_result:
    samples: numpy.ndarray
    symbols: list[str]
    frame_counts: list[int]


class tts_engine:
    """On-device Korean text-to-speech backed by MeloTTS.

    MeloTTS (torch + mecab-ko g2p) is imported lazily on first use so this
    module imports cleanly on hosts where the engine is not installed (e.g. the
    Windows dev box); the service is meant to run in the provided Docker image
    or on Linux. Configuration via environment:

    - TTS_LANGUAGE           (default KR)
    - TTS_SPEAKER            (default KR; key into the model's speaker table)
    - TTS_DEVICE             (default cpu; cuda on a GPU machine)
    - TTS_SPEED              (default 1.0)
    - TTS_VISEME_MIN_SECONDS (default 0.04; viseme spans shorter than this are
                              absorbed into a neighbour to stop visual flicker)
    - TTS_ENVELOPE_HZ        (default 50; RMS loudness samples per second)

    The viseme table in app/viseme.py is Korean-only. With TTS_LANGUAGE set to
    another language the audio and envelope are still correct, but every symbol
    falls through to the neutral shape.
    """

    def __init__(self) -> None:
        self.engine = os.environ.get("TTS_ENGINE", "melotts")
        self.language = os.environ.get("TTS_LANGUAGE", "KR")
        self.speaker = os.environ.get("TTS_SPEAKER", "KR")
        self.device = os.environ.get("TTS_DEVICE", "cpu")
        self.speed = float(os.environ.get("TTS_SPEED", "1.0"))
        self.viseme_minimum_seconds = float(
            os.environ.get("TTS_VISEME_MIN_SECONDS", "0.04")
        )
        self.envelope_hz = int(os.environ.get("TTS_ENVELOPE_HZ", "50"))
        # Loading the model is expensive, so it is cached lazily on first use.
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from melo.api import TTS

            self._model = TTS(language=self.language, device=self.device)

        return self._model

    @property
    def id_to_symbol(self) -> dict[int, str]:
        return {index: symbol for symbol, index in self.model.symbol_to_id.items()}

    def synthesize(self, text: str) -> bytes:
        return self.speak(text).audio

    def speak(self, text: str) -> speech_result:
        """Synthesize `text` and return the WAV together with its alignment.

        This is melo.api.TTS.tts_to_file's loop, inlined. The only reason to
        inline it is that tts_to_file discards SynthesizerTrn.infer's attention
        matrix, which is the phoneme alignment we need. Everything else -
        sentence splitting, inference arguments, inter-piece silence, sample
        rate, WAV subtype - is deliberately identical to upstream.
        """
        import soundfile
        import torch

        model = self.model
        sample_rate = int(model.hps.data.sampling_rate)
        speaker_id = model.hps.data.spk2id[self.speaker]
        add_blank = bool(model.hps.data.add_blank)
        # Verbatim from melo.api.TTS.audio_numpy_concat.
        silence_samples = int((sample_rate * melo_segment_silence_seconds) / self.speed)

        pieces = model.split_sentences_into_pieces(text, model.language, True)

        if len(pieces) == 0:
            raise RuntimeError(f"melotts split produced no sentence pieces for {text!r}")

        blocks: list[numpy.ndarray] = []
        spans: list[viseme.viseme_span] = []
        segments: list[speech_segment] = []
        offset_samples = 0

        for piece in pieces:
            piece_start_samples = offset_samples
            result = self.infer_piece(piece, speaker_id)
            total_frames = sum(result.frame_counts)

            # The vocoder upsamples each mel frame by exactly hop_length, so
            # this division is exact. If a MeloTTS bump ever breaks that, crash
            # here rather than ship a timeline that silently drifts.
            if total_frames == 0 or len(result.samples) % total_frames != 0:
                raise RuntimeError(
                    "melotts alignment mismatch: "
                    f"{len(result.samples)} samples over {total_frames} frames"
                )

            seconds_per_frame = (len(result.samples) / total_frames) / sample_rate
            blank_indices = (
                frozenset(range(0, len(result.symbols), 2))
                if add_blank
                else frozenset()
            )

            spans += viseme.build_spans(
                result.symbols,
                result.frame_counts,
                blank_indices,
                seconds_per_frame,
                offset_samples / sample_rate,
            )
            offset_samples += len(result.samples)

            spans.append(
                viseme.viseme_span(
                    start=offset_samples / sample_rate,
                    end=(offset_samples + silence_samples) / sample_rate,
                    viseme=viseme.viseme_silence,
                )
            )
            offset_samples += silence_samples

            # The trailing silence belongs to the segment, so segments tile the
            # whole timeline and a subtitle stays up through the pause after
            # its sentence instead of blinking out.
            segments.append(
                speech_segment(
                    start=piece_start_samples / sample_rate,
                    end=offset_samples / sample_rate,
                    text=piece,
                )
            )

            blocks += [
                result.samples,
                numpy.zeros(silence_samples, dtype=numpy.float32),
            ]

        torch.cuda.empty_cache()

        # Equivalent to melo's audio_numpy_concat without its 300k-element
        # .tolist() round trip: float32 -> float64 -> float32 is exact, and the
        # padding is integer zero.
        audio = numpy.concatenate(blocks).astype(numpy.float32)
        duration = len(audio) / sample_rate

        buffer = io.BytesIO()
        # soundfile's default WAV subtype is PCM_16, which is exactly what
        # melo.api wrote before (day-4 verified 44.1kHz / mono / 16bit).
        soundfile.write(buffer, audio, sample_rate, format="WAV")

        return speech_result(
            audio=buffer.getvalue(),
            sample_rate=sample_rate,
            duration=duration,
            visemes=viseme.merge_spans(spans, self.viseme_minimum_seconds, duration),
            segments=segments,
            envelope=viseme.rms_envelope(audio, sample_rate, self.envelope_hz),
            envelope_hz=self.envelope_hz,
        )

    def infer_piece(self, piece: str, speaker_id: int) -> piece_result:
        """Run one sentence piece through the model, keeping the alignment."""
        import torch
        from melo import utils as melo_utils

        model = self.model
        device = model.device

        # melo.api.tts_to_file applies this to EN / ZH_MIX_EN only. It is a
        # no-op for KR, and kept so an engine language swap behaves the same.
        if model.language in ("EN", "ZH_MIX_EN"):
            piece = re.sub(r"([a-z])([A-Z])", r"\1 \2", piece)

        bert, ja_bert, phones, tones, lang_ids = melo_utils.get_text_for_tts_infer(
            piece, model.language, model.hps, device, model.symbol_to_id
        )
        symbols = [self.id_to_symbol[token_id] for token_id in phones.tolist()]

        with torch.no_grad():
            output, attn, _, _ = model.model.infer(
                phones.to(device).unsqueeze(0),
                torch.LongTensor([phones.size(0)]).to(device),
                torch.LongTensor([speaker_id]).to(device),
                tones.to(device).unsqueeze(0),
                lang_ids.to(device).unsqueeze(0),
                bert.to(device).unsqueeze(0),
                ja_bert.to(device).unsqueeze(0),
                sdp_ratio=melo_sdp_ratio,
                noise_scale=melo_noise_scale,
                noise_scale_w=melo_noise_scale_w,
                length_scale=1.0 / self.speed,
            )

            if attn.dim() != 4:
                raise RuntimeError(
                    f"unexpected melotts alignment rank: {tuple(attn.shape)}"
                )

            samples = output[0, 0].data.cpu().float().numpy()
            # attn is [1, 1, mel_frames, tokens]. Reduce on-device so only a
            # few hundred ints cross to the host - the full matrix is ~600KB
            # per sentence and on cuda would force a sync plus a transfer.
            frame_counts = attn.sum(2)[0, 0].round().long().cpu().tolist()

        if len(frame_counts) != len(symbols):
            raise RuntimeError(
                f"melotts alignment width {len(frame_counts)} != {len(symbols)} tokens"
            )

        return piece_result(
            samples=samples, symbols=symbols, frame_counts=frame_counts
        )
