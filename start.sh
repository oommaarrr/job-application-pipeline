#!/usr/bin/env bash
#
# Start Job Pipeline and open the dashboard.
#
#   ./start.sh
#
# Keep this window open while you use it. Press Ctrl+C here to stop.
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Personal settings (gitignored), e.g. BRIDGE_PORT or AUTOBUILD=1.
if [ -f "$ROOT/local.env" ]; then set -a; . "$ROOT/local.env"; set +a; fi
PORT="${BRIDGE_PORT:-8765}"
URL="http://127.0.0.1:$PORT/dashboard"
PY="$ROOT/scraper/.venv/bin/python"

if [ ! -x "$PY" ]; then
  echo "Job Pipeline is not set up yet. Run this first:"
  echo "    ./setup.sh"
  exit 1
fi

open_browser() { (sleep 2; open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null) & }

# Already running (for example in another window)? Just open it.
if curl -s --max-time 2 -o /dev/null "http://127.0.0.1:$PORT/status"; then
  echo "Job Pipeline is already running. Opening $URL"
  open "$URL" 2>/dev/null || xdg-open "$URL" 2>/dev/null
  exit 0
fi

echo "Starting Job Pipeline on $URL"
echo "Keep this window open. Press Ctrl+C to stop."
open_browser
cd "$ROOT/scraper" || exit 1

# If the bridge crashes, bring it back instead of leaving the dashboard dead.
# Nothing is lost in a restart: every step's state lives on disk, and a build
# or rank that was running carries on in the background and is picked up again.
# Ctrl+C (exit 130) and a port already taken (the second copy case) end it.
restarts=0
while :; do
  "$PY" serve.py
  code=$?
  [ "$code" -eq 0 ] || [ "$code" -eq 130 ] && exit 0
  restarts=$((restarts + 1))
  if [ "$restarts" -gt 5 ]; then
    echo "The bridge keeps crashing (exit $code). The error is above; nothing on disk was lost."
    exit "$code"
  fi
  echo "The bridge stopped unexpectedly (exit $code). Restarting in 3 seconds ($restarts/5)..."
  sleep 3
done
