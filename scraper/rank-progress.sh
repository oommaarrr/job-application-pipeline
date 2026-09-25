#!/bin/bash
# How far the local ranking has got. Safe to run at any time, changes nothing.
cd "$(dirname "${BASH_SOURCE[0]}")" || exit 1
if pgrep -f "rank_ollama.py" > /dev/null; then echo "ranking: RUNNING"; else echo "ranking: not running"; fi
.venv/bin/python - <<'PY'
import json, hashlib, pathlib, datetime, glob
from jobkey import job_key
import config
import ollama_model
model = ollama_model.resolve(config) or ollama_model.wanted(config)
try:
    cache = json.loads(pathlib.Path('out/ollama_cache.json').read_text())
except Exception:
    cache = {}
day = datetime.date.today().isoformat()
files = sorted(glob.glob(f'inbox/job-collector-{day}.json')) or sorted(glob.glob('inbox/job-collector-*.json'))
if not files:
    raise SystemExit("no inbox yet")
d = json.loads(pathlib.Path(files[-1]).read_text())
jobs = d['jobs'] if isinstance(d, dict) else d
# Exclude what the ranker itself excludes, or the total is wrong and a finished
# run looks stuck: on 18 September this read 130/150 for a run that had judged
# every one of its 131 candidates.
from applied_index import index as applied_index, role_key
applied_urls, applied_roles = applied_index()

def seen(j):
    k = role_key(j.get('company', ''), j.get('title', ''))
    if k and k in applied_roles:
        return True
    u = (j.get('url') or '').split('?')[0].rstrip('/').lower()
    return job_key(j.get('url', '')) in applied_urls or u in applied_urls

pool = [j for j in jobs if (j.get('description') or '').strip() and not seen(j)]
done = sum(1 for j in pool
           if f"{job_key(j['url'])}|{model}|3|"
              f"{hashlib.sha1((j.get('description') or '').encode()).hexdigest()[:12]}" in cache)
pct = 100 * done // max(1, len(pool))
bar = "#" * (pct // 4) + "." * (25 - pct // 4)
print(f"  [{bar}] {done}/{len(pool)} judged ({pct}%)   source: {files[-1]}")
PY
echo
echo "last lines of the result file:"
tail -6 out/ollama_rank.txt 2>/dev/null || echo "  (not written yet — it lands when the run finishes)"
