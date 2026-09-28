"""
Arbeitnow: which of the board's jobs are worth ranking, decided by meaning.

The feed (https://www.arbeitnow.com/api/job-board-api) cannot be searched: any
search parameter is ignored and the whole board comes back, about 3,400 jobs a
week. Ranking all of them with the local model would take hours, so something
cheaper has to pick the ones worth a closer look first.

That used to be a list of title words, which missed a "Software Engineer" whose
whole description is LLM work, and only ever suited one person's search. Now:

  * **Searches** are a sentence each, in plain words ("Engineer building LLM
    apps and agents in production, Python"). A small local embedding model
    turns every job (title and description) and every search into a vector,
    and a job is kept when it is close in meaning to one of the searches.
    Set on the dashboard, or written by the local model from the profile.
  * **Rules** that need no judgement: how many days back, internships and
    working-student jobs in or out, and where the job is. Locations are turned
    into countries by the local model (Arbeitnow writes "Mainz", "Schweiz /
    Uri", "PARIS OFFICE or REMOTE FROM FRANCE"), cached per location string.

Everything kept then goes through the Ollama ranker like any other job, which
still makes the real call on fit. This step only decides what it reads.

Standard library only (no numpy): a week's feed scores in a fraction of a
second in plain Python.

Files, all in scraper/out/ (gitignored, and left alone by Erase):
    arbeitnow_settings.json   the searches and rules
    arbeitnow_feed.json       the last 7 days of the board, descriptions as text
    arbeitnow_vectors.json    which job each row of the .bin is
    arbeitnow_vectors.bin     one float32 vector per job
    arbeitnow_places.json     location string -> country codes
"""

from __future__ import annotations

import array
import datetime as dt
import hashlib
import json
import math
import operator
import os
import pathlib
import re
import statistics
import threading
import time
import urllib.error
import urllib.request

import config
import countries
import ollama_model
from profile_dir import profile_dir, profile_file

ROOT = pathlib.Path(__file__).resolve().parent
OUT = ROOT / "out"
SETTINGS = OUT / "arbeitnow_settings.json"
FEED = OUT / "arbeitnow_feed.json"
VEC_INDEX = OUT / "arbeitnow_vectors.json"
VEC_BIN = OUT / "arbeitnow_vectors.bin"
PLACES = OUT / "arbeitnow_places.json"

API = getattr(config, "ARBEITNOW_API", "https://www.arbeitnow.com/api/job-board-api")
OLLAMA_URL = getattr(config, "OLLAMA_URL", ollama_model.DEFAULT_URL)
EMBED_MODEL = getattr(config, "EMBED_MODEL", ollama_model.EMBED_MODEL)

# The board holds about a week, and nothing older is worth ranking.
MAX_DAYS = 7
DAY_CHOICES = (1, 3, 7)
# A week is about 31 pages. The cap is only there so a feed that never says
# "last page" cannot run forever.
MAX_PAGES = 60
# The API allows 50 requests in its window (x-ratelimit-limit). 1.3 s between
# pages stays under it for a whole week; a 429 is waited out, not fatal.
PAGE_DELAY = getattr(config, "ARBEITNOW_PAGE_DELAY", 1.3)
RATE_WAIT = getattr(config, "ARBEITNOW_RATE_WAIT", 30)
MAX_SEARCHES = 8

# How close a job has to be to a search. Two numbers, both needed:
#   z      how far above this week's typical job the match is, in standard
#          deviations. Adapts to how a search is worded: a long, specific one
#          scores everything lower, a one-word one everything higher.
#   floor  the plain similarity it must also reach. Without it a search for
#          something the board does not carry (tested: "Registered nurse", best
#          match 0.58, an "Entrepreneur in Residence - Healthcare") would still
#          keep its top few percent, all of them wrong.
# Tuned on the 28 September 2026 feed with nomic-embed-text: at "balanced" an
# AI engineering search kept 65 jobs, a frontend one about 30, a nurse none.
LEVELS = {
    "wide": (2.0, 0.60),
    "balanced": (2.5, 0.62),
    "close": (3.0, 0.65),
}
DEFAULT_LEVEL = "balanced"

# Internships, working-student jobs, theses and apprenticeships, from the title
# or from the job types Arbeitnow lists ("Working student", "Intern", ...).
STUDENT_TITLE = re.compile(
    r"\b(werkstudent\w*|praktikum|praktikant\w*|intern|internship|trainee\w*|"
    r"thesis|abschlussarbeit|bachelorarbeit|masterarbeit|ausbildung|"
    r"auszubildende\w*|duales?\b|dual stud\w*|schüler\w*|apprentice\w*)\b", re.I)
STUDENT_TYPE = re.compile(
    r"student|intern|trainee|apprentic|werkstudent|praktik|ausbildung|combined-study", re.I)
# Hybrid is not remote. Checked on the location text and on Arbeitnow's own
# remote flag; the model is never asked, because a word match cannot be wrong
# about whether a word is there.
REMOTE = re.compile(
    r"\b(remote|remotely|home[\s-]?office|homeoffice|work from home|wfh|"
    r"anywhere|fully distributed)\b", re.I)


# ------------------------------------------------------------------ files
def _read(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _replace(tmp: pathlib.Path, path: pathlib.Path) -> None:
    """
    os.replace, retried. On Windows a file another process has open (the
    bridge reading the feed for a preview while a pull writes it) cannot be
    replaced for that moment; macOS and Linux never refuse.
    """
    for attempt in range(20):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1)


def _tmp_for(path: pathlib.Path) -> pathlib.Path:
    # Process and thread: two threads of the bridge may write the same file.
    return path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")


def _write(path: pathlib.Path, data) -> None:
    """Atomic: the bridge and a run can both be busy with these files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(path)
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    _replace(tmp, path)


# ------------------------------------------------------------------ settings
def empty_settings() -> dict:
    return {"searches": [], "strictness": DEFAULT_LEVEL, "countries": [],
            "remote_elsewhere": True, "student_roles": False, "days": MAX_DAYS,
            "made_by": "none"}


def _clean_text(v, limit: int) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit]


def normalise(raw) -> dict:
    """Whatever came in (a file, the dashboard, the model), in a known shape."""
    raw = raw if isinstance(raw, dict) else {}
    s = empty_settings()
    searches = []
    for item in raw.get("searches") or []:
        if not isinstance(item, dict):
            continue
        name = _clean_text(item.get("name"), 60)
        text = _clean_text(item.get("looking_for"), 400)
        if not (name or text):
            continue
        searches.append({"name": name or text[:40], "looking_for": text or name,
                         "on": item.get("on", True) is not False})
    s["searches"] = searches[:MAX_SEARCHES]
    if raw.get("strictness") in LEVELS:
        s["strictness"] = raw["strictness"]
    codes = []
    for c in raw.get("countries") or []:
        c = countries.code(c) if isinstance(c, str) else None
        if c and c not in codes:
            codes.append(c)
    s["countries"] = codes
    s["remote_elsewhere"] = bool(raw.get("remote_elsewhere", True))
    s["student_roles"] = bool(raw.get("student_roles", False))
    try:
        days = int(raw.get("days", MAX_DAYS))
    except (TypeError, ValueError):
        days = MAX_DAYS
    s["days"] = days if days in DAY_CHOICES else MAX_DAYS
    for k in ("made_by", "profile", "profile_sig", "saved_at", "model"):
        if isinstance(raw.get(k), str):
            s[k] = raw[k]
    return s


def load() -> dict | None:
    """The saved settings, or None when there are none yet."""
    raw = _read(SETTINGS, None)
    return normalise(raw) if isinstance(raw, dict) else None


def save(settings: dict, made_by: str = "you") -> dict:
    s = normalise(settings)
    s["made_by"] = made_by
    # Which profile these were saved against, so the dashboard can say when
    # the profile has changed since.
    s["profile"], s["profile_sig"] = profile_signature()
    s["saved_at"] = dt.datetime.now().isoformat(timespec="seconds")
    _write(SETTINGS, s)
    return s


def profile_signature() -> tuple[str, str]:
    """(profile folder name, a hash of profile.md). Empty hash: no profile."""
    f = profile_file()
    try:
        text = f.read_text(encoding="utf-8")
    except OSError:
        return profile_dir().name, ""
    return profile_dir().name, hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def needs_suggestion(s: dict | None) -> bool:
    """
    Should the model write the settings (again)? Yes when there are none, or
    when the model wrote them and the profile has changed since. Never once
    someone has saved them from the dashboard: those are theirs.
    """
    _, sig = profile_signature()
    if not sig:
        return False
    if s is None:
        return True
    return s.get("made_by") == "model" and s.get("profile_sig") != sig


# ------------------------------------------------------------------ the model
def _model() -> str:
    return ollama_model.resolve(config) or ollama_model.wanted(config)


def generate(prompt: str, num_predict: int = 800, timeout: int = 300) -> dict:
    """One JSON answer from the local ranking model."""
    body = json.dumps({"model": _model(), "prompt": prompt, "stream": False,
                       "format": "json",
                       "options": {"temperature": 0, "num_predict": num_predict}}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        raw = json.loads(r.read()).get("response") or ""
    try:
        out = json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        out = json.loads(m.group(0)) if m else {}
    return out if isinstance(out, dict) else {}


# The example is deliberately far from any real user's field (marketing, in
# Dublin) and says nothing about location: a small model copies whatever its
# example contains, and in testing it copied an example's cities into profiles
# that never mentioned them.
_EXAMPLE_PROFILE = """# Sam Doyle
## Target
- Marketing manager or growth marketer at a B2B software company. Content and SEO are a plus. Not sales, not social media management.
## Experience
Five years in B2B marketing: demand generation, paid search, marketing automation (HubSpot)."""
_EXAMPLE_ANSWER = {"searches": [
    {"name": "B2B Marketing Manager",
     "looking_for": "Marketing manager at a B2B software company, running demand generation, "
                    "paid search and marketing automation campaigns."},
    {"name": "Growth Marketer",
     "looking_for": "Growth marketer for a B2B SaaS product, owning acquisition experiments, "
                    "SEO and content, and conversion."}],
    "student_roles": False}

SEARCHES_PROMPT = """You set up job searches for a job seeker. Each search is compared with job postings by meaning, so describe the jobs the way a posting would.

- searches: 1 to 4, one per distinct kind of job the profile says it wants, most wanted first. Leave out every kind of job the profile rules out or says does not count.
- "name": the job title, 2 to 5 words. "looking_for": one sentence with the role, what the work is, the field and the main tools. No places, no remote, nothing about the person.
- student_roles: true only if they want an internship, a working-student job, a thesis or an apprenticeship.

Example profile:
{ex_profile}

Answer for the example:
{ex_answer}

Profile:
{profile}

Answer JSON only, in the same shape as the example:"""

CHECK_PROMPT = """Here is a job seeker's profile:

{profile}

Does this profile say that the following kind of job is unwanted, out of their lane, or does not count?
"{name}: {looking_for}"

Answer JSON only: {{"ruled_out": true or false, "because": "<the profile's words, or empty>"}}"""

QUOTE_PROMPT = """Copy, word for word, every sentence of this profile that says where the person lives or where they would work (a city, a country, on site, hybrid, remote, relocating). Copy nothing else.
Answer JSON only: {{"quote": "<the sentences>"}}

Profile:
{profile}"""

WHERE_PROMPT = """A job seeker wrote this about where they would work:

"{quote}"

List every city, region or country named in that text. For each one:
- "name": the place exactly as written
- "countries": the ISO 3166 two-letter code of the country it is in (a region like "the EU" or "Europe" gets an empty list)
- "office": true if the text allows working there in an office, on site or hybrid; false if the place is named only for remote work

Then answer, for the whole text:
- "remote_abroad": true if the text accepts a fully remote job for a company in a country other than the one they live in (even as a last choice); false if remote is ruled out or only allowed within their own country.

Answer JSON only: {{"places": [{{"name": "...", "countries": ["..."], "office": true}}], "remote_abroad": false}}"""


def _searches_from(profile: str, log) -> tuple[list[dict], bool]:
    a = generate(SEARCHES_PROMPT.format(
        ex_profile=_EXAMPLE_PROFILE, ex_answer=json.dumps(_EXAMPLE_ANSWER),
        profile=profile), 1200)
    found = normalise({"searches": a.get("searches") or []})["searches"][:4]
    kept = []
    # A second, narrower question per search. In testing it caught every
    # planted mistake (a "Research ML Engineer" for a profile that says
    # research roles do not count, a ward manager for a nurse who rules out
    # management) and removed nothing that belonged.
    for s in found:
        c = generate(CHECK_PROMPT.format(profile=profile, name=s["name"],
                                         looking_for=s["looking_for"]), 200)
        if c.get("ruled_out") is True:
            log(f"  left out \"{s['name']}\": the profile rules it out "
                f"({_clean_text(c.get('because'), 120)})")
            continue
        kept.append(s)
    return kept, a.get("student_roles") is True


def _where_from(profile: str) -> tuple[list[str], bool]:
    """
    Countries for office work, and whether remote from abroad is fine.

    In two small steps, because one big question got it wrong in testing: the
    model added every neighbouring country, or copied its example's cities.
    First it copies the profile's own words about location. Then it lists the
    places named in them, and a place only counts if it really is in the
    quote. Remote from abroad needs the quote to say remote at all.
    """
    quote = _clean_text(generate(QUOTE_PROMPT.format(profile=profile), 400).get("quote"), 1200)
    if not quote:
        return [], True
    a = generate(WHERE_PROMPT.format(quote=quote.replace('"', "'")), 600)
    low = quote.lower()
    codes: list[str] = []
    for p in a.get("places") or []:
        if not isinstance(p, dict) or p.get("office") is not True:
            continue
        name = str(p.get("name") or "").strip().lower()
        if not name or name not in low:
            continue
        for c in p.get("countries") or []:
            if countries.valid(c) and c.upper() not in codes:
                codes.append(c.upper())
    remote = a.get("remote_abroad") is True and bool(REMOTE.search(quote))
    return codes, remote


def suggest(log=print) -> dict:
    """
    Settings written by the local model from the active profile. Raises when
    there is no profile or the model cannot be reached.
    """
    f = profile_file()
    profile = f.read_text(encoding="utf-8")
    log(f"Reading {profile_dir().name}/{f.name} to decide what to look for on Arbeitnow")
    searches, student = _searches_from(profile, log)
    if not searches:
        raise RuntimeError("the local model suggested no search it could stand behind")
    codes, remote = _where_from(profile)
    name, sig = profile_signature()
    s = normalise({"searches": searches, "strictness": DEFAULT_LEVEL,
                   "countries": codes, "remote_elsewhere": remote if codes else True,
                   "student_roles": student, "days": MAX_DAYS})
    s.update(made_by="model", profile=name, profile_sig=sig, model=_model())
    return s


def ensure_settings(log=print) -> tuple[dict, str]:
    """
    The settings to run with, writing them from the profile first when that is
    due (see needs_suggestion). Returns (settings, a note for the log).
    """
    s = load()
    if not needs_suggestion(s):
        return (s or empty_settings()), ""
    try:
        new = suggest(log)
    except Exception as e:                                   # noqa: BLE001
        why = f"could not write searches from the profile: {e}"
        return (s or empty_settings()), why
    new["saved_at"] = dt.datetime.now().isoformat(timespec="seconds")
    _write(SETTINGS, new)
    return new, "searches written from the profile by the local model"


# ------------------------------------------------------------------ the feed
def _html_to_text(html: str) -> str:
    if not html:
        return ""
    from bs4 import BeautifulSoup
    soup = BeautifulSoup(html, "html.parser")
    for node in soup(["script", "style", "noscript", "svg"]):
        node.decompose()
    text = soup.get_text("\n", strip=True)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _slim(rec: dict) -> dict | None:
    title = _clean_text(rec.get("title"), 300)
    url = (rec.get("url") or "").strip()
    if not title or not url:
        return None
    return {
        "title": title,
        "company": _clean_text(rec.get("company_name"), 200),
        "location": _clean_text(rec.get("location"), 200),
        "url": url,
        "remote": bool(rec.get("remote")),
        "types": [str(t) for t in (rec.get("job_types") or []) if t][:10],
        "created_at": int(rec.get("created_at") or 0),
        "text": _html_to_text(rec.get("description") or "")[
            :getattr(config, "OLLAMA_DESC_CHARS", 6000)],
    }


def load_feed() -> dict:
    feed = _read(FEED, None)
    if not isinstance(feed, dict) or not isinstance(feed.get("jobs"), dict):
        return {"fetched_at": None, "jobs": {}}
    return feed


def _get_page(page: int, log) -> dict | None:
    req = urllib.request.Request(
        f"{API}?page={page}",
        headers={"User-Agent": "job-pipeline/1.0 (personal job search tool)"})
    for attempt in range(1, 6):
        try:
            with urllib.request.urlopen(req, timeout=45) as res:
                return json.loads(res.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            if e.code == 429:
                log(f"  Arbeitnow asks to slow down; waiting {RATE_WAIT} s")
                time.sleep(RATE_WAIT)
                continue
            log(f"  ! page {page}, try {attempt}/5: HTTP {e.code}")
        except Exception as e:                               # noqa: BLE001
            log(f"  ! page {page}, try {attempt}/5: {e}")
        if attempt < 5:
            time.sleep(5 * attempt)
    return None


def fetch(log=print, max_pages: int = MAX_PAGES) -> tuple[dict, str]:
    """
    Bring the cached feed up to date. Returns (feed, error or "").

    The board is only roughly newest first: on 28 September page 1 opened with
    a posting from 12 August ahead of 325 from that week, and the old rule
    ("stop at the first page whose oldest job is past the cutoff") ended the
    run there with 8 jobs kept. Now it stops at a page with nothing inside the
    week, or, once a copy of the feed is on disk, at two pages in a row that
    bring nothing new: new postings arrive at the front.
    """
    feed = load_feed()
    jobs: dict = feed["jobs"]
    had = bool(jobs)
    cutoff = time.time() - MAX_DAYS * 86400
    quiet, error, pages = 0, "", 0
    for page in range(1, max_pages + 1):
        payload = _get_page(page, log)
        if payload is None:
            error = f"Arbeitnow did not answer (page {page})"
            break
        pages += 1
        rows = payload.get("data") or []
        if not rows:
            break
        fresh = [r for r in rows if (r.get("created_at") or 0) >= cutoff]
        added = 0
        for r in fresh:
            slug = str(r.get("slug") or r.get("url") or "")
            if not slug or slug in jobs:
                continue
            slim = _slim(r)
            if slim:
                jobs[slug] = slim
                added += 1
        log(f"  page {page}: {len(rows)} jobs, {len(fresh)} from the last {MAX_DAYS} days, "
            f"{added} new")
        if not fresh:
            break
        quiet = quiet + 1 if (had and not added) else 0
        if quiet >= 2:
            log("  nothing new on two pages in a row: the rest is already on disk")
            break
        if not (payload.get("links") or {}).get("next"):
            break
        time.sleep(PAGE_DELAY)
    for slug in [k for k, v in jobs.items() if (v.get("created_at") or 0) < cutoff]:
        del jobs[slug]
    if pages:
        feed["fetched_at"] = dt.datetime.now().isoformat(timespec="seconds")
    _write(FEED, feed)
    return feed, error


# ------------------------------------------------------------------ places
PLACES_PROMPT = """These are job locations from Arbeitnow, a job board based in Germany. Most are German cities and towns, often small ones; when a name could be a place in Germany or somewhere else, it is the German one unless the text says otherwise.

For each location, list the country or countries it names as ISO 3166 two-letter codes ("DE", "GB", "CH", ...). Read codes like DE, UK, GB, CH, AT, FR, NL, US as countries. Leave the list empty when no country can be told: "Remote", "EMEA", "Europe", "EU", "Homeoffice", or a building or office with no town.

Answer JSON only: {{"places": [{{"text": <the location exactly as given>, "countries": [<code>, ...]}}]}}

Locations:
{lines}"""


def _only_remote_words(loc: str) -> bool:
    rest = REMOTE.sub(" ", loc)
    rest = re.sub(r"\b(job|jobs|only|work|working|position|100%|full|fully|from)\b", " ", rest, flags=re.I)
    return not re.sub(r"[\W_]+", "", rest)


def _quick_place(loc: str) -> list[str] | None:
    """Answers that need no model; None when it has to be asked."""
    if not loc.strip() or _only_remote_words(loc):
        return []
    named = countries.named_in(loc)
    if countries.in_germany(loc) and set(named) <= {"DE"}:
        return ["DE"]
    return None


def _ask_places(chunk: list[str]) -> dict[str, list[str]]:
    """One question to the model. Raises when the answer is not usable JSON."""
    a = generate(PLACES_PROMPT.format(
        lines="\n".join(json.dumps(x, ensure_ascii=False) for x in chunk)),
        num_predict=60 * len(chunk) + 300, timeout=600)
    if not isinstance(a.get("places"), list):
        raise ValueError("no list of places in the answer")
    got = {}
    for p in a["places"]:
        if isinstance(p, dict) and isinstance(p.get("text"), str):
            got[p["text"].strip()] = sorted({c.upper() for c in (p.get("countries") or [])
                                             if countries.valid(c)})
    return got


def _place_chunk(chunk: list[str], cache: dict, log) -> None:
    """
    Place one batch, halving it when the answer is broken. A long batch is
    where a small model most often breaks its JSON (on 28 September a batch
    of 40 came back cut off), and one broken batch must not cost the rest.
    """
    try:
        got = _ask_places(chunk)
    except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
        raise RuntimeError(f"Ollama did not answer: {e}") from None
    except Exception as e:                                   # noqa: BLE001
        if len(chunk) > 4:
            half = len(chunk) // 2
            _place_chunk(chunk[:half], cache, log)
            _place_chunk(chunk[half:], cache, log)
            return
        log(f"  ! could not place {len(chunk)} location(s) ({e}); they are kept as unknown")
        got = {}
    for loc in chunk:
        # Not in the answer: unknown, which the location rule keeps.
        cache[loc] = got.get(loc, [])


def resolve_places(locations, log=print, batch: int = 25) -> dict:
    """location string -> country codes, asking the model only for new ones."""
    cache = _read(PLACES, {})
    cache = cache if isinstance(cache, dict) else {}
    todo = []
    for loc in sorted({(l or "").strip() for l in locations}):
        if loc in cache:
            continue
        quick = _quick_place(loc)
        if quick is not None:
            cache[loc] = quick
        else:
            todo.append(loc)
    if todo:
        log(f"  working out the country of {len(todo)} new location(s) with the local model")
    for i in range(0, len(todo), batch):
        try:
            _place_chunk(todo[i:i + batch], cache, log)
        except RuntimeError as e:
            # Ollama itself is gone: stop asking. What is not placed stays
            # unknown (kept) for now and is asked again next time.
            log(f"  ! {e}; {len(todo) - i} location(s) left unplaced for now")
            break
        _write(PLACES, cache)
    _write(PLACES, cache)
    return cache


def is_remote(job: dict) -> bool:
    return bool(job.get("remote")) or bool(REMOTE.search(job.get("location") or ""))


def shown_location(job: dict, codes: list[str]) -> str:
    """
    The location as the rest of the pipeline reads it: the country added when
    Arbeitnow left it out ("Mainz" -> "Mainz, Germany", so the ranker knows the
    job is in Germany), and "(Remote)" the way LinkedIn writes it.
    """
    loc = job.get("location") or ""
    names = [countries.name(c) for c in codes]
    if names and not any(n.lower() in loc.lower() for n in names):
        loc = f"{loc}, {' / '.join(names)}" if loc else " / ".join(names)
    if is_remote(job) and "remote" not in loc.lower():
        loc = f"{loc} (Remote)" if loc else "Remote"
    return loc


# ------------------------------------------------------------------ rules
def rule_out(job: dict, s: dict, places: dict, now: float | None = None) -> str:
    """Which rule drops this job ("old", "student", "location"), or ""."""
    now = now or time.time()
    if (job.get("created_at") or 0) < now - s["days"] * 86400:
        return "old"
    if not s["student_roles"] and (
            STUDENT_TITLE.search(job.get("title") or "")
            or any(STUDENT_TYPE.search(t) for t in job.get("types") or [])):
        return "student"
    if s["countries"]:
        codes = places.get((job.get("location") or "").strip(), [])
        # No country known: kept. The ranker reads the description and can
        # still turn it down; dropping it here could not be undone.
        if codes and not set(codes) & set(s["countries"]) \
                and not (is_remote(job) and s["remote_elsewhere"]):
            return "location"
    return ""


# ------------------------------------------------------------------ vectors
def _norm(v: list[float]) -> list[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def embed(texts: list[str], timeout: int = 300) -> list[list[float]]:
    body = json.dumps({"model": EMBED_MODEL, "input": texts, "truncate": True}).encode()
    req = urllib.request.Request(f"{OLLAMA_URL}/api/embed", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            vecs = json.loads(r.read()).get("embeddings") or []
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:200]
        if e.code == 404 and "not found" in detail.lower():
            raise RuntimeError(f"the embedding model '{EMBED_MODEL}' is not installed "
                               f"(ollama pull {EMBED_MODEL})") from None
        raise RuntimeError(f"Ollama could not embed ({e.code}): {detail}") from None
    if len(vecs) != len(texts):
        raise RuntimeError("Ollama returned the wrong number of embeddings")
    return [_norm(v) for v in vecs]


def _doc_text(job: dict) -> str:
    return (f"search_document: {job['title']}\n{job.get('company', '')}\n"
            f"{(job.get('text') or '')[:1500]}")


# The bridge asks for the vectors on every preview; reading and unpacking
# about 10 MB each time is wasted while the files have not changed.
_vec_memo: dict = {"stamp": None, "vecs": {}}


def load_vectors() -> dict[str, list[float]]:
    try:
        stamp = (VEC_BIN.stat().st_mtime_ns, VEC_BIN.stat().st_size,
                 VEC_INDEX.stat().st_mtime_ns, EMBED_MODEL)
    except OSError:
        return {}
    if _vec_memo["stamp"] == stamp:
        return dict(_vec_memo["vecs"])
    idx = _read(VEC_INDEX, None)
    if not isinstance(idx, dict) or idx.get("model") != EMBED_MODEL:
        return {}
    dim, keys = int(idx.get("dim") or 0), idx.get("keys") or []
    try:
        arr = array.array("f")
        arr.frombytes(VEC_BIN.read_bytes())
    except (OSError, ValueError):
        return {}
    if not dim or len(arr) != dim * len(keys):
        return {}
    vecs = {k: arr[i * dim:(i + 1) * dim].tolist() for i, k in enumerate(keys)}
    _vec_memo.update(stamp=stamp, vecs=vecs)
    return dict(vecs)


def _save_vectors(vecs: dict[str, list[float]]) -> None:
    keys = list(vecs)
    dim = len(vecs[keys[0]]) if keys else 0
    arr = array.array("f")
    for k in keys:
        arr.extend(vecs[k])
    OUT.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_for(VEC_BIN)
    tmp.write_bytes(arr.tobytes())
    _replace(tmp, VEC_BIN)
    _write(VEC_INDEX, {"model": EMBED_MODEL, "dim": dim, "keys": keys})


def ensure_vectors(jobs: dict, log=print, progress=None, batch: int = 64) -> dict:
    """
    One vector per job: half the description's, half the title's. Tested
    against either alone: title only missed plain "Software Engineer" posts
    whose description is the match; description only let in sales roles at AI
    companies. Only jobs not embedded before are sent to Ollama.
    """
    vecs = {k: v for k, v in load_vectors().items() if k in jobs}
    todo = [k for k in jobs if k not in vecs]
    if todo:
        log(f"  reading {len(todo)} new job(s) with {EMBED_MODEL}"
            + (" (the first time takes a couple of minutes)" if len(todo) > 500 else ""))
    for i in range(0, len(todo), batch):
        keys = todo[i:i + batch]
        docs = embed([_doc_text(jobs[k]) for k in keys])
        titles = embed([f"search_document: {jobs[k]['title']}" for k in keys])
        for k, d, t in zip(keys, docs, titles):
            vecs[k] = [0.5 * a + 0.5 * b for a, b in zip(d, t)]
        if progress:
            progress(min(i + batch, len(todo)), len(todo))
        if (i // batch) % 10 == 9:
            _save_vectors(vecs)                  # a stop halfway keeps what was done
    if todo or not VEC_BIN.exists():
        _save_vectors(vecs)
    return vecs


# ------------------------------------------------------------------ scoring
def _query_text(search: dict) -> str:
    return f"search_query: {search['name']}: {search['looking_for']}"


def score(s: dict, jobs: dict, vecs: dict) -> dict:
    """
    For every job with a vector: its best search, by how far it stands out.
    Returns {slug: (z, similarity, search index)}.
    """
    on = [(i, x) for i, x in enumerate(s["searches"]) if x.get("on", True)]
    keys = [k for k in jobs if k in vecs]
    if not on or len(keys) < 2:
        return {}
    qvecs = embed([_query_text(x) for _, x in on])
    best: dict = {}
    mul = operator.mul
    for (i, _), q in zip(on, qvecs):
        sims = [sum(map(mul, q, vecs[k])) for k in keys]
        mean, sd = statistics.fmean(sims), statistics.pstdev(sims) or 1.0
        for k, sim in zip(keys, sims):
            z = (sim - mean) / sd
            if k not in best or z > best[k][0]:
                best[k] = (z, sim, i)
    return best


def passes(z: float, sim: float, level: str) -> bool:
    zmin, floor = LEVELS[level]
    return z >= zmin and sim >= floor


def select(s: dict, feed: dict, vecs: dict, places: dict,
           scores: dict | None = None) -> dict:
    """
    Apply the rules and the searches. Returns the jobs to hand to the ranker
    and the counts behind them (for the log and the dashboard preview).
    """
    jobs = feed["jobs"]
    now = time.time()
    dropped = {"old": 0, "student": 0, "location": 0}
    eligible = []
    for k, j in jobs.items():
        why = rule_out(j, s, places, now)
        if why:
            dropped[why] += 1
        else:
            eligible.append(k)
    scores = score(s, jobs, vecs) if scores is None else scores
    level = s["strictness"]
    ranked = sorted((k for k in eligible if k in scores), key=lambda k: -scores[k][0])
    levels = {name: sum(1 for k in ranked if passes(scores[k][0], scores[k][1], name))
              for name in LEVELS}
    kept = [k for k in ranked if passes(scores[k][0], scores[k][1], level)]
    kept_set = set(kept)
    near = [k for k in ranked if k not in kept_set][:6]
    per_search = {x["name"]: 0 for x in s["searches"] if x.get("on", True)}
    # By position too: the page matches counts to its cards by position,
    # because a card can still be without a name while it is being typed.
    per_index = [0] * len(s["searches"])
    out = []
    for k in kept:
        j, (z, sim, i) = jobs[k], scores[k]
        name = s["searches"][i]["name"]
        per_search[name] = per_search.get(name, 0) + 1
        per_index[i] += 1
        out.append({
            "title": j["title"], "company": j.get("company", ""),
            "location": shown_location(j, places.get((j.get("location") or "").strip(), [])),
            "url": j["url"], "source": "Arbeitnow", "query": f"Arbeitnow: {name}",
            "description": j.get("text", ""),
            "match": round(sim, 3), "_z": round(z, 2), "_slug": k,
        })
    return {"total": len(jobs), "eligible": len(eligible), "dropped": dropped,
            "unscored": sum(1 for k in eligible if k not in scores),
            "levels": levels, "level": level, "kept": out,
            "near": [{"title": jobs[k]["title"], "company": jobs[k].get("company", ""),
                      "search": s["searches"][scores[k][2]]["name"],
                      "match": round(scores[k][1], 3), "_z": round(scores[k][0], 2)}
                     for k in near],
            "per_search": per_search, "per_index": per_index}


def preview(settings: dict) -> dict:
    """What a pull with these settings would keep, from the feed on disk."""
    s = normalise(settings)
    feed = load_feed()
    places = _read(PLACES, {})
    vecs = load_vectors()
    res = select(s, feed, vecs, places if isinstance(places, dict) else {})
    kept = res.pop("kept")
    res["examples"] = [{"title": j["title"], "company": j["company"], "location": j["location"],
                        "search": j["query"].split(": ", 1)[-1], "match": j["match"],
                        "_z": j["_z"]} for j in kept[:15]]
    res["kept"] = len(kept)
    res["feed_at"] = feed.get("fetched_at")
    return res
