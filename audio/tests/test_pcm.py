import numpy as np

from app.pcm import apply_gain, dbfs, read_wav, resample, wav_bytes


def test_wav_round_trip_keeps_samples_and_rate() -> None:
    samples = np.array([0, 1000, -1000, 32767, -32768], dtype=np.int16)

    decoded, rate = read_wav(wav_bytes(samples, 16000))

    assert rate == 16000
    assert decoded.tolist() == samples.tolist()


def test_read_wav_keeps_the_first_channel_of_stereo() -> None:
    import io
    import wave

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as writer:
        writer.setnchannels(2)
        writer.setsampwidth(2)
        writer.setframerate(16000)
        writer.writeframes(np.array([1, 2, 3, 4, 5, 6], dtype=np.int16).tobytes())

    decoded, _ = read_wav(buffer.getvalue())

    assert decoded.tolist() == [1, 3, 5]


def test_resample_scales_the_length_and_keeps_the_tone() -> None:
    seconds = np.arange(22050) / 22050
    tone = (np.sin(2 * np.pi * 440 * seconds) * 10000).astype(np.int16)

    out = resample(tone, 22050, 16000)

    assert len(out) == 16000
    # The zero crossings of a 440Hz tone over one second: ~880 either way.
    crossings = int(np.sum(np.diff(np.sign(out.astype(np.int32))) != 0))
    assert 870 <= crossings <= 890


def test_resample_is_identity_at_the_same_rate() -> None:
    samples = np.array([1, 2, 3], dtype=np.int16)

    assert resample(samples, 16000, 16000) is samples


def test_gain_attenuates_and_clips() -> None:
    quiet = apply_gain(np.array([10000], dtype=np.int16), -6.0)
    loud = apply_gain(np.array([30000], dtype=np.int16), 6.0)

    assert 5000 <= quiet[0] <= 5020
    assert loud[0] == 32767


def test_dbfs_of_full_scale_is_zero() -> None:
    assert abs(dbfs(np.array([32768 - 1, -32768], dtype=np.int16))) < 0.01
    assert dbfs(np.zeros(0, dtype=np.int16)) < -90
