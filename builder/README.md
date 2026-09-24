# Builder

Turns the ranked jobs from `../scraper/` into a tailored CV and cover letter per
role, as PDFs, using Claude Code. See the main [README](../README.md) for setup.

## How a build runs

```bash
./run-batch.sh               # build up to BUILD_TARGET from the current ranking
BATCH_SIZE=3 ./run-batch.sh  # just three this time
```

Or press **Build** on the dashboard, which runs the same script.

`run-batch.sh` ranks the pool locally, links the active profile at `profile/`,
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

Always `.venv/bin/python`: `setup.sh` links it to the scraper's environment.
