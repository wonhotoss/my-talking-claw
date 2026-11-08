import struct
import wave
from pathlib import Path

import pytest

from probe.common import frame_bytes, frames_of, pcm_seconds, peak_dbfs, read_pcm, sample_rate, table, write_wav


def test_frames_cover_everything_with_a_short_tail() -> None:
    pcm = bytes(frame_bytes * 2 + 10)
    frames = list(frames_of(pcm, frame_bytes))

    assert [len(frame) for frame in frames] == [frame_bytes, frame_bytes, 10]
    assert b"".join(frames) == pcm


def test_pcm_seconds() -> None:
    assert pcm_seconds(bytes(sample_rate * 2), sample_rate) == 1.0


def test_peak_dbfs() -> None:
    assert peak_dbfs(b"") == -100.0
    assert peak_dbfs(bytes(4)) == -100.0
    assert peak_dbfs(struct.pack("<2h", 0, -32768)) == pytest.approx(0.0)
    assert peak_dbfs(struct.pack("<2h", 3277, 0)) == pytest.approx(-20.0, abs=0.01)


def test_wav_round_trip_and_format_check(tmp_path: Path) -> None:
    pcm = struct.pack("<4h", 1, -2, 3, -4)
    path = tmp_path / "ok.wav"
    write_wav(path, pcm, sample_rate)

    assert read_pcm(path) == pcm

    wrong = tmp_path / "stereo.wav"

    with wave.open(str(wrong), "wb") as target:
        target.setnchannels(2)
        target.setsampwidth(2)
        target.setframerate(sample_rate)
        target.writeframes(pcm)

    with pytest.raises(ValueError, match="expected"):
        read_pcm(wrong)


def test_table_pads_columns() -> None:
    text = table([["a", "bbb"], ["cc", "d"]], ["x", "yy"])

    assert text.splitlines() == ["x   yy ", "--  ---", "a   bbb", "cc  d  "]
