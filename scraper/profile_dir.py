"""
Which profile is active. One rule, used by the ranker, the bridge and the build.

    1. $PROFILE_DIR, if set          (run several profiles side by side)
    2. profiles/me, if it exists      (what setup.sh creates for you)
    3. profiles/example               (the committed, fictional example)

Before this existed the ranker read scraper/profile.md and the bridge read
profiles/example, so the dashboard reported a profile as present while the
ranker crashed looking for a file that was no longer there.
"""

from __future__ import annotations

import os
import pathlib

REPO = pathlib.Path(__file__).resolve().parent.parent


def profile_dir() -> pathlib.Path:
    env = os.environ.get("PROFILE_DIR")
    if env:
        return pathlib.Path(env).expanduser().resolve()
    me = REPO / "profiles" / "me"
    return me if me.is_dir() else REPO / "profiles" / "example"


def profile_file() -> pathlib.Path:
    return profile_dir() / "profile.md"


if __name__ == "__main__":      # used by run-batch.sh
    print(profile_dir())
