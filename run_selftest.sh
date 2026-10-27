#!/usr/bin/env bash
# SHOBS-FLUX self-test on Linux checks this copy of SHOBS-FLUX with the same Python run_linux.sh uses
# (NumPy, SciPy, Astropy, Tkinter). It changes no data. The log goes to selftest_log.txt here and to
# ~/Downloads/SHOBS-FLUX_selftest_log.txt (when that folder exists). On a Mac, double-click "Run self-test.command" instead.
here=$(cd "$(dirname "$0")" && pwd -P) || exit 1
exec bash "$here/run_linux.sh" --selftest
