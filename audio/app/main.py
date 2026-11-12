"""Entry point: wire the loop from the environment and run it.

Configuration (all optional; defaults are the single-RPi layout of rpi-up.sh):

  BACKEND_URL / VOICE_URL / TTS_URL   http://127.0.0.1:8000 / :8100 / :8201
  PUSH_TOKEN                          backend's PUSH_TOKEN (for /api/device)
  AUDIO_INPUT_DEVICE                  PortAudio device index or name substring; empty = default
  AUDIO_OUTPUT_DEVICE                 same, for playback
  AUDIO_OUTPUT_RATE                   16000 (the ReSpeaker's USB firmware is fixed there)
  AUDIO_OUTPUT_CHANNELS               2
  AUDIO_PLAYBACK_GAIN_DB              -12 (day-9: the user's "enough" level, and below mic saturation)
  WAKE_WORD_MODEL                     alexa - comma-separated openWakeWord stock models, any of them wakes
                                      the device. One word since 2026-11-13: the wake word is the brain's
                                      name (brain-home persona.md), changed only by the administrator
  WAKE_WORD_THRESHOLD                 0.5
  WAKE_WORD_THRESHOLD_WHILE_SPEAKING  0.2 (barge-in without AEC: day-9 live, the user over our TTS
                                      peaked at 0.24 while our TTS alone peaked at 0.00)
  VAD_THRESHOLD                       0.5
  STT_ENGINE                          whisper: the voice service on this host, whole clip after the
                                      endpoint; azure: Azure Speech streamed while recording (day-11).
                                      The voice service reads the same variable, so the phone follows.
  STT_LANGUAGE                        ko (whisper's bare code, for STT_ENGINE=whisper)
  AZURE_STT_LANGUAGE                  ko-KR (BCP-47, for STT_ENGINE=azure; with AZURE_SPEECH_KEY/REGION)
  AZURE_STT_TIMEOUT_SECONDS           5: how long the endpoint waits for Azure's final text (probe: 0.1-0.5s)
  ENDPOINT_TRAILING_SILENCE_MS        700
  ENDPOINT_NO_SPEECH_TIMEOUT_MS       5000
  ENDPOINT_MAX_UTTERANCE_MS           15000
  ENDPOINT_MIN_SPEECH_MS              300
  AUDIO_DUMP_DIR                      empty = keep no recordings; a directory keeps each utterance as WAV
  AUDIO_WAKE_CHIME                    true: a short tone right after the wake word (the "I'm listening" cue)
  AUDIO_DIAGNOSTICS_SECONDS           5: log mic peak level + peak wake score this often; 0 = off
"""

import faulthandler
import os
import signal

import httpx

from app.capture import microphone
from app.device_client import device_client
from app.endpointer import endpoint_limits
from app.loop import audio_loop, log, loop_settings
from app.player import portaudio_sink
from app.transcriber import azure_transcriber, transcriber, whisper_transcriber
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


def transcriber_from_environment(client: device_client) -> transcriber:
    engine = env("STT_ENGINE", "whisper")

    if engine == "whisper":
        return whisper_transcriber(client, capture_rate, env("STT_LANGUAGE", "ko"))

    if engine == "azure":
        return azure_transcriber(
            # Missing variables raise KeyError on their own; that is the message we want.
            key=os.environ["AZURE_SPEECH_KEY"],
            region=os.environ["AZURE_SPEECH_REGION"],
            language=env("AZURE_STT_LANGUAGE", "ko-KR"),
            sample_rate=capture_rate,
            timeout_seconds=float(env("AZURE_STT_TIMEOUT_SECONDS", "5")),
        )

    raise RuntimeError(f"unknown STT_ENGINE {engine!r}; expected 'whisper' or 'azure'")


def main() -> None:
    settings = loop_settings(
        sample_rate=capture_rate,
        frame_samples=capture_frame_samples,
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
    client = device_client(
        http=httpx.Client(),
        backend_url=env("BACKEND_URL", "http://127.0.0.1:8000"),
        voice_url=env("VOICE_URL", "http://127.0.0.1:8100"),
        tts_url=env("TTS_URL", "http://127.0.0.1:8201"),
        push_token=env("PUSH_TOKEN", ""),
    )

    loop = audio_loop(
        client=client,
        source=microphone(device_from_env("AUDIO_INPUT_DEVICE"), capture_rate, capture_frame_samples),
        wake=openwakeword_detector(env("WAKE_WORD_MODEL", "alexa").split(",")),
        make_vad=lambda: silero_vad(vad_threshold),
        sink=portaudio_sink(
            device_from_env("AUDIO_OUTPUT_DEVICE"),
            int(env("AUDIO_OUTPUT_RATE", "16000")),
            int(env("AUDIO_OUTPUT_CHANNELS", "2")),
        ),
        stt=transcriber_from_environment(client),
        settings=settings,
    )

    # kill -USR1 <pid> prints every thread's stack to stderr: the cheap way to see
    # what a hot thread is doing.
    faulthandler.register(signal.SIGUSR1, all_threads=True)
    log(f"ready: wake words {env('WAKE_WORD_MODEL', 'alexa')}, gain {settings.playback_gain_db}dB, stt {env('STT_ENGINE', 'whisper')}")
    loop.run()


if __name__ == "__main__":
    main()
