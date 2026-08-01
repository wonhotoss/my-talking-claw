"""MeloTTS-Korean engine adapter.

Moved out of app/tts_engine.py when Piper became a second engine; the model
handling is unchanged. torch and melo are imported lazily on first use so this
module imports cleanly on hosts where the engine is not installed (the Windows
dev box, and the Piper image).

Environment (shared knobs are documented on speech.speech_engine):

- TTS_LANGUAGE  (default KR; the viseme table is Korean-only)
- TTS_SPEAKER   (default KR; key into the model's speaker table)
"""

import os
import re
from collections.abc import Iterable
from dataclasses import dataclass

import numpy

from app import speech, viseme


# melo.api.TTS.tts_to_file's own defaults. The previous implementation called
# that method, so pinning them here keeps the synthesized audio identical.
melo_sdp_ratio = 0.2
melo_noise_scale = 0.6
melo_noise_scale_w = 0.8
# melo.api.TTS.audio_numpy_concat pads *every* segment (the last one included)
# with this much silence. Reproduced by speech.build_speech's silence_seconds so
# the sample count - and therefore the timeline - matches exactly.
melo_segment_silence_seconds = 0.05


@dataclass(frozen=True)
class piece_alignment:
    samples: numpy.ndarray
    symbols: list[str]
    frame_counts: list[int]


class melo_engine(speech.speech_engine):
    """On-device Korean text-to-speech backed by MeloTTS.

    The viseme table in app/viseme.py is Korean-only. With TTS_LANGUAGE set to
    another language the audio and envelope are still correct, but every symbol
    falls through to the neutral shape.
    """

    engine = "melotts"
    viseme_table = viseme.korean_jamo

    def __init__(self) -> None:
        super().__init__()
        self.language = os.environ.get("TTS_LANGUAGE", "KR")
        self.speaker = os.environ.get("TTS_SPEAKER", "KR")
        # melo divides the padding by speed, so a faster voice gets a shorter
        # gap. Upstream writes int((rate * seconds) / speed); reassociating it
        # here can differ by one sample of padding at speeds other than 1.0,
        # which the timeline absorbs because it is derived from the same count.
        self.silence_seconds = melo_segment_silence_seconds / self.speed
        # Loading the model is expensive, so it is cached lazily on first use.
        self._model = None

    @property
    def model(self):
        if self._model is None:
            from melo.api import TTS

            self._model = TTS(language=self.language, device=self.device)

        return self._model

    @property
    def sample_rate(self) -> int:  # type: ignore[override]
        return int(self.model.hps.data.sampling_rate)

    @property
    def id_to_symbol(self) -> dict[int, str]:
        return {index: symbol for symbol, index in self.model.symbol_to_id.items()}

    def pieces(self, text: str) -> Iterable[speech.speech_piece]:
        """Melo's tts_to_file loop, inlined.

        The only reason to inline it is that tts_to_file discards
        SynthesizerTrn.infer's attention matrix, which is the phoneme alignment
        we need. Everything else - sentence splitting, inference arguments,
        inter-piece silence, sample rate, WAV subtype - is deliberately
        identical to upstream.
        """
        import torch

        model = self.model
        speaker_id = model.hps.data.spk2id[self.speaker]
        add_blank = bool(model.hps.data.add_blank)

        texts = model.split_sentences_into_pieces(text, model.language, True)

        if len(texts) == 0:
            raise RuntimeError(f"melotts split produced no sentence pieces for {text!r}")

        for piece in texts:
            alignment = self.infer_piece(piece, speaker_id)
            total_frames = sum(alignment.frame_counts)

            # The vocoder upsamples each mel frame by exactly hop_length, so
            # this division is exact. If a MeloTTS bump ever breaks that, crash
            # here rather than ship a timeline that silently drifts.
            if total_frames == 0 or len(alignment.samples) % total_frames != 0:
                raise RuntimeError(
                    "melotts alignment mismatch: "
                    f"{len(alignment.samples)} samples over {total_frames} frames"
                )

            samples_per_frame = len(alignment.samples) // total_frames

            yield speech.speech_piece(
                text=piece,
                samples=alignment.samples,
                symbols=alignment.symbols,
                sample_counts=[
                    count * samples_per_frame for count in alignment.frame_counts
                ],
                blank_indices=(
                    frozenset(range(0, len(alignment.symbols), 2))
                    if add_blank
                    else frozenset()
                ),
            )

        torch.cuda.empty_cache()

    def infer_piece(self, piece: str, speaker_id: int) -> piece_alignment:
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

        return piece_alignment(
            samples=samples, symbols=symbols, frame_counts=frame_counts
        )
