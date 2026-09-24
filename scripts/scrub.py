#!/usr/bin/env python3
"""
Refuse to let personal data reach a commit.

Run it by hand, or as a pre-commit hook:

    ln -sf ../../scripts/scrub.py .git/hooks/pre-commit

The pipeline writes real job descriptions, real CVs and a real phone number
into the working tree every time it runs. A contributor will eventually run it
inside their own clone, and this is what stops that reaching a pull request.

It is deliberately noisy about false positives rather than quiet about misses:
a wrongly-flagged file costs you one `--allow` flag, a missed one costs you
your address on GitHub forever.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent

PATTERNS = {
    "email":       re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.]{2,}\b"),
    "phone":       re.compile(r"(?<![\w.])\+\d{1,3}[\s.-]?\(?\d{2,4}\)?[\s.-]?\d{3,}[\s.-]?\d{3,}(?![\w.])"),
    "street":      re.compile(r"\b\d{1,4}\s+[A-Z][a-z]+\s+(?:Stra(?:ss|ß)e|Str\.|Street|Road|Avenue|Weg|Platz)\b"),
    "postcode_de": re.compile(r"\b\d{5}\s+(?:Berlin|Hamburg|M[üu]nchen|K[öo]ln|Frankfurt)\b"),
    "api_key":     re.compile(r"\b(?:sk-ant-|sk-proj-|ghp_|gho_|AKIA)[A-Za-z0-9_\-]{12,}"),
}

# Files that are SUPPOSED to contain a person: the example profile is a
# fictional one committed on purpose, and this scanner names the patterns it
# hunts for, which would otherwise flag itself.
ALLOW = (
    "profiles/example/",
    "scripts/scrub.py",
    ".gitignore",
)

TEXT_SUFFIXES = {".py", ".js", ".sh", ".md", ".json", ".html", ".css", ".txt",
                 ".yml", ".yaml", ".toml"}


def _ignored(path: pathlib.Path) -> bool:
    """Ask git itself, so there is one definition of ignored, not two."""
    try:
        r = subprocess.run(["git", "check-ignore", "-q", str(path)],
                           cwd=ROOT, capture_output=True)
        return r.returncode == 0
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


def tracked_files() -> list[pathlib.Path]:
    """What git would actually commit — not what is merely on disk."""
    try:
        out = subprocess.run(["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
                             cwd=ROOT, capture_output=True, text=True, check=True).stdout
        staged = [ROOT / n for n in out.split("\n") if n.strip()]
        if staged:
            return staged
        out = subprocess.run(["git", "ls-files"], cwd=ROOT,
                             capture_output=True, text=True, check=True).stdout
        return [ROOT / n for n in out.split("\n") if n.strip()]
    except (subprocess.CalledProcessError, FileNotFoundError):
        # Not a git repo yet — scan everything not ignored by suffix.
        return [p for p in ROOT.rglob("*") if p.is_file()]


# Documentation needs example addresses. example.com/.org/.net are reserved for
# exactly that (RFC 2606), and a phone number of zeros is not anyone's, so
# neither may block a commit, or every edit to a docs example would.
_EXAMPLE_DOMAIN = re.compile(r"@(?:[\w-]+\.)*example\.(?:com|org|net)\b", re.I)


def _placeholder(kind: str, value: str) -> bool:
    if kind == "email":
        return bool(_EXAMPLE_DOMAIN.search(value))
    if kind == "phone":
        return set(re.sub(r"\D", "", value)[-7:]) <= {"0"}
    return False


def scan(paths: list[pathlib.Path]) -> list[tuple[str, int, str, str]]:
    hits = []
    for path in paths:
        rel = path.relative_to(ROOT).as_posix() if path.is_absolute() else str(path)
        if any(rel.startswith(a) for a in ALLOW):
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES:
            # A PDF or a .docx in the tree is itself the finding.
            if path.suffix.lower() in {".pdf", ".docx"} and path.exists():
                hits.append((rel, 0, "document", path.name))
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        for lineno, line in enumerate(text.split("\n"), 1):
            for name, rx in PATTERNS.items():
                m = rx.search(line)
                if m and not _placeholder(name, m.group(0)):
                    hits.append((rel, lineno, name, m.group(0)[:60]))
    return hits


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--all", action="store_true",
                    help="scan every tracked file, not only what is staged")
    args = ap.parse_args()

    paths = tracked_files()
    if args.all:
        # Everything on disk EXCEPT what .gitignore already excludes.
        #
        # Without the filter this walks out/, inbox/ and applications/ — which
        # are full of real job descriptions by design — and reports dozens of
        # recruiter addresses that were never going to be committed. A scanner
        # that cries wolf gets ignored, which is worse than not having one.
        paths = [q for q in ROOT.rglob("*")
                 if q.is_file() and ".git/" not in q.as_posix()
                 and not _ignored(q)]

    hits = scan(paths)
    if not hits:
        print(f"scrub: clean ({len(paths)} file(s) checked)")
        return 0

    print("scrub: personal data found — commit refused\n", file=sys.stderr)
    for rel, lineno, kind, sample in hits:
        where = f"{rel}:{lineno}" if lineno else rel
        print(f"  {kind:11} {where}\n              {sample}", file=sys.stderr)
    print("\nEither gitignore the file, or move the value into profiles/<you>/.",
          file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
