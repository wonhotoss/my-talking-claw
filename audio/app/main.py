"""Entry point: wire the loop from the environment and run it.

Configuration (all optional; defaults are the single-RPi layout of rpi-up.sh):

  BACKEND_URL / VOICE_URL / TTS_URL   http://127.0.0.1:8000 / :8100 / :8201
  PUSH_TOKEN                          backend's PUSH_TOKEN (for /api/device)
  AUDIO_INPUT_DEVICE                  PortAudio device index or name substring; empty = default
  AUDIO_OUTPUT_DEVICE                 same, for playback
  AUDIO_OUTPUT_RATE                   16000 (the ReSpeaker's USB firmware is fixed there)
  AUDIO_OUTPUT_CHANNELS               2
  AUDIO_PLAYBACK_GAIN_DB              -12 (day-9: the user's "enough" level, and below mic saturation)
  WAKE_WORD_MODEL                     hey_jarvis,alexa,hey_mycroft - comma-separated openWakeWord stock
                                      models, any of them wakes the device (placeholders, see wake_word.py)
  WAKE_WORD_THRESHOLD                 0.5
  WAKE_WORD_THRESHOLD_WHILE_SPEAKING  0.2 (barge-in without AEC: day-9 live, the user over our TTS
                                      peaked at 0.24 while our TTS alone peaked at 0.00)
  VAD_THRESHOLD                       0.5
  STT_LANGUAGE                        ko
  ENDPOINT_TRAILING_SILENCE_MS        700
  ENDPOINT_NO_SPEECH_TIMEOUT_MS       5000
  ENDPOINT_MAX_UTTERANCE_MS           15000
  ENDPOINT_MIN_SPEECH_MS              300
  AUDIO_DUMP_DIR                      empty = keep no recordings; a directory keeps each utterance as WAV
  AUDIO_WAKE_CHIME                    true: a short tone right after the wake word (the "I'm listening" cue)
  AUDIO_DIAGNOSTICS_SECONDS           5: log mic peak level + peak wake score this often; 0 = off
"""

import os

import httpx

from app.capture import microphone
from app.device_client import device_client
from app.endpointer import endpoint_limits
from app.loop import audio_loop, log, loop_settings
from app.player import portaudio_sink
from app.vad import silero_vad
from app.wake_word import openwakeword_detector

capture_rate = 16000
# 80ms: openWakeWord's native frame.
capture_frame_samples = 1280


def env(name: str, fallback: str) -> str:
    return os.environ.get(name, fallback)


def device_from_env(name: str) -> int | str | None:
    value = env(name, "")

    if value == "":
        return None

    return int(value) if value.isdigit() else value


def main() -> None:
    settings = loop_settings(
        sample_rate=capture_rate,
        frame_samples=capture_frame_samples,
        language=env("STT_LANGUAGE", "ko"),
        playback_gain_db=float(env("AUDIO_PLAYBACK_GAIN_DB", "-12")),
        dump_dir=env("AUDIO_DUMP_DIR", "") or None,
        wake_chime=env("AUDIO_WAKE_CHIME", "true").lower() == "true",
        wake_threshold=float(env("WAKE_WORD_THRESHOLD", "0.5")),
        diagnostics_seconds=float(env("AUDIO_DIAGNOSTICS_SECONDS", "5")),
        wake_threshold_while_speaking=float(env("WAKE_WORD_THRESHOLD_WHILE_SPEAKING", "0.2")),
        limits=endpoint_limits(
            trailing_silence_ms=int(env("ENDPOINT_TRAILING_SILENCE_MS", "700")),
            no_speech_timeout_ms=int(env("ENDPOINT_NO_SPEECH_TIMEOUT_MS", "5000")),
            max_utterance_ms=int(env("ENDPOINT_MAX_UTTERANCE_MS", "15000")),
            min_speech_ms=int(env("ENDPOINT_MIN_SPEECH_MS", "300")),
        ),
    )
    vad_threshold = float(env("VAD_THRESHOLD", "0.5"))

    loop = audio_loop(
        client=device_client(
            http=httpx.Client(),
            backend_url=env("BACKEND_URL", "http://127.0.0.1:8000"),
            voice_url=env("VOICE_URL", "http://127.0.0.1:8100"),
            tts_url=env("TTS_URL", "http://127.0.0.1:8201"),
            push_token=env("PUSH_TOKEN", ""),
        ),
        source=microphone(device_from_env("AUDIO_INPUT_DEVICE"), capture_rate, capture_frame_samples),
        wake=openwakeword_detector(env("WAKE_WORD_MODEL", "hey_jarvis,alexa,hey_mycroft").split(",")),
        make_vad=lambda: silero_vad(vad_threshold),
        sink=portaudio_sink(
            device_from_env("AUDIO_OUTPUT_DEVICE"),
            int(env("AUDIO_OUTPUT_RATE", "16000")),
            int(env("AUDIO_OUTPUT_CHANNELS", "2")),
        ),
        settings=settings,
    )

    log(f"ready: wake words {env('WAKE_WORD_MODEL', 'hey_jarvis,alexa,hey_mycroft')}, gain {settings.playback_gain_db}dB")
    loop.run()


if __name__ == "__main__":
    main()
