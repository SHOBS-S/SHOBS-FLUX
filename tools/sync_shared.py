"""Copies the master shobs_profile.py (this repository) into SHOBS-P and SHOBS-SCHED checkouts, byte for byte.

    python tools/sync_shared.py PATH_TO_SHOBS-P [PATH_TO_SHOBS-SCHED ...]        copy
    python tools/sync_shared.py --check PATH ...                                 only compare (exit 1 when different)

shobs_profile.py is ONE file shared by the three apps (John, 27 Oct 2026): change it here only, raise
SHOBS_PROFILE_VERSION, then run this and commit the copy in each app with its own build. Each app's tests check
the version they were built against."""
from __future__ import annotations

import os
import shutil
import sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MASTER = os.path.join(HERE, "shobs_profile.py")


def same(a: str, b: str) -> bool:
    try:
        with open(a, "rb") as fa, open(b, "rb") as fb:
            return fa.read() == fb.read()
    except OSError:
        return False


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    dests = [a for a in argv if a != "--check"]
    if not dests:
        print(__doc__)
        return 2
    bad = 0
    for d in dests:
        target = os.path.join(d, "shobs_profile.py")
        if same(MASTER, target):
            print(f"  same      {target}")
            continue
        if check_only:
            print(f"  DIFFERENT {target}")
            bad += 1
            continue
        if not os.path.isdir(d):
            print(f"  no folder {d}")
            bad += 1
            continue
        shutil.copyfile(MASTER, target)
        print(f"  copied    {target}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
