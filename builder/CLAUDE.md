# Builder: context for every Claude session in this folder

This file loads automatically in any Claude Code session opened in `builder/`,
including the headless one `run_batch.py` starts. Nothing carries over between
sessions; everything that must survive is on disk.

## What this folder does

Turns the ranked jobs in `../scraper/out/ranked.json` into a tailored CV and
cover letter per role, as PDFs, plus a review page. The batch procedure is the
`/apply-batch` command in `.claude/commands/apply-batch.md`.

## Before writing any CV or letter

1. **Invoke the `cv-builder` skill** (`.claude/skills/cv-builder/`). It holds the
   rules: structure, bullets, summary, tailoring, honesty, format, checklist.
2. **Read the profile at `profile/`.** `run_batch.py` links the active profile
   there before Claude starts. `identity.md`, `profile.md` and
   `reference/PROJECTS.md` are the only source of facts. Nothing goes on a
   document that is not in them. Rows in `PROJECTS.md` copied word for word
   from the fictional example (Demand forecasting service, Document retrieval pipeline, Model release gate, Open-source contribution, with the
   example's descriptions) are not this person's: ignore them.
3. **Follow `reference/DOCUMENT_WORKFLOW.md`**: plan each document, criticise the
   plan as an applicant tracking system and as a human screener, then build once.

## How documents are built

```
.venv/bin/python check_spread.py applications/<date>/<Company>/cv_<company>.json
.venv/bin/python build_docs.py cv     <cv>.json     applications/<date>/<Company>/<First>_<Last>_CV_<Company>.pdf
.venv/bin/python build_docs.py letter <letter>.json applications/<date>/<Company>/<First>_<Last>_CoverLetter_<Company>.pdf
```

Always `.venv/bin/python`, never bare `python3`: the system Python may not have
the PDF libraries. `.venv` here is a link to `../scraper/.venv` (a junction on
Windows), created by setup. On Windows setup also adds `.venv/bin/python`, so
the same command works in Git Bash; `.venv/Scripts/python.exe` is the same
interpreter.

## Hard rules

- **No invented facts.** No tool, number, date, title or employer that is not in
  the profile.
- **No stated weaknesses** on any document. Honest caveats go in the chat
  assessment only.
- **No em dashes, en dashes or double hyphens.** `build_docs.py` rewrites any it
  finds (a hyphen in a range, a comma elsewhere) and says so.
- **The person's limits decide.** Their `preferences.json`, printed at the top of
  `shortlist.py`, says which roles, kinds of job, languages, years, places and
  CV shape and length are theirs. Nothing in the code refuses a role or a
  document; a role is dropped only when its description breaks one of those
  limits, and a limit that is not set rules nothing out.
- **A one-page CV fills its page** when the profile has the content.
  `build_docs.py` notes a short page; add real content, never spacing.
- **Show range**: spread the page across what the person has done.
  `check_spread.py` shows the spread; it never fails.
- **Every CV in a batch reads differently.** A clone with a swapped company name
  is a failure.

## Where things are

| Path | What |
|---|---|
| `applications/<date>/<Company>/` | the payloads and the two PDFs for one role |
| `applications/<date>/batch.json` | what was built, what was dropped, and why |
| `applications/<date>/batch.html` | the review page, rendered by `batch_report.py` |
| `profile/` | the active profile (a link; not part of this folder) |
| `reference/DOCUMENT_WORKFLOW.md` | plan, criticise, build |
