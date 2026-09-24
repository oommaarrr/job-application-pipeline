#!/usr/bin/env bash
#
# Copy exactly what is publishable into a separate folder: every file git would
# commit, nothing it ignores. Your profile, settings, job data, history and
# built CVs never leave this folder.
#
#   scripts/export-open-source.sh [target]     default: ../job-pipeline-open-source
#
# Run it after changing the code here, then commit and push from the target.
# The target's own .git folder is left alone, so its history survives.
set -euo pipefail
SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${1:-$SRC/../job-pipeline-open-source}"
mkdir -p "$DEST"
DEST="$(cd "$DEST" && pwd)"
[ "$DEST" != "$SRC" ] || { echo "target is this folder"; exit 1; }

# Ask git which files are publishable, using a throwaway index so this folder
# does not need to be a repository itself.
TMPGIT="$(mktemp -d)"; trap 'rm -rf "$TMPGIT"' EXIT
git init -q --bare "$TMPGIT"
LIST="$TMPGIT/files.txt"
git --git-dir="$TMPGIT" --work-tree="$SRC" ls-files --others --exclude-standard > "$LIST"

# Mirror: add and update those files, and remove anything in the target that is
# no longer publishable (except its .git).
rsync -a --files-from="$LIST" "$SRC/" "$DEST/"
( cd "$DEST" && find . -path ./.git -prune -o -type f -print | sed 's|^\./||' ) \
  | sort > "$TMPGIT/have.txt"
sort "$LIST" > "$TMPGIT/want.txt"
comm -23 "$TMPGIT/have.txt" "$TMPGIT/want.txt" | while IFS= read -r f; do rm -f "$DEST/$f"; done
find "$DEST" -path "$DEST/.git" -prune -o -type d -empty -delete 2>/dev/null || true

echo "$(wc -l < "$LIST" | tr -d ' ') files copied to $DEST"
# The privacy scan, run on the copy: it scans the folder its own file is in.
python3 "$DEST/scripts/scrub.py" --all
