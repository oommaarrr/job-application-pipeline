"""
The saved searches the Chrome extension runs, kept by the pipeline.

Until 28 September 2026 the list lived in the extension's own storage and was
only mirrored here, so every change meant opening the popup. Now the pipeline
holds it and the dashboard edits it; the extension pulls the list on every wake
(about once a minute) and runs it in the logged-in browser, which is the part
that still needs Chrome. The popup keeps one way to add to it: "Add the page I
am on".

The file is out/searches.json:

    {"version": 2, "pages": 1,
     "searches": [{"id", "label", "url", "enabled"}],
     "geo": {"berlin": "106967730"}}

An older copy is a bare list (the mirror the extension used to write); it is
read as the list and rewritten in this shape the first time it is loaded.

Standard library only.
"""

from __future__ import annotations

import datetime as dt
import json
import os
import pathlib
import re
import secrets
import threading
from urllib.parse import parse_qsl, unquote, urlencode, urlsplit, urlunsplit

FILE = pathlib.Path(__file__).resolve().parent / "out" / "searches.json"
VERSION = 2
PAGES = (1, 2, 3)
MAX_SEARCHES = 60
LABEL_MAX = 120

# The sites the extension can read. Anything else is refused, because the
# extension would open it and find nothing to collect.
SITES = (
    ("LinkedIn", re.compile(r"(^|\.)linkedin\.com$")),
    ("Indeed", re.compile(r"(^|\.)indeed\.com$")),
    ("StepStone", re.compile(r"(^|\.)stepstone\.de$")),
)

# A first install starts with one example per site, matching the example
# profile, so a new user sees all three working and can copy the pattern.
# Germany, last 7 days. The LinkedIn one pins the country by geoId, because
# LinkedIn falls back to the country on your profile when the text is vague.
EXAMPLES = [
    {"label": "Example: Machine Learning Engineer",
     "url": "https://www.linkedin.com/jobs/search/?keywords=Machine%20Learning%20Engineer"
            "&location=Germany&geoId=101282230&f_TPR=r604800"},
    {"label": "Example: Applied AI Engineer",
     "url": "https://de.indeed.com/jobs?q=Applied+AI+Engineer&l=Deutschland&fromage=7"},
    {"label": "Example: MLOps Engineer",
     "url": "https://www.stepstone.de/jobs?what=MLOps+Engineer&ag=7"},
]

# LinkedIn puts the selected job, the way the search was typed and tracking ids
# into the address bar. None of them change which jobs a search returns, and a
# week-old currentJobId made a saved search hang (18 September 2026).
VOLATILE = {"currentJobId", "origin", "refresh", "trk", "trackingId", "refId",
            "position", "pageNum", "eBP", "lipi"}

_lock = threading.Lock()


def site(url: str) -> str:
    """LinkedIn, Indeed or StepStone, or "" for a site the extension cannot read."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
    return next((name for name, rx in SITES if rx.search(host)), "")


def clean_url(url: str) -> str:
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    if site(url) != "LinkedIn":
        return url
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k not in VOLATILE]
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def keywords(url: str) -> str:
    try:
        parts = urlsplit(url)
    except ValueError:
        return ""
    q = dict(parse_qsl(parts.query))
    kw = q.get("keywords") or q.get("q") or q.get("what") or ""
    if not kw:
        # StepStone also writes the search into the path: /jobs/ml-engineer/in-berlin
        seg = [s for s in parts.path.split("/") if s]
        if len(seg) > 1 and seg[0] == "jobs":
            kw = unquote(seg[1]).replace("-", " ")
    return kw.strip()


def key(url: str) -> str:
    """
    Two URLs that run the same search. Site, place and keywords, so the same
    title searched in two countries stays two searches. Mirrors searchKey() in
    the extension, which used it for the same job before the list moved here.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return url
    host = (parts.hostname or "").removeprefix("www.").split(".")[0]
    q = dict(parse_qsl(parts.query))
    geo = q.get("geoId") or q.get("location") or q.get("l") or q.get("where") or ""
    return f"{host}:{geo.strip().lower()}:{keywords(url).lower()}"


def default_label(url: str) -> str:
    return f"{site(url) or 'Search'}: {keywords(url) or 'search'}"


def _check(row, errors: list[str], n: int):
    """One search from outside, cleaned, or None with the reason in errors."""
    if not isinstance(row, dict):
        errors.append(f"search {n}: not an object")
        return None
    url = clean_url(str(row.get("url") or ""))
    if not re.match(r"^https://", url, re.I):
        errors.append(f"search {n}: the address must start with https://")
        return None
    if not site(url):
        errors.append(f"search {n}: {urlsplit(url).hostname or url} is not LinkedIn, "
                      "Indeed or StepStone, so the extension cannot collect it")
        return None
    label = " ".join(str(row.get("label") or "").split())[:LABEL_MAX] or default_label(url)
    rid = str(row.get("id") or "")
    if not re.fullmatch(r"[0-9a-f]{8}", rid):
        rid = secrets.token_hex(4)
    return {"id": rid, "label": label, "url": url, "enabled": row.get("enabled") is not False}


def _dedupe(rows: list[dict]) -> list[dict]:
    seen_ids, seen_keys, out = set(), set(), []
    for r in rows:
        k = key(r["url"])
        if k in seen_keys:
            continue
        while r["id"] in seen_ids:
            r = {**r, "id": secrets.token_hex(4)}
        seen_ids.add(r["id"])
        seen_keys.add(k)
        out.append(r)
    return out


def _write(state: dict) -> None:
    FILE.parent.mkdir(exist_ok=True)
    tmp = FILE.with_suffix(f".{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, FILE)


def _load() -> dict:
    try:
        raw = json.loads(FILE.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raw = None
    except (OSError, ValueError):
        # Damaged: keep it aside rather than overwrite the only copy.
        try:
            os.replace(FILE, FILE.with_suffix(f".broken-{dt.datetime.now():%Y%m%d-%H%M%S}.json"))
        except OSError:
            pass
        raw = None
    if isinstance(raw, dict) and raw.get("version") == VERSION:
        state = raw
        changed = False
    else:
        rows = raw if isinstance(raw, list) else (EXAMPLES if raw is None else [])
        state = {"version": VERSION, "pages": 1, "searches": rows, "geo": {}}
        changed = True
    errors: list[str] = []
    rows = [r for i, x in enumerate(state.get("searches") or [], 1)
            if (r := _check(x, errors, i))]
    rows = _dedupe(rows)
    pages = state.get("pages") if state.get("pages") in PAGES else 1
    geo = {str(k): str(v) for k, v in (state.get("geo") or {}).items()
           if re.fullmatch(r"\d{3,15}", str(v))}
    tidy = {"version": VERSION, "pages": pages, "searches": rows, "geo": geo,
            "updated": state.get("updated")}
    if changed or tidy != state:
        tidy["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        try:
            _write(tidy)
        except OSError:
            pass
    return tidy


def load() -> dict:
    with _lock:
        return _load()


def active(state: dict | None = None) -> list[dict]:
    """The switched-on searches, the ones a run opens."""
    return [q for q in (state or load())["searches"] if q.get("enabled") is not False]


def save(searches=None, pages=None) -> tuple[dict, list[str]]:
    """
    Replace the list and/or pages. Nothing is written when any search is
    unusable: a half-saved list is worse than an error the page can show.
    """
    errors: list[str] = []
    with _lock:
        state = _load()
        if searches is not None:
            if not isinstance(searches, list):
                return state, ["searches must be a list"]
            if len(searches) > MAX_SEARCHES:
                return state, [f"at most {MAX_SEARCHES} saved searches"]
            rows = [r for i, x in enumerate(searches, 1) if (r := _check(x, errors, i))]
            if errors:
                return state, errors
            keys = [key(r["url"]) for r in rows]
            dupes = sorted({rows[i]["label"] for i, k in enumerate(keys) if keys.index(k) != i})
            if dupes:
                return state, [f"{d}: the same search is in the list twice" for d in dupes]
            state["searches"] = _dedupe(rows)
        if pages is not None:
            try:
                p = int(pages)
            except (TypeError, ValueError):
                p = 0
            if p not in PAGES:
                return state, [f"pages must be one of {', '.join(map(str, PAGES))}"]
            state["pages"] = p
        state["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        _write(state)
        return state, []


def add(url: str, label: str = "") -> tuple[dict, str, str]:
    """
    Add one search. Returns (state, outcome, why): outcome is "added",
    "duplicate" or "refused".
    """
    errors: list[str] = []
    row = _check({"url": url, "label": label}, errors, 1)
    with _lock:
        state = _load()
        if not row:
            return state, "refused", errors[0].removeprefix("search 1: ")
        k = key(row["url"])
        hit = next((q for q in state["searches"] if key(q["url"]) == k), None)
        if hit:
            return state, "duplicate", f"already saved as “{hit['label']}”"
        if len(state["searches"]) >= MAX_SEARCHES:
            return state, "refused", f"at most {MAX_SEARCHES} saved searches"
        state["searches"] = _dedupe([*state["searches"], row])
        state["updated"] = dt.datetime.now().isoformat(timespec="seconds")
        _write(state)
        return state, "added", ""


def merge(searches) -> tuple[dict, int]:
    """
    Add every search not already in the list. Used once, by an extension that
    still holds a list of its own from before the pipeline kept it, so moving
    the list here can never lose a search.
    """
    errors: list[str] = []
    with _lock:
        state = _load()
        have = {key(q["url"]) for q in state["searches"]}
        new = []
        for i, x in enumerate(searches if isinstance(searches, list) else [], 1):
            r = _check(x, errors, i)
            if r and key(r["url"]) not in have and len(state["searches"]) + len(new) < MAX_SEARCHES:
                have.add(key(r["url"]))
                new.append(r)
        if new:
            state["searches"] = _dedupe([*state["searches"], *new])
            state["updated"] = dt.datetime.now().isoformat(timespec="seconds")
            _write(state)
        return state, len(new)


def learn_geo(location: str, geo_id: str) -> bool:
    """
    Remember which LinkedIn geoId a typed place resolves to. LinkedIn's own
    search URL carries it, so the extension reports it whenever a LinkedIn
    search is open, and the dashboard's search builder reuses it. Nothing is
    guessed and no id is hardcoded.
    """
    loc = " ".join((location or "").lower().split())[:80]
    if not loc or not re.fullmatch(r"\d{3,15}", str(geo_id or "")):
        return False
    # LinkedIn rewrites "berlin" to "Berlin, Berlin, Germany". Keep both, so
    # typing just the city finds it; the short form never overwrites a place
    # learned under that exact name.
    short = loc.split(",")[0].strip()
    with _lock:
        state = _load()
        before = dict(state["geo"])
        state["geo"][loc] = str(geo_id)
        if short and short != loc:
            state["geo"].setdefault(short, str(geo_id))
        if state["geo"] == before:
            return False
        _write(state)
        return True
