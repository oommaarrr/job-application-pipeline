# Why the skill lives inside the project

A Claude Code skill can live in your Claude account or inside a project. For
this pipeline it **must** live in the project, at `.claude/skills/cv-builder/`.

The build runs headless: `run_batch.py` starts `claude -p "/apply-batch N"` with
no interactive session attached. An account-synced skill resolves in an
interactive session and is **missing from every headless run**, because the
account copy sits under a session-scoped path that no longer exists when the
script starts. A batch that runs without the skill still writes documents, from
whatever it can reconstruct, and nothing in the output tells you the rules were
never loaded.

So:

- Keep the skill here. Edit it here.
- `apply-batch.md` tells Claude to **stop** if the skill does not load, rather
  than writing without it.
- The skill holds rules only. Facts about the candidate come from the active
  profile, linked at `builder/profile/` for each run.
