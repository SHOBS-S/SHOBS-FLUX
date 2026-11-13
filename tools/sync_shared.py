"""Copies the master shared modules (this repository) into SHOBS-P and SHOBS-SCHED checkouts, byte for byte:
shobs_profile.py (the instrument profiles) and shobs_update.py (the new-version check; since FLUX 1.0.1 this copy
is the SCHED 1.4.1 / SHOBS-P 2.3.3 one with the Highlights, byte-identical in all three).

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
SHARED = ("shobs_profile.py", "shobs_update.py")


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
        if not os.path.isdir(d):
            print(f"  no folder {d}")
            bad += 1
            continue
        for name in SHARED:
            master = os.path.join(HERE, name)
            target = os.path.join(d, name)
            if same(master, target):
                print(f"  same      {target}")
                continue
            if check_only:
                print(f"  DIFFERENT {target}")
                bad += 1
                continue
            shutil.copyfile(master, target)
            print(f"  copied    {target}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
