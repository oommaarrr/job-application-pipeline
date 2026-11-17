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
Since November 2026 the model judges each posting against the person's own
job preferences (profiles/<name>/preferences.json, see preferences.py) as well
as their profile: is it one of the roles they want, where is it and does that
suit them, what languages and how many years does it really require, is it a
management role, and what kind of job it is (a regular job, a working-student
job, an internship, a thesis, an apprenticeship, freelance or gig work). The
code then applies the person's own limits
to those answers (verdict below). Before, the limits were constants and word
lists set for one AI engineer in Berlin, and a salesperson, a marketer or
anyone outside Germany had nearly every job thrown out before a model read it.
Every limit comes from the preferences, which Claude writes from the profile
and the person can change: a student looking for working-student jobs gets
them, and an engineer does not get internships. The one rule left in code is
that a posting with no description cannot be judged.

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
import statistics
import subprocess
import sys
import time
import urllib.request

import config
import ollama_model
import ledger
import platform_util as pu
import rank_engine
from jobkey import job_key

# Repeated names, kept in one place.
BUILD_THEN_SCRAPE = "BUILD THEN SCRAPE"

HERE = pathlib.Path(__file__).parent
INBOX = HERE / "inbox"
OUT = HERE / "out"
CACHE_PATH = OUT / "ollama_cache.json"
from profile_dir import profile_file
PROFILE_PATH = profile_file()   # profiles/me, else profiles/example — see profile_dir.py

# Bump when the prompt or the answer shape changes, so old cached answers are
# not silently reused against a different question.
PROMPT_VERSION = "6"      # 4: preferences.json; 5: job_kind; 6: posting language by the work

import preferences as prefs_mod   # noqa: E402

# Language levels in order; "native" counts as the top.
LEVEL_RANK = {lv: i for i, lv in enumerate(prefs_mod.LEVELS)}
LEVEL_RANK["c2"] = LEVEL_RANK["native"]


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


# --------------------------------------------------------------- the model
# The fields asked for, shared by the local prompt (one posting) and Claude's
# (several postings in one call). The model judges against the candidate's
# profile and preferences; the code applies their limits (verdict).
ANSWER_FIELDS = """  "role_match": "yes", "close" or "no". What the work ACTUALLY is, judged from
      the responsibilities, not the title, against the roles the candidate wants
      and the work they say is not theirs. "yes": one of the roles they want.
      "close": a neighbouring role they could credibly do. "no": a different
      field, or work they say is not theirs.
  "years_required": integer, or null when the posting states no number. The
      years of professional experience the posting REQUIRES, at the low end of a
      range ("2-4 years" is 2, "5+ years" is 5, "mindestens 3 Jahre" is 3).
      Ignore numbers about anything else, such as how long the company has
      existed or the length of a contract.
  "languages_required": the languages the posting REQUIRES and at what level, as
      [{"language": "German", "level": "b2"}], level one of "a1","a2","b1","b2",
      "c1","c2","native". "Fluent", "business fluent" or "verhandlungssicher" is
      c1; "native" or "Muttersprache" is native. A language that is only "a
      plus" or "nice to have" is not required. A posting written entirely in one
      language and silent about it needs that language at the level the work
      itself needs: say c1 only for work done mostly in writing or with clients
      in it, b2 for a team that works in it, nothing when the posting says
      another language is the working language. [] when none.
  "is_management": true only when the core of the job is leading people or
      owning headcount. A senior individual role that mentors is NOT management.
  "location": "best", "ok" or "no", against where the candidate would work.
      A fully remote job that someone living in one of their places may take
      counts as remote in that place, wherever the employer is. "best": the
      first place they list, in a way they would work there (on site, hybrid
      or remote). "ok": another place or way they would take, or a posting that
      does not say where. "no": a place or way they would not take, such as on
      site in a city they do not list, or remote only for people in a country
      they do not list (unless remote jobs from anywhere are fine for them).
      When they would relocate, an on-site or hybrid job elsewhere is "ok",
      not "no".
  "job_kind": what kind of job this is: "regular" (an ordinary full-time or
      part-time job), "working_student" (Werkstudent), "internship" (Praktikum,
      not a trainee or graduate programme, which is "regular"), "thesis" (a Bachelor or Master thesis
      placement), "apprenticeship" (Ausbildung, dual study), "freelance"
      (freelance or contract work for a client), or "gig" (per-task work
      through a platform, such as data annotation or AI-training tasks).
  "fit": integer 0-100, how well the candidate's actual experience matches what
      this job needs. 100 means it could have been written for them. 0 means a
      different field.
  "reason": one short sentence, at most 20 words, naming the deciding factor.
"""


def preferences_text(prefs: dict) -> str:
    """The person's preferences in plain words, for the prompt. Anything not
    set is said to be open, so the model does not invent a limit."""
    def lst(v):
        return "; ".join(v) if v else "not stated"
    places = [f"{p['place']}" + (f" ({p['country']})" if p.get("country") else "")
              + f": {', '.join(p['work'])}" for p in prefs.get("places") or []]
    langs = [f"{x['language']} {prefs_mod._level_word(x['level'])}"
             for x in prefs.get("languages") or []]
    yrs = prefs.get("years_experience")
    return "\n".join([
        "=== WHAT THE CANDIDATE IS LOOKING FOR ===",
        f"Roles they want: {lst(prefs.get('target_roles'))}",
        f"Work that is not theirs: {lst(prefs.get('not_my_field'))}",
        f"Years of professional experience: {yrs if yrs is not None else 'not stated'}",
        f"Languages they speak: {lst(langs)}",
        "Where they would work, best first: " + (lst(places) if places else "anywhere"),
        "Remote jobs only open to people in other countries: "
        + ("fine" if prefs.get("remote_anywhere") or not places else "not wanted"),
        "Would relocate for the right job: " + ("yes" if prefs.get("relocate") else "no"),
        "Roles that mainly manage a team: "
        + ("fine" if prefs.get("accept_management") else "not wanted"),
        "Kinds of job they want: " + (", ".join(prefs_mod.KIND_NAMES[k] for k in prefs["job_kinds"])
                                      if prefs.get("job_kinds") else "any kind"),
    ])


def candidate_text(profile: str, prefs: dict) -> str:
    return f"{profile.strip()}\n\n{preferences_text(prefs)}"


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
{ANSWER_FIELDS}}}"""


def build_batch_prompt(jobs: list[tuple[str, dict]], profile: str) -> str:
    """Several postings in one call, each answered under its id."""
    parts = []
    for jid, job in jobs:
        desc = (job.get("description") or "")[: config.OLLAMA_DESC_CHARS]
        parts.append(f"=== POSTING {jid} ===\nTitle: {job.get('title') or ''}\n"
                     f"Company: {job.get('company') or ''}\nLocation: {job.get('location') or ''}\n\n"
                     f"{desc}\n=== END POSTING {jid} ===")
    return f"""You are screening job postings for one specific candidate. Do not use any
tools: read the postings below and answer.

{profile}

{chr(10).join(parts)}

Answer with ONE JSON object and nothing else. No markdown, no explanation:
{{"answers": [ one object per posting, in any order ]}}
where each object has "id" (the posting's id, as written above) and these fields:

{{
{ANSWER_FIELDS}}}"""


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


class StopRanking(Exception):
    """A failure no retry fixes: Claude signed out, or its usage spent."""


CLAUDE_AUTH = re.compile(r"OAuth access token|authentication_error|Failed to authenticate|"
                         r"Invalid API key|Please run /login|Not logged in", re.I)
CLAUDE_LIMIT = re.compile(r"hit your limit|usage limit|rate limit|quota", re.I)

# A ranking call needs none of Claude Code's own machinery: no tools, no MCP
# servers, plugins, hooks or skills from the user's setup, no saved session,
# and a one-line system prompt instead of the coding one. Measured 22 October
# 2026: 41,000 input tokens per job became 3,500, and on a machine with MCP
# servers or plugins the start-up they cost (10 minutes for 58 jobs, reported)
# goes too. --bare would be leaner still but cannot use a subscription sign-in.
CLAUDE_LEAN = ["--tools", "", "--strict-mcp-config", "--disable-slash-commands",
               "--no-session-persistence", "--setting-sources", "project",
               "--system-prompt", "You screen job postings for one candidate and answer "
                                  "only with the JSON you are asked for."]
_lean = {"ok": True}      # an older Claude Code without these options: run without
# Seconds per call, total and inside Claude's API, for the summary line at the
# end: when a ranking is slow on someone's machine, it says whether the time
# went to Claude answering or to Claude Code starting (and the network).
_timings: list[tuple[float, float]] = []


def _run_claude(args: list[str], stdin: str, cwd: pathlib.Path) -> subprocess.CompletedProcess:
    """
    One `claude -p` call, ended for good if it runs past CLAUDE_RANK_TIMEOUT.

    subprocess.run's timeout kills only the process it started, and on Windows
    that is cmd.exe running npm's claude.cmd: Claude Code's own Node process
    under it carried on. Every stalled call left one behind (3 November 2026,
    128 jobs, every call timing out on a laptop), each slowing the next. The
    whole tree goes now.
    """
    proc = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, encoding="utf-8",
                            errors="replace", cwd=cwd, **pu.quiet_kwargs())
    _live.add(proc.pid)
    try:
        out, err = proc.communicate(stdin, timeout=config.CLAUDE_RANK_TIMEOUT)
    except subprocess.TimeoutExpired:
        pu.kill_tree(proc.pid, grace=1.0)
        proc.communicate()
        raise
    finally:
        _live.discard(proc.pid)
    return subprocess.CompletedProcess(args, proc.returncode, out, err)


# Claude calls in flight, so stopping the ranking (Stop, or giving up) ends
# them too rather than leaving them running with nobody waiting.
_live: set[int] = set()


def end_calls_in_flight() -> None:
    for pid in list(_live):
        pu.kill_tree(pid, grace=0.5)


def claude_signed_out(claude: str) -> bool:
    """
    True only when `claude auth status` says plainly that nobody is signed in.

    A signed-out Claude Code does not always fail fast in -p mode: on
    3 November 2026 every call on a Windows laptop ran into the timeout
    instead, 128 jobs at 45 s twice each, while the dashboard said "0 judged".
    Asked once, before anything else. Anything unclear (no answer, not JSON,
    a token in the environment, which this cannot see) counts as signed in,
    and the calls then speak for themselves.
    """
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or os.environ.get("ANTHROPIC_API_KEY"):
        return False
    try:
        out = subprocess.run([claude, "auth", "status"], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=60,
                             stdin=subprocess.DEVNULL, **pu.quiet_kwargs()).stdout
        return json.loads(out).get("loggedIn") is False
    except (OSError, ValueError, AttributeError, subprocess.TimeoutExpired):
        return False


def ask_claude(model: str, jobs: list[tuple[str, dict]], profile: str) -> dict[str, dict]:
    """
    Several postings in one `claude -p` call; {id: answer} for those answered.
    The prompt goes in on stdin: a batch is far longer than Windows allows on a
    command line. Run from an empty folder so no project instructions load.
    """
    claude = pu.find_claude()
    if not claude:
        raise StopRanking("Claude Code is not installed, so Claude cannot rank. "
                          "Switch the ranker to Local, or install Claude Code")
    cwd = OUT / ".rank-claude"
    cwd.mkdir(parents=True, exist_ok=True)
    def call(lean: bool):
        return _run_claude([claude, "-p", "--model", model, "--output-format", "json",
                            "--max-turns", "1", *(CLAUDE_LEAN if lean else [])],
                           build_batch_prompt(jobs, profile), cwd)
    t0 = time.time()
    try:
        p = call(_lean["ok"])
    except subprocess.TimeoutExpired:
        # A stalled call (see CLAUDE_RANK_TIMEOUT): a fresh one usually answers
        # in seconds.
        print(f"  (a call took over {config.CLAUDE_RANK_TIMEOUT}s; asking again)", flush=True)
        p = call(_lean["ok"])
    if _lean["ok"] and p.returncode != 0 and re.search(r"unknown option|unrecognized", p.stderr or "", re.I):
        _lean["ok"] = False
        said = next((ln.strip() for ln in (p.stderr or "").splitlines() if ln.strip()), "")
        print("  (Claude Code refused the lean options, so ranking runs without them: "
              f"{said[:160]})", flush=True)
        p = call(False)
    out = (p.stdout or "") + (p.stderr or "")
    try:
        env = json.loads(p.stdout)
    except ValueError:
        env = {"is_error": True, "result": out}
    # One append per call (thread-safe), retries included in its time.
    _timings.append((time.time() - t0, (env.get("duration_api_ms") or 0) / 1000
                     if isinstance(env, dict) else 0.0))
    text = str(env.get("result") or "")
    if env.get("is_error") or p.returncode != 0:
        if CLAUDE_AUTH.search(out):
            raise StopRanking("Claude is signed out. In a terminal run: claude update, "
                              "then claude auth login, then rank again")
        if CLAUDE_LIMIT.search(out):
            raise StopRanking("Claude's usage limit is reached. Rank again after it resets, "
                              "or switch the ranker to Local")
        raise ValueError(f"Claude did not answer: {text[:160] or out[:160]}")
    data = _parse(text)
    answers = data.get("answers") if isinstance(data, dict) else data
    answers = [a for a in (answers or []) if isinstance(a, dict)]
    # Haiku now and then labels an answer "POSTING 1", or with a number from
    # the posting itself (2 of 24, measured). One posting, one answer: it is
    # that posting's, whatever the label.
    if len(jobs) == 1 and len(answers) == 1:
        return {jobs[0][0]: answers[0]}
    return {re.sub(r"(?i)^\s*posting\s*", "", str(a.get("id"))).strip(): a for a in answers}


# ------------------------------------------------------- the pre-filter
VEC_CACHE = OUT / "rank_similarity.json"


def target_roles() -> list[str]:
    """What the candidate is looking for: the roles in their preferences, or
    else the Arbeitnow searches (which said it in more words)."""
    roles = prefs_mod.current().get("target_roles") or []
    if roles:
        return list(roles)
    try:
        import arbeitnow_match as am
        st = am.load() or {}
    except Exception:                                       # noqa: BLE001
        return []
    return [f"{q.get('name', '')}: {q.get('looking_for', '')}".strip()
            for q in st.get("searches", []) if q.get("on", True) and q.get("looking_for")]


def prefilter(pool: list[dict]) -> tuple[list[dict], list[tuple[dict, float]], str]:
    """
    Set aside jobs far from the target roles by meaning, before any model reads
    them (config.EMBED_PREFILTER_FLOOR). Returns (kept, set aside with their
    similarity, a note for the log). Anything that stops it (no target roles,
    Ollama or the embedding model missing) keeps every job: it only ever saves
    work, it is never a reason to fail.
    """
    floor = float(getattr(config, "EMBED_PREFILTER_FLOOR", 0) or 0)
    if floor <= 0 or not pool:
        return pool, [], ""
    roles = target_roles()
    if not roles:
        return pool, [], "pre-filter skipped: no target roles yet (set them under Preferences)"
    import arbeitnow_match as am
    qkey = hashlib.sha1(json.dumps([am.EMBED_MODEL, roles]).encode()).hexdigest()[:10]
    cache = _read(VEC_CACHE, {})
    if cache.get("q") != qkey:
        cache = {"q": qkey, "sims": {}}

    def key(j):
        fp = hashlib.sha1((j.get("description") or "").encode()).hexdigest()[:12]
        return f"{job_key(j['url'])}|{fp}"
    todo = [j for j in pool if key(j) not in cache["sims"]]
    try:
        qv = am.embed(["search_query: " + r for r in roles])
        for i in range(0, len(todo), 64):
            chunk = todo[i:i + 64]
            vecs = am.embed([f"search_document: {j.get('title') or ''}\n"
                             f"{(j.get('description') or '')[:1500]}" for j in chunk])
            for j, v in zip(chunk, vecs):
                cache["sims"][key(j)] = round(max(sum(a * b for a, b in zip(q, v)) for q in qv), 4)
    except Exception as e:                                  # noqa: BLE001
        return pool, [], f"pre-filter skipped: {e}"[:160]
    _atomic_write(VEC_CACHE, json.dumps(cache), encoding="utf-8")
    kept, aside = [], []
    for j in pool:
        sim = cache["sims"][key(j)]
        (aside.append((j, sim)) if sim < floor else kept.append(j))
    # The floor was measured on one field in English. When it would set aside
    # most of a pool, it does not fit this person's field or language (a
    # German nursing pool against "Pflegefachkraft", say): read everything.
    if len(aside) > len(pool) / 2:
        return pool, [], (f"pre-filter skipped: it would set aside {len(aside)} of {len(pool)}, "
                          "so it does not fit your field; every job is read")
    return kept, aside, (f"pre-filter: {len(aside)} of {len(pool)} set aside as far from "
                         f"your target roles (similarity below {floor})")


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
    """Coerce whatever came back into the shape the verdict expects. Anything
    unreadable comes out as the answer that rules nothing out."""
    def _int(v):
        if isinstance(v, bool) or v is None:
            return None
        if isinstance(v, (int, float)):
            return int(v)
        m = re.search(r"\d+", str(v))
        return int(m.group(0)) if m else None

    def _pick(v, allowed, default):
        t = str(v or "").strip().lower()
        return t if t in allowed else default

    yrs = _int(a.get("years_required"))
    fit = _int(a.get("fit")) or 0
    langs = []
    for item in a.get("languages_required") or []:
        if not isinstance(item, dict):
            continue
        name = re.sub(r"\s+", " ", str(item.get("language") or "")).strip()[:30]
        lv = prefs_mod._level(item.get("level"))
        if name and lv and lv != "none":
            langs.append({"language": name[:1].upper() + name[1:], "level": lv})
    return {
        "role_match": _pick(a.get("role_match"), ("yes", "close", "no"), "close"),
        "years_required": yrs if (yrs is None or 0 <= yrs <= 30) else None,
        "languages_required": langs[:6],
        "is_management": a.get("is_management") is True,
        "location": _pick(a.get("location"), ("best", "ok", "no"), "ok"),
        "job_kind": _pick(a.get("job_kind"), prefs_mod.KINDS,
                          "gig" if a.get("gig_work") is True else "regular"),
        "fit": max(0, min(100, fit)),
        "reason": str(a.get("reason", ""))[:160],
    }


# --------------------------------------------------------------- the verdict
# Internships, working-student jobs and theses used to be dropped here by a
# title word list, for everyone (18 September 2026). Since November 2026 the
# model says what kind of job each posting is and the person's own job_kinds
# decide, so a student looking for a Werkstudent job is shown them.
def kind_gap(v: dict, prefs: dict) -> str:
    """Why the kind of job is not one the person wants, or "". No kinds set
    means any kind is fine."""
    want = prefs.get("job_kinds") or []
    if not want or v["job_kind"] in want:
        return ""
    names = prefs_mod.KIND_NAMES
    return (f"{names[v['job_kind']]}, not a kind of job you want "
            f"({', '.join(names[k] for k in want)})")


def _level_name(lv: str) -> str:
    return "native" if lv == "native" else lv.upper()


def language_gap(v: dict, prefs: dict) -> str:
    """The first language the posting requires above the person's level, said
    plainly, or "". Nothing is ruled out when the person listed no languages:
    an unknown is never a reason to drop a job."""
    if not prefs.get("languages"):
        return ""
    for req in v.get("languages_required") or []:
        # "French or English": any one is enough. "English and German": each.
        for need in re.split(r"\s*(?:,|&|\band\b|\bund\b|\bet\b)\s*", req["language"]):
            options = [x for x in re.split(r"\s*(?:/|\bor\b|\boder\b|\bou\b)\s*", need) if x]
            gaps = []
            for name in options or [need]:
                have = prefs_mod.level_of(prefs, name)
                if have == "" or LEVEL_RANK.get(req["level"], 0) <= LEVEL_RANK.get(have, 0):
                    gaps = []          # they speak it well enough, or at a level nobody stated
                    break
                gaps.append((name, have))
            if gaps:
                name, have = gaps[0]
                said = "none" if have == "none" else _level_name(have)
                return f"needs {name} {_level_name(req['level'])}; you have {said}"
    return ""


def verdict(v: dict, prefs: dict) -> tuple[str, str]:
    """The model's answers, held against the person's own limits."""
    gap = kind_gap(v, prefs)
    if gap:
        return "job-kind", gap
    if v["role_match"] == "no":
        return "off-lane", v["reason"] or "not one of the roles you want"
    gap = language_gap(v, prefs)
    if gap:
        return "language", gap
    most = prefs.get("max_years_asked")
    if most is not None and v["years_required"] is not None and v["years_required"] > most:
        return "years", f"asks for {v['years_required']}+ years; your limit is {most}"
    if v["is_management"] and not prefs.get("accept_management", True):
        return "management", "the job is mainly managing a team"
    if v["location"] == "no" and (prefs.get("places") or []):
        return "location", v["reason"] or "not somewhere, or not a way, you would work"
    # One of the roles they asked for is not dropped on fit alone: a career
    # changer or a student is, by definition, a weak fit on experience for the
    # very roles they want. It still ranks below closer fits. Only a fit near
    # zero, which the prompt defines as a different field, still counts.
    floor = config.MIN_FIT if v["role_match"] != "yes" else getattr(config, "ROLE_MATCH_MIN_FIT", 15)
    if v["fit"] < floor:
        return "weak-fit", f"scored {v['fit']}, below the {floor} floor"
    return "passed", ""


_LOCAL_MIN_FIT = config.MIN_FIT     # before main() swaps in Claude's floor


def strong_line(prefs: dict | None = None) -> int:
    """STRONG_SCORE was set on the local model's scores, including the
    first-place bonus. With no places set nobody can earn that bonus, and
    Claude scores the same jobs lower (its fit floor is lower by as much), so
    the line comes down by both."""
    prefs = prefs if prefs is not None else prefs_mod.current()
    return (config.STRONG_SCORE - (0 if prefs.get("places") else config.BONUS_BEST_PLACE)
            - max(0, _LOCAL_MIN_FIT - config.MIN_FIT))


def score(v: dict, prefs: dict) -> int:
    s = v["fit"]
    if v["role_match"] == "yes":
        s += config.BONUS_ROLE_MATCH
    if v["location"] == "best":
        s += config.BONUS_BEST_PLACE
    # A role asking more years than the person has is kept (unless it is over
    # their own limit) but ranked lower, one step per extra year.
    have, yrs = prefs.get("years_experience"), v.get("years_required")
    if have is not None and yrs is not None and yrs > have:
        s -= config.PENALTY_PER_YEAR_OVER * (yrs - have)
    return max(0, s)


_ORDER = {"yes": 0, "close": 1, "no": 2, "best": 0, "ok": 1}


def sort_key(r: dict) -> tuple:
    # Ties (models score in blunt steps) go to the closer role, then the
    # better place.
    return (-r["rank_score"],
            _ORDER.get(r.get("role_match"), 1),
            _ORDER.get(r.get("location_fit"), 1),
            -r["match_score"],
            r["title"])


# --------------------------------------------------------------- driver
def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default=None,
                    help="default: OLLAMA_MODEL, or a model already installed in Ollama")
    ap.add_argument("--top", type=int, default=config.TOP_N)
    ap.add_argument("--all", action="store_true",
                    help="hand every role that passed to the build, best first; "
                         "--top then only sizes the batch the pool health judges")
    ap.add_argument("--day", default=None, help="only this scrape date, e.g. 2026-09-13")
    ap.add_argument("--limit", type=int, default=0, help="only the first N, for testing")
    ap.add_argument("--recheck", action="store_true", help="ignore cached answers")
    ap.add_argument("--engine", choices=rank_engine.ENGINES, default=None,
                    help="local (Ollama) or claude; default: the dashboard's choice")
    args = ap.parse_args()
    engine = args.engine or rank_engine.get()
    if engine == "claude":
        args.model = args.model or rank_engine.claude_model()
        # Claude's fit scores run lower than the local model's: its own floor.
        config.MIN_FIT = getattr(config, "CLAUDE_MIN_FIT", config.MIN_FIT)
    else:
        # OLLAMA_MODEL if it is installed, otherwise one Ollama already has.
        args.model = args.model or ollama_model.resolve(config) or ollama_model.wanted(config)

    OUT.mkdir(exist_ok=True)
    # What the model reads about the person: their profile, then their job
    # preferences in plain words. The same preferences decide the verdicts.
    prefs = prefs_mod.current()
    behind = prefs_mod.due()
    if behind:
        print(f"note: your job preferences are not up to date with your profile ({behind}); "
              "jobs are judged against them as they are. They are rewritten when Claude can "
              "answer (Preferences on the dashboard).")
    profile = candidate_text(PROFILE_PATH.read_text(encoding="utf-8"), prefs)

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
    # The pre-filter saves the local model hours on a slow laptop. Claude reads
    # every job itself and decides: no similarity cut stands in for its
    # judgement (decided 16 November 2026).
    pool, aside, note = prefilter(pool) if engine != "claude" else (pool, [], "")
    if note:
        print(note)

    cache = _read(CACHE_PATH, {})
    profile_fp = hashlib.sha1(profile.encode()).hexdigest()[:12]
    # Answers cached before the key carried the profile (November 2026) were
    # given against the profile as it was then, which for nearly everyone is
    # the profile now. They are carried over to it rather than dropped:
    # dropping them sent every job back to the model on the first ranking
    # after the update, which on a slow PC sat on "0 judged" for many minutes
    # (3 November 2026). Only a later profile change asks again.
    old = [k for k in cache if k.count("|") == 3]
    for k in old:
        cache.setdefault(f"{k}|{profile_fp}", cache[k])
        del cache[k]
    if old:
        _save_cache(cache)
    per = (f"{config.CLAUDE_RANK_BATCH} per call, {config.CLAUDE_RANK_CONCURRENCY} calls at a time"
           if engine == "claude" else f"{config.OLLAMA_CONCURRENCY} at a time")
    print(f"model {args.model} ({rank_engine.LABELS[engine]}) · {per}\n")

    def cache_key(job: dict) -> str:
        """
        Keyed on the DESCRIPTION, not just the job.

        The 67 LinkedIn descriptions repaired on 18 September kept their old
        answers otherwise: same URL, same model, same prompt version, so the
        cache handed back verdicts computed from the page furniture the repair
        had just replaced. A description fingerprint makes a repaired posting a
        different question, which is exactly what it is.

        And on the PROFILE: a job still posted days later is judged again
        (config.DROP_REPEATS), and after you change profile.md the answer must
        be the new profile's, not the one cached against the old.
        """
        fp = hashlib.sha1((job.get("description") or "").encode()).hexdigest()[:12]
        return f"{job_key(job['url'])}|{args.model}|{PROMPT_VERSION}|{fp}|{profile_fp}"

    fresh: set[str] = set()       # answered by Claude in this run, not from an older one

    def one(job: dict) -> tuple[dict, dict | None, str]:
        ck = cache_key(job)
        if ck in cache and (not args.recheck or ck in fresh):
            return job, cache[ck], ("asked" if ck in fresh else "cached")
        try:
            if engine == "claude":
                got = ask_claude(args.model, [("1", job)], profile).get("1")
                if not got:
                    raise ValueError("Claude gave no answer for it")
                return job, normalise(got), "asked"
            return job, normalise(ask(args.model, build_prompt(job, profile))), "asked"
        except StopRanking as e:
            return job, None, f"failed: {e}"[:120]
        except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as e:
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
        end_calls_in_flight()
        print(f"\nstopped after {len(rows) + len(failures)}/{len(pool)} "
              "judged; press Rank pool to continue from here", flush=True)
        os._exit(143)
    for _sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(_sig, _stop)

    def _give_up(why: str) -> None:
        """Stop the ranking with a reason the dashboard shows. Exits at once,
        like Stop: calls still running would otherwise hold the exit for up to
        a timeout each."""
        _flush_judged()
        end_calls_in_flight()
        print(f"\nRANKING STOPPED: {why}", flush=True)
        os._exit(3)

    if engine == "claude":
        todo = [j for j in pool if args.recheck or cache_key(j) not in cache]
        batches = [todo[i:i + config.CLAUDE_RANK_BATCH]
                   for i in range(0, len(todo), config.CLAUDE_RANK_BATCH)]

        answered, in_a_row, last_error = 0, 0, ""

        def batch(jobs: list[dict]) -> tuple[list[dict], dict]:
            # Only the call runs in a worker. The answers are stored by the
            # main thread below: a worker writing the cache while another
            # thread saves it can fail with "dictionary changed size".
            return jobs, ask_claude(args.model, [(str(n), j) for n, j in enumerate(jobs, 1)], profile)
        done = len(pool) - len(todo)
        claude = pu.find_claude()
        if todo and claude and claude_signed_out(claude):
            _give_up("Claude Code is signed out, so Claude cannot rank. In PowerShell or a "
                     "terminal run: claude auth login (claude auth status should then say "
                     "loggedIn true), then rank again. Or switch the ranker to Local")
        if todo:
            print(f"asking Claude about {len(todo)} job(s) in {len(batches)} call(s)")
            # Counted from the start, so the dashboard shows "0 of N" rather
            # than a bare 0 until the first call comes back.
            print(f"  {done}/{len(pool)} judged", flush=True)
        try:
            with futures.ThreadPoolExecutor(max_workers=config.CLAUDE_RANK_CONCURRENCY) as ex:
                for fut in futures.as_completed([ex.submit(batch, b) for b in batches]):
                    try:
                        jobs, got = fut.result()
                        for n, j in enumerate(jobs, 1):
                            if str(n) in got:
                                cache[cache_key(j)] = normalise(got[str(n)])
                                fresh.add(cache_key(j))
                        done += len(jobs)
                        answered += 1
                        in_a_row = 0
                    except (OSError, ValueError, KeyError, subprocess.TimeoutExpired) as e:
                        # Left uncached; the per-job pass below asks once more.
                        in_a_row += 1
                        last_error = f"{type(e).__name__}: {str(e)[:100]}"
                        print(f"  !! a call failed ({last_error})")
                    _save_cache(cache)
                    print(f"  {min(done, len(pool))}/{len(pool)} judged", flush=True)
                    # Calls that all fail are not worth another hour: say so and
                    # stop. Two rounds of calls with no answer at all, or three
                    # in a row once some have answered.
                    limit = 2 * config.CLAUDE_RANK_CONCURRENCY if not answered \
                        else 3 * config.CLAUDE_RANK_CONCURRENCY
                    if in_a_row >= limit:
                        _save_cache(cache)
                        _give_up(f"the last {in_a_row} calls to Claude got no answer ({last_error}). "
                                 + ("None has answered yet. Check that Claude Code is signed in: "
                                    "claude auth status should say loggedIn true; if not, run "
                                    "claude auth login. Or switch the ranker to Local"
                                    if not answered else
                                    f"{answered} answered before that, so it is probably the "
                                    "network or Claude's service. Rank again in a while: "
                                    "answers so far are kept"))
        except StopRanking as e:
            _save_cache(cache)
            _give_up(str(e))

    if engine != "claude" and pool:
        # Said before the first answer, which can take minutes on a slow PC
        # (the model is loaded on the first call): without it the dashboard
        # showed "judged 0" and nothing else, and looked stuck.
        first_new = next((j for j in pool if args.recheck or cache_key(j) not in cache), None)
        print(f"  0/{len(pool)} judged"
              + (" · the model loads on the first job, which can take a few minutes"
                 if first_new is not None else ""), flush=True)
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
            vd, why = "", ""
            if len((job.get("description") or "").strip()) < 200:
                # Nothing to judge and nothing to tailor against. Both survivors
                # of the 19 September re-rank were empty records that passed on
                # a default score: a CV cannot be written from a blank posting,
                # so a blank posting is not a candidate.
                vd, why = "no-description", "the posting has no description to read"
            if not vd:
                vd, why = verdict(v, prefs)
            rows.append({
                "title": job.get("title") or "", "company": job.get("company") or "",
                "location": job.get("location") or "", "url": job["url"],
                "source": job.get("source") or "", "query": job.get("query") or "",
                "description": job.get("description") or "",
                "verdict": vd, "why": why, "rank_score": score(v, prefs),
                "match_score": round(v["fit"] / 100, 2),
                "years_required": v["years_required"],
                "languages_required": v["languages_required"],
                "role_match": v["role_match"], "location_fit": v["location"],
                "job_kind": v["job_kind"],
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

    # Set aside by the pre-filter: decided, so recorded as judged, but no
    # model read them, so they are not counted as read.
    judged_now.extend(j for j, _ in aside)
    _save_cache(cache)
    _flush_judged()

    passed = sorted([r for r in rows if r["verdict"] == "passed"], key=sort_key)
    dropped = [r for r in rows if r["verdict"] != "passed"]
    dropped += [{"title": j.get("title") or "", "company": j.get("company") or "",
                 "location": j.get("location") or "", "url": j["url"],
                 "source": j.get("source") or "", "verdict": "far-from-target",
                 "why": f"far from your target roles by meaning (similarity {sim:.2f})",
                 "rank_score": 0, "reason": ""} for j, sim in aside]
    # --all: the build stops at its target anyway, so a short list only costs
    # roles. On 29 September a build of 4 got the top 6, Claude rejected 4 of
    # them as out of lane, and it stopped at 2 with 8 good roles never shown.
    top = passed if args.all else passed[: args.top]
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
        "model": args.model, "engine": engine, "top_n": args.top,
        "health": health,
        "counts": {"scraped": breakdown["collected"], "judged": len(rows), "passed": len(passed),
                   "dropped": len(dropped), "sent_to_claude": len(top),
                   "failed": len(failures), "seen_earlier": len(repeats),
                   "set_aside": len(aside)},
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
    if _timings:
        tot = sorted(t for t, _ in _timings)
        api = [a for _, a in _timings if a]
        print(f"\nClaude calls: {len(tot)}, {statistics.median(tot):.1f}s each (median), slowest "
              f"{tot[-1]:.0f}s; of that, {statistics.median(api) if api else 0:.1f}s answering. "
              "A big gap between the two is Claude Code starting or the network, not Claude.")
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
    strong = [r for r in passed if r["rank_score"] >= strong_line()]
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
        verdict, why = BUILD_THEN_SCRAPE, (
            f"{buildable} buildable, short of a full batch of {target} — "
            f"build all {buildable}, then scrape")
    elif len(strong) < target:
        verdict, why = BUILD_THEN_SCRAPE, (
            f"{buildable} buildable but only {len(strong)} strong, "
            f"enough for this batch and thin for the next")
    elif share < config.STRONG_SHARE_WARN:
        verdict, why = BUILD_THEN_SCRAPE, (
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
          f"{h['strong']} strong (>= {strong_line()}) · "
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
             f"· model {args.model} · "
             + ("every role that passed" if args.all else f"top {args.top}"), "",
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
        langs = ", ".join(f"{x['language']} {x['level']}" for x in r.get("languages_required") or [])
        lines.append(f"    {r['location']} · role {r.get('role_match')} · place {r.get('location_fit')} · "
                     f"languages {langs or 'none'} · years {r['years_required']}")
        lines.append(f"    {r['reason']}")
        if r["needs_company"]:
            lines.append("    !! no company name captured — read it out of the description")
        lines.append(f"    {r['url']}")
    for name in ("job-kind", "off-lane", "language", "years", "management", "location",
                 "no-description", "weak-fit"):
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
