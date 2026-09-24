#!/usr/bin/env bash
#
# Start Job Pipeline automatically at login (macOS), and restart it if it
# crashes. After this you never need ./start.sh: the dashboard is always at
# http://127.0.0.1:8765/dashboard and the Chrome extension always finds it.
#
#   scripts/install-agent.sh            install (or update) and start it now
#   scripts/install-agent.sh --remove   stop it and stop starting at login
#
# Settings in local.env (e.g. AUTOBUILD=1) are read by the bridge itself, so
# they apply here too. Its output goes to scraper/out/bridge.log.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LABEL="com.jobpipeline.bridge"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
PY="$ROOT/scraper/.venv/bin/python"

if [ "$(uname)" != "Darwin" ]; then
  echo "This installs a macOS login agent. On Linux, run ./start.sh (or add it to your session's autostart)."
  exit 1
fi

launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true

if [ "${1:-}" = "--remove" ]; then
  rm -f "$PLIST"
  echo "Removed. Start it by hand with ./start.sh from now on."
  exit 0
fi

[ -x "$PY" ] || { echo "Not set up yet. Run ./setup.sh first."; exit 1; }
mkdir -p "$HOME/Library/LaunchAgents" "$ROOT/scraper/out"

# XML-escape the paths (a folder name with & in it is legal).
x() { printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'; }

cat > "$PLIST" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$(x "$PY")</string>
    <string>$(x "$ROOT/scraper/serve.py")</string>
  </array>
  <key>WorkingDirectory</key><string>$(x "$ROOT/scraper")</string>
  <key>RunAtLoad</key><true/>
  <!-- Restart after a crash, not after a clean exit (a clean exit means the
       port was already taken by another copy, and retrying would loop). -->
  <key>KeepAlive</key><dict><key>SuccessfulExit</key><false/></dict>
  <key>ThrottleInterval</key><integer>10</integer>
  <key>StandardOutPath</key><string>$(x "$ROOT/scraper/out/bridge.log")</string>
  <key>StandardErrorPath</key><string>$(x "$ROOT/scraper/out/bridge.log")</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <!-- A login agent gets a bare PATH; builds need ollama, claude and curl. -->
    <key>PATH</key><string>$(x "$HOME/.local/bin"):/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
  </dict>
</dict>
</plist>
PLIST

launchctl bootstrap "gui/$(id -u)" "$PLIST"
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -s --max-time 2 -o /dev/null "http://127.0.0.1:8765/status" && break
  sleep 1
done
if curl -s --max-time 2 -o /dev/null "http://127.0.0.1:8765/status"; then
  echo "Running, and it will start at every login: http://127.0.0.1:8765/dashboard"
else
  echo "Installed, but it is not answering yet. Its log: $ROOT/scraper/out/bridge.log"
  echo "If another copy already uses port 8765, stop that one first."
fi
