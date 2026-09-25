# Builder

Turns the ranked jobs from `../scraper/` into a tailored CV and cover letter per
role, as PDFs, using Claude Code. See the main [README](../README.md) for setup.

## How a build runs

```bash
.venv/bin/python run_batch.py      # build up to BUILD_TARGET from the current ranking
.venv/bin/python run_batch.py 3    # just three this time
```

(Windows: `.venv\Scripts\python.exe run_batch.py`. `run-batch.sh` still works
on Mac and Linux; it calls the same file.)

Or press **Build** on the dashboard, which runs the same script.

`run_batch.py` checks the profile, links it at `profile/`, ranks the pool locally,
then starts `claude -p "/apply-batch N"` in this folder. Claude reads the
`cv-builder` skill for the rules and your profile for the facts, writes a JSON
payload per document, and `build_docs.py` renders the PDFs.

## Files

| File | What it is |
|---|---|
| `.claude/skills/cv-builder/SKILL.md` | the writing rules: structure, bullets, summary, honesty, format |
| `.claude/commands/apply-batch.md` | the `/apply-batch` procedure Claude follows |
| `CLAUDE.md` | loads automatically in any Claude session opened here |
| `reference/DOCUMENT_WORKFLOW.md` | plan, criticise, then build each document once |
| `build_docs.py` | renders a CV or letter payload to an A4 PDF; refuses dashes and thin pages |
| `check_spread.py` | refuses a CV that leans on one project |
| `shortlist.py` | joins the ranked list to the full job descriptions |
| `batch_report.py` | renders the Applications review page |
| `applications/<date>/<Company>/` | the payloads and the two PDFs per role |

## Useful by hand

```bash
.venv/bin/python shortlist.py              # the ranked list, one line each
.venv/bin/python build_docs.py cv payload.json out.pdf
.venv/bin/python check_spread.py applications/<date>/*/cv_*.json
```

Always `.venv/bin/python`: setup links it to the scraper's environment (on Windows it also adds that path for Git Bash; `.venv\Scripts\python.exe` is the same interpreter).
