#!/usr/bin/env bash
#
# Start Job Pipeline and open the dashboard (macOS / Linux).
#
#   ./start.sh
#
# Keep this window open while you use it. Press Ctrl+C here to stop.
# The logic lives in start.py, shared with Windows (start.bat).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/scraper/.venv/bin/python"
if [ ! -x "$PY" ]; then
  echo "Job Pipeline is not set up yet. Run this first:"
  echo "    ./setup.sh"
  exit 1
fi
exec "$PY" "$ROOT/start.py" "$@"
