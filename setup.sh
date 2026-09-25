#!/usr/bin/env bash
#
# One-shot setup (macOS / Linux). Safe to re-run: every step checks first.
#
#   ./setup.sh
#
# The steps live in setup.py, shared with Windows (setup.bat).
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PY_BIN="$(command -v python3 || true)"
[ -n "$PY_BIN" ] || { echo "python3 not found — install Python 3.11+ first"; exit 1; }
exec "$PY_BIN" "$ROOT/setup.py" "$@"
