#!/usr/bin/env bash
#
# Start Job Pipeline and open the dashboard (macOS / Linux).
#
#   ./start.sh
#
# The first run sets everything up by itself. Keep this window open while you
# use it; press Ctrl+C here to stop. The logic lives in start.py, shared with
# Windows (start.bat).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY="$ROOT/scraper/.venv/bin/python"
[ -x "$PY" ] || PY="$(command -v python3 || true)"
[ -n "$PY" ] || { echo "python3 not found — install Python 3.11+ first"; exit 1; }
exec "$PY" "$ROOT/start.py" "$@"
