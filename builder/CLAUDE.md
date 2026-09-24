# Builder: context for every Claude session in this folder

This file loads automatically in any Claude Code session opened in `builder/`,
including the headless one `run-batch.sh` starts. Nothing carries over between
sessions; everything that must survive is on disk.

## What this folder does

Turns the ranked jobs in `../scraper/out/ranked.json` into a tailored CV and
cover letter per role, as PDFs, plus a review page. The batch procedure is the
`/apply-batch` command in `.claude/commands/apply-batch.md`.

## Before writing any CV or letter

1. **Invoke the `cv-builder` skill** (`.claude/skills/cv-builder/`). It holds the
   rules: structure, bullets, summary, tailoring, honesty, format, checklist.
2. **Read the profile at `profile/`.** `run-batch.sh` links the active profile
   there before Claude starts. `identity.md`, `profile.md` and
   `reference/PROJECTS.md` are the only source of facts. Nothing goes on a
   document that is not in them.
3. **Follow `reference/DOCUMENT_WORKFLOW.md`**: plan each document, criticise the
   plan as an applicant tracking system and as a human screener, then build once.

## How documents are built

```
.venv/bin/python check_spread.py applications/<date>/<Company>/cv_<company>.json
.venv/bin/python build_docs.py cv     <cv>.json     applications/<date>/<Company>/<First>_<Last>_CV_<Company>.pdf
.venv/bin/python build_docs.py letter <letter>.json applications/<date>/<Company>/<First>_<Last>_CoverLetter_<Company>.pdf
```

Always `.venv/bin/python`, never bare `python3`: the system Python may not have
the PDF libraries. `.venv` here is a link to `../scraper/.venv`, created by
`setup.sh`.

## Hard rules

- **No invented facts.** No tool, number, date, title or employer that is not in
  the profile.
- **No stated weaknesses** on any document. Honest caveats go in the chat
  assessment only.
- **No em dashes, en dashes or double hyphens.** `build_docs.py` refuses them.
- **A single-page CV fills its page.** `build_docs.py` refuses one under 90%;
  add real content from the profile, never spacing.
- **Evidence is spread**: four or more distinct projects, at most two bullets
  each. `check_spread.py` enforces it; fix the payload, never the checker.
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
