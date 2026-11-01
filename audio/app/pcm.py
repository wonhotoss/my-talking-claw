"""Small PCM helpers shared by capture, playback and the STT upload.

int16 mono numpy arrays everywhere; the sample rate travels alongside.
"""

import io
import wave

import numpy as np


def wav_bytes(samples: np.ndarray, sample_rate: int) -> bytes:
    """A mono 16-bit WAV, as the voice service's /transcribe expects."""
    buffer = io.BytesIO()

    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(samples.astype(np.int16).tobytes())

    return buffer.getvalue()


def read_wav(data: bytes) -> tuple[np.ndarray, int]:
    """Mono int16 samples and their rate; a stereo file keeps its first channel."""
    with wave.open(io.BytesIO(data), "rb") as reader:
        channels = reader.getnchannels()
        width = reader.getsampwidth()
        rate = reader.getframerate()
        frames = reader.readframes(reader.getnframes())

    if width != 2:
        raise ValueError(f"expected 16-bit PCM, got {width * 8}-bit")

    samples = np.frombuffer(frames, dtype=np.int16)

    return samples[::channels], rate


def resample(samples: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    """Linear resampling. Speech from Piper at 22050 -> 16000 for the ReSpeaker;
    not hi-fi, and it does not need to be."""
    if from_rate == to_rate:
        return samples

    count = int(round(len(samples) * to_rate / from_rate))
    positions = np.linspace(0, len(samples) - 1, count)

    return np.interp(positions, np.arange(len(samples)), samples.astype(np.float32)).astype(np.int16)


def apply_gain(samples: np.ndarray, gain_db: float) -> np.ndarray:
    factor = 10 ** (gain_db / 20)

    return np.clip(samples.astype(np.float32) * factor, -32768, 32767).astype(np.int16)


def dbfs(samples: np.ndarray) -> float:
    rms = float(np.sqrt(np.mean(samples.astype(np.float32) ** 2))) if len(samples) > 0 else 0.0

    return 20 * np.log10(max(rms, 1.0) / 32768)


def tone(frequency_hz: float, seconds: float, sample_rate: int, gain_db: float) -> np.ndarray:
    """A short sine with 10ms fades, for the wake-word chime."""
    count = int(seconds * sample_rate)
    t = np.arange(count) / sample_rate
    fade = min(int(0.01 * sample_rate), count // 2)
    envelope = np.ones(count)
    envelope[:fade] = np.linspace(0, 1, fade)
    envelope[count - fade :] = np.linspace(1, 0, fade)
    samples = np.sin(2 * np.pi * frequency_hz * t) * envelope * 32767

    return apply_gain(samples.astype(np.int16), gain_db)
