#!/bin/bash
# Wait for the current batch to finish, then say so out loud and on screen.
# Read-only: it watches, it never touches the run. Safe to close.
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
if ! pgrep -f "run-batch.sh" > /dev/null; then
  echo "No batch is running right now."; exit 0
fi
echo "Watching. You will get a notification and a sound when it finishes."
while pgrep -f "run-batch.sh" > /dev/null; do sleep 15; done
DAY=$(date +%F); DIR="applications/$DAY"
n=0
for d in "$DIR"/*/; do
  [ -d "$d" ] || continue
  cv=$(find "$d" -maxdepth 1 -name '*_CV_*.pdf' -size +8k | head -1)
  cl=$(find "$d" -maxdepth 1 -name '*_CoverLetter_*.pdf' -size +8k | head -1)
  [ -n "$cv" ] && [ -n "$cl" ] && n=$((n + 1))
done
afplay /System/Library/Sounds/Hero.aiff 2>/dev/null &
osascript -e "display notification \"$n application(s) built\" with title \"CV Builder finished\"" 2>/dev/null
echo "Finished: $n application(s) built."
[ -f "$DIR/batch.html" ] && { echo "Opening the report."; open "$DIR/batch.html"; }
