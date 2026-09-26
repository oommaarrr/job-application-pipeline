#!/usr/bin/env python3
"""
Rank every scraped job with a local model, so only roles that already fit reach
Claude.

Why this exists
---------------
Claude used to rank the pool itself. On 4 September a 109 job scrape spent the
entire usage window reading descriptions and wrote zero documents. Reading is
cheap work: it is extraction and judgement against a fixed yardstick, and a
local 8B model does it well enough. Writing a CV is not cheap work, and that is
the only thing Claude should be spending a subscription on.

So: Ollama reads all of them, this file decides which survive, and the top
TOP_N go to Claude already ranked.

What the model is asked for
---------------------------
Extraction plus one judgement, as strict JSON. The model never gets to decide
whether a job is dropped; it reports what the posting says and how close the
work is, and the gates below make the call. A wording change in a posting
therefore cannot quietly loosen a rule.

Every answer is cached by job and prompt version, so re-running after a config
change costs nothing for jobs already seen.

    .venv/bin/python rank_ollama.py                 rank today's scrape
    .venv/bin/python rank_ollama.py --top 25        override TOP_N
    .venv/bin/python rank_ollama.py --model qwen3   override the model
    .venv/bin/python rank_ollama.py --recheck       ignore the cache
"""

from __future__ import annotations

import argparse
import concurrent.futures as futures
import hashlib
import datetime as dt
import json
import pathlib
import os
import re
import signal
import sys
import urllib.error
import urllib.request

import config
import ollama_model
from applied_index import index as applied_index
import ledger
from jobkey import job_key

HERE = pathlib.Path(__file__).parent
INBOX = HERE / "inbox"
OUT = HERE / "out"
CACHE_PATH = OUT / "ollama_cache.json"
from profile_dir import profile_file
PROFILE_PATH = profile_file()   # profiles/me, else profiles/example — see profile_dir.py

# Bump when the prompt or the answer shape changes, so old cached answers are
# not silently reused against a different question.
PROMPT_VERSION = "3"

GERMAN_SCALE = {"none": 0, "a1": 1, "a2": 2, "b1": 3, "b2": 4, "c1": 5,
                "c2": 6, "native": 6, "fluent": 6, "unknown": 0}

AI_FAMILIES = {"ai_engineer", "ml_engineer", "ai_platform"}
OFF_LANE = {"data_engineer", "frontend", "computer_vision", "embedded_ml",
            "research", "devops", "annotation"}


# --------------------------------------------------------------- input
def _save_cache(cache: dict) -> None:
    """Write via a temp file, so an interrupt cannot leave a half-written cache."""
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=1, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def _atomic_write(path: pathlib.Path, text: str, encoding: str = "utf-8") -> None:
    """Temp file then rename. The bridge and the build read these files while a
    rank is running; a rank killed mid-write must leave the previous copy whole,
    not half a JSON document that reads as "nothing ranked"."""
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding=encoding)
    tmp.replace(path)


def _read(path: pathlib.Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def already_applied() -> tuple[set[str], dict[str, str]]:
    """
    Two filters, because one board's link is not another's.

    Decision, 18 September 2026: match on the title, not the URL. A role
    is (company, title) normalised, which survives the same posting appearing on
    LinkedIn, StepStone and the company site under three different links. The
    URL set is kept alongside it as a second net, not as the primary one.

    The case that forced this: a role built once came back a month later
    under a new link, missed by the URL check and caught only by a human
    reading the batch.
    """
    urls, roles = applied_index()
    return {job_key(u) for u in urls} | urls, roles


# --------------------------------------------------------------- the model
def build_prompt(job: dict, profile: str) -> str:
    desc = (job.get("description") or "")[: config.OLLAMA_DESC_CHARS]
    return f"""You are screening job postings for one specific candidate.

{profile}

=== THE POSTING ===
Title: {job.get('title') or ''}
Company: {job.get('company') or ''}
Location: {job.get('location') or ''}

{desc}
=== END POSTING ===

Answer with ONE JSON object and nothing else. No markdown, no explanation.

{{
  "german_level": one of "none","a1","a2","b1","b2","c1","native" — the German
      the posting REQUIRES. "German is a plus" is "none". Only say "c1" or
      "native" when fluent or business-fluent German is stated as a requirement.
      A posting written in German does not by itself require German.
  "years_required": integer, or null when the posting states no number. Read
      only years of professional experience the posting REQUIRES. A range like
      "2-4 years" is 2. Ignore years mentioned for anything else, such as how
      long the company has existed or the length of a contract.
  "role_family": one of "ai_engineer","ml_engineer","ai_platform","backend",
      "data_engineer","frontend","devops","computer_vision","embedded_ml",
      "research","annotation","other" — what the work ACTUALLY is, judged from
      the responsibilities, not from the job title.
  "is_management": true only when the core of the job is leading people, owning
      headcount or setting department strategy. A senior engineer who mentors
      is NOT management.
  "berlin": true when the job is in Berlin or hybrid in Berlin.
  "remote_germany": true when it is remote and open to Germany.
  "fit": integer 0-100, how close this work is to what the candidate has actually built.
      100 means it could have been written for them. 0 means a different field.
  "reason": one short sentence, at most 20 words, naming the deciding factor.
}}"""


def ask(model: str, prompt: str) -> dict:
    body = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 400},
    }).encode()
    req = urllib.request.Request(
        f"{config.OLLAMA_URL}/api/generate", data=body,
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=config.OLLAMA_TIMEOUT) as r:
        raw = json.loads(r.read())["response"]
    return _parse(raw)


def _parse(raw: str) -> dict:
    """format:json should give clean JSON, but a small model sometimes wraps it."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", raw, re.S)
    if not m:
        raise ValueError(f"no JSON in answer: {raw[:200]}")
    return json.loads(m.group(0))


def normalise(a: dict) -> dict:
    """Coerce whatever came back into the shape the gates expect."""
    def _int(v):
        if isinstance(v, bool) or v is None:
            return None
        if isinstance(v, (int, float)):
            return int(v)
        m = re.search(r"\d+", str(v))
        return int(m.group(0)) if m else None

    lvl = str(a.get("german_level", "none")).strip().lower()
    yrs = _int(a.get("years_required"))
    fit = _int(a.get("fit")) or 0
    return {
        "german_level": lvl if lvl in GERMAN_SCALE else "none",
        "german_num": GERMAN_SCALE.get(lvl, 0),
        "years_required": yrs if (yrs is None or 0 <= yrs <= 30) else None,
        "role_family": str(a.get("role_family", "other")).strip().lower(),
        "is_management": bool(a.get("is_management")),
        "berlin": bool(a.get("berlin")),
        "remote_germany": bool(a.get("remote_germany")),
        "fit": max(0, min(100, fit)),
        "reason": str(a.get("reason", ""))[:160],
    }


# --------------------------------------------------------------- the gates
# Berlin postcodes are 10115-14199. A posting that gives only "10587" is a
# Berlin job and a posting that gives "30938" is not, and no model needs to be
# asked about either.
POSTCODE = re.compile(r"\b(\d{5})\b")


def berlin_from_text(location: str, description: str) -> bool | None:
    """
    True, False, or None when the text genuinely does not say.

    llama3.1 answered berlin=true for all 188 postings, Burgwedel and
    Bergkirchen included, which made the Berlin bonus meaningless: everything
    got it, so it ranked nothing. The location string was sitting right there in
    every one of those records. Read it instead of asking.
    """
    loc = (location or "").strip().lower()
    if not loc:
        head = (description or "")[:400].lower()
        if "berlin" in head:
            return True
        return None
    if "berlin" in loc:
        return True
    codes = POSTCODE.findall(loc)
    if codes:
        # A postcode that is not Berlin's is a definite no, whatever else the
        # line says.
        return any(10115 <= int(c) <= 14199 for c in codes)
    if re.search(r"remote|homeoffice|home office|bundesweit|deutschlandweit|"
                 r"anywhere|deutschland|germany", loc):
        return None          # not Berlin specifically, but not ruled out either
    return False             # a named place that is not Berlin


# Shapes of role that are wrong whatever the description says, judged from the
# title because that is where they are declared. Added 18 September 2026 after a
# top 20 came back holding a Chief of Staff, two internships, a Product Manager
# and a Working Student.
#
# These are not seniority filters. A junior or graduate engineering role is
# fine and stays: the objection to an internship is that it is not a job, and
# the objection to a Product Manager is that it is not engineering.
NOT_A_JOB = re.compile(
    r"\b(intern|internship|praktikum|praktikant\w*|working\s*student|"
    r"werkstudent\w*|ausbildung|azubi|dual(?:es)?\s*stud\w*|"
    r"master\s*thesis|bachelor\s*thesis|abschlussarbeit)\b", re.I)

NOT_ENGINEERING = re.compile(
    r"\b(product\s*manager|product\s*owner|project\s*manager|programme?\s*manager|"
    r"chief\s*of\s*staff|scrum\s*master|agile\s*coach|recruit\w*|"
    r"account\s*(?:executive|manager)|sales|business\s*development|"
    r"marketing|designer|ux\s*research\w*)\b", re.I)


# Marketplaces and AI-training contractors. Decision, 18 September 2026.
#
# These are not employers. They are platforms that place you on task work, or
# annotation and model-training gigs dressed as engineering roles: "Machine
# Learning Engineer" at Alignerr is paid per task, not a job at a company with a
# product. They rank well because the titles are perfect and the descriptions
# talk about ML all the way through, which is exactly why a title-and-fit
# ranking cannot catch them and a name list is needed.
MARKETPLACE_NAMES = re.compile("|".join([
    r"alignerr", r"xpertdirect", r"jobgether", r"turing\b", r"toptal", r"upwork",
    r"fiverr", r"andela", r"crossover", r"remotasks", r"outlier\s*ai", r"mercor",
    r"scale\s*ai", r"appen", r"lionbridge", r"telus\s*international", r"clickworker",
    # Deel was on this list and should not have been: it is a payroll and HR
    # SaaS company that hires its own engineers, not a task marketplace, and the
    # entry silently dropped real in-lane roles. Oyster is kept: it is an EOR
    # whose postings are placements, not its own roles.
    r"braintrust", r"gun\.io", r"arc\.dev", r"revelo", r"oyster\s*hr",
    r"micro1", r"invisible\s*technologies", r"surge\s*ai", r"labelbox",
    r"dataannotation", r"data\s*annotation",
]), re.I)

# The same thing said in the posting rather than in the name.
GIG_SIGNALS = re.compile("|".join([
    r"pay(?:ment)?\s*per\s*(?:task|project|hour|word|assignment)",
    r"per[- ]task", r"task[- ]based\s*(?:work|pay)", r"work\s*when\s*you\s*want",
    r"set\s*your\s*own\s*(?:hours|schedule)", r"as\s*(?:much|little)\s*as\s*you\s*want",
    r"no\s*(?:long[- ]term\s*)?commitment", r"1099\b", r"independent\s*contractor",
    r"freelance\s*(?:marketplace|platform)", r"training\s*data\s*(?:contributor|annotator)",
    r"(?:rate|earn)[^.]{0,40}\$\d+\s*(?:/|per\s*)h",
]), re.I)


def is_marketplace(company: str, description: str) -> str:
    if MARKETPLACE_NAMES.search(company or ""):
        return "a task marketplace, not an employer"
    hits = GIG_SIGNALS.findall(description or "")
    # One phrase can appear innocently; two is the shape of a gig posting.
    if len(hits) >= 2:
        return "the posting describes per-task gig work, not a role"
    return ""


def shape_of(title: str) -> tuple[str, str]:
    t = title or ""
    if NOT_A_JOB.search(t):
        return "not-a-job", "internship, working student or thesis placement"
    if NOT_ENGINEERING.search(t):
        return "not-engineering", "the role is not an engineering job"
    return "", ""


# --------------------------------------------------------------- German
# The most common German function words. Deliberately closed-class: articles,
# prepositions, conjunctions, pronouns and auxiliaries appear in any German
# prose regardless of subject, while nouns would make this a topic detector.
GERMAN_STOPWORDS = frozenset("""
und der die das mit fuer für von den dem des ein eine einer einem einen als auch
im in bei sich nicht sind ist wir uns unser unsere unserem unseren du dir dein
deine sie ihre ihr wird werden haben hat sowie oder zu zum zur aus ueber über
nach durch bis dass wenn was wie sehr gute guten gutes kenntnisse erfahrung
aufgaben profil bieten suchen freuen dich dass weil damit schon noch mehr
""".split())

_WORDS = re.compile(r"[a-zäöüß]+")


def written_in_german(description: str) -> float:
    """
    Share of the posting's words that are German function words.

    An English posting scores near zero even when the company, the city and half
    the benefits are German, because none of those are function words. A German
    posting scores a quarter or more, because you cannot write a German sentence
    without them.
    """
    words = _WORDS.findall((description or "").lower())
    if len(words) < 40:          # too short to measure; do not guess from noise
        return 0.0
    return sum(w in GERMAN_STOPWORDS for w in words) / len(words)


# --------------------------------------------------------------------------- #
# Deterministic scans of the actual description, because the model under-calls
# every one of these. On 21 September the ranker sent 20 roles to Claude and
# Claude rejected 18 by hand: EIGHT were hard German-fluency requirements the
# model had tagged "b1", THREE were stated years gates ("5-8 years", "5+ years")
# the model missed entirely, and the rest were plainly off-lane roles the model
# still labelled "ai_engineer". The whole point of ranking locally is to spend
# Claude on writing, not on rejecting — so these gates read the text ourselves.
# The model extracts; the code below decides.

# Each of these names German at a hard level (fluent, C1/C2, native, professional,
# verhandlungssicher) that the profile (see GERMAN_CAP) does not meet. They are specific enough —
# every one requires the word "german"/"deutsch" adjacent to a fluency term — that
# a softened "German is a plus" (which carries none of these terms) never matches,
# so no soft-window exception is needed. An earlier version applied one and it
# read "TypeScript being a plus. Fluent German" as softened, leaking the role.
# "both" and a hyphen in "professional-level" are allowed because real postings
# use them ("Fluency in both German and English", "Professional-level German").
_GERMAN_HARD = [re.compile(p, re.I) for p in (
    r"fluent\s+(?:in\s+)?(?:both\s+)?german", r"german\s+fluency",
    r"fluency\s+(?:in\s+)?(?:both\s+)?german",
    r"(?:professional|business|native|proficient|advanced)[-\s]+(?:level\s+)?(?:in\s+)?german",
    r"german\s*[:(]?\s*(?:c1|c2)\b", r"\b(?:c1|c2)\s+(?:level\s+)?(?:in\s+)?german",
    r"german\s+to\s+a\s+minimum\s+(?:of\s+)?(?:c1|c2)", r"you\s+speak\s+german",
    r"german\s+and\s+english\s+(?:language\s+)?(?:fluency|proficiency|skills|communication|speaking)",
    r"(?:fluent|fluency|proficient|proficiency)\s+(?:in\s+)?(?:both\s+)?german\s+and\s+english",
    r"verhandlungssicher", r"muttersprach", r"flie(?:ss|ß)end(?:e|es)?\s+deutsch",
    r"sehr\s+gute?\s+deutschkenntnisse",
    r"deutschkenntnisse\s*[:(]?\s*(?:c1|c2|verhandlungssicher|flie)",
    r"deutsch\s*[:(]?\s*(?:c1|c2)\b",
)]


def german_required(description: str) -> str:
    """The hard German-fluency requirement the posting states, or '' if none."""
    text = (description or "").lower()
    if not text:
        return ""
    for rx in _GERMAN_HARD:
        m = rx.search(text)
        if m:
            return " ".join(m.group(0).split())
    return ""


# A stated minimum years-of-experience requirement. The floor of a range is the
# requirement ("5-8 years" -> 5), and "experience" must be near the number so a
# founding date or a "2 year contract" is not read as a seniority bar.
# Only EXPLICIT requirement phrasings, each read at its FLOOR (the least the
# posting would accept): "5+ years", "at least 8 years", and a range at its lower
# bound ("2-5 years" -> 2, so a two-year candidate is not gated; "5-8 years" -> 5).
# Bare "5 years of existence" carries none of these markers and is ignored, which
# is why this is trusted over the model — the model reads a range at its ceiling
# and gated "2-5 years" roles the user qualifies for. The MAX across matches is the
# seniority bar (a posting stating both "2-5" somewhere and "8+ years required"
# is an eight-year role).
_YEARS_REQ = [re.compile(p, re.I) for p in (
    r"(\d{1,2})\s*\+\s*years?",
    r"(?:at\s+least|minimum(?:\s+of)?|min\.?|mindestens)\s+(\d{1,2})\s*\+?\s*years?",
    r"(\d{1,2})\s*(?:-|–|to|bis)\s*\d{1,2}\s*\+?\s*years?",
)]


def stated_years(description: str):
    """The stated years-of-experience requirement (its floor), or None. Read from
    the text, because the model mis-reads ranges as their upper bound."""
    text = (description or "").lower()
    floors = [int(m.group(1)) for rx in _YEARS_REQ for m in rx.finditer(text)
              if 0 <= int(m.group(1)) <= 30]
    return max(floors) if floors else None


# Unambiguous off-lane markers: phrases that name a different discipline outright.
# Kept deliberately narrow — a false positive here throws away a good role — so
# only fields the user plainly does not work in, by their own specific vocabulary.
_OFF_LANE_TEXT = [
    (re.compile(r"\bmidjourney\b|\brunway\s+ml\b|ad[- ]creative|creative\s+production", re.I),
     "creative/ad-media production, not AI engineering"),
    (re.compile(r"computational\s+geometry|\bcad/cae\b|\bcae\b|mesh\s+(?:generation|representation)|finite[-\s]element", re.I),
     "CAD/CAE/geometry engineering, off-lane for this profile"),
    (re.compile(r"\bitil\b|incident\s+management|infrastructure\s+administration|"
                r"service\s+management", re.I),
     "IT service management / infra administration, not AI engineering"),
    (re.compile(r"automated\s+driving|autonomous\s+driving|\badas\b|iso\s*26262|functional\s+safety", re.I),
     "automotive/autonomous-driving safety, off-lane for this profile"),
]


def off_lane_text(description: str) -> str:
    text = description or ""
    for rx, why in _OFF_LANE_TEXT:
        if rx.search(text):
            return why
    return ""


# German places that appear in a LinkedIn location string without the word
# "Germany" attached. Everything else that is not "Germany"/"Deutschland" counts
# as outside Germany, which is the looser-years side of the line.
_DE_PLACES = re.compile(
    r"\b(?:germany|deutschland|berlin|munich|münchen|hamburg|cologne|köln|"
    r"frankfurt|stuttgart|düsseldorf|dusseldorf|dresden|leipzig|hannover|"
    r"hanover|nuremberg|nürnberg|bremen|essen|dortmund|aachen|heidelberg|"
    r"karlsruhe|mannheim|bonn|münster|munster|bavaria|bayern|hesse|hessen|"
    r"saxony|sachsen|thuringia|brandenburg|baden[- ]württemberg|"
    r"baden[- ]wurttemberg|north rhine[- ]westphalia|rhineland[- ]palatinate|"
    r"lower saxony|schleswig|mecklenburg|saarland)\b", re.I)


def in_germany(location: str) -> bool:
    """Is this posting located in Germany?

    Decides which years ceiling applies. A regional label that merely includes
    Germany ("EMEA", "European Union", "DACH") is NOT Germany for this purpose:
    those are remote-abroad listings and get the looser ceiling, which is the
    whole point of the split.
    """
    return bool(_DE_PLACES.search(location or ""))


def years_ceiling(v: dict) -> int:
    """The years ceiling for this posting: tighter at home, looser abroad."""
    if v.get("in_germany", True):
        return config.DROP_YEARS_AT
    return getattr(config, "DROP_YEARS_AT_REMOTE", config.DROP_YEARS_AT)


def verdict(v: dict) -> tuple[str, str]:
    """The decision is here, in code, never in the model's answer."""
    # Written in German beats anything the model said about German. On
    # 19 September both Reply and secureIO were returned as german_level="none"
    # and both postings are German from first line to last.
    if v.get("german_share", 0.0) >= config.GERMAN_TEXT_SHARE:
        return "german", (f"the posting is written in German "
                          f"({v['german_share']:.0%} German prose)")
    # A hard German requirement the text states outright, whatever level the
    # model guessed. This is the gate that leaked eight roles on 21 September.
    if v.get("german_req_text"):
        return "german", f"requires fluent German (\"{v['german_req_text']}\")"
    if v["german_num"] >= config.DROP_GERMAN_AT:
        return "german", f"needs {v['german_level'].upper()} German"
    if v.get("years_gate") is not None and v["years_gate"] >= years_ceiling(v):
        where = "" if v.get("in_germany", True) else " for a role outside Germany"
        return "years", f"asks for {v['years_gate']}+ years{where}"
    if config.DROP_MANAGEMENT and v["is_management"]:
        return "management", "the job is running a team, not building"
    # A different discipline named outright in the description, even when the
    # model still labelled the role "ai_engineer".
    if v.get("off_lane_text"):
        return "off-lane", v["off_lane_text"]
    if v["role_family"] in OFF_LANE:
        return "off-lane", f"{v['role_family'].replace('_', ' ')} is off-lane for this profile"
    if v["fit"] < config.MIN_FIT:
        return "weak-fit", f"scored {v['fit']}, below the {config.MIN_FIT} floor"
    return "passed", ""


def score(v: dict) -> int:
    s = v["fit"]
    if v["role_family"] in AI_FAMILIES:
        s += config.BONUS_AI_ROLE
    if v["berlin"]:
        s += config.BONUS_BERLIN
    elif v["remote_germany"]:
        s += config.BONUS_REMOTE_DE
    if v["role_family"] in OFF_LANE:
        s -= config.PENALTY_OFF_LANE
    # A role asking more years than the user has is kept (unless it crosses
    # DROP_YEARS_AT) but ranked lower, one step per extra year.
    yrs = v.get("years_required")
    if yrs is not None and yrs > config.YOUR_YEARS:
        s -= config.PENALTY_PER_YEAR_OVER * (yrs - config.YOUR_YEARS)
    return max(0, s)


# An 8B model scores in blunt steps — five roles came back at exactly 80 — so
# the headline score alone leaves most of the order to chance. These two are
# deterministic, read from the posting rather than from the model, and only
# ever break a tie.
TITLE_EXACT = re.compile(
    r"\b(applied ai|ai engineer|a\.?i\.? engineer|llm engineer|genai engineer|"
    r"machine learning engineer|ml engineer|ai/ml engineer|agent engineer)\b", re.I)


def title_bonus(title: str) -> int:
    """Does the posting actually call itself the thing the user is looking for."""
    t = title or ""
    if TITLE_EXACT.search(t):
        return 2
    if re.search(r"\b(ai|ml|machine learning|llm)\b", t, re.I):
        return 1
    return 0


def berlin_precision(location: str) -> int:
    """
    Berlin alone beats Berlin in a list of nine cities.

    "bundesweit, Berlin, Frankfurt, Hamburg, Köln, München" is a nationwide
    posting that mentions Berlin, not a Berlin job, and it should not outrank a
    role that is actually in Berlin.
    """
    loc = (location or "").lower()
    if "berlin" not in loc:
        return 0
    if re.search(r"bundesweit|nationwide|deutschlandweit", loc):
        return 1
    cities = [p for p in re.split(r"[,/|]| und ", loc) if p.strip()]
    return 3 if len(cities) <= 2 else 2


def sort_key(r: dict) -> tuple:
    return (-r["rank_score"],
            -title_bonus(r["title"]),
            -berlin_precision(r["location"]),
            -r["match_score"],
            r["title"])


# --------------------------------------------------------------- driver
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=None,
                    help="default: OLLAMA_MODEL, or a model already installed in Ollama")
    ap.add_argument("--top", type=int, default=config.TOP_N)
    ap.add_argument("--day", default=None, help="only this scrape date, e.g. 2026-09-13")
    ap.add_argument("--limit", type=int, default=0, help="only the first N, for testing")
    ap.add_argument("--recheck", action="store_true", help="ignore cached answers")
    args = ap.parse_args()
    # OLLAMA_MODEL if it is installed, otherwise one Ollama already has.
    args.model = args.model or ollama_model.resolve(config) or ollama_model.wanted(config)

    OUT.mkdir(exist_ok=True)
    profile = PROFILE_PATH.read_text(encoding="utf-8")

    # Which jobs the model reads is decided in pool.py, the one rule the
    # dashboard's numbers use too, so the two can never disagree again.
    import pool as pool_rules
    jobs, too_old = pool_rules.load(args.day)
    buckets = pool_rules.split(jobs, record=True)
    pool = buckets["to_judge"]
    repeats = [{"title": j.get("title") or "", "company": j.get("company") or "",
                "url": j.get("url") or "", "location": j.get("location") or "",
                "source": j.get("source") or "", "rank_score": 0,
                "verdict": "seen-earlier",
                "why": f"already judged on {j['_judged']}, when it was first collected"}
               for j in buckets["repeat"]]
    if args.limit:
        pool = pool[: args.limit]
    breakdown = {k: len(v) for k, v in buckets.items()}
    breakdown.update(too_old=too_old, collected=len(jobs) + too_old)

    if not pool and not repeats:
        print("Nothing to rank. Scrape some jobs first.")
        return 1
    print(pool_rules.explain(breakdown))

    cache = _read(CACHE_PATH, {})
    print(f"model {args.model} · {config.OLLAMA_CONCURRENCY} at a time\n")

    def cache_key(job: dict) -> str:
        """
        Keyed on the DESCRIPTION, not just the job.

        The 67 LinkedIn descriptions repaired on 18 September kept their old
        answers otherwise: same URL, same model, same prompt version, so the
        cache handed back verdicts computed from the page furniture the repair
        had just replaced. A description fingerprint makes a repaired posting a
        different question, which is exactly what it is.
        """
        fp = hashlib.sha1((job.get("description") or "").encode()).hexdigest()[:12]
        return f"{job_key(job['url'])}|{args.model}|{PROMPT_VERSION}|{fp}"

    def one(job: dict) -> tuple[dict, dict | None, str]:
        ck = cache_key(job)
        if not args.recheck and ck in cache:
            return job, cache[ck], "cached"
        try:
            return job, normalise(ask(args.model, build_prompt(job, profile))), "asked"
        except (urllib.error.URLError, OSError, ValueError, KeyError) as e:
            return job, None, f"failed: {type(e).__name__}: {e}"[:120]

    rows, failures, asked = [], [], 0
    # Roles the model has now judged (answered fresh or from the cache), written
    # to history/seen.csv: only a role judged before can later be dropped as a
    # repeat. Flushed every ten, at the end, and on Stop.
    judged_now: list[dict] = []

    def _flush_judged() -> None:
        if judged_now:
            try:
                ledger.mark_judged(judged_now)
            except OSError as e:
                print(f"  ! could not record judged roles ({e})")
            judged_now.clear()

    # Stopped from the dashboard (or Ctrl+C): every answer is already saved, so
    # say so and leave at once rather than waiting for the model calls in
    # flight. os._exit because those calls run in worker threads.
    def _stop(signum, _frame):
        _save_cache(cache)
        _flush_judged()
        print(f"\nstopped after {len(rows) + len(failures)}/{len(pool)} "
              "judged; press Rank pool to continue from here", flush=True)
        os._exit(143)
    for _sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(_sig, _stop)
    with futures.ThreadPoolExecutor(max_workers=config.OLLAMA_CONCURRENCY) as pool_ex:
        for i, (job, v, how) in enumerate(pool_ex.map(one, pool), 1):
            if v is None:
                failures.append((job, how))
                print(f"  {i:>3}/{len(pool)}  !! {(job.get('title') or '')[:48]:<48} {how}")
                continue
            judged_now.append(job)
            if len(judged_now) >= 10:
                _flush_judged()
            if how == "asked":
                asked += 1
                cache[cache_key(job)] = v
                # Flush every answer. A run over 195 jobs takes the better part
                # of an hour, and the dashboard's Stop ranking button ends it at
                # any moment (on Windows without a chance to clean up), so an
                # answer not on disk is an answer bought twice. The write is a
                # few milliseconds against seconds per model call.
                _save_cache(cache)
            # The location string decides Berlin, not the model. Fall back to
            # the model only where the posting truly does not say.
            b = berlin_from_text(job.get("location"), job.get("description"))
            v["berlin"] = v["berlin"] if b is None else b
            if b is False:
                v["remote_germany"] = False
            vd, why = shape_of(job.get("title", ""))
            if not vd:
                gig = is_marketplace(job.get("company", ""), job.get("description", ""))
                if gig:
                    vd, why = "marketplace", gig
            if not vd and len((job.get("description") or "").strip()) < 200:
                # Nothing to judge and nothing to tailor against. Both survivors
                # of the 19 September re-rank were empty records that passed on
                # a default score: a CV cannot be written from a blank posting,
                # so a blank posting is not a candidate.
                vd, why = "no-description", "the posting has no description to read"
            if not vd:
                desc = job.get("description") or ""
                v["german_share"] = written_in_german(desc)
                # Which years ceiling applies, decided from the location string
                # rather than the model: Germany gets the tight one, anything
                # remote/abroad (other countries, EMEA, EU) the looser one.
                v["in_germany"] = in_germany(job.get("location"))
                # Deterministic overrides for what the model routinely under-calls.
                v["german_req_text"] = german_required(desc)
                # Gate on the text's own figure, not the model's — the model reads
                # a range at its ceiling and gated roles the user qualifies for. The
                # model's number is still used for scoring (ranking seniors lower).
                v["years_gate"] = stated_years(desc)
                v["off_lane_text"] = off_lane_text(desc)
                vd, why = verdict(v)
            rows.append({
                "title": job.get("title") or "", "company": job.get("company") or "",
                "location": job.get("location") or "", "url": job["url"],
                "source": job.get("source") or "", "query": job.get("query") or "",
                "description": job.get("description") or "",
                "verdict": vd, "why": why, "rank_score": score(v),
                "match_score": round(v["fit"] / 100, 2),
                "years_required": v["years_required"],
                "german_level": v["german_level"],
                "years_source": "ollama" if v["years_required"] is not None else "unstated",
                "role_family": v["role_family"], "berlin": v["berlin"],
                "reason": v["reason"],
                # 14 of 85 LinkedIn cards carried no company name on
                # 13 September. The description almost always names the
                # employer, and whoever writes the letter is reading it anyway,
                # so flag it rather than addressing a letter to nobody.
                "needs_company": not (job.get("company") or "").strip(),
            })
            if i % 10 == 0:
                print(f"  --- {i}/{len(pool)} judged, {len(rows)} kept so far ---")
            mark = "·" if vd == "passed" else "✗"
            print(f"  {i:>3}/{len(pool)}  {mark} {v['fit']:>3} {(job.get('title') or '')[:46]:<46} "
                  f"{vd:<11} {v['reason'][:44]}")

    _save_cache(cache)
    _flush_judged()

    passed = sorted([r for r in rows if r["verdict"] == "passed"], key=sort_key)
    dropped = [r for r in rows if r["verdict"] != "passed"]
    top = passed[: args.top]
    for i, r in enumerate(top, 1):
        r["rank"] = i

    # Computed before anything is written, because both files carry it.
    health = pool_health(rows, passed, args.top)

    # ranked.json, NOT jobs.json.
    #
    # out/jobs.json belongs to the old run.py pipeline, which the bridge still
    # runs on every push from the extension. On 18 September it overwrote this
    # ranking twenty minutes after it was written, and the batch would have
    # built from 90 unranked rows carrying none of these filters. Two writers,
    # one filename. This one gets its own.
    _atomic_write(OUT / "ranked.json",
        json.dumps([{k: v for k, v in r.items() if k != "description"} for r in top],
                   indent=2, ensure_ascii=False), encoding="utf-8")
    _atomic_write(OUT / "ollama_rank.json", json.dumps({
        "generated": dt.datetime.now().isoformat(timespec="seconds"),
        "model": args.model, "top_n": args.top,
        "health": health,
        "counts": {"scraped": breakdown["collected"], "judged": len(rows), "passed": len(passed),
                   "dropped": len(dropped), "sent_to_claude": len(top),
                   "failed": len(failures), "seen_earlier": len(repeats)},
        # Why the rest of what was collected was not read, bucket by bucket.
        "not_read": {k: breakdown[k] for k in ("repeat", "applied", "no_description", "too_old")},
        "top": top, "passed": passed, "dropped": dropped, "repeats": repeats,
    }, indent=2, ensure_ascii=False), encoding="utf-8")

    _write_audit(rows, top, args, health)

    by = {}
    for r in dropped:
        by[r["verdict"]] = by.get(r["verdict"], 0) + 1
    print(f"\n{len(rows)} judged ({asked} new, {len(rows) - asked} cached) · "
          f"{len(passed)} passed · {len(dropped)} dropped "
          f"({', '.join(f'{k} {v}' for k, v in sorted(by.items())) or 'none'})")
    if failures:
        print(f"{len(failures)} could not be judged and were left out")
    print_health(health)
    print(f"Top {len(top)} written to out/ranked.json:")
    for r in top:
        print(f"  {r['rank']:>2}. {r['rank_score']:>3}  {r['title'][:50]:<50} "
              f"{r['company'][:22]:<22} {r['location'][:20]}")
    print("\nFull detail in out/ollama_rank.json and out/ollama_rank.txt")
    return 0


def pool_health(rows: list[dict], passed: list[dict], target: int) -> dict:
    """
    Scrape again, or keep building from what is here?

    Decision, 19 September 2026. The question is not "are there any jobs
    left" but "are there enough GOOD ones left", and those come apart: a pool can
    hold forty survivors and nothing worth a tailored CV.

    Three things decide it, in order of how much they matter:

      1. Can the pool still fill one batch at all. Below that, nothing but
         scraping helps.
      2. How many of those are strong rather than merely eligible. When the
         strong ones run out, the next batch is weaker than the last one and
         that is the moment to scrape, not after it has already happened.
      3. Whether the SEARCHES have drifted. A very low pass rate is not a thin
         pool, it is the wrong queries: more scraping on the same searches just
         produces more of what is already being thrown away. That one needs
         different search terms, so it is called out separately.
    """
    judged = len(rows)
    buildable = len(passed)
    strong = [r for r in passed if r["rank_score"] >= config.STRONG_SCORE]
    share = (len(strong) / buildable) if buildable else 0.0
    pass_rate = (buildable / judged) if judged else 0.0
    scores = sorted((r["rank_score"] for r in passed), reverse=True)
    median_top = scores[min(len(scores), target) // 2] if scores else 0

    if buildable < config.MIN_WORTH_BUILDING:
        verdict, why = "SCRAPE", (
            f"only {buildable} buildable role(s) left, below the floor of "
            f"{config.MIN_WORTH_BUILDING} — too thin to be worth a run")
    elif buildable < target:
        # Decision, 19 September 2026. A pool of 10 is not a failed pool
        # of 15. The target is a ceiling on one batch, not a quota the pool has
        # to meet: build all ten, THEN scrape. The alternative was throwing ten
        # good roles away for the crime of not being fifteen.
        verdict, why = "BUILD THEN SCRAPE", (
            f"{buildable} buildable, short of a full batch of {target} — "
            f"build all {buildable}, then scrape")
    elif len(strong) < target:
        verdict, why = "BUILD THEN SCRAPE", (
            f"{buildable} buildable but only {len(strong)} strong, "
            f"enough for this batch and thin for the next")
    elif share < config.STRONG_SHARE_WARN:
        verdict, why = "BUILD THEN SCRAPE", (
            f"only {share:.0%} of the pool is a strong match")
    else:
        verdict, why = "BUILD", (
            f"{buildable} buildable, {len(strong)} strong — the pool is healthy")

    drift = pass_rate < config.PASS_RATE_DRIFT and judged >= 40
    return {"verdict": verdict, "why": why, "judged": judged,
            # How many to build in THIS batch: everything that is here, capped
            # at the target. run-batch.sh aims at this, not at the target.
            "build_now": min(buildable, target),
            "buildable": buildable, "strong": len(strong),
            "strong_share": round(share, 3), "pass_rate": round(pass_rate, 3),
            "median_top_score": median_top, "target": target,
            "search_drift": drift}


def print_health(h: dict) -> None:
    bar = "=" * 64
    print(f"\n{bar}\n  POOL: {h['verdict']} — {h['why']}\n{bar}")
    print(f"  {h['judged']} judged · {h['buildable']} buildable · "
          f"{h['strong']} strong (>= {config.STRONG_SCORE}) · "
          f"{h['pass_rate']:.0%} pass rate · median top score {h['median_top_score']}")
    if h["search_drift"]:
        print(f"  !! only {h['pass_rate']:.0%} of what was read survived the gates. "
              f"That is the SEARCHES drifting, not a thin pool:")
        print("     scraping more of the same queries will return more of the "
              "same rejects. Change the search terms instead.")
    if h["verdict"] == "SCRAPE":
        print("  -> Scrape before building. There is not enough here to be "
              "worth a run.")
    elif h["verdict"].startswith("BUILD THEN"):
        print(f"  -> Build {h['build_now']} now, then scrape before the next "
              f"batch.")
    else:
        print("  -> Build. No need to scrape yet.")
    print()


def _write_audit(rows: list[dict], top: list[dict], args, health: dict) -> None:
    lines = [f"OLLAMA RANK · {dt.datetime.now().isoformat(timespec='seconds')} "
             f"· model {args.model} · top {args.top}", "",
             f"POOL: {health['verdict']} — {health['why']}",
             f"  {health['judged']} judged · {health['buildable']} buildable · "
             f"{health['strong']} strong · {health['pass_rate']:.0%} pass rate",
             ""]
    if health["search_drift"]:
        lines += ["  !! SEARCH DRIFT: very little of what was read survived the "
                  "gates. Change the search terms rather than scraping more.", ""]
    lines.append(f"SENT TO CLAUDE ({len(top)})")
    for r in top:
        lines.append(f"{r['rank']:>2}. [{r['rank_score']:>3}] {r['title']} @ {r['company']}")
        lines.append(f"    {r['location']} · {r['role_family']} · "
                     f"german {r['german_level']} · years {r['years_required']}")
        lines.append(f"    {r['reason']}")
        if r["needs_company"]:
            lines.append("    !! no company name captured — read it out of the description")
        lines.append(f"    {r['url']}")
    for name in ("german", "years", "management", "off-lane", "not-a-job",
                 "not-engineering", "marketplace", "weak-fit"):
        group = [r for r in rows if r["verdict"] == name]
        if not group:
            continue
        lines += ["", f"DROPPED — {name} ({len(group)})"]
        for r in group:
            lines.append(f"  {r['title'][:60]} @ {r['company'][:28]} — {r['why']}")
    rest = [r for r in rows if r["verdict"] == "passed" and r not in top]
    if rest:
        lines += ["", f"PASSED BUT BELOW THE CUT ({len(rest)})"]
        for r in sorted(rest, key=sort_key):
            lines.append(f"  [{r['rank_score']:>3}] {r['title'][:58]} @ {r['company'][:26]}")
    (OUT / "ollama_rank.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    # Python block-buffers stdout when it is not a terminal, so a run sent to a
    # log file printed nothing for the full twenty minutes and looked hung.
    # There is no reason to buffer a progress line.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass
    sys.exit(main())
