---
description: Create your profile from your existing CV (PDF, Word, or pasted text)
argument-hint: "path/to/your-cv.pdf  (or paste the CV text after the command)"
allowed-tools: Read, Write, Edit, Bash
---

Create the user's profile in `profiles/me/` from their existing CV.

The CV is at: $ARGUMENTS

If that is a file path, read it. If it is text, use the text. If nothing was
given, ask for the path to their CV and stop.

## What to write

Use `profiles/example/` as the template for all three files: same headings,
same shape, same length. Replace every fictional detail with the user's own.

1. **`profiles/me/identity.md`**: the name exactly as it should appear on a
   CV, the contact line (city, email, phone, LinkedIn, GitHub if present),
   languages with honest levels, availability if the CV states it, and the
   **Employment history** and **Education** sections: every employer with its
   exact name, title, city and dates, and every degree with institution, years,
   grade and the full thesis title. The CV builder takes these word for word.
2. **`profiles/me/profile.md`**: what the local ranker judges each job
   against. Target role, preferred location, language constraints, a short
   experience summary, what they are strong at, what is **not their lane**, and
   the two hard rules (years and titles). Infer the target role from their most
   recent work; keep the years rule consistent with how much experience the CV
   shows.
3. **`profiles/me/reference/PROJECTS.md`**: one row per project or major
   piece of work from the CV: what it proves, with the numbers the CV gives, and
   which kind of role it should lead for.

## Rules

- **Only facts from the CV.** Never invent a number, a tool, a date or a result.
  Where the CV is silent on something the template asks for, write `TODO:` and
  say what is needed, rather than guessing.
- Keep the example's headings so the ranker and the build can read the files.
- Do not touch `profiles/example/`.

## Finish

List every `TODO:` you left, in one line each, and tell the user:

> Your profile is ready in `profiles/me/`. Read `profile.md` once: it decides
> which jobs are ranked highly, so fix anything that is not quite you. Then
> restart the dashboard and press **Rank pool**.
