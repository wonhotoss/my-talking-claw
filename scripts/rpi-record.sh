#!/usr/bin/env bash
# Recording studio on the RPi: the family voice checklist on the screen, one prompt at a time
# (audio/app/voice_studio.py; why and what: 다음-개선-후보.md 1-1 "가족 녹음").
#
#   scripts/rpi-up.sh down       first - the stack holds the ReSpeaker and the screen
#   scripts/rpi-record.sh        start the studio and its kiosk (creates the checklist on first run)
#   scripts/rpi-record.sh down   stop it, restore screen blanking; then scripts/rpi-up.sh again
#
# The same page opens on a phone at http://<rpi>:8300, so a parent can press the buttons.
# CHECKLIST picks another CSV for a later campaign (columns of audio/app/voice_checklist.py).
# Voices are biometric: the checklist and the WAVs stay in ~/claw-data on this host, not in git.
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
session="claw-record"
stack_session="claw"
export PATH="$HOME/.local/bin:$PATH"
export WAYLAND_DISPLAY="${WAYLAND_DISPLAY:-wayland-0}"
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"

checklist="${CHECKLIST:-$HOME/claw-data/voices/checklist.csv}"
port="${STUDIO_PORT:-8300}"
# Same devices and playback level as the audio loop in rpi-up.sh.
audio_device="${AUDIO_DEVICE:-ReSpeaker}"
playback_gain_db="${AUDIO_PLAYBACK_GAIN_DB:--12}"
kiosk_profile="$HOME/.config/claw-record-kiosk"

# The two screen helpers are rpi-up.sh's (see there for the day-11 reason behind naming outputs).
screen_keep_on() {
  pkill -x swayidle || true
  for output in $(wlopm 2>/dev/null | awk '{print $1}'); do
    wlopm --on "$output" || echo "could not turn on $output"
  done
}

screen_restore_blanking() {
  pgrep -x swayidle >/dev/null || setsid swayidle -w timeout 600 'wlopm --off *' resume 'wlopm --on *' >/dev/null 2>&1 < /dev/null &
}

case "${1:-up}" in
  down)
    pkill -f "user-data-dir=$kiosk_profile" || true
    pkill -f "python -m app.voice_studio" || true
    tmux kill-session -t "$session" 2>/dev/null && echo "stopped" || echo "not running"
    screen_restore_blanking
    exit 0 ;;
  up) ;;
  *) echo "usage: $0 [up|down]"; exit 2 ;;
esac

if tmux has-session -t "$stack_session" 2>/dev/null; then
  echo "the stack is running (tmux '$stack_session') and holds the ReSpeaker - run scripts/rpi-up.sh down first" >&2
  exit 1
fi

if tmux has-session -t "$session" 2>/dev/null; then
  echo "studio already running - attaching (use '$0 down' to stop)"
  exec tmux attach -t "$session"
fi

if [ ! -f "$checklist" ]; then
  (cd "$repo/audio" && uv run python -m app.voice_checklist "$checklist")
fi

tmux new-session -d -s "$session" -n studio -c "$repo/audio"
tmux send-keys -t "$session:studio" \
  "AUDIO_INPUT_DEVICE=$audio_device AUDIO_OUTPUT_DEVICE=$audio_device AUDIO_PLAYBACK_GAIN_DB=$playback_gain_db \
uv run python -m app.voice_studio '$checklist' --port $port" C-m

screen_keep_on
tmux new-window -t "$session" -n kiosk -c "$HOME"
tmux send-keys -t "$session:kiosk" \
  "until curl -s -m 2 -o /dev/null http://127.0.0.1:$port/state; do sleep 1; done; \
LANGUAGE=ko LANG=ko_KR.UTF-8 chromium --kiosk --ozone-platform=wayland --lang=ko --password-store=basic \
--noerrdialogs --disable-infobars --user-data-dir=$kiosk_profile 'http://localhost:$port/' 2>&1 | grep -v -E '^\\[|GPU|dbus'" C-m

echo "started tmux session '$session' (windows: studio kiosk)   checklist: $checklist"
echo "attach: tmux attach -t $session     phone: http://$(hostname -I | awk '{print $1}'):$port"
