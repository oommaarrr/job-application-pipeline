#!/bin/bash
#
# Scheduled /apply-batch, resumable.
#
# Runs Claude Code headless, in this directory, so it has the skill, the
# reference files, the venv and applications/ exactly as an interactive session
# would. Nothing goes to the cloud and nothing needs a browser.
#
# Two waits shape this script, and they exist for different reasons.
#
# The first is for the scrape. It starts at a random minute inside its window,
# so a fixed start time here would sometimes fire mid-collect and build
# documents from half a pool. This polls the bridge until the collected count
# has stopped moving, which is the only reliable "the scrape finished" signal
# available from outside the extension.
#
# The second is the retry loop. Building five applications is twenty minutes of
# model calls, file writes and PDF generation, and the ways it fails are mostly
# transient: the network drops, a rate limit is hit, the machine sleeps. A run
# that dies at application three used to throw away all three and wait for
# tomorrow. Now the work already on disk is counted, the remainder is what gets
# asked for, and the day is only finished when the documents are actually there.
#
# Safe to run repeatedly. If today is already complete it exits in a second.

set -u

# ---------------------------------------------------------------- roots
# Everything is derived from where this script actually is, so the checkout
# runs from any directory. Before 24 September 2026 these were absolute paths
# into one laptop's ~/Desktop.  Override either with an env var if your layout
# differs: SCRAPER_DIR=..., PY=...
BUILDER_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$BUILDER_ROOT/.." && pwd)"
SCRAPER_DIR="${SCRAPER_DIR:-$REPO_ROOT/scraper}"
PY="${PY:-$SCRAPER_DIR/.venv/bin/python}"
[ -x "$PY" ] || PY="$(command -v python3)"

cd "$(dirname "$0")" || exit 1

LOG_DIR="logs"; mkdir -p "$LOG_DIR"
DAY="$(date +%F)"
LOG="$LOG_DIR/$DAY.log"
say() { echo "[$(date +%H:%M:%S)] $*" >> "$LOG"; }
notify() { osascript -e "display notification \"$1\" with title \"Job Pipeline\"${2:+ subtitle \"$2\"}" 2>/dev/null; }

# ---------------------------------------------------------------- outcome
#
# Every way this script ends records WHY in logs/last-outcome.json, in plain
# words with the fix, and the bridge turns it into a line in the dashboard's
# Activity panel with a Retry button. Before this the only record was the log,
# and "nothing happened" looked the same whether the pool was empty, Claude
# was signed out or the usage window was spent.
#
#   end_with <ok|failed|stopped|info> "<what happened>" "<what to do>" <exit code>
OUTCOME_STATUS=""; OUTCOME_MSG=""; OUTCOME_FIX=""
end_with() {
  OUTCOME_STATUS="$1"; OUTCOME_MSG="$2"; OUTCOME_FIX="${3:-}"
  say "$2"
  exit "${4:-0}"
}
write_outcome() {
  local code=$?
  if [ -z "$OUTCOME_STATUS" ]; then
    # Reached only by an exit nobody labelled: a crash, a kill, a bug here.
    OUTCOME_STATUS="failed"
    OUTCOME_MSG="the build stopped unexpectedly (exit $code)"
    OUTCOME_FIX="Press Retry. Finished applications are kept and skipped. Details are in the build log."
  fi
  local built=0
  type n_built >/dev/null 2>&1 && built=$(n_built 2>/dev/null || echo 0)
  S="$OUTCOME_STATUS" M="$OUTCOME_MSG" F="$OUTCOME_FIX" B="$built" P="$$" \
    "${PY:-python3}" -c '
import json, os, datetime
print(json.dumps({"at": datetime.datetime.now().isoformat(timespec="seconds"),
  "status": os.environ["S"], "message": os.environ["M"], "fix": os.environ["F"],
  "built": int(os.environ["B"] or 0), "pid": int(os.environ["P"])}))' \
    > "$LOG_DIR/last-outcome.json.tmp" 2>/dev/null \
    && mv "$LOG_DIR/last-outcome.json.tmp" "$LOG_DIR/last-outcome.json"
  return 0
}

# ---------------------------------------------------------------- one at a time
#
# Only one batch may run on this machine. Decision, 19 September 2026.
#
# The bridge already refuses to start a second build, but that lock only covers
# builds the bridge itself starts. Anything run by hand walks straight past it,
# and on 19 September two were alive at once: one left from the night before,
# still holding a claude process that had hung when the network dropped, and a
# fresh one started that morning. Both were writing to the same logs and would
# have written to the same applications folder.
#
# mkdir is the lock because it is atomic: it either creates the directory or
# fails, with no window between checking and taking it. A pid file alone has
# that window.
LOCK="$LOG_DIR/.run.lock"
if ! mkdir "$LOCK" 2>/dev/null; then
  other=$(cat "$LOCK/pid" 2>/dev/null || echo "")
  if [ -n "$other" ] && kill -0 "$other" 2>/dev/null; then
    started=$(ps -o lstart= -p "$other" 2>/dev/null | sed 's/^ *//')
    say "another batch is already running (pid $other, started $started) — not starting a second"
    echo "A batch is already running: pid $other, started $started"
    echo "Watch it:  tail -f \"$(pwd)/$LOG\""
    echo "Stop it:   kill $other"
    exit 0
  fi
  # The holder is gone, so the lock is stale. This is the case that matters
  # after a crash or a kill: without it the next run would refuse forever.
  say "clearing a stale lock left by pid ${other:-unknown}"
  rm -rf "$LOCK"
  mkdir "$LOCK" 2>/dev/null || { echo "could not take the run lock"; exit 1; }
fi
echo $$ > "$LOCK/pid"
# Released on every exit path.
#
# INT and TERM need their own handler that EXITS. A trap on those signals runs
# and then lets the script carry on, so a single `trap ... EXIT INT TERM` would
# delete the lock on Ctrl-C while the run kept going: unlocked and still
# working, which is worse than either state alone.
# Only release a lock we still hold.
#
# A run that is asleep does not process a signal until the sleep returns, so a
# killed run can wake minutes later, run its trap, and delete a lock that a
# DIFFERENT run has since taken. That is worse than the duplicate it was meant
# to prevent: two runs, neither locked. Checking the pid first makes the release
# idempotent and safe to arrive late.
release_lock() {
  [ "$(cat "$LOCK/pid" 2>/dev/null)" = "$$" ] && rm -rf "$LOCK"
  return 0
}
trap 'write_outcome; release_lock' EXIT
trap 'OUTCOME_STATUS=stopped; OUTCOME_MSG="the build was interrupted"; OUTCOME_FIX="Press Build to continue. Finished applications are kept and skipped."; release_lock; exit 130' INT
trap 'OUTCOME_STATUS=stopped; OUTCOME_MSG="the build was stopped"; OUTCOME_FIX="Press Build to continue. Finished applications are kept and skipped."; release_lock; exit 143' TERM

BRIDGE="http://127.0.0.1:${BRIDGE_PORT:-8765}/status"
# No number means "every role that fits", which is the standing rule in
# apply-batch.md. Passing a count here would quietly reimpose the cap of five
# that the user explicitly removed on 20 August. A number is only passed when one is
# given on the command line, which is how a deliberately small test run works.
BATCH_SIZE="${1:-}"
TODAY_DIR="applications/$DAY"
STATE="$TODAY_DIR/.batch-state"
MAX_WAIT=$((30 * 60))
STABLE_FOR=$((3 * 60))    # count unchanged this long means collecting finished
MAX_ATTEMPTS=3
# 2 minutes. Decision, 19 September 2026.
#
# It was 10, on the reasoning that a failed round should "give whatever broke a
# chance to clear". That reasoning does not survive contact with the actual
# causes: a rate limit clears in seconds, a usage limit takes hours (and is
# detected and handled separately anyway), and a bad shortlist does not clear at
# all until someone fixes it. Ten minutes was long enough to feel broken and too
# short to fix anything.
RETRY_GAP=$((2 * 60))

mkdir -p "$TODAY_DIR"

# A company folder counts as built only when both documents are present and
# neither is a stub. build_docs.py inflates every PDF past 8 KB, so anything
# smaller is a half-written file from a run that was interrupted mid-write.
built_companies() {
  local d name cv cl
  for d in "$TODAY_DIR"/*/; do
    [ -d "$d" ] || continue
    name=$(basename "$d")
    cv=$(find "$d" -maxdepth 1 -name '*_CV_*.pdf' -size +8k 2>/dev/null | head -1)
    cl=$(find "$d" -maxdepth 1 -name '*_CoverLetter_*.pdf' -size +8k 2>/dev/null | head -1)
    [ -n "$cv" ] && [ -n "$cl" ] && echo "$name"
  done
}

n_built() { built_companies | wc -l | tr -d ' '; }

# Finish the review page from whatever is on disk. A round cut off after the
# PDFs were written but before the report (a usage limit, a watchdog kill, a
# crash in the merge) used to leave good applications with no page, until a
# whole new attempt re-ran the ranking just to write it. The fragments and the
# PDFs are enough: merge, render, done.
finish_report() {
  [ -d "$TODAY_DIR" ] || return 0
  if [ ! -f "$TODAY_DIR/batch.json" ] && ls "$TODAY_DIR"/batch.shard-*.json >/dev/null 2>&1; then
    say "finishing the report: merging shard fragments"
    .venv/bin/python merge_batches.py "$TODAY_DIR" >> "$LOG" 2>&1 || say "merge failed — see above"
  fi
  if [ -f "$TODAY_DIR/batch.json" ]; then
    .venv/bin/python batch_report.py "$DAY" >> "$LOG" 2>&1 || say "report render failed — see above"
  fi
  return 0
}

# Read the target early: the completion check below needs to know what it is
# aiming for, and it runs long before the ranking section sets BUILD_TARGET.
BUILD_TARGET_PRE="${BATCH_SIZE:-$(
  "$PY" -c "
import sys; sys.path.insert(0, "$SCRAPER_DIR")
import config; print(config.BUILD_TARGET)" 2>/dev/null || echo 15)}"

# Already finished? Then this tick has nothing to do.
#
# batch.html is the completion marker because it is written in the last step of
# the command, after every PDF has been generated and verified. Its presence
# means the run reached the end; its absence means it did not, whatever else is
# on disk. A count cannot serve this purpose now that the batch has no fixed
# size, and a flag file could outlive work that was never finished.
#
# "Complete" means the target was reached, not merely that a batch ran. On
# 18 September a run of 6 wrote batch.html, and every later attempt exited in
# under a second with "nothing to do" while the target was 15. Three runs in a
# row printed nothing the user could see and built nothing. A completion marker
# has to know what it was aiming for.
if [ -f "$TODAY_DIR/batch.html" ]; then
  _have=$(n_built)
  if [ "${FORCE:-0}" = "1" ]; then
    say "already complete for $DAY ($_have built) — FORCE set, continuing"
  elif [ -n "${BUILD_TARGET_PRE:-}" ] && [ "$_have" -lt "$BUILD_TARGET_PRE" ]; then
    say "$_have built for $DAY, target is $BUILD_TARGET_PRE — building the rest"
  else
    say "to build more anyway: FORCE=1 bash run-batch.sh [target]"
    end_with info "already $_have built today, which meets the target of ${BUILD_TARGET_PRE}" \
      "To build more today, set a number above $_have and press Build." 0
  fi
fi

# A build someone started by hand (Build or Retry on the dashboard) gets a fresh
# attempt budget. The budget exists to stop an unattended schedule from
# hammering a broken setup; it must never make a deliberate click do nothing.
[ "${BUILD_MANUAL:-0}" = "1" ] && rm -f "$STATE"
attempt=$(cat "$STATE" 2>/dev/null || echo 0)
attempt=$((attempt + 1))
if [ "$attempt" -gt "$MAX_ATTEMPTS" ]; then
  end_with stopped "already tried $MAX_ATTEMPTS times today, not trying again automatically" \
    "Press Build (or Retry) to try again now." 0
fi
echo "$attempt" > "$STATE"

say "=== batch attempt $attempt/$MAX_ATTEMPTS for $DAY ==="

# ---------------------------------------------------------------- profile
# Checked before anything slow, so a missing profile fails in a second rather
# than after twenty minutes of ranking.
SCRAPER="${SCRAPER_DIR:-$REPO_ROOT/scraper}"
# The active profile: $PROFILE_DIR, else profiles/me, else profiles/example —
# the same rule the ranker and the bridge use (scraper/profile_dir.py).
PROFILE_DIR="$("$PY" "$SCRAPER/profile_dir.py" 2>/dev/null || echo "$REPO_ROOT/profiles/example")"
export PROFILE_DIR
# Claude runs with cwd = builder/, and the instructions refer to the profile as
# `profile/...`. A symlink keeps those instructions identical for every user,
# whichever profile folder is active. Gitignored.
ln -sfn "$PROFILE_DIR" "$BUILDER_ROOT/profile"

# Never spend Claude usage writing CVs for the fictional example person.
# setup.sh copies the example to profiles/me, so "profiles/me exists" proves
# nothing; compare the contents. The dashboard's Setup panel shows the same
# check, with the command to fix it.
_example="$REPO_ROOT/profiles/example"
_unfilled=""
for _f in profile.md identity.md; do
  if [ -f "$PROFILE_DIR/$_f" ] && cmp -s "$PROFILE_DIR/$_f" "$_example/$_f"; then
    _unfilled="$_unfilled $_f"
  fi
done
grep -q "example.com" "$PROFILE_DIR/identity.md" 2>/dev/null && case "$_unfilled" in *identity.md*) ;; *) _unfilled="$_unfilled identity.md";; esac
if [ "$(basename "$PROFILE_DIR")" = "example" ] || [ -n "$_unfilled" ]; then
  notify "Build not started" "Fill in your profile first"
  end_with failed "your profile is still the fictional example (${_unfilled# }), so no CV was written" \
    "In the project folder run: claude \"/make-profile path/to/your-cv.pdf\", then press Retry." 1
fi

# ---------------------------------------------------------------- wait for the network
#
# launchd runs a missed calendar job as soon as the machine wakes, which is the
# behaviour we want, but a Mac that has just woken has no usable network for
# several seconds and sometimes minutes: Wi-Fi has to associate and DNS has to
# come back. Starting into that produces an auth failure or a dead bridge and
# burns an attempt on a problem that would have cleared by itself.
#
# So wait for the network rather than assume it. Two hours is generous on
# purpose: a laptop opened at 3pm should still get its batch, and the cost of
# waiting is nothing but a sleeping process.
wait_for_net() {
  # Two hours for an unattended run; two minutes when someone just pressed a
  # button and is watching, so they hear about it instead of waiting.
  local patience=$(( 2 * 60 * 60 ))
  [ "${BUILD_MANUAL:-0}" = "1" ] && patience=120
  local deadline=$(( $(date +%s) + patience )) announced=0
  while ! curl -sSf --max-time 8 -o /dev/null https://api.anthropic.com/ 2>/dev/null \
     && ! curl -sSf --max-time 8 -o /dev/null https://www.google.com/ 2>/dev/null; do
    if [ "$(date +%s)" -ge "$deadline" ]; then
      say "no network after $((patience / 60)) min, giving up on this attempt"
      return 1
    fi
    [ "$announced" = 0 ] && { say "no network yet, waiting"; announced=1; }
    sleep 60
  done
  [ "$announced" = 1 ] && say "network is back"
  return 0
}

wait_for_net || { notify "No internet, batch postponed" "will retry";
  end_with failed "no internet connection, so nothing was built" \
    "Reconnect, then press Retry." 0; }

# ---------------------------------------------------------------- the pool
#
# Rule: if today already has jobs and no scrape is running, BUILD. Do not wait.
#
# Earlier versions waited for a scrape to start even with 73 jobs sitting in
# today's pool, which is backwards. Waiting is only ever useful when there is
# nothing to work with yet. A run that arrives after the scraping is done should
# pick up exactly where things were left and go straight to building, which is
# what "resume" means.
status_json() { curl -s --max-time 5 "$BRIDGE"; }
field() { sed -n "s/.*\"$1\": *\([0-9]*\).*/\1/p"; }
# Only the SCRAPE object's running flag. Their build deadlocked here on
# 20 September: the status JSON also carries "build": {"running": true} while a
# build is going, and grepping the whole document for "running": true matched
# the build's own flag — so the build waited forever for a "scrape in flight"
# that was really itself. Isolate the scrape object (it has no nested braces)
# before testing it.
scraping() { printf '%s' "$1" | grep -o '"scrape"[^}]*}' | grep -q '"running": *true'; }

js=$(status_json)
if [ -z "$js" ]; then
  notify "Bridge is down" "nothing was built"
  end_with failed "the bridge is not answering, so the pool could not be read" \
    "Run ./start.sh, then press Retry." 1
fi
have_jobs=$(printf '%s' "$js" | field collected_today); [ -z "$have_jobs" ] && have_jobs=0

# What is actually buildable, which is not the same as what arrived today.
#
# The old gate asked the bridge for `collected_today`, a count of one file named
# after today's date. On 19 September that left 158 scraped jobs and 23
# buildable roles sitting untouched while this script printed "pool is empty,
# waiting for a scrape" every twenty seconds. The pool was fine; the clock had
# rolled past midnight. A posting does not expire at midnight.
POOL_JSON=$("$PY" \
            "$SCRAPER_DIR/pool_status.py" 2>/dev/null || echo '{}')
pool_field() { printf '%s' "$POOL_JSON" | sed -n "s/.*\"$1\": *\([0-9]*\).*/\1/p"; }
usable=$(pool_field usable); [ -z "$usable" ] && usable=0
enough=$(printf '%s' "$POOL_JSON" | grep -q '"enough_to_build": true' && echo 1 || echo 0)
stale=$(printf '%s' "$POOL_JSON" | grep -q '"stale": true' && echo 1 || echo 0)

if [ "$enough" = "1" ] && [ "$stale" = "0" ] && ! scraping "$js"; then
  say "$usable usable role(s) already collected and nothing is scraping — building now"
  last="$usable"
elif [ "$have_jobs" -gt 0 ] && ! scraping "$js"; then
  say "$have_jobs jobs collected today and nothing is scraping — building now"
  last="$have_jobs"
else
  [ "$stale" = "1" ] && say "the newest scrape is older than $(printf '%s' "$POOL_JSON" | sed -n 's/.*"age_days": *\([0-9]*\).*/\1/p') days — waiting for a fresh one"
  [ "$usable" -gt 0 ] && [ "$enough" = "0" ] && say "only $usable usable role(s), not enough to fill a batch — waiting for a scrape"
  # Either a scrape is in flight, or the pool is empty and we are hoping one
  # starts. Both are the same wait: watch until it is running and then not, or
  # until the pool becomes non-empty.
  start=$(date +%s); last=0
  while :; do
    now=$(date +%s)
    if (( now - start > MAX_WAIT )); then say "gave up waiting after $((MAX_WAIT / 60)) min"; break; fi

    js=$(status_json)
    if [ -z "$js" ]; then say "bridge not answering, retrying"; sleep 20; continue; fi
    n=$(printf '%s' "$js" | field collected_today); [ -z "$n" ] && n=0

    if scraping "$js"; then
      say "scrape in flight · $n jobs so far"
      sleep 30; continue
    fi

    if [ "$n" -gt 0 ]; then
      say "scrape finished · $n jobs"
      notify "Scrape done: $n jobs. Building applications…"
      last="$n"; break
    fi

    say "pool is empty, waiting for a scrape"
    sleep 20
  done
fi

if [ "${last:-0}" -le 0 ]; then
  notify "No jobs collected today" "nothing was built"
  end_with info "the job pool is empty, so there was nothing to build" \
    "Collect first: press + Arbeitnow, or run your saved searches." 0
fi

# ---------------------------------------------------------------- rank locally
#
# Every scraped description is read by a local model before Claude sees
# anything. This is the whole point of the stage: reading and extracting is
# cheap, mechanical work that an 8B model on this laptop does well enough, and
# writing a CV is not. On 4 September Claude ranked a 109 job pool itself and
# spent the entire usage window without producing a single document.
#
# The ranker writes out/jobs.json containing ONLY the top N, so /apply-batch
# never sees the rest. N comes from TOP_N in the scraper's config.py, or from
# the first argument to this script.
SCRAPER_DIR="${SCRAPER_DIR:-$REPO_ROOT/scraper}"

# Two numbers, not one. BUILD_TARGET is how many applications should exist at
# the end; TOP_N is how many candidates the ranker hands over to reach it. They
# differ because some roles can only be rejected after the description is read,
# and a batch of exactly 15 candidates produced 6 documents on 18 September.
read -r CFG_TARGET CFG_MAX CFG_TOP <<EOF
$("$SCRAPER_DIR/.venv/bin/python" -c "
import sys; sys.path.insert(0, '$SCRAPER_DIR')
import config
print(config.BUILD_TARGET, getattr(config, 'BUILD_TARGET_MAX', config.BUILD_TARGET),
      config.TOP_N)" 2>/dev/null || echo "15 20 20")
EOF
BUILD_TARGET="${BATCH_SIZE:-${CFG_TARGET:-15}}"
BUILD_TARGET_MAX="${BATCH_SIZE:-${CFG_MAX:-$BUILD_TARGET}}"
# Keep the same ratio when an explicit target is passed on the command line.
RANK_TOP=$(( BUILD_TARGET + (BUILD_TARGET + 2) / 3 ))
[ -n "${BATCH_SIZE:-}" ] || RANK_TOP="${CFG_TOP:-20}"

# Hand over enough candidates to reach the CEILING, not the floor.
#
# The target can grow after the rank (see below), but the ranker has already run
# by then and ranked.json is already written — so if the handover were sized for
# a target of 15, a target that grew to 20 would have nothing to grow into.
# Overshooting is free: /apply-batch stops at BUILD_TARGET and never reads the
# rest. Decision, 24 September 2026.
_ceil_top=$(( BUILD_TARGET_MAX + (BUILD_TARGET_MAX + 2) / 3 ))
[ "$RANK_TOP" -ge "$_ceil_top" ] || RANK_TOP="$_ceil_top"

# Ollama has to be up. It is a normal user process, not a service, so a laptop
# that rebooted has it installed and not running, which would otherwise fail
# every job in the pool one timeout at a time.
if ! curl -s --max-time 4 -o /dev/null http://127.0.0.1:11434/api/version; then
  say "ollama is not running, starting it"
  nohup ollama serve >> "$LOG_DIR/ollama.log" 2>&1 &
  for _ in $(seq 1 20); do
    sleep 1
    curl -s --max-time 3 -o /dev/null http://127.0.0.1:11434/api/version && break
  done
fi
if ! curl -s --max-time 4 -o /dev/null http://127.0.0.1:11434/api/version; then
  notify "Ollama is down" "nothing was built"
  end_with failed "Ollama is not running and could not be started, so nothing was ranked" \
    "Open the Ollama app (or run: ollama serve), then press Retry." 1
fi

say "ranking $last jobs with the local model (this is the slow part)"
notify "Ranking $last jobs locally…"
say "target $BUILD_TARGET built, from $RANK_TOP ranked candidates"
if ! "$SCRAPER_DIR/.venv/bin/python" "$SCRAPER_DIR/rank_ollama.py" \
       --top "$RANK_TOP" 2>&1 | tee -a "$LOG"; then
  notify "Local ranking failed" "nothing was built"
  end_with failed "the local ranking failed, so nothing was built from an unranked pool" \
    "Press Retry: jobs already judged are cached, so it resumes. Details are in the build log." 1
fi

# Grow the target to match a strong pool. Decision, 24 September 2026.
#
# "Strong" is the ranker's own count of roles scoring >= STRONG_SCORE, which is
# a much tighter bar than "passed the gates": on 23 September 49 passed but only
# 26 were strong. Before this, a scrape like that built 15 and discarded 11
# strong roles, because BUILD_TARGET was a fixed number rather than a floor.
#
# The ranker has already sorted by fit (sort_key: rank_score, then title
# closeness, then Berlin precision), and ranked.json is that order sliced, so
# growing the target simply reaches further down a list that is already ordered
# best-first. The strongest always build; growth only ever adds weaker ones
# after them, never displaces a better role.
_strong=$("$SCRAPER_DIR/.venv/bin/python" -c "
import json, pathlib
p = pathlib.Path('$SCRAPER_DIR/out/ollama_rank.json')
print(json.loads(p.read_text())['health']['strong'] if p.exists() else 0)" \
  2>/dev/null || echo 0)
if [ "${_strong:-0}" -gt "$BUILD_TARGET" ]; then
  _grown="$_strong"
  [ "$_grown" -le "$BUILD_TARGET_MAX" ] || _grown="$BUILD_TARGET_MAX"
  if [ "$_grown" -gt "$BUILD_TARGET" ]; then
    say "$_strong strong candidates — target rises from $BUILD_TARGET to $_grown (ceiling $BUILD_TARGET_MAX)"
    BUILD_TARGET="$_grown"
    # Tell the dashboard, or its denominator stays at the old number.
    #
    # out/.build_target is the durable channel serve.py already reads (it was
    # added so a bridge restart could not lose the figure). An autobuild passes
    # no target at all, so without this line the tracker would read "of 15"
    # while the build was actually making 20 — exactly the mismatch they hit on
    # 23 September.
    printf '%s' "$BUILD_TARGET" > "$SCRAPER_DIR/out/.build_target" 2>/dev/null || true
  fi
fi

# From here on the pool is the ranked file, not the raw scrape.
# ranked.json, not jobs.json. The latter belongs to the old run.py pipeline and
# the bridge rewrites it on every push: reading it here printed "90 roles passed
# and are going to Claude" for a batch of 20.
ranked=$("$SCRAPER_DIR/.venv/bin/python" -c "
import json, pathlib
p = pathlib.Path('$SCRAPER_DIR/out/ranked.json')
print(len(json.loads(p.read_text())) if p.exists() else 0)" 2>/dev/null || echo 0)
if [ "${ranked:-0}" -le 0 ]; then
  notify "No roles passed the filters" "nothing was built"
  end_with info "no job passed the ranking filters, so nothing was built" \
    "See why on the Fit ranking page (tick Show dropped). Collect more, or loosen a filter in scraper/config_local.py." 0
fi
# ---------------------------------------------------------------- still open?
#
# Decision, 19 September 2026: of 15 applications built, several could not be
# sent because the posting had closed. A scrape is a snapshot and a CV takes a
# day to reach the employer, so the pool is checked against the live posting
# before anything is written for it. Cheap: one request per candidate, no
# session needed, and anything that cannot be checked is kept rather than
# dropped on a bot wall's say-so.
say "checking which of the $ranked are still open"
"$SCRAPER_DIR/.venv/bin/python" "$SCRAPER_DIR/check_live.py" 2>&1 | tee -a "$LOG"
ranked=$("$SCRAPER_DIR/.venv/bin/python" -c "
import json, pathlib
p = pathlib.Path('$SCRAPER_DIR/out/ranked.json')
print(len(json.loads(p.read_text())) if p.exists() else 0)" 2>/dev/null || echo 0)
if [ "${ranked:-0}" -le 0 ]; then
  notify "All candidate postings have closed" "nothing was built"
  end_with info "every candidate posting has closed, so nothing was built" \
    "Collect fresh jobs, then build again." 0
fi

say "$ranked roles passed and are going to Claude"
last="$ranked"

# BUILD_TARGET is a CEILING, not a quota. Decision, 19 September 2026.
#
# It used to be a number the run chased for three rounds however few candidates
# existed. With 10 roles in the pool and a target of 15 that meant two extra
# rounds asking Claude to build five applications that had no postings behind
# them, and a run that never reported itself complete. Worse, the gate upstream
# refused to start at all, so ten perfectly good roles were thrown away and
# re-scraped for the crime of not being fifteen.
#
# Now: build everything that passed, up to the target. Ten passed, ten get
# built, and the run finishes.
# The target counts everything built today, and some of it may already exist
# (a resumed run, or a second batch on the same day), so the pool only has to
# cover the REMAINDER. Clamping against the whole target instead would set the
# target below what is already on disk and the build loop would exit having
# built nothing.
_have=$(n_built)
_want=$(( BUILD_TARGET - _have ))
if [ "$_want" -gt 0 ] && [ "$ranked" -lt "$_want" ]; then
  say "pool is short of a full batch — target drops from $BUILD_TARGET to $((_have + ranked)) ($_have already built + $ranked available)"
  BUILD_TARGET=$(( _have + ranked ))
fi

# ---------------------------------------------------------------- environment
# MODEL: this runs on the Claude Pro subscription, not on API billing, so a run
# spends usage quota rather than dollars. --max-budget-usd was removed for that
# reason: it caps API spend and does nothing on a subscription, so leaving it in
# would have read like a safety net that was not there.
MODEL="${MODEL:-claude-sonnet-5}"

# Everything /apply-batch needs to read, and two of them are outside this
# folder. Running in this directory covers the skill invocation, CLAUDE.md,
# reference/, the venv and applications/, but not:
#
#   Job Scraper   step 0 of the command reads ../Job Scraper/out/audit.txt in
#                 full, and shortlist.py reads out/jobs.json through the path in
#                 pipeline.json. Without this the audit review is skipped
#                 silently, which is the step that catches wrongly rejected jobs.
#   PDF PERSONAL  the reference CVs named in pipeline.json.
#
# Read access is refused outside the working directory unless it is listed here,
# and a refusal mid-run reads like the command misbehaving rather than a missing
# path.
REF_CVS="${REF_CVS:-$PROFILE_DIR}"

# Resolve the binary rather than trusting PATH. launchd hands an agent a bare
# PATH that does not include ~/.local/bin, which is where the native install
# lives now that the old npm copy at /usr/local/bin is gone. Left to PATH this
# would fail with "claude: command not found" every morning while working
# perfectly whenever tested by hand from a normal shell.
CLAUDE_BIN=""
for c in "$HOME/.local/bin/claude" /usr/local/bin/claude /opt/homebrew/bin/claude; do
  [ -x "$c" ] && { CLAUDE_BIN="$c"; break; }
done
[ -z "$CLAUDE_BIN" ] && CLAUDE_BIN="$(command -v claude 2>/dev/null)"
if [ -z "$CLAUDE_BIN" ]; then
  notify "claude binary not found"
  end_with failed "Claude Code is not installed, so nothing could be written" \
    "Install it: curl -fsSL https://claude.ai/install.sh | bash, then press Retry." 1
fi

# ---------------------------------------------------------------- watchdog + shards
#
# Two limits, because a build has two very different phases and one number for
# both is wrong. claude -p prints nothing until it exits, so the only signal
# that it is alive is documents appearing under today's folder.
#
#   STARTUP_GRACE   Before the FIRST new document, Claude is reading: the whole
#                   shortlist of descriptions, the skill, PROJECTS.md and the
#                   reference docs, then planning. On 20 September that reading
#                   phase alone ran 9m24s before the first PLAN.md hit disk, with
#                   nothing written the entire time. The old flat 12-minute
#                   watchdog killed a perfectly healthy build the moment the pool
#                   grew big enough to push reading past twelve minutes. So allow
#                   a generous silence until the first file.
#   CLAUDE_STALL    Once documents are appearing, a healthy build writes PLAN.md,
#                   JSON and PDFs every couple of minutes. A gap this long now
#                   really is a hang (a dropped network, a wedged call), so kill
#                   it and let the retry loop start fresh.
STARTUP_GRACE=$(( ${STARTUP_GRACE_MIN:-25} * 60 ))
CLAUDE_STALL=$(( ${CLAUDE_STALL_MIN:-12} * 60 ))

# How many parallel Claude sessions build one round. Splitting the batch across
# sessions cuts wall-clock roughly in proportion (two sessions ~= half the time)
# for the same total usage, spent concurrently. Each session is a "shard" and
# sees a disjoint slice of the ranked pool (shortlist.py honours APPLY_SHARD /
# APPLY_SHARDS), builds into its own company folders, and writes its own batch
# fragment; this script merges the fragments and renders the report once, after
# all shards finish. A batch too small to be worth splitting runs single.
SHARDS="${SHARDS:-2}"
SHARD_MIN=4        # fewer than this to build and it is one session, not two

# Watch one claude pid and kill it if it goes silent for too long. Phase-aware:
# STARTUP_GRACE until the first new document, CLAUDE_STALL after. Watches the
# whole day folder, which is shared across shards — a cost worth naming: a
# healthy shard still writing keeps a hung sibling's timer alive until it
# finishes, so a stuck shard is caught within CLAUDE_STALL of the last sibling
# write rather than of its own. Acceptable: shards start together and write
# around the same time, and the run still completes.
_watch() {
  local cpid="$1"
  local base prev now cur last_change writing=0 limit
  base=$(find "$TODAY_DIR" -type f 2>/dev/null | wc -l | tr -d " ")
  prev="$base"; last_change=$(date +%s)
  while kill -0 "$cpid" 2>/dev/null; do
    sleep 30
    cur=$(find "$TODAY_DIR" -type f 2>/dev/null | wc -l | tr -d " ")
    now=$(date +%s)
    [ "$cur" != "$prev" ] && { prev="$cur"; last_change="$now"; }
    [ "$cur" -gt "$base" ] && writing=1
    limit="$STARTUP_GRACE"; [ "$writing" = "1" ] && limit="$CLAUDE_STALL"
    if [ $(( now - last_change )) -ge "$limit" ]; then
      echo "[$(date +%H:%M:%S)] watchdog: no new documents for $(( (now-last_change)/60 )) min (limit $((limit/60)) min, phase=$([ "$writing" = 1 ] && echo building || echo reading)) — killing claude ($cpid)" >> "$LOG"
      kill -TERM "$cpid" 2>/dev/null; sleep 5; kill -9 "$cpid" 2>/dev/null
      break
    fi
  done
}

# Run one shard: a single claude -p /apply-batch call under the watchdog.
# Args: want, shard_index, shards_total, output_file. Returns claude's status.
build_shard() {
  local want_s="$1" idx="$2" tot="$3" out="$4" cpid wpid st
  APPLY_SHARD="$idx" APPLY_SHARDS="$tot" "$CLAUDE_BIN" -p "/apply-batch${want_s:+ $want_s}" \
    --permission-mode bypassPermissions \
    --model "$MODEL" \
    --add-dir "$SCRAPER" \
    --add-dir "$REF_CVS" \
    > "$out" 2>&1 &
  cpid=$!
  ( _watch "$cpid" ) &
  wpid=$!
  wait "$cpid" 2>/dev/null; st=$?
  kill "$wpid" 2>/dev/null; wait "$wpid" 2>/dev/null
  return "$st"
}

# ---------------------------------------------------------------- build, resuming
for round in $(seq 1 "$MAX_ATTEMPTS"); do
  have=$(n_built)
  # Stop on the TARGET, not on the marker existing. batch.html is rewritten
  # every round, so testing for it here ended the loop after one round however
  # far short of the target it finished.
  [ "$have" -ge "$BUILD_TARGET" ] && break

  # An explicit count shrinks by whatever is already done; no count stays no
  # count, so a resumed run still builds every remaining role that fits.
  if [ -n "$BUILD_TARGET" ]; then
    want=$((BUILD_TARGET - have))
    [ "$want" -le 0 ] && want=1     # documents exist but no report: finish it
    want_txt="$want"
  else
    want=""
    want_txt="every remaining role that fits"
  fi

  # RESUME.md is how the command learns what not to rebuild. It is read in
  # step 5 of apply-batch.md. Writing it as a file rather than passing it in the
  # prompt keeps the slash command's own arguments untouched.
  if [ "$have" -gt 0 ]; then
    {
      echo "# Resume notice"
      echo
      echo "A previous attempt today already built these companies. Their PDFs are"
      echo "on disk and verified. Do NOT rebuild them, do NOT re-rank them, and do"
      echo "NOT drop them from batch.json: carry their existing entries through."
      echo
      built_companies | sed 's/^/- /'
      echo
      echo "Build the number the command asks for, excluding the companies above."
    } > "$TODAY_DIR/RESUME.md"
      say "resuming: $have already built, asking for $want_txt"
  else
    rm -f "$TODAY_DIR/RESUME.md"
  fi

  [ "$round" -gt 1 ] && notify "Retrying build (round $round of $MAX_ATTEMPTS)"

  # This round's output goes to its own file as well as the log.
  #
  # The checks below used to grep $LOG, which is the whole day appended into one
  # file. On 3 September that meant matching "nothing to build" written by a run
  # five hours earlier, so a build that had actually been cut off by a usage
  # limit was recorded as "the pool had nothing eligible" and never retried.
  # A verdict about this round has to be read from this round.
  ROUND_OUT="$LOG_DIR/.round-$DAY-$round.out"
  : > "$ROUND_OUT"
  rm -f "$LOG_DIR/.round-$DAY-$round".shard-*.out

  # How many sessions this round. A small batch is not worth splitting: the
  # fixed reading cost (skill, PROJECTS.md, references) is paid once per session,
  # so two sessions building one document each is slower, not faster, than one
  # session building two.
  nshards="$SHARDS"
  { [ -z "$want" ] || [ "$want" -lt "$SHARD_MIN" ]; } && nshards=1

  if [ "$nshards" -le 1 ]; then
    say "round $round: /apply-batch ${want:-<all>} on $MODEL (single session)"
    build_shard "${want}" 1 1 "$LOG_DIR/.round-$DAY-$round.shard-1.out"
    status=$?
    cat "$LOG_DIR/.round-$DAY-$round.shard-1.out" >> "$ROUND_OUT"
  else
    # Parallel. Split want across shards as evenly as possible (ceil first), then
    # launch each as a background shard. Each writes batch.shard-<i>.json and
    # skips the report; we merge and render once, below, after all have finished.
    say "round $round: /apply-batch $want split across $nshards parallel sessions on $MODEL"
    notify "Building $want applications across $nshards parallel sessions…"
    pids=(); i=1; rem="$want"
    while [ "$i" -le "$nshards" ]; do
      left=$(( nshards - i + 1 ))
      ws=$(( (rem + left - 1) / left ))
      rem=$(( rem - ws ))
      out="$LOG_DIR/.round-$DAY-$round.shard-$i.out"
      build_shard "$ws" "$i" "$nshards" "$out" &
      pids+=($!)
      say "  shard $i/$nshards: building $ws (pid $!)"
      i=$(( i + 1 ))
    done
    status=0
    for p in "${pids[@]}"; do wait "$p" || status=1; done
    for f in "$LOG_DIR/.round-$DAY-$round".shard-*.out; do
      [ -f "$f" ] && { echo "----- $(basename "$f") -----" >> "$ROUND_OUT"; cat "$f" >> "$ROUND_OUT"; }
    done
    say "all shards finished — merging fragments and rendering the report"
    .venv/bin/python merge_batches.py "$TODAY_DIR" 2>&1 | tee -a "$LOG"
    .venv/bin/python batch_report.py "$DAY" 2>&1 | tee -a "$LOG"
  fi
  cat "$ROUND_OUT" >> "$LOG"
  say "claude exited $status"

  # A usage limit is not a failure of the pool and not something a retry ten
  # minutes later can fix: the quota resets at a stated time. Stop, say when,
  # and leave the attempt budget untouched so the next run starts clean. The
  # work already on disk is kept and RESUME.md will skip it.
  if grep -qiE "hit your limit|usage limit|rate limit|quota" "$ROUND_OUT"; then
    when=$(grep -oiE "resets [^.]*" "$ROUND_OUT" | head -1)
    notify "Claude usage limit reached" "${when:-try again after it resets}"
    rm -f "$STATE"
    finish_report
    end_with stopped "Claude usage limit reached${when:+ ($when)}. $(n_built) built so far" \
      "Press Retry after the limit resets. Finished applications are kept and skipped." 0
  fi

  # An expired or revoked OAuth token is the one failure retrying cannot fix,
  # because headless has no way to prompt for a login. Without this check the
  # run would burn all three attempts against a wall and then look, from the
  # outside, exactly like the scheduler never firing at all.
  # This round's output only. Grepping the whole day's log meant one sign-in
  # failure in the morning failed every later build that day, even after
  # signing back in.
  if grep -qiE "OAuth access token|authentication_error|Failed to authenticate|Invalid API key|Please run /login|Not logged in" "$ROUND_OUT"; then
    notify "Claude is signed out. Run: claude auth login" "No applications built"
    rm -f "$STATE"
    end_with failed "Claude is signed out, so nothing could be written" \
      "In a terminal run: claude auth login, then press Retry." 1
  fi

  # Claude stops ON PURPOSE when it cannot write honestly: the profile is still
  # the example, the skill did not load, nothing is buildable. It says so with
  # a BATCH-STOP line. Retrying a deliberate stop only re-spends usage to hear
  # the same answer, which is exactly what a first test run did.
  if grep -q "BATCH-STOP:" "$ROUND_OUT"; then
    why=$(grep -o "BATCH-STOP:.*" "$ROUND_OUT" | head -1 | cut -c12- | cut -c1-160)
    notify "Build stopped" "${why:-see the build log}"
    rm -f "$STATE"
    finish_report
    end_with stopped "Claude stopped the batch on purpose:${why}" \
      "Fix what it names, then press Retry." 0
  fi

  now_built=$(n_built)
  say "after round $round: $now_built built${BUILD_TARGET:+/$BUILD_TARGET}"

  [ "$now_built" -ge "$BUILD_TARGET" ] && break

  # "There was nothing eligible" is a verdict, not a failure. Retrying it just
  # re-reads the same pool and reaches the same answer, which on 3 September
  # burned three rounds and half an hour to be told three times that every job
  # was a duplicate. Only retry when something actually broke.
  if grep -qiE "nothing to build|no batch to build|zero (eligible|ranked|open) roles?|nothing to rank|no eligible roles" "$ROUND_OUT"; then
    notify "No eligible roles today" "nothing was built"
    finish_report
    end_with info "Claude read the candidates and found nothing eligible to build" \
      "Its reasons are on the Applications page. Collect more jobs and build again." 0
  fi
  if [ "$now_built" -le "$have" ] && [ "$round" -lt "$MAX_ATTEMPTS" ]; then
    # No progress at all. A short pause before spending another attempt, mostly
    # so a transient network or rate-limit blip is not retried inside the same
    # second. The round check matters: sleeping after the LAST round is a
    # process sitting there having already decided to give up.
    say "no progress this round, waiting $((RETRY_GAP / 60)) min before retrying"
    sleep "$RETRY_GAP"
  fi
done

# ---------------------------------------------------------------- report
# One file on the Desktop that always opens the newest report.
#
# A symlink would be shorter but the report links to its PDFs with relative
# paths, and how those resolve through a symlink depends on the browser. A tiny
# redirect page carries an absolute file:// URL instead, so the links work the
# same way every time, and the Desktop file itself never has to move.
publish_desktop_link() {
  local target="$PWD/$TODAY_DIR/batch.html"
  local jobs="$SCRAPER_DIR/out/jobs.html"
  local out="$BUILDER_ROOT/applications/index.html"   # inside the repo, not ~/Desktop
  {
    echo "<!doctype html><meta charset=utf-8>"
    echo "<title>Job Applications</title>"
    echo "<meta http-equiv=refresh content=\"0; url=file://${target// /%20}\">"
    echo "<p>Opening today's applications…"
    echo "<p><a href=\"file://${target// /%20}\">Today's batch ($DAY)</a>"
    [ -f "$jobs" ] && echo "<p><a href=\"file://${jobs// /%20}\">All scraped jobs</a>"
  } > "$out"
  say "desktop link updated -> $target"
}

finish_report
final=$(n_built)
if [ -f "$TODAY_DIR/batch.html" ] && [ "$final" -gt 0 ]; then
  say "done: $final application folder(s) in $TODAY_DIR"
  publish_desktop_link
  notify "$final applications ready — open 'Job Applications' on your Desktop" "$TODAY_DIR"
  rm -f "$TODAY_DIR/RESUME.md"
elif [ "$final" -gt 0 ]; then
  say "$final built but no batch.html — will finish the report on the next attempt"
  notify "$final built, report incomplete" "retrying later"
else
  notify "Batch built nothing. Check the log." "$LOG"
fi
say "=== attempt $attempt done ==="
if [ "$final" -ge "${BUILD_TARGET:-0}" ] && [ "$final" -gt 0 ]; then
  end_with ok "$final application(s) built today" "" 0
elif [ "$final" -gt 0 ]; then
  end_with stopped "$final of ${BUILD_TARGET} built after $MAX_ATTEMPTS rounds" \
    "Press Retry to build the rest. Finished applications are kept and skipped." 0
else
  end_with failed "the build ran but produced no applications" \
    "Open the build log from the dashboard to see why, then press Retry." 1
fi
