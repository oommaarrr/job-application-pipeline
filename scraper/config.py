"""
Search and ranking defaults. Who you are lives in profiles/<you>/profile.md.

Everything you'd want to tune lives in this file. No logic here.
"""

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
ENABLE_IMPORTED = True         # read Chrome-extension exports from inbox/

# ---------------------------------------------------------------- applied
# Jobs you have already applied to, maintained by the extension's "applied"
# button through serve.py. They are dropped from the ranked output so a
# shortlist only ever shows work still open to you.
APPLIED_FILE = "applied.json"   # legacy; the list now lives in history/applied.csv (ledger.py)

# How long a collected job is remembered (history/seen.csv, title and company
# only). A job collected again within this many days of first being collected,
# on a LATER day, is dropped before ranking: it has already had its chance.
# Survives "Erase everything". 0 turns the check off.
SEEN_DAYS = 30

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

# Hard drops applied AFTER the model answers. The model extracts, these decide,
# so a wording change in a posting cannot quietly loosen a rule.
# B2 and above, same 0-6 scale as GERMAN_CAP. Was 5 (C1).
#
# Decision, 19 September 2026: of 15 applications built, only 10 could be
# sent. "gute Deutschkenntnisse" is a B2 ask in practice and it sailed through a
# C1 threshold, so a CV was written for a role the user cannot take. B2 is already out
# of reach at A2 with B1 in progress, so the line belongs at B2, not above it.
DROP_GERMAN_AT = 4

# A posting WRITTEN in German requires German, whether or not it ever names a
# level. This is the leak the CEFR gate could not catch: Reply and secureIO both
# came back german_level="none" from the model, because neither text states a
# level — they are simply written in German throughout, which tells you the
# working language more reliably than any sentence about it would.
#
# Measured as the share of words that are German function words (und, der, die,
# mit, für...). An English posting with a German company name scores near zero;
# the three German postings in the 19 September batch scored 25%, 29% and 27%,
# and every English one scored under 1%. Anywhere in that gap works; 12% is
# chosen so a short German section inside an English posting still trips it.
GERMAN_TEXT_SHARE = 0.12
DROP_YEARS_AT = 4              # 4+ years stated in the description is a hard drop

# Outside Germany the years ceiling is looser. The rule, 22 September
# 2026: a remote role abroad asking a bit more experience is still worth taking,
# where the same ask in Germany is not. Evidence from the 22 September rank: the
# 4-year ceiling was dropping three in-lane remote roles that each asked exactly
# 5 years.
# At 6 those come back while the genuinely senior asks (6, 10, 12 years) stay
# out. Applies to anything not located in Germany, including the EMEA and EU
# remote listings where the volume actually is.
# Years above YOUR_YEARS are still penalised in the ranking, so a 5-year ask sits
# below an equal role that fits the profile's experience rather than crowding it out.
DROP_YEARS_AT_REMOTE = 6

# Years between YOUR_YEARS and the drop line are KEPT but penalised. Their
# instruction, 20 September 2026: a role asking 3 years when the user has 2 is still
# worth applying to, it just ranks below an equal role that fits the profile's experience.
# Points off per year required above YOUR_YEARS (so a 3-year ask loses one step).
PENALTY_PER_YEAR_OVER = 8
DROP_MANAGEMENT = True          # Head of / Director / VP / owning headcount

# Ranking weights. Fit is the model's 0-100 score; these tilt it toward what
# the user actually wants rather than what merely passes.
BONUS_BERLIN = 12               # on site or hybrid in Berlin
BONUS_REMOTE_DE = 4             # remote within Germany
BONUS_AI_ROLE = 15              # the role really is AI/ML engineering
PENALTY_OFF_LANE = 25           # data eng, frontend, embedded, research

# A role scoring below this is not worth a tailored CV. Without a floor, 140 of
# 188 postings "passed" on 18 September, including a front-end staff role and an
# IT application developer that the model itself scored 0.
MIN_FIT = 45

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

# Kept as the old name so nothing that imports it breaks; it now means the
# floor, not the target.
MIN_BUILDABLE = MIN_WORTH_BUILDING

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
try:
    from config_local import *          # noqa: F401,F403
except ImportError:
    pass
