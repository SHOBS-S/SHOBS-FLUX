"""SHOBS-FLUX settings, log and names.

Everything lives in the SHOBS data folder (~/.shobs, shared with SHOBS-P and SHOBS-SCHED through shobs_profile.py):
  flux_settings.json    the last folders used and the answers to header questions
  flux.log              what FLUX did, and any error with its traceback
Tests point the whole folder elsewhere with the SHOBS_DATA_HOME environment variable."""
from __future__ import annotations

import json
import os
import time

import shobs_profile as sp

APP_NAME = "SHOBS-FLUX"
APP_TITLE = "Shiloh Hill Observatory – Optical System Characterization (SHOBS-FLUX)"
APP_VERSION = "0.2.0"          # milestone builds 0.1–0.6; 1.0.0 at release (John, 27 Oct 2026)
WRITTEN_BY = f"{APP_NAME} {APP_VERSION}"

DEFAULTS = {"last_folder": "", "header_answers": {}}


def settings_path() -> str:
    return os.path.join(sp.data_dir(), "flux_settings.json")


def log_path() -> str:
    return os.path.join(sp.data_dir(), "flux.log")


def load() -> dict:
    out = json.loads(json.dumps(DEFAULTS))
    try:
        with open(settings_path(), encoding="utf-8") as fh:
            got = json.load(fh)
        if isinstance(got, dict):
            out.update(got)
    except (OSError, ValueError):
        pass
    return out


def save(s: dict) -> bool:
    try:
        sp.write_json(settings_path(), s)
        return True
    except OSError:
        return False


def log(text: str) -> None:
    """One dated line (or block) in flux.log; never raises."""
    try:
        with open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(time.strftime("%Y-%m-%d %H:%M:%S ") + text.rstrip() + "\n")
    except OSError:
        pass
