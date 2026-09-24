#!/usr/bin/env bash
#
# One-shot setup. Safe to re-run: every step checks before it acts.
#
#   ./setup.sh
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
no()  { printf '  \033[31m✗\033[0m %s\n' "$*"; }

say "1. Python environment"
PY_BIN="$(command -v python3 || true)"
[ -n "$PY_BIN" ] || { no "python3 not found — install Python 3.11+ first"; exit 1; }
if [ ! -x "$ROOT/scraper/.venv/bin/python" ]; then
  "$PY_BIN" -m venv "$ROOT/scraper/.venv"
  ok "created scraper/.venv"
else
  ok "scraper/.venv already exists"
fi
VENV="$ROOT/scraper/.venv/bin/python"
"$VENV" -m pip install --quiet --upgrade pip
"$VENV" -m pip install --quiet -r "$ROOT/scraper/requirements.txt"
"$VENV" -m pip install --quiet -r "$ROOT/builder/requirements.txt"
ok "dependencies installed"
# One environment for both halves. The build runs `.venv/bin/python` from inside
# builder/, so give it the same interpreter rather than a second install.
ln -sfn ../scraper/.venv "$ROOT/builder/.venv"
ok "builder/.venv linked to the same environment"

say "2. Local model (Ollama)"
if ! command -v ollama >/dev/null 2>&1; then
  no "ollama not installed — https://ollama.com/download"
  no "the ranker cannot run without it"
else
  ok "ollama is installed"
  MODEL="$("$VENV" -c "import sys;sys.path.insert(0,'$ROOT/scraper');import config;print(config.OLLAMA_MODEL)" 2>/dev/null || echo llama3.1)"
  # Capture first, then match. `ollama list | grep -q` under pipefail is a
  # race: grep exits on the first match, ollama gets SIGPIPE if it is still
  # writing, the pipeline reports failure, and a model you already have is
  # re-pulled (4.9 GB). Seen on the first real run, 24 Sep 2026.
  MODELS="$(ollama list 2>/dev/null || true)"
  if printf '%s\n' "$MODELS" | awk '{print $1}' | grep -qx "${MODEL%%:*}\(:.*\)\{0,1\}"; then
    ok "model '$MODEL' is present"
  else
    echo "  pulling $MODEL (this is a few GB, once)…"
    ollama pull "$MODEL" && ok "pulled $MODEL"
  fi
fi

say "   PDF optimiser (optional)"
if command -v qpdf >/dev/null 2>&1; then ok "qpdf found"
else echo "  - qpdf not installed. Documents still build; install it for smaller,"
     echo "    faster-loading PDFs:  brew install qpdf"; fi

say "3. Your profile"
if [ ! -d "$ROOT/profiles/me" ]; then
  cp -r "$ROOT/profiles/example" "$ROOT/profiles/me"
  ok "created profiles/me (a copy of the example — it is not you yet)"
  echo "  Next, make it yours. Easiest: let Claude read your current CV:"
  echo "      claude \"/make-profile ~/path/to/your-cv.pdf\""
  echo "  Or edit profiles/me/identity.md and profiles/me/profile.md by hand."
else
  ok "profiles/me already exists"
fi

say "4. Chrome extension"
echo "  Load it by hand, once:"
echo "    1. open chrome://extensions"
echo "    2. turn on Developer mode (top right)"
echo "    3. Load unpacked → $ROOT/extension"

say "5. Claude Code CLI (only needed to WRITE documents)"
if command -v claude >/dev/null 2>&1; then ok "claude found: $(command -v claude)"
else no "not installed — https://claude.com/claude-code"; fi

say "Done. Start it with:"
echo "    ./start.sh"
echo "  It opens the dashboard in your browser. Work down the Setup panel there."
