#!/usr/bin/env python3
"""
Entry point.

    ./.venv/bin/python run.py --inbox    ONLY process the Chrome-extension
                                         export in inbox/. No network, instant.
                                         Use this right after collecting.

    ./.venv/bin/python run.py            inbox + live StepStone scan
    ./.venv/bin/python run.py --quick    live scan, 1 page/query, no details
    ./.venv/bin/python run.py --no-open  don't open the browser at the end

Whatever is in inbox/ is always processed first and never blocked by a slow or
throttled live source.
"""

import pathlib
import sys
from collections import Counter

import config
import report
import runner

OUT = pathlib.Path(__file__).parent / "out"


def main() -> int:
    args = set(sys.argv[1:])

    if "--quick" in args:
        config.MAX_PAGES_PER_QUERY = 1
        config.FETCH_DESCRIPTIONS = False
        config.REQUEST_DELAY = (0.8, 1.6)
        print("⚡ quick mode: 1 page/query, no description fetches "
              "(German gate will read 'not mentioned' for everything)\n")

    live = "--inbox" not in args
    all_jobs, ranked, runs = runner.run(live=live)

    OUT.mkdir(exist_ok=True)
    html_path = report.build(ranked, all_jobs, runs, OUT / "jobs.html")
    md_path = report.write_markdown(ranked, all_jobs, runs, OUT / "jobs.md")
    json_path = report.write_json(ranked, OUT / "jobs.json")
    all_path = report.write_all_json(all_jobs, OUT / "all_jobs.json")
    audit_path = report.write_audit(all_jobs, ranked, OUT / "audit.json")

    print("\n" + "=" * 70)
    print(f"  {len(ranked)} matches · {len(all_jobs)} collected · "
          f"{sum(1 for r in runs if r.drifted)} queries drifted")
    print("=" * 70)

    # Where everything went, so a small match count is explainable at a glance
    # rather than looking like the scan failed.
    verdicts = Counter(j.extras.get("verdict", "unknown") for j in all_jobs)
    LABEL = {"passed": "passed all filters", "applied": "already applied",
             "seen-before": "seen in an earlier scrape",
             "off-profile": "off-profile title", "german": "German above B1",
             "years": f"requires {config.MAX_YEARS_REQUIRED}+ years"}
    for verdict, n in verdicts.most_common():
        print(f"  {n:>4}  {LABEL.get(verdict, verdict)}")
    print("=" * 70)

    for i, j in enumerate(ranked[:12], 1):
        yrs = j.experience.label
        print(f"  {i:>2}. {j.title[:52]:<52} {j.location[:20]:<20} "
              f"{yrs:<12} {j.language.badge}")
    if len(ranked) > 12:
        print(f"      … and {len(ranked) - 12} more in the report")

    unchecked = sum(1 for j in ranked if not j.language.checked)
    if unchecked:
        print(f"\n  ⚠ {unchecked}/{len(ranked)} are 'not checked' — no description was "
              f"fetched, so the German filter never saw them.\n"
              f"    Raise MAX_DETAIL_FETCHES in config.py to verify more.")

    print(f"\n  html  → {html_path}")
    print(f"  md    → {md_path}")
    print(f"  json  → {json_path}")
    print(f"  all   → {all_path}")
    print(f"  audit → {audit_path}   (one-pass filter review)")

    if "--no-open" not in args:
        from platform_util import open_path
        open_path(html_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
