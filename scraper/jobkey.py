"""One canonical key per job.

Stripping the query is right for LinkedIn and StepStone, where the path
identifies the job, and wrong for Indeed, where it does not: every posting is
/viewjob?jk=<id> or /rc/clk?jk=<id>. Under the old key an entire Indeed page
collapsed into one entry, which is why a search showing twenty-five jobs
reported two.

This lived in four copies across serve.py, runner.py and fetchers.py, plus the
extension. Four copies of a rule is four chances to fix three of them.
"""

from __future__ import annotations

import re

_JK = re.compile(r"[?&](?:jk|vjk)=([0-9a-zA-Z]{6,})")
_LI = re.compile(r"/jobs/view/(\d+)")
_LI_PARAM = re.compile(r"[?&]currentJobId=(\d+)")


def job_key(url: str) -> str:
    u = (url or "").strip()
    if not u:
        return ""
    m = _JK.search(u)
    if m:
        return f"indeed:{m.group(1).lower()}"
    m = _LI.search(u) or _LI_PARAM.search(u)
    if m:
        return f"linkedin:{m.group(1)}"
    return u.split("?")[0].rstrip("/").lower()
