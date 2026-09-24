# Job Scraper — v2

Berlin job scan with automated search-term rotation, a no-LLM German-requirement
filter, and ranking by fewest years of experience required.

## The loop

The extension pushes what you collect straight into the pipeline, so there is no
file to save and nothing to move:

1. `./install-agent.sh` once. The bridge now starts at login.
2. Browse LinkedIn or Indeed. Click **Collect this page**. The rank updates in
   about a second, and the popup's dot turns green when the bridge is up.
3. Open `~/Desktop/CV Builder` in Claude Code and run `/apply-batch`.
4. After applying, press **Mark this job applied**. That job never shows up in
   a shortlist again.

Only applying is manual.

## Run it by hand

```bash
./.venv/bin/python serve.py          # the bridge, if you don't want the agent
./.venv/bin/python run.py --inbox    # rank what's in inbox/ — no network, <1s
./.venv/bin/python run.py            # inbox + live StepStone scan
./.venv/bin/python run.py --quick    # live, 1 page/query, no detail fetches
./.venv/bin/python run.py --no-open  # don't open the browser
./.venv/bin/python test_gate.py      # 50 assertions on the gate + years parser
```

The inbox is **always processed first and never blocked by a live source**, so a
throttled StepStone can't stop you seeing jobs you already collected by hand.
`--inbox` is what the bridge runs on every collect; use plain `run.py` when you
want StepStone scanned too.

## The bridge (`serve.py`)

A Chrome extension cannot write into this folder — `chrome.downloads` only
writes inside Downloads, which is why v1 made you save a file and move it. The
bridge closes that gap: a tiny loopback-only server the extension POSTs to.

| Endpoint | Does |
|---|---|
| `GET /status` | counts, and how the last rank went — drives the popup's dot |
| `POST /ingest` | merge jobs into today's inbox file, then rerank |
| `POST /applied` | add to `applied.json`, then rerank |

It listens on `127.0.0.1:8765` only, so nothing off this machine can reach it.

**Today's collection is the working set.** On the first ingest of a day, older
inbox files move to `inbox/archive/`, which `run.py` doesn't glob. Files stamped
with today's date are absorbed rather than archived, so a manual export or an
earlier session isn't lost. Nothing is ever deleted.

If the bridge is down, collecting still works — everything stays in
`chrome.storage`, and **Resend everything to pipeline** catches it up later. The
manual JSON export is still there as a last resort.

## Applied jobs

`applied.json` holds what you've already applied to, written by the extension's
**Mark this job applied** button. Those URLs are dropped from the ranked output,
so a shortlist only ever shows roles still open to you. Matching ignores the
query string, so a tracking parameter can't sneak a job back onto the list.

A full live run is slow by design — 12 queries × up to 5 pages plus detail
fetches, paced 2–4.5s apart to avoid being throttled. Budget ~10 minutes.

Output lands in:

| File | What it's for |
|---|---|
| `out/jobs.html` | the visual report, every job linked by title |
| `out/jobs.md` | ranked list as Markdown, for notes or pasting |
| `out/jobs.json` | the survivors, machine-readable |
| `out/all_jobs.json` | every job with the flag that decided it, so the numbers reconcile |
| `out/audit.txt` | one-pass filter review, see below |

All are rewritten on every run.

## Auditing the filters

Everything here is lexical: ordered phrase tables and regex. That fails in ways
only a reader catches, and reading full descriptions to find those failures
costs more than the scan did.

`out/audit.txt` compresses every decision to one line so the whole run can be
checked in a single model call:

```
REJECTED (2) · id | verdict | detail | title @ company | evidence
R01 | german | needs B2 | AI Engineer (m/w/d) @ Bravo GmbH | Sehr gute Deutschkenntnisse in Wort und Schrift sind zwingend erforderlich.
R02 | off-profile | score 0.00 | Senior Sales Manager @ Delta | title scored 0.00, below the 0.35 floor

PASSED (3) · id | title @ company | years | german | excerpt
P01 | Applied AI Engineer @ Acme | not stated | not mentioned | You will build LLM agents in Python…
```

A **rejected** job carries the exact sentence the verdict was read from and
nothing else, which is all you need to see that a filter misfired. A job that
**passed** carries a description excerpt capped at `AUDIT_DESC_CHARS` (700),
because catching a false accept needs some substance and that cap is what keeps
the file to one affordable call.

Jobs held back as **already applied** are counted but not listed. That is
bookkeeping, not a judgement that can be wrong.

`/apply-batch` reads this file before it picks anything, so a job the gate
rejected by mistake can be rescued into the same batch rather than lost.

## How the three moving parts work

### 1. Search-term rotation with abandon-on-drift

`config.QUERIES` is tried top to bottom. Every result title is scored 0–1 against
term tables derived from your CV. A rolling window of the last
`DRIFT_WINDOW` (10) scores is kept; when its mean drops below
`DRIFT_THRESHOLD` (0.30), the query has drifted off-profile — the runner logs how
deep it got and moves to the next term.

This works because job boards rank by relevance, so drift is real and roughly
monotonic as you page deeper. The **Query scoreboard** in the report shows which
terms produced matches and which drifted early — that's your signal for which
search terms to keep, rewrite, or retire.

### 2. German gate (no LLM)

Regexing the *meaning* of a language requirement is unbounded — `fließend`,
`verhandlungssicher`, `sehr gut`, `stilsicher`, `in Wort und Schrift`… You'd
never finish the list.

So instead the gate regexes the **topic word**, which is a closed set of about
ten stems (`deutsch`, `german`, `sprach`, `niveau`, CEFR codes). German
compounding means the single stem `deutsch` already covers `Deutschkenntnisse`,
`Deutschniveau`, `deutschsprachig`, and `verhandlungssicheres Deutsch`. Once you
know *where* German is discussed, you score the level **inside that sentence**
using an ordered phrase table.

Decisions, in priority order:

| Situation | Result |
|---|---|
| No German mentioned anywhere | **accept** |
| Explicit negation (`keine Deutschkenntnisse erforderlich`, `no German required`) | **accept** |
| Optionality marker (`von Vorteil`, `wünschenswert`, `idealerweise`, `nice to have`, `kein Muss`) | **accept** — it's a Kann- not a Muss-Anforderung |
| Stated level ≤ B1 | **accept** |
| Stated level ≥ B2 | **reject** |
| German mentioned, no level stated | **accept + flag** (lightest filter) |

Three details that matter:

- **Ordered matching.** `sehr gute` is tested before `gute`, otherwise every B2
  posting would read as B1. This is the single most important line in the file.
- **Clause-level scoring.** `Verhandlungssicheres Englisch und gute
  Deutschkenntnisse` is split on conjunctions so the C1 belongs to English and
  the B1 to German — it accepts. But `Sehr gute Deutsch- und Englischkenntnisse`
  keeps the shared modifier and correctly rejects.
- **Strictest wins** across multiple sentences.

Everything is **lightest-filter by default** (`ACCEPT_WHEN_UNCLEAR = True`):
ambiguity accepts and flags rather than dropping. The report prints the exact
sentence each verdict came from, so you can eyeball the borderline ones and
tighten `config.py` once you've seen real output.

### 3. Ranking

Primary sort is **fewest years of experience required** — your stated main
measurement. Ties break on match score, then on German level.

Years are read in three passes, in order:

1. **Explicit numbers** — `mindestens 3 Jahre Berufserfahrung`, `2-4 Jahre`,
   `5+ years`. Within a *range* the low end wins (that's the entry bar); across
   *separate* requirements the high end wins, because "3 yrs Python **and** 5 yrs
   AWS" means the job wants 5 years of you. Understating here would float
   demanding roles to the top of your list, which is the one direction that
   actually costs you. A bare number only counts near an experience word, so
   `auf 2 Jahre befristet` (a contract term) is correctly ignored.
2. **Qualitative German phrases** — `mehrjährige Erfahrung` → 3,
   `langjährige` → 5, `erste Erfahrung` → 0, `einschlägige` → 2. These appeared
   in half the Berlin postings sampled and carry no number; treating them as
   "not stated" would have ranked them as the *most* accessible jobs.
3. **Title seniority** — `Junior` → 0, `Senior` → 5, `Head of` → 8.

Postings that state no requirement at all sort **first** — no stated barrier is
the most accessible.

## Tuning

Everything lives in `config.py`; no logic there.

| Want to… | Change |
|---|---|
| Tighten the German cap | `GERMAN_CAP` (3 = B1) |
| Stop accepting ambiguous postings | `ACCEPT_WHEN_UNCLEAR = False` |
| Make drift detection more/less eager | `DRIFT_THRESHOLD`, `DRIFT_WINDOW` |
| Change what counts as on-profile | `RELEVANCE_FLOOR`, `CORE_TERMS` |
| Add/remove search terms | `QUERIES` |
| Search deeper | `MAX_PAGES_PER_QUERY` |
| Verify German on more jobs | `MAX_DETAIL_FETCHES` |

### Reading the report honestly

Two labels look similar and mean very different things:

- **`no German required`** — the description *was* fetched and read, and it
  genuinely never demands German.
- **`not checked`** — the description was never fetched (you hit
  `MAX_DETAIL_FETCHES`), so the German filter has not seen this job at all.

The header stat **"German actually verified"** is the number you should trust.
If it's much lower than "passed all filters", raise `MAX_DETAIL_FETCHES`.

Likewise in the query scoreboard, **`blocked / timed out`** is a network problem,
not a verdict on the search term — don't retire a query on that basis.

## Sources

**StepStone** — **use the extension.** The Python scraper still works for a
handful of requests and then stops working, permanently, for the rest of the
session.

Akamai does not ban, it stalls: it holds the socket open until read timeout with
no status code and no error page. A single request from a script succeeds. A
sustained scan gets the IP flagged around the third page, and from then on
everything times out, including every detail fetch. `fetchers.py` now retries,
waits out a 90 second cooldown and only then gives up, and the volume was cut to
3 pages per query at 3.5 to 7 second intervals. It still gets flagged. The
`Sec-Fetch-*` headers and same-origin `Referer` are necessary but not sufficient,
so don't strip them, and don't expect them to save a long run either.

The extension does not have this problem, because the request is not coming from
a script. On a StepStone results page it reads each posting with `fetch()` from
inside the tab you are already on: same origin, your real session, your real TLS
fingerprint. Measured at roughly 0.5s per description with no stalling.

StepStone also navigates on click rather than opening a side pane, which would
destroy the content script on every job. The `fetchDesc` hook in
`SITES.stepstone` exists for exactly that: fetch the detail page instead of
clicking through to it.

**Indeed** — blocks scripted HTTP (**403**). **LinkedIn** — requires a logged-in
session and is the most automation-sensitive of the three. Both are collected by
the Chrome extension in `extension/` instead. No Playwright, no headless browser,
no automation driver: it runs inside a tab you're already looking at, using your
own session, which is why it works where scripted HTTP doesn't.

LinkedIn specifics: the adapter paces itself roughly 3× slower than the others,
and its selectors all have fallbacks because LinkedIn reshuffles class names
often. If a collect returns 0 cards on a page that clearly has jobs, the
selectors have moved — update `SITES.linkedin` in `extension/content.js`.

### Using the extension

1. Chrome → `chrome://extensions` → enable **Developer mode** → **Load unpacked**
   → select the `extension/` folder.
2. Open the popup, expand **Search**, and build the query there: keywords,
   location, radius, site, date posted, remote only. **Open this search** builds
   the right URL for whichever site is selected and opens it. The three sites
   spell the same four filters differently (`radius` vs `distance` in miles,
   `ag` in seconds vs `f_TPR` vs `fromage` in days, `wfh` vs `f_WT` vs an Indeed
   attribute code), which is the whole reason this panel exists rather than
   hand-edited URLs. Your last search is remembered, so widening one term does
   not mean retyping the rest.
3. Click **Collect this page**, or set a page count and hit
   **Start auto-collect**.
4. Leave **"also capture descriptions"** ticked — the German filter can't do
   anything without them. It clicks each card to load the description pane, so
   it's slower but it's the whole point.

> **Lazy loading:** LinkedIn and Indeed only render the cards near the viewport,
> so collecting a freshly-opened page used to capture ~7 jobs out of ~25. The
> extension now scrolls the results list until the card count stops growing
> before it reads anything. If a collect still looks short, check the popup's
> "N on this page" figure against what you can see.
5. Nothing. Collected jobs are pushed to the bridge automatically and ranked
   within a second. The **Export JSON** button is only a fallback for when the
   bridge is offline.

Imported jobs flow through the identical scoring, German gate, and ranking as
StepStone.

The extension is deliberately a **dumb collector** — it captures title,
location, company, URL and description, and makes no decisions. All the logic
stays in Python so there's exactly one implementation of each rule. It also
works on StepStone if you ever want to collect that by hand too.

## Known limits (v1)

- **No LLM anywhere.** Everything is lexical: ordered phrase tables and regex.
  It reads what a posting *says* — it can't catch a role that is de-facto
  German-speaking without saying so, or infer a seniority bar from tone.
- **Verification is capped.** Descriptions are only fetched for titles that
  already clear `RELEVANCE_FLOOR`, up to `MAX_DETAIL_FETCHES`. Everything else
  shows `not checked` rather than a German verdict.
- **StepStone skews German.** In the Berlin samples taken while building this,
  most StepStone AI postings demanded B2+ and were correctly rejected. That's a
  real property of the board, not a bug — outside research suggests roughly one
  in five Berlin tech roles has no German requirement, and those cluster on
  English-first boards. If the accept rate feels low, that's the market, and
  adding a source like Berlin Startup Jobs or Arbeitnow would help more than
  loosening the filter.
- **The extension is manual by design.** It collects the pages you're viewing;
  it does not run unattended.
- Scraping Indeed is against its ToS. At manual volume, in your own browser, for
  your own job search, this is the low-risk end of that — but it isn't zero.
