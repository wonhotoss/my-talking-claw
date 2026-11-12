#!/usr/bin/env bash
# Day-8/9 launcher: the whole stack on one RPi, one tmux session, one window per service,
# plus the Chromium kiosk showing the face on the RPi display.
#
#   scripts/rpi-up.sh          start (or re-attach to) the stack
#   scripts/rpi-up.sh health   curl every /health
#   scripts/rpi-up.sh down     kill the tmux session (and every service with it), restore screen blanking
#
# Deviations from README defaults, all forced by this machine:
#   - agent-gateway on :3001 — the real nullclaw gateway already owns :3000 (HANDOFF "포트 3000 주의").
#   - whisper defaults to base + beam 1 (day-9 decision, see day-9.md "STT 모델 결정": on ReSpeaker
#     input tiny hallucinates and then pays a 40-50s temperature-fallback tail; base beam 1 is
#     accurate with no tail at RTF 0.65-0.74). WHISPER_MODEL=<local dir> / WHISPER_BEAM_SIZE override.
#   - tts and stt are Azure Speech by default (day-11: first sentence 0.1-0.3s vs Piper 2.5-3s; the
#     utterance streams to Azure while the user talks, text ready at the endpoint vs whisper's 6.8s).
#     The key comes from scripts/azure-probe/.env (gitignored, never inside the brain's home).
#     STT_ENGINE goes to BOTH the audio loop and the voice service, so the phone follows. Falling back
#     is manual: TTS_ENGINE=piper STT_ENGINE=whisper and rerun - no runtime fallback by decision
#     (뇌중심-로드맵.md). tts then runs natively with the env values from tts/docker-compose.yml `piper`.
#   - agent-gateway runs the day-10 SDK brain with its home in ~/claw-home (AGENT_BRAIN=cli brings back
#     the per-turn claude -p). The home's .claude/settings.json is the tool policy now.
#   - audio: the resident loop (audio/) owns the ReSpeaker; the kiosk is a viewer (#/kiosk is always one).
#   - kiosk: Chromium on labwc/Wayland (day-9.md "키오스크 1단계" for why each flag is there), at #/kiosk:
#     the face and its captions, nothing else. The face idles in events (blinks, glances, a slow drift
#     against burn-in) and costs ~10% of a core like a still face would (day-12; it runs on the GPU -
#     V3D via ANGLE/GLES). --force-prefers-reduced-motion is no longer passed: it was the day-9 fix
#     for a continuous CSS animation that no longer exists, and it froze the face (no blinks at all).
#     Screen blanking (swayidle from ~/.config/labwc/autostart) is stopped while the stack runs and
#     restarted on `down`; a reboot restores the default by itself.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
session="claw"
export PATH="$HOME/.local/bin:$PATH"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

gateway_port="${GATEWAY_PORT:-3001}"
push_token="${PUSH_TOKEN:-day8-push}"
# day-10: the brain's home (brain-home.example copied here, trusted once with `claude`).
brain_home="${BRAIN_HOME:-$HOME/claw-home}"
agent_brain="${AGENT_BRAIN:-sdk}"
whisper_model="${WHISPER_MODEL:-base}"
whisper_beam_size="${WHISPER_BEAM_SIZE:-1}"
# Azure by default since day-11 (end of speech -> first sound ~3.3 s against 14.5 s local).
# TTS_ENGINE=piper / STT_ENGINE=whisper bring the local engines back for the run.
tts_engine="${TTS_ENGINE:-azure}"
tts_voice="${AZURE_TTS_VOICE:-ko-KR-SunHiNeural}"
stt_engine="${STT_ENGINE:-azure}"
# The Azure Speech key, exported so the tmux server (started below) hands it to every window.
# One gitignored file serves the probes and the stack; the brain can Read its home but not this.
azure_env="$repo/scripts/azure-probe/.env"
if [ -f "$azure_env" ]; then
  set -a
  . "$azure_env"
  set +a
fi
kiosk_profile="$HOME/.config/claw-kiosk"
# The kiosk draws only; audio/ owns the microphone and the speaker. #/face?role=viewer is the same
# face with the console button, for debugging.
kiosk_url="${KIOSK_URL:-https://localhost:5173/#/kiosk}"
playback_gain_db="${AUDIO_PLAYBACK_GAIN_DB:--12}"
# PortAudio's default output here is the bcm2835 headphone jack; name the ReSpeaker on both sides.
audio_device="${AUDIO_DEVICE:-ReSpeaker}"
audio_dump_dir="${AUDIO_DUMP_DIR:-$HOME/claw-recordings}"
# The wake word is the brain's name (2026-11-13): one word, changed only by the administrator,
# together with the name in the brain home's persona.md.
wake_word_model="${WAKE_WORD_MODEL:-alexa}"

health() {
  for pair in "backend http://127.0.0.1:8000/health" \
              "gateway http://127.0.0.1:${gateway_port}/health" \
              "voice   http://127.0.0.1:8100/health" \
              "tts     http://127.0.0.1:8201/health"; do
    set -- $pair
    printf '%-8s %s\n' "$1" "$(curl -s -m 5 "$2" || echo '(down)')"
  done
  printf '%-8s %s\n' frontend "$(curl -sk -m 5 -o /dev/null -w '%{http_code}' https://127.0.0.1:5173/ || echo '(down)')"
  printf '%-8s %s\n' kiosk "$(pgrep -f "user-data-dir=$kiosk_profile" >/dev/null && echo running || echo '(down)')"
  printf '%-8s %s\n' audio "$(pgrep -f "python -m app.main" >/dev/null && echo running || echo '(down)')"
}

screen_keep_on() {
  pkill -x swayidle || true
  # By name, not '*': day-11 the stack came up with the DSI panel still off after
  # swayidle had blanked it, and `wlopm --on '*'` had been silently swallowed.
  for output in $(wlopm 2>/dev/null | awk '{print $1}'); do
    wlopm --on "$output" || echo "could not turn on $output"
  done
}

screen_restore_blanking() {
  # Same line as ~/.config/labwc/autostart, so `down` leaves the desktop as a reboot would.
  pgrep -x swayidle >/dev/null || setsid swayidle -w timeout 600 'wlopm --off *' resume 'wlopm --on *' >/dev/null 2>&1 < /dev/null &
}

case "${1:-up}" in
  health) health; exit 0 ;;
  down)
    pkill -f "user-data-dir=$kiosk_profile" || true
    pkill -f "python -m app.main" || true
    tmux kill-session -t "$session" 2>/dev/null && echo "stopped" || echo "not running"
    screen_restore_blanking
    exit 0 ;;
  up) ;;
  *) echo "usage: $0 [up|health|down]"; exit 2 ;;
esac

if tmux has-session -t "$session" 2>/dev/null; then
  echo "session '$session' already running — attaching (use '$0 down' to stop)"
  exec tmux attach -t "$session"
fi

mkdir -p "$audio_dump_dir"
[ -f "$brain_home/CLAUDE.md" ] || { echo "missing brain home: copy brain-home.example to $brain_home and run claude there once (brain-home.example/README.md)"; exit 1; }
case "$tts_engine" in
  piper) [ -f "$repo/tts/voices/ko_KR-kss-medium-aligned.onnx" ] || { echo "missing patched piper voice: run the tts setup in HANDOFF.md §설치 순서 2"; exit 1; } ;;
  azure) ;;
  *) echo "unknown TTS_ENGINE '$tts_engine' (piper|azure)"; exit 1 ;;
esac
case "$stt_engine" in
  whisper) [ -f "$whisper_model/model.bin" ] || [ "$whisper_model" = "${whisper_model#/}" ] || { echo "missing whisper model dir: $whisper_model (day-8.md \"whisper tiny 비교\" — curl -C - model.bin)"; exit 1; } ;;
  azure) ;;
  *) echo "unknown STT_ENGINE '$stt_engine' (whisper|azure)"; exit 1 ;;
esac
if [ "$tts_engine" = azure ] || [ "$stt_engine" = azure ]; then
  [ -n "${AZURE_SPEECH_KEY:-}" ] && [ -n "${AZURE_SPEECH_REGION:-}" ] || { echo "an azure engine needs AZURE_SPEECH_KEY and AZURE_SPEECH_REGION (expected in $azure_env — scripts/azure-probe/README.md)"; exit 1; }
fi

# A fresh Chromium profile offers Google Translate over the face; neither --lang nor
# --disable-features=Translate suppresses it on this build, the profile preference does.
if [ ! -f "$kiosk_profile/Default/Preferences" ]; then
  mkdir -p "$kiosk_profile/Default"
  printf '%s' '{"translate":{"enabled":false},"intl":{"accept_languages":"ko-KR,ko"}}' > "$kiosk_profile/Default/Preferences"
fi

tmux new-session -d -s "$session" -n tts -c "$repo/tts"
tmux send-keys -t "$session:tts" \
  "TTS_ENGINE=$tts_engine TTS_DEVICE=cpu TTS_SPEED=1.0 TTS_VISEME_MIN_SECONDS=0.02 TTS_ENVELOPE_HZ=50 \
PIPER_MODEL=$repo/tts/voices/ko_KR-kss-medium-aligned.onnx PIPER_CONFIG=$repo/tts/voices/ko_KR-kss-medium.onnx.json \
AZURE_TTS_VOICE=$tts_voice \
uv run uvicorn app.server:app --host 127.0.0.1 --port 8201" C-m

tmux new-window -t "$session" -n voice -c "$repo/voice"
tmux send-keys -t "$session:voice" \
  "STT_ENGINE=$stt_engine WHISPER_MODEL=$whisper_model WHISPER_BEAM_SIZE=$whisper_beam_size uv run uvicorn app.server:app --host 127.0.0.1 --port 8100" C-m

tmux new-window -t "$session" -n gateway -c "$repo/agent-gateway"
tmux send-keys -t "$session:gateway" \
  "AGENT_PAIRING_CODE=000000 AGENT_BRAIN=$agent_brain BRAIN_HOME=$brain_home \
DEVICE_PUSH_URL=http://127.0.0.1:8000/api/push DEVICE_PUSH_TOKEN=$push_token \
uv run uvicorn app.server:app --host 127.0.0.1 --port $gateway_port" C-m

tmux new-window -t "$session" -n backend -c "$repo/backend"
tmux send-keys -t "$session:backend" \
  "AGENT_GATEWAY_URL=http://127.0.0.1:$gateway_port AGENT_PAIRING_CODE=000000 PUSH_TOKEN=$push_token \
uv run uvicorn app.server:app --host 0.0.0.0 --port 8000" C-m

tmux new-window -t "$session" -n frontend -c "$repo/frontend"
tmux send-keys -t "$session:frontend" "npm run dev" C-m

tmux new-window -t "$session" -n audio -c "$repo/audio"
tmux send-keys -t "$session:audio"   "until curl -s -m 2 -o /dev/null http://127.0.0.1:8000/health && curl -s -m 2 -o /dev/null http://127.0.0.1:8201/health; do sleep 1; done; PUSH_TOKEN=$push_token STT_ENGINE=$stt_engine AUDIO_INPUT_DEVICE=$audio_device AUDIO_OUTPUT_DEVICE=$audio_device AUDIO_DUMP_DIR=$audio_dump_dir AUDIO_PLAYBACK_GAIN_DB=$playback_gain_db WAKE_WORD_MODEL=$wake_word_model uv run python -m app.main" C-m

screen_keep_on
tmux new-window -t "$session" -n kiosk -c "$HOME"
tmux send-keys -t "$session:kiosk" \
  "until curl -sk -m 2 -o /dev/null https://127.0.0.1:5173/; do sleep 1; done; \
LANGUAGE=ko LANG=ko_KR.UTF-8 chromium --kiosk --ozone-platform=wayland --lang=ko --password-store=basic \
--ignore-certificate-errors --autoplay-policy=no-user-gesture-required --use-fake-ui-for-media-stream \
--noerrdialogs --disable-infobars --user-data-dir=$kiosk_profile '$kiosk_url' 2>&1 | grep -v -E '^\\[|GPU|dbus'" C-m

tmux new-window -t "$session" -n shell -c "$repo"
tmux send-keys -t "$session:shell" "sleep 8; $repo/scripts/rpi-up.sh health; hostname -I" C-m

echo "started tmux session '$session' (windows: tts voice gateway backend frontend audio kiosk shell)"
echo "PUSH_TOKEN=$push_token   gateway=:$gateway_port   brain=$agent_brain home=$brain_home   stt=$stt_engine (whisper=$whisper_model beam=$whisper_beam_size)   tts=$tts_engine$([ "$tts_engine" = azure ] && echo " voice=$tts_voice")"
echo "attach: tmux attach -t $session     phone: https://$(hostname -I | awk '{print $1}'):5173"
