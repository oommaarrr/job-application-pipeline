"""
Job preferences: what this person is looking for, as settings every part of the
pipeline can read.

November 2026. The profile (profile.md, identity.md) is free text, written for a
person or a model to read. The rules that decide which jobs are kept and how a
CV is shaped were constants instead, set for one AI engineer in Berlin with two
years of experience: a sales manager with seven years, a marketer who speaks
German natively, or someone looking for work in Dubai had every search come
back nearly empty, and nothing they could change from the dashboard fixed it.

This file is the bridge between the two. Each profile folder gets a
preferences.json with the few facts the rules need:

    target_roles        the jobs they are looking for, as postings title them
    not_my_field        kinds of work they do not want
    years_experience    their professional years
    max_years_asked     skip a posting asking more than this (None: no limit)
    languages           [{"language": "German", "level": "b1"}, ...]; level ""
                        when the profile names the language without one
    places              [{"place": "Berlin", "country": "DE", "work": ["onsite", ...]}]
    remote_anywhere     fully remote jobs based in any country are fine
    relocate            they would move for the right job, so on-site jobs
                        elsewhere are acceptable (their places still come first)
    accept_management   roles that mainly manage a team are fine
    job_kinds           which kinds of job they want: regular jobs, working
                        student, internship, thesis, apprenticeship,
                        freelance, gig work ([] means any kind)
    cv_shape            "projects" (named things they built) or "achievements"
                        (results inside each role: sales, marketing, operations)
    cv_max_pages        1, 2 or 3
    cv_state_years      say "N years of experience" on the CV

Claude (Sonnet) writes it from the profile, and lists what the profile leaves
open as questions for the person (shown on the dashboard until they save the
preferences). The local model is
the fallback. The person can change any of it on the dashboard; a field
they changed, there or by editing the file, is theirs and is never rewritten.
Every other field follows the profile: when profile.md or identity.md changes,
or a new version of the pipeline needs a field it did not have, the bridge
writes them again (see serve.py, _prefs_watch).

The fictional example profile ships its own preferences.json and is never
rewritten: it is part of the code, and an update must not find it changed.

    python preferences.py              print the active profile's preferences
    python preferences.py --refresh    write them from the profile if due
    python preferences.py --force      write them from the profile now

Standard library only, plus the project's platform_util.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import pathlib
import re
import subprocess
import sys
import threading
import time
import urllib.request

import config
import countries
import platform_util as pu
from profile_dir import REPO, profile_dir

VERSION = 2        # 2: job_kinds, and Claude's open questions
FILE_NAME = "preferences.json"
SOURCES = ("profile.md", "identity.md")
OUT = pathlib.Path(__file__).resolve().parent / "out"
EXAMPLE = REPO / "profiles" / "example"

LEVELS = ("none", "a1", "a2", "b1", "b2", "c1", "c2", "native")
WORK = ("onsite", "hybrid", "remote")
SHAPES = ("projects", "achievements")
# Kinds of job. A person picks any number; [] means any kind is fine.
KINDS = ("regular", "working_student", "internship", "thesis", "apprenticeship",
         "freelance", "gig")
KIND_NAMES = {"regular": "regular job", "working_student": "working-student job",
              "internship": "internship", "thesis": "thesis placement",
              "apprenticeship": "apprenticeship", "freelance": "freelance or contract work",
              "gig": "gig work through a task platform"}
MAX_QUESTIONS = 6
MAX_ITEMS = 12

FIELDS = ("target_roles", "not_my_field", "years_experience", "max_years_asked",
          "languages", "places", "remote_anywhere", "relocate", "accept_management",
          "job_kinds", "cv_shape", "cv_max_pages", "cv_state_years")

MODEL = getattr(config, "CLAUDE_PREFS_MODEL", "claude-sonnet-5-5")
TIMEOUT = getattr(config, "CLAUDE_PREFS_TIMEOUT", 120 if os.name == "nt" else 90)


class GenerateError(Exception):
    """The preferences could not be written from the profile. `fix` says what to do."""

    def __init__(self, message: str, fix: str = ""):
        super().__init__(message)
        self.fix = fix


# ------------------------------------------------------------------ values
def defaults() -> dict:
    """What a field holds when the profile says nothing about it. Nothing here
    rules a job out: an unknown is never a reason to drop one."""
    return {"target_roles": [], "not_my_field": [], "years_experience": None,
            "max_years_asked": None, "languages": [], "places": [],
            "remote_anywhere": False, "relocate": False, "accept_management": True,
            "job_kinds": [], "cv_shape": "achievements", "cv_max_pages": 1, "cv_state_years": False}


def _text(v, limit: int = 80) -> str:
    return re.sub(r"\s+", " ", str(v or "")).strip()[:limit].strip()


def _texts(v) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[\n;]+", v)
    out: list[str] = []
    seen: set[str] = set()
    for item in v if isinstance(v, list) else []:
        t = _text(item)
        if t and t.lower() not in seen:
            seen.add(t.lower())
            out.append(t)
    return out[:MAX_ITEMS]


def _years(v):
    """A whole number of years from 3, 3.5, "3", "5+" or "about 4"; None otherwise."""
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        n = int(round(v))
    else:
        m = re.search(r"\d{1,2}", str(v or ""))
        if not m:
            return None
        n = int(m.group(0))
    return n if 0 <= n <= 60 else None


def _bool(v, default: bool) -> bool:
    if isinstance(v, bool):
        return v
    t = str(v or "").strip().lower()
    if t in ("true", "yes", "y", "1"):
        return True
    if t in ("false", "no", "n", "0"):
        return False
    return default


_LEVEL_WORDS = {"mother tongue": "native", "muttersprache": "native", "first language": "native",
                "fluent": "c1", "fließend": "c1", "fliessend": "c1", "business fluent": "c1",
                "proficient": "c1", "advanced": "c1", "intermediate": "b1",
                "basic": "a2", "beginner": "a1", "elementary": "a2"}


def _level(v) -> str | None:
    t = _text(v, 30).lower()
    if t in LEVELS:
        return t
    m = re.match(r"^([abc][12])\b", t)
    if m:
        return m.group(1)
    return _LEVEL_WORDS.get(t)


def _languages(v) -> list[dict]:
    if isinstance(v, dict):                     # {"German": "B1"}
        v = [{"language": k, "level": x} for k, x in v.items()]
    out: list[dict] = []
    seen: set[str] = set()
    for item in v if isinstance(v, list) else []:
        if not isinstance(item, dict):
            continue
        name = _text(item.get("language") or item.get("name"), 40)
        key = language_key(name)
        # A language they speak at a level nobody stated is kept, level "":
        # dropping it would read as "does not speak it" and rule jobs out on
        # an unknown (17 November 2026: English left out this way dropped
        # every English-language posting for an English speaker).
        level = _level(item.get("level")) or ""
        if not name or key in seen:
            continue
        seen.add(key)
        out.append({"language": name[:1].upper() + name[1:], "level": level})
    return out[:MAX_ITEMS]


def _country(v, place: str) -> str:
    c = _text(v, 60)
    if countries.valid(c):
        return c.upper()
    return countries.code(c) or countries.code(place) or ""


def _places(v) -> list[dict]:
    out: list[dict] = []
    seen: set[str] = set()
    for item in v if isinstance(v, list) else []:
        if isinstance(item, str):
            item = {"place": item}
        if not isinstance(item, dict):
            continue
        place = _text(item.get("place") or item.get("name"), 60)
        country = _country(item.get("country"), place)
        if not place and country:
            place = countries.name(country)
        if not place or place.lower() in seen:
            continue
        seen.add(place.lower())
        work = item.get("work")
        work = [w for w in WORK if isinstance(work, list) and w in [str(x).lower().replace("-", "")
                                                                     .replace(" ", "") for x in work]]
        out.append({"place": place, "country": country, "work": work or list(WORK)})
    return out[:MAX_ITEMS]


_KIND_WORDS = {"full_time": "regular", "fulltime": "regular", "part_time": "regular",
               "parttime": "regular", "permanent": "regular", "job": "regular",
               "employee": "regular", "werkstudent": "working_student",
               "student": "working_student", "intern": "internship",
               "praktikum": "internship", "ausbildung": "apprenticeship",
               "contract": "freelance", "contractor": "freelance", "freelancer": "freelance"}


def _kinds(v) -> list[str]:
    if isinstance(v, str):
        v = re.split(r"[,;\n]+", v)
    out: list[str] = []
    for item in v if isinstance(v, list) else []:
        k = re.sub(r"[\s-]+", "_", str(item or "").strip().lower())
        k = _KIND_WORDS.get(k, k)
        if k in KINDS and k not in out:
            out.append(k)
    # Every kind is the same as no limit.
    return [] if len(out) == len(KINDS) else [k for k in KINDS if k in out]


def questions(raw) -> list[dict]:
    """Claude's open questions, cleaned: [{"field", "question"}]."""
    out: list[dict] = []
    for item in raw if isinstance(raw, list) else []:
        if isinstance(item, str):
            item = {"question": item}
        if not isinstance(item, dict):
            continue
        q = _text(item.get("question"), 240)
        f = str(item.get("field") or "").strip()
        if q:
            out.append({"field": f if f in FIELDS else "", "question": q})
    return out[:MAX_QUESTIONS]


def _pages(v) -> int:
    """1, 2 or 3. More than 3 is 3; nothing readable is 1."""
    n = _years(v)
    return 1 if not n else min(n, 3)


def normalise(raw) -> dict:
    """Any answer or form, cleaned into exactly the fields above."""
    raw = raw if isinstance(raw, dict) else {}
    d = defaults()
    out = {
        "target_roles": _texts(raw.get("target_roles")),
        "not_my_field": _texts(raw.get("not_my_field")),
        "years_experience": _years(raw.get("years_experience")),
        "max_years_asked": _years(raw.get("max_years_asked")),
        "languages": _languages(raw.get("languages")),
        "places": _places(raw.get("places")),
        "remote_anywhere": _bool(raw.get("remote_anywhere"), d["remote_anywhere"]),
        "relocate": _bool(raw.get("relocate"), d["relocate"]),
        "accept_management": _bool(raw.get("accept_management"), d["accept_management"]),
        "job_kinds": _kinds(raw.get("job_kinds")),
        "cv_shape": raw.get("cv_shape") if raw.get("cv_shape") in SHAPES else d["cv_shape"],
        "cv_max_pages": _pages(raw.get("cv_max_pages")),
        "cv_state_years": _bool(raw.get("cv_state_years"), d["cv_state_years"]),
    }
    return out


# One name per language, whatever language it is written in: a posting may
# say "Deutsch" or "allemand" for what the person listed as "German".
_LANG_NAMES = {
    "german": ("german", "deutsch", "allemand", "alemán", "aleman", "tedesco", "alemão", "duits",
               "niemiecki", "almanca", "немецкий", "ألمانية", "الألمانية"),
    "english": ("english", "englisch", "anglais", "inglés", "ingles", "inglese", "inglês", "engels",
                "angielski", "ingilizce", "английский", "الإنجليزية", "إنجليزية"),
    "french": ("french", "französisch", "franzoesisch", "français", "francais", "francés", "frances",
               "francese", "francês", "frans", "francuski", "fransızca", "французский", "الفرنسية"),
    "spanish": ("spanish", "spanisch", "espagnol", "español", "espanol", "spagnolo", "espanhol",
                "spaans", "hiszpański", "ispanyolca", "испанский", "castellano", "الإسبانية"),
    "italian": ("italian", "italienisch", "italien", "italiano", "italiaans", "włoski", "italyanca"),
    "portuguese": ("portuguese", "portugiesisch", "portugais", "portugués", "português", "portoghese",
                   "portugees", "portugalski"),
    "dutch": ("dutch", "niederländisch", "niederlaendisch", "nederlands", "néerlandais", "flemish",
              "holländisch", "neerlandés", "olandese"),
    "polish": ("polish", "polnisch", "polski", "polonais", "polaco", "polacco", "lehçe"),
    "turkish": ("turkish", "türkisch", "tuerkisch", "türkçe", "turkce", "turc", "turco"),
    "arabic": ("arabic", "arabisch", "arabe", "árabe", "arabo", "العربية", "عربي", "arapça"),
    "russian": ("russian", "russisch", "russe", "ruso", "russo", "русский", "rosyjski", "rusça"),
    "ukrainian": ("ukrainian", "ukrainisch", "ukrainien", "ucraniano", "українська"),
    "chinese": ("chinese", "mandarin", "chinesisch", "chinois", "chino", "cinese", "中文", "普通话",
                "汉语"),
    "cantonese": ("cantonese", "kantonesisch", "cantonais", "cantonés", "粵語", "廣東話"),
    "japanese": ("japanese", "japanisch", "japonais", "japonés", "giapponese", "日本語"),
    "korean": ("korean", "koreanisch", "coréen", "coreano", "한국어"),
    "hindi": ("hindi", "हिन्दी", "हिंदी"),
    "greek": ("greek", "griechisch", "grec", "griego", "greco", "ελληνικά"),
    "czech": ("czech", "tschechisch", "tchèque", "checo", "čeština", "cestina"),
    "swedish": ("swedish", "schwedisch", "suédois", "sueco", "svenska"),
    "danish": ("danish", "dänisch", "daenisch", "danois", "danés", "dansk"),
    "norwegian": ("norwegian", "norwegisch", "norvégien", "noruego", "norsk"),
    "finnish": ("finnish", "finnisch", "finnois", "finlandés", "suomi"),
    "hebrew": ("hebrew", "hebräisch", "hébreu", "hebreo", "עברית"),
    "romanian": ("romanian", "rumänisch", "roumain", "rumano", "română", "romana"),
    "hungarian": ("hungarian", "ungarisch", "hongrois", "húngaro", "magyar"),
    "persian": ("persian", "farsi", "persisch", "persan", "فارسی"),
    "urdu": ("urdu", "اردو"),
    "vietnamese": ("vietnamese", "vietnamesisch", "tiếng việt"),
    "croatian": ("croatian", "kroatisch", "hrvatski"),
    "serbian": ("serbian", "serbisch", "srpski", "српски"),
    "bosnian": ("bosnian", "bosnisch", "bosanski"),
}
_LANG_CANON = {alias: canon for canon, names in _LANG_NAMES.items() for alias in names}


def language_key(name: str) -> str:
    """The same key for "German", "Deutsch" and "allemand", and for "Business
    English", "Swiss German", "Deutschkenntnisse" or "Mandarin Chinese"."""
    t = re.sub(r"\s+", " ", str(name or "")).strip().lower()
    t = re.sub(r"\s*\(.*?\)\s*", "", t).strip()          # "Englisch (verhandlungssicher)"
    if t in _LANG_CANON:
        return _LANG_CANON[t]
    words = re.findall(r"\w+", t)
    for w in reversed(words):                 # the language is usually the last word
        if w in _LANG_CANON:
            return _LANG_CANON[w]
    for w in words:                           # "Deutschkenntnisse", "Englischkenntnisse"
        for alias, canon in _LANG_CANON.items():
            if len(alias) >= 5 and w.startswith(alias):
                return canon
    return t


def level_of(prefs: dict, language: str) -> str:
    """The person's level in a language ("none" when it is not listed, ""
    when it is listed without a level)."""
    want = language_key(language)
    for item in prefs.get("languages") or []:
        if language_key(item.get("language", "")) == want:
            return item.get("level") or ""
    return "none"


def summary(prefs: dict) -> str:
    """One plain line, for the activity list."""
    bits = []
    if prefs.get("target_roles"):
        bits.append(", ".join(prefs["target_roles"][:3]) + ("…" if len(prefs["target_roles"]) > 3 else ""))
    if prefs.get("years_experience") is not None:
        bits.append(f"{prefs['years_experience']} years")
    if prefs.get("places"):
        bits.append(", ".join(p["place"] for p in prefs["places"][:3]))
    langs = [f"{x['language']} {_level_word(x['level'])}".strip()
             for x in prefs.get("languages") or []]
    if langs:
        bits.append(", ".join(langs[:3]))
    if prefs.get("job_kinds"):
        bits.append(", ".join(KIND_NAMES[k] for k in prefs["job_kinds"][:3]))
    return "; ".join(bits) or "nothing the profile states"


def _level_word(lv: str) -> str:
    if not lv:
        return "(level not stated)"
    return lv if lv in ("none", "native") else lv.upper()


def limits_text(prefs: dict) -> str:
    """
    The person's limits in plain words, one line each, for whoever judges a
    posting against them (the build, which prints this at the top of the
    shortlist). Every line says what is open as well as what is set, so an
    unset limit is never read as a strict one.
    """
    def lst(v, none="not stated"):
        return "; ".join(v) if v else none
    places = [p["place"] + (f" ({p['country']})" if p.get("country") else "")
              + ": " + ", ".join(p["work"]) for p in prefs.get("places") or []]
    langs = [f"{x['language']} {_level_word(x['level'])}" for x in prefs.get("languages") or []]
    most = prefs.get("max_years_asked")
    kinds = prefs.get("job_kinds") or []
    return "\n".join([
        f"Roles they want: {lst(prefs.get('target_roles'))}",
        f"Work that is not theirs: {lst(prefs.get('not_my_field'), 'nothing ruled out')}",
        "Kinds of job: " + (", ".join(KIND_NAMES[k] for k in kinds) if kinds else "any kind"),
        f"Languages they speak: {lst(langs, 'not stated, so no language rules a job out')}",
        "Most years a posting may ask for: "
        + (str(most) if most is not None else "no limit"),
        "Where they would work: " + lst(places, "anywhere"),
        "Fully remote jobs based in other countries: "
        + ("fine" if prefs.get("remote_anywhere") or not places else "not wanted"),
        "Would relocate for the right job: " + ("yes" if prefs.get("relocate") else "no"),
        "Roles that mainly manage a team: "
        + ("fine" if prefs.get("accept_management", True) else "not wanted"),
        f"CV shape: {prefs.get('cv_shape')}, at most {prefs.get('cv_max_pages')} page(s)"
        + (", says how many years of experience" if prefs.get("cv_state_years") else ""),
    ])


# ------------------------------------------------------------------ files
def folder_of(folder: pathlib.Path | None = None) -> pathlib.Path:
    return pathlib.Path(folder) if folder else profile_dir()


def path(folder: pathlib.Path | None = None) -> pathlib.Path:
    return folder_of(folder) / FILE_NAME


def is_example(folder: pathlib.Path | None = None) -> bool:
    try:
        return folder_of(folder).resolve() == EXAMPLE.resolve()
    except OSError:
        return False


def has_profile(folder: pathlib.Path | None = None) -> bool:
    return (folder_of(folder) / "profile.md").is_file()


def _source_text(folder: pathlib.Path) -> dict[str, str]:
    out = {}
    for name in SOURCES:
        try:
            # Line endings normalised: a Windows checkout of the same text must
            # not count as a changed profile.
            out[name] = (folder / name).read_text(encoding="utf-8").replace("\r\n", "\n")
        except (OSError, UnicodeDecodeError):
            out[name] = ""
    return out


def profile_sig(folder: pathlib.Path | None = None) -> str:
    h = hashlib.sha1()
    for name, text in _source_text(folder_of(folder)).items():
        h.update(name.encode() + b"\0" + text.encode("utf-8") + b"\0")
    return h.hexdigest()[:16]


def _vsig(value) -> str:
    return hashlib.sha1(json.dumps(value, sort_keys=True, ensure_ascii=False)
                        .encode("utf-8")).hexdigest()[:12]


def _now() -> str:
    return dt.datetime.now().isoformat(timespec="seconds")


def read(folder: pathlib.Path | None = None) -> dict | None:
    """The file as written, or None when there is none. A file that is not
    valid JSON is kept beside it as preferences.json.damaged-<time> and
    treated as missing, so it is written again from the profile."""
    p = path(folder)
    text = None
    for attempt in range(5):
        try:
            text = p.read_text(encoding="utf-8")
            break
        except FileNotFoundError:
            return None
        except UnicodeDecodeError:
            break
        except OSError:
            # Windows: the moment the file is being replaced it cannot be
            # opened. That is not damage; wait and read it again.
            if attempt == 4:
                return None
            time.sleep(0.05)
    try:
        doc = json.loads(text) if text is not None else None
        if doc is None:
            raise ValueError("not text")
    except ValueError:
        if p.exists() and not is_example(folder):
            keep = p.with_name(f"{FILE_NAME}.damaged-{dt.datetime.now():%Y%m%d-%H%M%S}")
            try:
                os.replace(p, keep)
            except OSError:
                pass
        return None
    if not isinstance(doc, dict):
        return None
    # The example's own file, copied along with the example into someone's
    # folder (an older setup did): those are Alex Rivera's limits, not this
    # person's. Treated as no file, so nothing is ruled out until Claude has
    # written theirs (17 November 2026).
    if doc.get("model") == "example" and not is_example(folder):
        return None
    return doc


def _write(folder: pathlib.Path, doc: dict) -> None:
    p = path(folder)
    tmp = p.with_name(f"{FILE_NAME}.{os.getpid()}.{threading.get_ident()}.tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for attempt in range(20):            # Windows refuses while a reader has it open
        try:
            os.replace(tmp, p)
            return
        except PermissionError:
            if attempt == 19:
                tmp.unlink(missing_ok=True)
                raise
            time.sleep(0.1)


def made_by(doc: dict | None) -> dict[str, str]:
    """
    Who set each field: "model" or "you". A field the model wrote but whose
    value is no longer what it wrote was changed by hand (in the file, or by
    the Edit profile chat), so it is the person's.
    """
    if not doc:
        return {f: "model" for f in FIELDS}
    values = doc.get("preferences") or {}
    who = doc.get("made_by") or {}
    sigs = doc.get("model_values") or {}
    clean = normalise(values)
    out = {}
    for f in FIELDS:
        if who.get(f) == "you":
            out[f] = "you"
        elif f in values and f in sigs and _vsig(clean[f]) != sigs[f]:
            out[f] = "you"
        else:
            out[f] = "model"
    return out


def load(folder: pathlib.Path | None = None) -> dict:
    """
    What the rest of the pipeline reads: {"preferences": the values, cleaned,
    with defaults where a field is missing; "made_by": per field; "meta"}.
    Never raises. "exists" is False when there is no file yet.
    """
    doc = read(folder)
    values = normalise((doc or {}).get("preferences") or {})
    meta = {k: (doc or {}).get(k) for k in ("profile", "profile_sig", "made_at", "saved_at",
                                            "model", "reviewed", "changed", "version")}
    # Claude's open questions count until the person has looked (saved).
    meta["questions"] = [] if meta.get("reviewed") else questions((doc or {}).get("questions"))
    return {"exists": doc is not None, "preferences": values, "made_by": made_by(doc),
            "meta": meta, "example": is_example(folder)}


def current(folder: pathlib.Path | None = None) -> dict:
    """Just the values (defaults when there is no file)."""
    return load(folder)["preferences"]


def due(folder: pathlib.Path | None = None) -> str:
    """Why the preferences should be written from the profile now, or ""."""
    folder = folder_of(folder)
    if is_example(folder) or not has_profile(folder):
        return ""
    doc = read(folder)
    if doc is None:
        return "missing"
    if int(doc.get("version") or 0) < VERSION:
        return "version"
    if doc.get("profile_sig") != profile_sig(folder):
        return "profile"
    return ""


# ------------------------------------------------------------------ the model
SYSTEM = ("You read one job seeker's profile and answer only with the JSON you "
          "are asked for.")

PROMPT = """Below is a job seeker's profile. Fill in their job-search preferences from it, as one JSON object with exactly these keys. Use only what the profile says or plainly implies; when it says nothing about a key, use null (or [] for a list). Never guess.

"target_roles": the job titles they are looking for, as job postings would title them (up to 8). Include close variants they would take.
"not_my_field": kinds of work they do not want or cannot do, a few words each (up to 8), from what the profile excludes. [] when it excludes nothing.
"years_experience": their years of professional work as a whole number. Work it out from the employment dates when it is not stated (today is {today}). Do not count studies, or student and side jobs unrelated to their field.
"max_years_asked": the most years of experience a posting may ask for before they would skip it, only when the profile states such a rule (for example "out when four or more years are required" means 3). Otherwise null.
"languages": every language they speak, as [{{"language": "English", "level": "c1"}}]. Level is one of "a1","a2","b1","b2","c1","c2","native", or null when the profile names the language without a level (still list it: a language they work in is one they speak).
"places": where they would work, as [{{"place": a city, region or country as the profile names it, "country": its ISO 3166 two-letter code (or "" for a region spanning countries), "work": the ways they would work there, any of "onsite","hybrid","remote"}}]. A city they would work in on site or hybrid usually also takes remote jobs there.
"remote_anywhere": true when fully remote jobs based in any country are fine for them, false when the profile limits where they work, null when it does not say.
"relocate": true when they say anywhere in the profile that they would move for a job (open to relocating, location is not a blocker, would move to another city or country), false when they rule it out, null when neither is said. Read both files: this is often only in identity.md.
"accept_management": false when the profile rules out roles whose main job is managing a team or owning headcount, true when it wants or accepts them, null when it does not say.
"job_kinds": the kinds of job they are looking for, any of "regular" (an ordinary full-time or part-time job), "working_student" (Werkstudent), "internship" (Praktikum), "thesis" (a Bachelor or Master thesis placement), "apprenticeship" (Ausbildung), "freelance" (freelance or contract work), "gig" (per-task work through a platform, such as data annotation or AI training tasks). Someone studying who looks for student jobs gets the student kinds they name, not "regular". Someone with a career gets "regular", plus "freelance" unless they rule out contract work. A trainee or graduate programme is a "regular" job. Add a kind only when the profile wants it or plainly fits it; leave out any kind it rules out. When you inferred the kinds rather than read them in the profile, ask about it in open_questions.
"cv_shape": "projects" when their work is best shown as named things they built or delivered (engineers, researchers, designers, developers), "achievements" when it is results inside each role (sales, business development, marketing, operations, finance, management, consulting).
"cv_max_pages": the most pages their CV should run to: 1 for early careers, 2 for about 8 or more years, 3 for fields where long CVs are normal (academia, medicine, law, senior executives with long careers), or what the profile asks for.
"cv_state_years": true when saying how many years of experience they have would help them (about 5 or more years), false otherwise or when the profile says not to.
"open_questions": what the profile leaves open that would change which jobs they are shown or how their CV is written, as up to 6 short questions to ask them, each [{{"field": the key above it is about, "question": the question in plain words, addressed to them}}]. Ask only what the profile does not answer, for example whether they would take a role managing a team, whether they would relocate, which kinds of job they want, how many years asked is too many, or their level in a language they mention without one. [] when nothing important is open.

Answer with the JSON object only.

{texts}"""


def build_prompt(folder: pathlib.Path) -> str:
    texts = "\n\n".join(f"=== {name} ===\n{text.strip()[:20000]}"
                        for name, text in _source_text(folder).items() if text.strip())
    return PROMPT.format(today=dt.date.today().isoformat(), texts=texts)


def _parse(raw: str) -> dict:
    raw = (raw or "").strip()
    try:
        out = json.loads(raw)
    except ValueError:
        m = re.search(r"\{.*\}", raw, re.S)
        if not m:
            raise ValueError(f"no JSON in the answer: {raw[:160]}")
        out = json.loads(m.group(0))
    if not isinstance(out, dict):
        raise ValueError("the answer is not a JSON object")
    return out


_AUTH = re.compile(r"OAuth access token|authentication_error|Failed to authenticate|"
                   r"Invalid API key|Please run /login|Not logged in", re.I)
_LIMIT = re.compile(r"hit your limit|usage limit|rate limit|quota", re.I)
LEAN = ["--tools", "", "--strict-mcp-config", "--disable-slash-commands",
        "--no-session-persistence", "--setting-sources", "project", "--system-prompt", SYSTEM]


def _claude_call(claude: str, prompt: str, lean: bool) -> subprocess.CompletedProcess:
    cwd = OUT / ".prefs-claude"           # an empty folder: no project instructions load
    cwd.mkdir(parents=True, exist_ok=True)
    args = [claude, "-p", "--model", MODEL, "--output-format", "json", "--max-turns", "1",
            *(LEAN if lean else [])]
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", cwd=cwd, **pu.quiet_kwargs())
    try:
        out, err = proc.communicate(prompt, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        pu.kill_tree(proc.pid, grace=1.0)     # the whole tree: see rank_ollama._run_claude
        proc.communicate()
        raise
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


def ask_claude(prompt: str) -> dict:
    claude = pu.find_claude()
    if not claude:
        raise GenerateError("Claude Code is not installed",
                            "Install Claude Code and sign in, then press Fill from my profile")
    try:
        p = _claude_call(claude, prompt, True)
    except subprocess.TimeoutExpired:
        try:
            p = _claude_call(claude, prompt, True)
        except subprocess.TimeoutExpired:
            raise GenerateError(f"Claude did not answer within {TIMEOUT}s, twice",
                                "Check the internet connection, then press Fill from my profile")
    if p.returncode != 0 and re.search(r"unknown option|unrecognized", p.stderr or "", re.I):
        p = _claude_call(claude, prompt, False)        # an older Claude Code
    said = (p.stdout or "") + (p.stderr or "")
    try:
        env = json.loads(p.stdout)
    except ValueError:
        env = {"is_error": True, "result": said}
    if not isinstance(env, dict):
        env = {"is_error": True, "result": said}
    if env.get("is_error") or p.returncode != 0:
        if _AUTH.search(said):
            # `claude auth status` can say signed in while the token behind it
            # has expired (16 November 2026, Claude Code 2.1.101): say which.
            what = ("Claude's sign-in has expired" if re.search(r"expired", said, re.I)
                    else "Claude is signed out")
            raise GenerateError(what, "In a terminal run: claude auth login, "
                                      "then press Fill from my profile")
        if _LIMIT.search(said):
            raise GenerateError("Claude's usage limit is reached",
                                "Press Fill from my profile after the limit resets")
        raise GenerateError("Claude did not answer: " + _text(env.get("result") or said, 200))
    try:
        return _parse(str(env.get("result") or ""))
    except ValueError as e:
        raise GenerateError(f"Claude's answer could not be read ({e})")


def ask_local(prompt: str) -> tuple[dict, str]:
    import ollama_model
    url = getattr(config, "OLLAMA_URL", "http://127.0.0.1:11434")
    if not pu.ollama_up(url):
        raise GenerateError("the local model is not running")
    model = ollama_model.resolve(config) or ollama_model.wanted(config)
    body = json.dumps({"model": model, "prompt": prompt, "stream": False, "format": "json",
                       "options": {"temperature": 0, "num_predict": 1200}}).encode()
    req = urllib.request.Request(f"{url}/api/generate", data=body,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=300) as r:
            return _parse(json.loads(r.read()).get("response") or ""), model
    except (OSError, ValueError) as e:
        raise GenerateError(f"the local model did not answer ({e})")


def generate(folder: pathlib.Path | None = None, log=print) -> tuple[dict, str, list]:
    """(values, which model wrote them, its open questions), from the profile.
    Claude first; the local model when Claude cannot answer. Raises
    GenerateError."""
    folder = folder_of(folder)
    if not has_profile(folder):
        raise GenerateError("there is no profile yet",
                            "Make your profile first (Edit profile, or /make-profile)")
    prompt = build_prompt(folder)
    try:
        raw = ask_claude(prompt)
        return normalise(raw), MODEL, questions(raw.get("open_questions"))
    except GenerateError as e:
        first = e
    # PREFS_LOCAL=0: never the local model (the selftest, which must not reach a
    # real Ollama that happens to be running on the machine).
    if os.environ.get("PREFS_LOCAL") == "0":
        raise first
    log(f"Claude could not write the preferences ({first}); trying the local model")
    try:
        values, model = ask_local(prompt)
        return normalise(values), "local:" + model, questions(values.get("open_questions"))
    except GenerateError as e:
        raise GenerateError(f"{first}; and {e}", first.fix) from None


# ------------------------------------------------------------------ writing
def refresh(folder: pathlib.Path | None = None, reset: bool = False, log=print) -> dict:
    """
    Write the preferences from the profile and return load()'s result. Fields
    the person set are kept, unless reset (Fill from my profile, everything).
    Never writes the example profile's file.
    """
    folder = folder_of(folder)
    if is_example(folder):
        raise GenerateError("the active profile is the fictional example, whose preferences "
                            "ship with the code", "Make your own profile first")
    sig = profile_sig(folder)
    new, model, asked = generate(folder, log)
    old = read(folder)
    old_values = normalise((old or {}).get("preferences") or {})
    who = made_by(old)
    values, by, sigs, changed = {}, {}, {}, []
    for f in FIELDS:
        if old is not None and not reset and who[f] == "you":
            values[f], by[f] = old_values[f], "you"
            continue
        values[f], by[f], sigs[f] = new[f], "model", _vsig(new[f])
        if old is not None and new[f] != old_values[f]:
            changed.append(f)
    doc = {"version": VERSION, "preferences": values, "made_by": by, "model_values": sigs,
           "profile": folder.name, "profile_sig": sig, "made_at": _now(),
           "saved_at": (old or {}).get("saved_at"), "model": model,
           # Something to look at: a first write, or one that changed a value.
           "reviewed": bool(old) and not changed and bool(old.get("reviewed")),
           "changed": changed,
           # Only what is still the model's to decide: a field the person set
           # has been answered.
           "questions": [q for q in asked if by.get(q["field"]) != "you"]}
    _write(folder, doc)
    return load(folder)


def save(values: dict, folder: pathlib.Path | None = None) -> dict:
    """The person's own edit (the dashboard's Save). A field whose value they
    changed becomes theirs; Save also marks the whole set as looked at."""
    folder = folder_of(folder)
    if is_example(folder):
        raise GenerateError("the example profile's preferences ship with the code and are not "
                            "edited here", "Make your own profile first")
    old = read(folder)
    new = normalise({**normalise((old or {}).get("preferences") or {}),
                     **{k: v for k, v in (values or {}).items() if k in FIELDS}})
    if old is None:
        # Filled in by hand before Claude could (signed out, say): what the
        # person set is theirs, and everything still at its default is left for
        # Claude, which fills it in from the profile once it can. No profile
        # signature yet, so the bridge knows that is still to do.
        base = defaults()
        by = {f: "you" if new[f] != base[f] else "model" for f in FIELDS}
        doc = {"version": VERSION, "preferences": new, "made_by": by, "model_values": {},
               "profile": folder.name, "profile_sig": None, "made_at": None, "model": "",
               "saved_at": _now(), "reviewed": True, "changed": [], "questions": []}
        _write(folder, doc)
        return load(folder)
    old_values = normalise(old.get("preferences") or {})
    who = made_by(old)
    sigs = dict(old.get("model_values") or {})
    by = {}
    for f in FIELDS:
        by[f] = "you" if new[f] != old_values[f] else who[f]
        if by[f] == "you":
            sigs.pop(f, None)
    doc = {**old, "version": max(int(old.get("version") or 0), VERSION),
           "preferences": new, "made_by": by, "model_values": sigs,
           "profile": folder.name, "saved_at": _now(), "reviewed": True, "changed": [],
           # Saving is the answer to Claude's questions.
           "questions": []}
    _write(folder, doc)
    return load(folder)


def main(argv: list[str]) -> int:
    if "--refresh" in argv or "--force" in argv:
        why = due() if "--refresh" in argv else "asked"
        if not why:
            print("Up to date with the profile.")
        else:
            try:
                refresh(log=print)
                print(f"Written from the profile ({why}).")
            except GenerateError as e:
                print(f"Could not write them: {e}" + (f"\n  {e.fix}" if e.fix else ""))
                return 1
    st = load()
    print(json.dumps({**st["preferences"], "_made_by": st["made_by"]}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
