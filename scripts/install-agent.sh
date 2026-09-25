#!/usr/bin/env bash
#
# Start Job Pipeline automatically at login, and restart it if it crashes.
# Optional. macOS: a launchd agent. Linux: a systemd user service.
#
#   scripts/install-agent.sh            install (or update) and start it now
#   scripts/install-agent.sh --remove   stop it and stop starting at login
#
# The logic lives in install-agent.py, shared with Windows (install-agent.bat).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PY="$ROOT/scraper/.venv/bin/python"
[ -x "$PY" ] || { echo "Not set up yet. Run ./setup.sh first."; exit 1; }
exec "$PY" "$ROOT/scripts/install-agent.py" "$@"
