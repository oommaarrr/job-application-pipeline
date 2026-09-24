#!/usr/bin/env python3
"""
What has already been applied for, matched on the ROLE rather than the link.

Decision, 18 September 2026: filter on the job title, not the URL.

The URL was never a good key. The same posting appears on LinkedIn, StepStone
and the company's own site with three different links, and LinkedIn alone gives
one job two different URLs depending on whether it was opened from a card or
from the results pane. Once, a batch built CVs for two roles that had both been
applied to weeks earlier, because their links this time round differed from the
links recorded then.

A role is (company, title), normalised. That travels across job boards, which is
exactly what a link does not do.

Two sources, because neither is complete on its own:

  - applied.json, which is a bare list of URLs with no titles at all, so it can
    only ever be matched by link. Kept, because it is the record of what was
    actually sent.
  - every applications/<date>/batch.json, which carries company and title for
    each document built. That is the title source.
"""

from __future__ import annotations

import json
import pathlib
import re

from jobkey import job_key
import unicodedata

HERE = pathlib.Path(__file__).parent
# builder/ sits next to scraper/ in the monorepo. This is what stops a job that
# was already BUILT (not just sent) from being handed to Claude again.
BATCHES = HERE.parent / "builder" / "applications"
# Archived batches (moved aside by a fresh start) still count for dedup, so a
# rebuild never redoes a role that an earlier, archived batch already built.
ARCHIVE_BATCHES = BATCHES / "archive"


def _batch_files():
    files = list(BATCHES.glob("*/batch.json"))
    files += list(ARCHIVE_BATCHES.glob("*/batch.json"))
    # De-dupe by path and keep the archive dir itself from matching as a "day".
    return sorted({f for f in files if f.parent.name != "archive"})
APPLIED = HERE / "applied.json"

# Legal forms and gender markers carry no meaning for identity and differ freely
# between boards: "Acme GmbH" on one and "Acme" on another is the same employer.
COMPANY_NOISE = re.compile(
    r"\b(gmbh|mbh|ag|se|kg|ug|ohg|e\.?v\.?|ltd|limited|inc|llc|plc|b\.?v\.?|"
    r"n\.?v\.?|s\.?a\.?|sarl|co|company|group|deutschland|germany)\b", re.I)
TITLE_NOISE = re.compile(
    r"\((?:[mwdfxgn][/\s]*)+\)|\(all genders?\)|\(gn\)|\(m/f/d\)|\(d/f/m\)|"
    r"\b[mwdfxgn](?:/[mwdfxgn])+\b|\(remote\)|\(hybrid\)|\(berlin\)", re.I)


def norm_company(s: str) -> str:
    s = _fold(s).lower()
    # Cut at the first separator: boards append the full legal name, the city,
    # or a tagline after one of these, and none of that is identity.
    s = re.split(r"[·|,(]| - | – | — ", s)[0]
    s = COMPANY_NOISE.sub(" ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    return " ".join(s.split())


def same_company(a: str, b: str) -> bool:
    """
    One name being a prefix of the other is the same employer.

    batch.json records whatever short name the writer chose, "ACME", while the
    scraper carries what the board printed, "ACME - Association for Creative
    Media and Entertainment e.V.". Exact comparison called
    those two different companies and let a role already built today come back
    as rank 1 in the very next batch.

    A prefix of at least three characters, so "AI" does not match "AI Superior".
    """
    a, b = norm_company(a), norm_company(b)
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = (a, b) if len(a) <= len(b) else (b, a)
    return len(short) >= 3 and long_.startswith(short + " ")


def _fold(s: str) -> str:
    """
    Plain ASCII, so a document written with transliterated umlauts still matches
    the posting it came from. "Consultant fuer Cybersecurity" in a CV is the same
    role as "Consultant für Cybersecurity" on StepStone, and stripping the umlaut
    naively turns "für" into "f r", which matches nothing.
    """
    s = (s or "")
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("ß", "ss"),
                 ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue")):
        s = s.replace(a, b)
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def norm_title(s: str) -> str:
    s = _fold(s).lower()
    s = TITLE_NOISE.sub(" ", s)
    # Seniority words are dropped on purpose. "Senior AI Engineer" and
    # "AI Engineer" at the same company, a month apart, is one role reposted,
    # and applying twice is the thing being prevented.
    s = re.sub(r"\b(senior|sr|junior|jr|lead|staff|principal|mid|level)\b", " ", s)
    s = re.sub(r"[^a-z0-9]+", " ", s)
    # Grammar words carry no identity and are exactly what drifts when a title
    # is rewritten for a document: "Applied AI Developer for the AI Frontrunner"
    # became "Applied AI Developer, AI Frontrunner team". Dropping them leaves
    # the words that actually name the role.
    stop = {"for", "the", "a", "an", "and", "of", "in", "at", "to", "with",
            "our", "team", "teams", "group", "department", "division", "unit",
            "m", "w", "d", "f", "x", "gn", "all", "genders", "divers"}
    return " ".join(w for w in s.split() if w not in stop)


def role_key(company: str, title: str) -> str:
    c, t = norm_company(company), norm_title(title)
    return f"{c}|{t}" if c and t else ""


def _read(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def applied_urls() -> set[str]:
    # history/applied.csv is the list now (ledger.py). applied.json is read too
    # while it still exists, so nothing is missed before the one-time migration.
    import ledger
    ledger_urls = ledger.applied_urls()
    rows = _read(APPLIED, [])
    rows = rows.get("applied", []) if isinstance(rows, dict) else rows
    out = set()
    for r in rows:
        url = r.get("url") if isinstance(r, dict) else r
        if not url:
            continue
        # job_key, not split("?"). Indeed identifies a posting ENTIRELY in the
        # query string — de.indeed.com/viewjob?jk=<id> — so stripping it left
        # the literal key "https://de.indeed.com/viewjob", which then matched
        # every Indeed posting ever scraped. One applied Indeed job was quietly
        # deleting the whole of Indeed from the pool: 36 roles on 19 September,
        # which is why a 118-job pool ranked only 82. Same bug as the one fixed
        # in CV Builder/shortlist.py, in a second place.
        out.add(job_key(str(url)))
    out |= ledger_urls

    # Roles already BUILT count too, by link as well as by title.
    #
    # applied.json only records what was actually sent, so a document built
    # this morning and not yet sent was invisible here and the title match was
    # the only defence. That fails on a mis-parsed card: one posting came back
    # with company="Share" and the employer's name in the title field, so
    # (company, title) matched nothing, and the very same
    # LinkedIn URL that had just been built came back as the top candidate for
    # the next batch. The URL was right there in batch.json.
    for path in _batch_files():
        payload = _read(path, {})
        rows = payload.get("built", []) if isinstance(payload, dict) else payload
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict) and r.get("url"):
                out.add(job_key(str(r["url"])))
    return out


def built_roles() -> dict[str, list[tuple[str, str]]]:
    """normalised title -> [(company as written, date built)]."""
    out: dict[str, list[tuple[str, str]]] = {}
    if not BATCHES.exists():
        return out
    for path in _batch_files():
        payload = _read(path, {})
        rows = payload.get("built", []) if isinstance(payload, dict) else payload
        date = payload.get("date") if isinstance(payload, dict) else path.parent.name
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            t = norm_title(r.get("title", ""))
            c = r.get("company", "")
            if t and c:
                out.setdefault(t, []).append((c, date or path.parent.name))
    return out


def built_before(company: str, title: str,
                 roles: dict[str, list[tuple[str, str]]]) -> str:
    """
    The date this role was built, or "" if it never was.

    Titles drift between the posting and the document written from it: a writer
    tidies "Lead AI Engineer (DRWN), GenAI & Voice Agents (freelance)" down to
    "Lead AI Engineer (DRWN)", or expands "for the AI Frontrunner" away. Exact
    comparison then calls them different roles and the next batch cheerfully
    rebuilds one already on disk, which is what happened on 19 September.

    So: exact match first, then one title containing the other, and only ever
    within the same company. Containment alone would be far too loose; paired
    with the company it is safe, because a company advertising two roles whose
    titles contain one another is the same role posted twice.
    """
    t = norm_title(title)
    if not t:
        return ""
    for c, when in roles.get(t, []):
        if same_company(company, c):
            return when

    # Same company, and one title's words are all present in the other.
    #
    # Word sets rather than substrings, because the drift is usually inserted
    # words rather than a clean truncation: "Applied AI Developer, AI
    # Frontrunner" against "Applied AI Developer for the AI Frontrunner" is the
    # same job, and neither string contains the other.
    #
    # The three word floor is what keeps this safe, and it is deliberately
    # conservative. One startup advertised both "Founding Engineer" and "Founding
    # Product Engineer", genuinely different roles, and the first is a two word
    # subset of the second: matching them would cost a real application. Letting
    # a duplicate through costs a rebuild that whoever writes it will notice and
    # drop, because they read the description. Missing a real role is the more
    # expensive mistake, so the rule errs that way.
    mine = set(t.split())
    if len(mine) >= 3:
        for other_t, entries in roles.items():
            if other_t == t or not other_t:
                continue
            theirs = set(other_t.split())
            if len(theirs) < 3:
                continue
            if not (mine <= theirs or theirs <= mine):
                continue
            for c, when in entries:
                if same_company(company, c):
                    return when
    return ""


def index() -> tuple[set[str], dict[str, str]]:
    return applied_urls(), built_roles()


if __name__ == "__main__":
    urls, roles = index()
    n = sum(len(v) for v in roles.values())
    print(f"{len(urls)} applied URLs · {n} roles built in past batches\n")
    rows = [(w, c, t) for t, v in roles.items() for c, w in v]
    for when, c, t in sorted(rows)[-12:]:
        print(f"  {when}  {c[:28]:<28} {t}")
