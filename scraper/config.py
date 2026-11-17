"""
Search and ranking defaults. Who you are lives in profiles/<you>/profile.md.

Everything you'd want to tune lives in this file. No logic here.
"""

import os   # only to tell Windows apart, for CLAUDE_RANK_TIMEOUT

# ---------------------------------------------------------------- location
LOCATION = "Berlin"
RADIUS_KM = 30

# ---------------------------------------------------------------- language
# Your German cap. Accept a job if the German it demands is <= this level.
# Scale: 0=none 1=A1 2=A2 3=B1 4=B2 5=C1 6=C2/native
GERMAN_CAP = 3  # B1

# v1 = lightest filter. Ambiguous postings are ACCEPTED and flagged, not dropped.
ACCEPT_WHEN_UNCLEAR = True

# ---------------------------------------------------------------- experience
# Your actual professional experience, in years .
# Used only to mark which postings are within reach; ranking is by raw years.
YOUR_YEARS = 2

# ---------------------------------------------------------------- queries
# Rotation order. The runner tries these top to bottom and abandons one the
# moment its results drift away from your profile.
QUERIES = [
    "AI Engineer",
    "AI Automation Engineer",
    "LLM Engineer",
    "Machine Learning Engineer",
    "Python Developer",
    "Backend Engineer Python",
    "MLOps Engineer",
    "AI Agent Engineer",
    "Automation Engineer",
    "Software Engineer AI",
    "Test Automation Engineer",
    "Integration Engineer",
]

# ---------------------------------------------------------------- scoring
# Title-relevance scoring. A job title is scored 0..1 against these.
# CORE: the domain you actually work in. Hitting one of these is most of the score.
CORE_TERMS = {
    "ai": 1.0, "a.i.": 1.0, "ki": 0.8, "artificial intelligence": 1.0,
    "llm": 1.0, "genai": 1.0, "gen ai": 1.0, "nlp": 0.9,
    "machine learning": 1.0, "ml": 0.8, "mlops": 1.0, "llmops": 1.0,
    "agent": 0.9, "agentic": 1.0, "rag": 0.9,
    "automation": 0.9, "automatisierung": 0.9,
    "data engineer": 0.7, "data scientist": 0.6,
}

# SUPPORT: reinforces a match but can't carry a title on its own.
SUPPORT_TERMS = {
    "engineer": 0.5, "ingenieur": 0.4, "developer": 0.5, "entwickler": 0.5,
    "python": 0.8, "typescript": 0.6, "backend": 0.6, "fullstack": 0.4,
    "full stack": 0.4, "platform": 0.5, "software": 0.4, "cloud": 0.4,
    "integration": 0.4, "api": 0.5, "devops": 0.4, "qa": 0.3,
    "test automation": 0.6, "e2e": 0.5, "playwright": 0.7,
}

# PENALTY: strong signal the search has drifted into an unrelated field.
PENALTY_TERMS = {
    "vertrieb": -1.0, "sales": -0.9, "account executive": -1.0,
    "marketing": -0.5, "recruiter": -1.0, "recruiting": -0.9, "hr ": -0.8,
    "pflege": -1.0, "verkauf": -1.0, "verkäufer": -1.0, "einkauf": -0.8,
    "buchhaltung": -1.0, "accounting": -0.9, "steuer": -1.0,
    "lager": -1.0, "logistik": -0.7, "fahrer": -1.0, "produktion": -0.7,
    "elektriker": -1.0, "mechaniker": -1.0, "handwerk": -1.0,
    "kundenservice": -0.8, "customer service": -0.8, "call center": -1.0,
    "ausbildung": -0.6, "duales studium": -0.7, "reinigung": -1.0,
    "consultant sap": -0.8, "salesforce": -0.6, "servicetechniker": -0.9,
}

# Seniority in the title, as a soft penalty (you're junior-to-mid).
# Not a rejection — just ranks these lower.
SENIORITY_PENALTY = {
    "senior": -0.15, "lead ": -0.25, "principal": -0.35, "staff ": -0.25,
    "head of": -0.45, "director": -0.5, "vp ": -0.5, "chief": -0.5,
    "manager": -0.2, "architekt": -0.2, "architect": -0.2,
}

# A result scoring below this is "off-profile".
#
# Set to 0.0 deliberately: title scoring no longer rejects anything. It kept
# dropping roles that are squarely in scope because the title lacked an AI or
# ML term — "Software Engineer - Quality Engineering" scored 0.10 and never got
# read, despite the E2E suite being one of the strongest projects on the CV.
# The title score is still computed and still used for ranking and drift
# detection; it just no longer gates. German level and stated years do the
# blocking now.
RELEVANCE_FLOOR = 0.0

# Reject when the description states this many years or more. Unstated means
# unstated: no figure found is never a barrier. Years inferred from a title
# ("Senior") do not count either — that inference put a phantom 5-year
# requirement on a posting whose text named no years at all.
# 4, Decision, 7 September 2026: "jobs requiring 4 or more years should
# be dropped". It was 3, which was stricter than asked and contradicted the
# batch rule that four years or fewer is still in reach. On the 7 September pool
# the change lets exactly two more roles through, both stating 3 years.
MAX_YEARS_REQUIRED = 4

# ---------------------------------------------------------------- drift
DRIFT_WINDOW = 10        # rolling window size, in results
DRIFT_THRESHOLD = 0.30   # abandon query when window mean drops below this
DRIFT_MIN_RESULTS = 12   # never abandon before seeing at least this many
MAX_PAGES_PER_QUERY = 3  # hard ceiling per query; 5 tripped Akamai's throttle

# ---------------------------------------------------------------- fetching
# A live run got stalled by Akamai three pages in, then lost all 41 detail
# fetches to the same stall. Volume and pace were the trigger, so both came
# down: fewer pages per query, fewer detail fetches, and a slower floor.
REQUEST_DELAY = (3.5, 7.0)   # random sleep between page fetches, seconds
FETCH_DESCRIPTIONS = True    # fetch detail pages (needed for the German gate)
MAX_DETAIL_FETCHES = 60      # cap detail fetches per run

# ---------------------------------------------------------------- applied
# Jobs you have already applied to, maintained by the extension's "applied"
# button through serve.py. They are dropped from the ranked output so a
# shortlist only ever shows work still open to you.
APPLIED_FILE = "applied.json"   # legacy; the list now lives in history/applied.csv (ledger.py)

# How long a collected job is remembered (history/seen.csv, title and company
# only, with the day the model first judged it). Survives "Erase everything".
# 0 turns the log off.
SEEN_DAYS = 30

# Whether a job collected again on a later day, and already judged then, is
# dropped before ranking. Off since 2 November 2026: with it on, every day's
# scrape lost the roles that were still posted, a third to a half of the pool
# within a week, and the batch ran short. A repeat now goes back to the ranker
# (an unchanged posting against an unchanged profile is answered from the
# cache, so this costs nothing) and can be built. Only jobs you applied to, or
# that already have an application built, are left out. True restores the old
# rule, for the ranker and for run.py's "seen in an earlier scrape".
DROP_REPEATS = False

# ---------------------------------------------------------------- history
# Every job URL ever collected, with the date it was first seen. A job that was
# already in an earlier scrape is dropped from the ranked output rather than
# reviewed again, so each scrape only shows work you have not looked at yet.
#
# The date matters, not just the presence of the key: a job first seen TODAY is
# still new. Without that, running run.py twice on the same inbox file would
# mark everything as seen and hand back an empty report, which looks exactly
# like a broken scrape.
HISTORY_FILE = "seen_jobs.json"

# ---------------------------------------------------------------- audit
# out/audit.txt is built to be read in a single pass by a model checking the
# filters for mistakes, so every job is one line. A rejected job carries the
# exact sentence that condemned it and nothing else. A job that passed carries
# a description excerpt, because catching a false accept needs some substance,
# and this cap is what keeps the whole file to one affordable call.
AUDIT_DESC_CHARS = 700

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------- Ollama rank
# The local model reads every scraped description and judges it, so the only
# jobs that reach Claude are ones already known to fit. Before this, Claude
# ranked the whole pool itself and a 109 job scrape burned the entire usage
# window without writing a single document.
OLLAMA_URL = "http://127.0.0.1:11434"
OLLAMA_MODEL = "llama3.1"       # any `ollama list` name; bigger reads better
# If OLLAMA_MODEL is not installed but another local model is, rank with that
# one instead of downloading OLLAMA_MODEL (a few GB). False: always use, and
# download if needed, exactly OLLAMA_MODEL.
OLLAMA_USE_INSTALLED = True
OLLAMA_CONCURRENCY = 2          # parallel requests; 16 GB holds two 8B streams
OLLAMA_DESC_CHARS = 6000        # description truncation; the ask is near the top
OLLAMA_TIMEOUT = 180            # seconds per job
# Arbeitnow's feed cannot be searched, so its jobs are picked by meaning first
# (arbeitnow_match.py): this small model turns each job and each search into a
# vector. About 270 MB, downloaded by setup or the bridge. The match levels in
# arbeitnow_match.LEVELS are tuned for it; another model needs them retuned.
EMBED_MODEL = "nomic-embed-text"
ARBEITNOW_API = "https://www.arbeitnow.com/api/job-board-api"

# Which model ranks the pool, switched on the dashboard (rank_engine.py keeps
# the choice). "local" is Ollama above. "claude" is a small fast Claude model
# through the Claude Code sign-in the build already uses: minutes instead of
# hours on a slow laptop, no Ollama needed, and it uses some of the Claude
# usage the CVs also need.
RANK_ENGINE = "local"
CLAUDE_RANK_MODEL = "claude-haiku-5-5"
# One posting per call. Measured on 48 past jobs, 22 October 2026: with 4 or
# 8 postings per call Haiku left 17-33% of them unanswered and its verdicts
# moved on about 1 in 5 of the rest, for a run 2-3 times faster. One at a
# time answered all 48 in 89 seconds, about 3 minutes for a pool of 100.
CLAUDE_RANK_BATCH = 1           # postings per call
CLAUDE_RANK_CONCURRENCY = 4     # calls at a time
# Seconds before a call is given up and asked again. A call takes about 5
# seconds, but now and then one stalls for minutes (358 s measured, the same
# job answered in 5 s by the next call), and the whole ranking waits for it:
# 58 jobs took 10 minutes on 22 October 2026. A stalled call is abandoned and
# retried at once, and the final pass tries once more.
# On Windows, 120: there Claude Code starts through npm's claude.cmd, and on a
# 2021 laptop (i7-1165G7, no graphics card) a call took 37 s, all but 3 s of it
# Claude Code starting, so 45 s timed out half the calls (3 November 2026).
CLAUDE_RANK_TIMEOUT = 120 if os.name == "nt" else 45
# Claude scores the same jobs lower than the local model, so it has its own
# fit floor (MIN_FIT below is the local model's). Measured on 80 past jobs,
# 22 October 2026: of roles Sonnet later built, Haiku kept 68% at 30 (43% at
# 45); of roles Sonnet rejected as the wrong field, it let 22% through at 30.
# The local model let all of those through, which is why Sonnet rejected so
# many. Lower keeps more stretch roles; higher sends Sonnet fewer misfits.
CLAUDE_MIN_FIT = 30

# Edit profile (profile_chat.py): the dashboard window where you tell Claude
# what to change in your profile. Sonnet, because it edits several files and
# has to keep them consistent; seconds per message is the timeout.
CLAUDE_PROFILE_MODEL = "claude-sonnet-5-5"
CLAUDE_PROFILE_TIMEOUT = 300

# Job preferences (preferences.py): the profile's preferences.json, written by
# Claude from profile.md and identity.md and rewritten when they change. One
# call per change, and every rule reads the result, so Sonnet (decided
# 16 November 2026).
CLAUDE_PREFS_MODEL = "claude-sonnet-5-5"
CLAUDE_PREFS_TIMEOUT = 120 if os.name == "nt" else 90

# Updates (updater.py): once a day the pipeline asks GitHub whether a newer
# version exists, and the dashboard offers Update now. Only version numbers
# are fetched. UPDATE_CHECK=0 in local.env turns it off; it must be off in the
# copy the project is developed in, so it never overwrites unpushed work.
UPDATE_REPO = "oommaarrr/job-application-pipeline"
UPDATE_CHECK = True

# Before the LOCAL model reads anything, jobs far from your target roles (your
# Preferences, or else the Arbeitnow searches) by meaning are set aside, using
# EMBED_MODEL. Claude reads every job itself: no cut stands in for it. Measured on
# 1,270 collected jobs, 22 October 2026: a floor of 0.62 set aside about 15%
# of them and only 1 of the 221 that were later built. The signal is weak
# inside one field (good and out-of-lane roles score alike), so the floor is
# kept low: it removes the unrelated, not the borderline. 0 turns it off.
EMBED_PREFILTER_FLOOR = 0.62

# How many applications actually get written. THIS IS THE DIAL.
#
# It is a FLOOR now, not a fixed number. When the local ranker finds more
# "strong" roles than this, run-batch.sh raises the target to match, up to
# BUILD_TARGET_MAX. Decision, 24 September 2026: a scrape that produced
# 26 strong candidates should not stop at 15 and throw 11 of them away.
BUILD_TARGET = 5

# The ceiling that growth stops at. A batch of 20 is roughly what one Claude
# usage window writes without running dry, which is the real constraint — not
# the size of the pool. Strong is rank_score >= STRONG_SCORE, so this only ever
# triggers on a genuinely good scrape.
BUILD_TARGET_MAX = 10

# How many candidates Claude is given to reach that target. It has to be larger,
# because some roles can only be rejected once the description has been read in
# full: an already-applied repeat the ranker did not recognise, a posting whose
# real work is out of lane, a description that turns out to be untailorable. On
# 18 September a batch of 15 candidates produced 6 documents for exactly those
# reasons.
#
# Claude stops at BUILD_TARGET built, so a generous overshoot costs nothing when
# the candidates are good: the extra ones are simply never reached.
TOP_N = 8

# What decides which jobs are dropped (rank_ollama.verdict) is the person's own
# preferences.json since 16 November 2026: their languages and levels, the most
# years a job may ask for, whether management roles are fine, where they would
# work. These used to be constants here (DROP_GERMAN_AT, GERMAN_TEXT_SHARE,
# DROP_YEARS_AT, DROP_YEARS_AT_REMOTE, DROP_MANAGEMENT), set for one engineer
# in Berlin with two years of experience; anyone else had almost every job
# thrown out. Change them under Preferences on the dashboard.
#
# Points off per year a job asks above the person's own years of experience:
# kept, but ranked below an equal role that fits (20 September 2026).
PENALTY_PER_YEAR_OVER = 8

# Ranking weights. Fit is the model's 0-100 score; these tilt it toward what
# the person actually wants rather than what merely passes.
BONUS_ROLE_MATCH = 15           # one of the roles they want, not just a neighbour
BONUS_BEST_PLACE = 12           # their first-choice place, in a way they would work

# A role scoring below this is not worth a tailored CV. Without a floor, 140 of
# 188 postings "passed" on 18 September, including a front-end staff role and an
# IT application developer that the model itself scored 0.
MIN_FIT = 45
# The floor for a role the person asked for (role_match "yes"): only a fit near
# zero, which the model is told means a different field. A career changer or a
# student scores low on experience for exactly the roles they want.
ROLE_MATCH_MIN_FIT = 15

# ---------------------------------------------------------------- pool health
# When to scrape again, and when the pool you already have is still worth
# working. Decision, 19 September 2026: the ranker should say which.
#
# A role at or above this is a genuinely strong match rather than something that
# merely survived the gates. On the 18 September pool the top scores were 107
# (in lane, in Berlin) and the tail sat at 72 (in lane, wrong city or wrong
# flavour), so the line falls between them.
STRONG_SCORE = 95

# The floor under "is this pool still worth a batch at all".
#
# Decision, 19 September 2026. This used to be MIN_BUILDABLE = 15, i.e.
# the build target, which made the target a REQUIREMENT rather than a ceiling: a
# pool holding 10 good roles was declared unbuildable and the whole thing was
# thrown away and re-scraped. Ten tailored applications were binned because
# there were not fifteen of them.
#
# BUILD_TARGET is now a ceiling — build at most that many in one go — and this
# is the floor. Above it, build what is here and then scrape. Below it, the
# batch is too thin to be worth a run and scraping really is the only fix.
MIN_WORTH_BUILDING = 4

# Below this share of strong roles among the buildable ones, the pool is being
# scraped through: still usable, but the next batch will be weaker than the last.
STRONG_SHARE_WARN = 0.40

# The searches themselves drift. If this little of what was judged survives the
# gates, the queries are returning the wrong kind of job and the fix is better
# searches, not more of them.
PASS_RATE_DRIFT = 0.12

# How old a scrape may be and still be worth building from. A posting does not
# expire at midnight, which is what the old "collected today" gate assumed: on
# 19 September a pool of 158 jobs went unused because the clock had rolled over.
MAX_POOL_AGE_DAYS = 7

# ---------------------------------------------------------------- your overrides
# Change any value above for yourself without editing this file: put it in
# scraper/config_local.py (gitignored), e.g.
#
#     BUILD_TARGET = 15
#     BUILD_TARGET_MAX = 20
#     TOP_N = 20
#
# so pulling a new version never overwrites your settings.
# Every public name in it replaces the one above, as `from config_local import *`
# would; spelled out so the override is explicit.
try:
    import config_local as _local
except ImportError:
    _local = None
if _local is not None:
    globals().update({k: v for k, v in vars(_local).items() if not k.startswith("_")})
