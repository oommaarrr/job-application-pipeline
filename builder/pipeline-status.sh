#!/bin/bash
#
# Where the pipeline is, right now. Read-only: it changes nothing.
#
#   bash pipeline-status.sh          print once
#   bash pipeline-status.sh -w       refresh every 10s until the run finishes
#
# The log cannot answer this on its own. `claude -p` does not stream: it prints
# its whole result when it exits, so during the fifteen minutes it spends
# writing documents the log file does not change at all and a healthy run looks
# identical to a dead one. What DOES change is the documents themselves, so this
# counts those.
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
SCRAPER="$(cd "$(dirname "${BASH_SOURCE[0]}")/../scraper" && pwd)"

one_pass() {
  DAY=$(date +%F)
  DIR="applications/$DAY"
  TARGET=$("$SCRAPER/.venv/bin/python" -c "
import sys; sys.path.insert(0,'$SCRAPER')
import config; print(config.BUILD_TARGET)" 2>/dev/null || echo 15)

  # Three phases per role, and each leaves a different file behind. Showing only
  # the last one makes ten minutes of real work look like nothing happening:
  #   PLAN.md      the role has been read and planned
  #   *.json       the payloads are written, PDFs are next
  #   both PDFs    finished
  done_n=0; payload_n=0; plan_n=0; names=""
  for d in "$DIR"/*/; do
    [ -d "$d" ] || continue
    n=$(basename "$d")
    cv=$(find "$d" -maxdepth 1 -name '*_CV_*.pdf' -size +8k 2>/dev/null | head -1)
    cl=$(find "$d" -maxdepth 1 -name '*_CoverLetter_*.pdf' -size +8k 2>/dev/null | head -1)
    js=$(find "$d" -maxdepth 1 -name '*.json' 2>/dev/null | head -1)
    if [ -n "$cv" ] && [ -n "$cl" ]; then
      done_n=$((done_n + 1));    names="$names  [done]    $n"$'\n'
    elif [ -n "$js" ]; then
      payload_n=$((payload_n + 1)); names="$names  [payload] $n"$'\n'
    else
      plan_n=$((plan_n + 1));    names="$names  [planned] $n"$'\n'
    fi
  done

  batch=$(pgrep -f "run_batch.py|run-batch.sh" | head -1)
  writer=$(pgrep -f "claude -p" | head -1)
  rank=$(pgrep -f "rank_ollama" | head -1)

  echo "──────────────────────────────────────────────────────────"
  date "+ %H:%M:%S"
  if [ -n "$rank" ]; then
    echo " STAGE: ranking locally with Ollama (no CVs yet)"
    bash "$SCRAPER/rank-progress.sh" 2>/dev/null | sed -n '2p'
  elif [ -n "$writer" ]; then
    el=$(ps -o etime= -p "$writer" | tr -d ' ')
    echo " STAGE: Claude is writing documents (running $el)"
    echo "        the log stays silent until it finishes — that is normal"
  elif [ -n "$batch" ]; then
    echo " STAGE: batch running, between stages"
  else
    echo " STAGE: nothing running"
  fi

  echo " BUILT: $done_n of $TARGET finished"
  echo "        $payload_n with payloads written, $plan_n planned only"
  [ -n "$names" ] && printf '%s' "$names"
  [ -f "$DIR/batch.html" ] && echo " REPORT: $DIR/batch.html  <- written, so the run reached the end"
  echo " LOG:   $(tail -1 "logs/$DAY.log" 2>/dev/null)"
}

# A compact one-liner, for following without redrawing the screen.
line_pass() {
  DAY=$(date +%F); DIR="applications/$DAY"
  d=0; pay=0; pl=0
  for x in "$DIR"/*/; do
    [ -d "$x" ] || continue
    cv=$(find "$x" -maxdepth 1 -name '*_CV_*.pdf' -size +8k 2>/dev/null | head -1)
    cl=$(find "$x" -maxdepth 1 -name '*_CoverLetter_*.pdf' -size +8k 2>/dev/null | head -1)
    js=$(find "$x" -maxdepth 1 -name '*.json' 2>/dev/null | head -1)
    if [ -n "$cv" ] && [ -n "$cl" ]; then d=$((d+1))
    elif [ -n "$js" ]; then pay=$((pay+1)); else pl=$((pl+1)); fi
  done
  if pgrep -f "claude -p" > /dev/null; then st="writing"
  elif pgrep -f "rank_ollama" > /dev/null; then st="ranking"
  elif pgrep -f "run_batch.py|run-batch.sh" > /dev/null; then st="between stages"
  else st="not running"; fi
  printf '%s  %-14s built %2d  payloads %2d  planned %2d\n' \
         "$(date +%H:%M:%S)" "$st" "$d" "$pay" "$pl"
}

case "${1:-}" in
  -w)
    # Redraw in place. Some terminals scroll or flicker on `clear`, which looks
    # like the run misbehaving when it is only this watcher repainting, so -f
    # below exists for when that is distracting.
    while :; do
      clear; one_pass
      if ! pgrep -f "run_batch.py|run-batch.sh" > /dev/null; then
        echo; echo " The run has finished. Press Ctrl-C to close."; break
      fi
      sleep 10
    done
    ;;
  -f)
    # Append one line, and ONLY when something actually changed. No clearing,
    # no flicker, and the scrollback becomes a history of the run.
    echo "Following. One line per change. Ctrl-C stops watching, not the run."
    prev=""
    while :; do
      cur=$(line_pass)
      key=${cur#* }
      if [ "$key" != "$prev" ]; then echo "$cur"; prev="$key"; fi
      pgrep -f "run_batch.py|run-batch.sh" > /dev/null || { echo "run finished."; break; }
      sleep 10
    done
    ;;
  *) one_pass ;;
esac
