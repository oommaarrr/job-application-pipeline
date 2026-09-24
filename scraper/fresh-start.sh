#!/bin/bash
#
# Wipe the scraped pool, keep the record of what was applied for.
#
# Decision, 19 September 2026: after each scrape-and-build cycle, start
# clean rather than carrying months of postings forward.
#
# WHAT IS DELETED
#   inbox/*.json            the scraped postings and their descriptions
#   out/ranked.json         the ranking handed to Claude
#   out/ollama_cache.json   the local model's answers
#   out/jobs.* all_jobs.*   the old run.py pipeline's output
#   out/scrape_audit.*      per-search audit of the last scrape
#   seen_jobs.json          the "already collected" history
#
# WHAT IS KEPT, AND WHY IT MUST BE
#   applied.json                    what was actually sent
#   ../CV Builder/applications/*/   the PDFs, and batch.json
#
# batch.json is not a souvenir. It is the ONLY record of which roles have
# already been built, and it is what the repeat filter reads: delete it and the
# pipeline will happily write a second CV for a job applied to last week. Keep
# the folders even after the PDFs are sent.
set -u
cd "$(dirname "$0")" || exit 1
BUILDER="$(cd "$(dirname "${BASH_SOURCE[0]}")/../builder" && pwd)"
KEEP_DAYS="${KEEP_DAYS:-0}"     # KEEP_DAYS=2 keeps the last 2 days of scrapes

if [ "${1:-}" != "--yes" ]; then
  usable=$(.venv/bin/python pool_status.py --human 2>/dev/null)
  echo "About to clear the scraped pool."
  echo "  now: $usable"
  echo
  echo "KEPT:     applied.json, and every applications/<date>/ folder"
  echo "DELETED:  inbox, ranking, ollama cache, seen_jobs, audits"
  echo
  read -r -p "Type yes to continue: " ans
  [ "$ans" = "yes" ] || { echo "nothing was deleted"; exit 0; }
fi

TS=$(date +%Y%m%dT%H%M%S)
DEST="purged-$TS"
mkdir -p "$DEST"

moved=0
for f in inbox/job-collector-*.json; do
  [ -e "$f" ] || continue
  if [ "$KEEP_DAYS" -gt 0 ]; then
    day=$(echo "$f" | sed -n 's/.*\([0-9]\{4\}-[0-9]\{2\}-[0-9]\{2\}\).*/\1/p')
    cutoff=$(date -v-"${KEEP_DAYS}"d +%F 2>/dev/null || echo "0000-00-00")
    [ -n "$day" ] && [ "$day" \> "$cutoff" ] && continue
  fi
  mv "$f" "$DEST/" && moved=$((moved + 1))
done
[ -d inbox/archive ] && { mv inbox/archive "$DEST/archive" 2>/dev/null; mkdir -p inbox/archive; }
mv seen_jobs.json "$DEST/" 2>/dev/null
for f in ranked.json ollama_cache.json ollama_rank.json ollama_rank.txt \
         jobs.json jobs.html jobs.md all_jobs.json audit.json audit.txt \
         scrape_audit.json scrape_audit.txt; do
  mv "out/$f" "$DEST/" 2>/dev/null
done
mv "$BUILDER/shortlist.json" "$DEST/" 2>/dev/null

# Tell the bridge, so its in-memory counts match the disk.
curl -s -m 4 -X POST -H 'Content-Type: application/json' \
     -d '{"hard": true}' http://127.0.0.1:${BRIDGE_PORT:-8765}/reset > /dev/null 2>&1

echo
echo "Cleared. $moved scrape file(s) archived to $DEST/"
echo "Kept: applied.json ($(python3 -c "
import json
try:
    d=json.load(open('applied.json')); a=d.get('applied',d)
    print(len(a))
except Exception: print(0)") entries) and $(ls -d "$BUILDER"/applications/*/ 2>/dev/null | wc -l | tr -d ' ') application folder(s)"
echo
echo "The extension keeps its own copy. To clear that too:"
echo "  popup -> Maintenance -> Erase all jobs and start fresh"
