#!/usr/bin/env bash
# Day-8 launcher: the whole server stack on one RPi, one tmux session, five windows.
#
#   scripts/rpi-up.sh          start (or re-attach to) the stack
#   scripts/rpi-up.sh health   curl every /health
#   scripts/rpi-up.sh down     kill the tmux session (and every service with it)
#
# Deviations from README defaults, all forced by this machine:
#   - agent-gateway on :3001 — the real nullclaw gateway already owns :3000 (HANDOFF "포트 3000 주의").
#   - whisper defaults to the local tiny model dir (day-8 user verdict: the LLM repairs garbled
#     transcripts, so tiny's 2x speed wins). WHISPER_MODEL=base or a HF name still works.
#   - tts runs natively (no Docker on this RPi) with the env values from tts/docker-compose.yml `piper`.
#   - CLAUDE_WORKDIR is an empty dir outside the repo: --allowedTools is not a sandbox (README warning).
set -euo pipefail

repo="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
session="claw"
export PATH="$HOME/.local/bin:$PATH"

gateway_port="${GATEWAY_PORT:-3001}"
push_token="${PUSH_TOKEN:-day8-push}"
claude_workdir="${CLAUDE_WORKDIR:-$HOME/claw-home}"
whisper_model="${WHISPER_MODEL:-$repo/voice/models/faster-whisper-tiny}"

health() {
  for pair in "backend http://127.0.0.1:8000/health" \
              "gateway http://127.0.0.1:${gateway_port}/health" \
              "voice   http://127.0.0.1:8100/health" \
              "tts     http://127.0.0.1:8201/health"; do
    set -- $pair
    printf '%-8s %s\n' "$1" "$(curl -s -m 5 "$2" || echo '(down)')"
  done
  printf '%-8s %s\n' frontend "$(curl -sk -m 5 -o /dev/null -w '%{http_code}' https://127.0.0.1:5173/ || echo '(down)')"
}

case "${1:-up}" in
  health) health; exit 0 ;;
  down)   tmux kill-session -t "$session" 2>/dev/null && echo "stopped" || echo "not running"; exit 0 ;;
  up) ;;
  *) echo "usage: $0 [up|health|down]"; exit 2 ;;
esac

if tmux has-session -t "$session" 2>/dev/null; then
  echo "session '$session' already running — attaching (use '$0 down' to stop)"
  exec tmux attach -t "$session"
fi

mkdir -p "$claude_workdir"
[ -f "$repo/tts/voices/ko_KR-kss-medium-aligned.onnx" ] || { echo "missing patched piper voice: run the tts setup in HANDOFF.md §설치 순서 2"; exit 1; }
[ -f "$whisper_model/model.bin" ] || [ "$whisper_model" = "${whisper_model#/}" ] || { echo "missing whisper model dir: $whisper_model (day-8.md \"whisper tiny 비교\" — curl -C - model.bin)"; exit 1; }

tmux new-session -d -s "$session" -n tts -c "$repo/tts"
tmux send-keys -t "$session:tts" \
  "TTS_ENGINE=piper TTS_DEVICE=cpu TTS_SPEED=1.0 TTS_VISEME_MIN_SECONDS=0.02 TTS_ENVELOPE_HZ=50 \
PIPER_MODEL=$repo/tts/voices/ko_KR-kss-medium-aligned.onnx PIPER_CONFIG=$repo/tts/voices/ko_KR-kss-medium.onnx.json \
uv run uvicorn app.server:app --host 127.0.0.1 --port 8201" C-m

tmux new-window -t "$session" -n voice -c "$repo/voice"
tmux send-keys -t "$session:voice" \
  "WHISPER_MODEL=$whisper_model uv run uvicorn app.server:app --host 127.0.0.1 --port 8100" C-m

tmux new-window -t "$session" -n gateway -c "$repo/agent-gateway"
tmux send-keys -t "$session:gateway" \
  "AGENT_PAIRING_CODE=000000 CLAUDE_WORKDIR=$claude_workdir \
DEVICE_PUSH_URL=http://127.0.0.1:8000/api/push DEVICE_PUSH_TOKEN=$push_token \
uv run uvicorn app.server:app --host 127.0.0.1 --port $gateway_port" C-m

tmux new-window -t "$session" -n backend -c "$repo/backend"
tmux send-keys -t "$session:backend" \
  "AGENT_GATEWAY_URL=http://127.0.0.1:$gateway_port AGENT_PAIRING_CODE=000000 PUSH_TOKEN=$push_token \
uv run uvicorn app.server:app --host 0.0.0.0 --port 8000" C-m

tmux new-window -t "$session" -n frontend -c "$repo/frontend"
tmux send-keys -t "$session:frontend" "npm run dev" C-m

tmux new-window -t "$session" -n shell -c "$repo"
tmux send-keys -t "$session:shell" "sleep 8; $repo/scripts/rpi-up.sh health; hostname -I" C-m

echo "started tmux session '$session' (windows: tts voice gateway backend frontend shell)"
echo "PUSH_TOKEN=$push_token   gateway=:$gateway_port   CLAUDE_WORKDIR=$claude_workdir"
echo "attach: tmux attach -t $session     phone: https://$(hostname -I | awk '{print $1}'):5173"
