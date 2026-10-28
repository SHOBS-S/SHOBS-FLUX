"""SHOBS-FLUX run history: one SQLite database (flux_runs.sqlite in the SHOBS data folder).

Every run FLUX makes (a Check image, later a bench run, darks, an on-sky run) is one row in runs, with its inputs and
results as JSON; every value a run measures is one row in run_values, so the History page (M5) can plot any value
over time and trace it back to its run. SHOBS-P's on-sky log files are recorded in onsky_files when FLUX reads them,
so no night is counted twice.

The schema has a version (PRAGMA user_version); a newer FLUX adds tables or columns and never drops data."""
from __future__ import annotations

import json
import os
import sqlite3
import time

import shobs_profile as sp

SCHEMA_VERSION = 1

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    run_key     TEXT UNIQUE,                 -- e.g. check-20261027-101530-3f2a, written into value records' "run"
    kind        TEXT NOT NULL,               -- check / bench / darks / onsky
    started     TEXT NOT NULL,               -- local ISO time with offset
    finished    TEXT,
    app         TEXT,                        -- "SHOBS-FLUX 0.1.0"
    camera_id   TEXT,
    system_id   TEXT,
    source      TEXT,                        -- the frame, folder or on-sky log the run read
    n_frames    INTEGER,
    status      TEXT,                        -- ok / warn / bad / failed
    summary     TEXT                         -- JSON
);
CREATE INDEX IF NOT EXISTS runs_by_camera ON runs(camera_id, started);
CREATE TABLE IF NOT EXISTS run_values (
    run_id      INTEGER NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
    profile_id  TEXT,
    key         TEXT NOT NULL,               -- e.g. gain_e_per_adu, bands.TG.zero_point
    channel     TEXT,                        -- R / G / B / L or a band, or NULL
    value       REAL,
    sigma       REAL,
    unit        TEXT,
    source      TEXT,
    date        TEXT
);
CREATE INDEX IF NOT EXISTS values_by_key ON run_values(profile_id, key, date);
CREATE TABLE IF NOT EXISTS onsky_files (
    path        TEXT PRIMARY KEY,
    size        INTEGER,
    mtime       REAL,
    read_at     TEXT,
    run_id      INTEGER REFERENCES runs(id)
);
"""


def _clean(v):
    """NaN / infinity → None, numpy scalars → plain numbers, so the stored JSON is strict."""
    if isinstance(v, dict):
        return {str(k): _clean(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_clean(x) for x in v]
    if hasattr(v, "item") and callable(v.item) and not isinstance(v, (str, bytes)):
        try:
            v = v.item()
        except (TypeError, ValueError):
            return None
    if isinstance(v, float) and v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def db_path() -> str:
    return sp.runs_db_path()


def connect() -> sqlite3.Connection:
    """Open (and create or upgrade) the run database."""
    con = sqlite3.connect(db_path(), timeout=10)
    con.execute("PRAGMA foreign_keys = ON")
    ver = con.execute("PRAGMA user_version").fetchone()[0]
    if ver < SCHEMA_VERSION:
        con.executescript(SCHEMA)
        con.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        con.commit()
    return con


def new_run_key(kind: str) -> str:
    return f"{kind}-{time.strftime('%Y%m%d-%H%M%S')}-{os.urandom(2).hex()}"


def record_run(kind: str, source: str, summary: dict, camera_id: str | None = None, system_id: str | None = None,
               n_frames: int | None = None, status: str = "ok", app: str = "", values: list | None = None,
               run_key: str | None = None) -> str | None:
    """Store one finished run (and its values: dicts with key, value, unit, source, and optionally channel, sigma,
    profile_id, date). Returns its run key, or None when the database could not be written (never raises)."""
    key = run_key or new_run_key(kind)
    now = sp.now_iso()
    try:
        con = connect()
        try:
            cur = con.execute(
                "INSERT INTO runs (run_key, kind, started, finished, app, camera_id, system_id, source, n_frames, "
                "status, summary) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (key, kind, now, now, app, camera_id, system_id, source, n_frames, status,
                 json.dumps(_clean(summary), ensure_ascii=False, default=str, allow_nan=False)))
            rid = cur.lastrowid
            for v in values or []:
                con.execute("INSERT INTO run_values (run_id, profile_id, key, channel, value, sigma, unit, source, "
                            "date) VALUES (?,?,?,?,?,?,?,?,?)",
                            (rid, v.get("profile_id"), v["key"], v.get("channel"), _clean(v.get("value")),
                             _clean(v.get("sigma")),
                             v.get("unit"), v.get("source"), v.get("date") or now[:10]))
            con.commit()
        finally:
            con.close()
        return key
    except (sqlite3.Error, OSError, KeyError, ValueError, TypeError):
        return None


def recent_runs(limit: int = 50, kind: str | None = None) -> list[dict]:
    try:
        con = connect()
        try:
            con.row_factory = sqlite3.Row
            q = "SELECT * FROM runs" + (" WHERE kind = ?" if kind else "") + " ORDER BY id DESC LIMIT ?"
            rows = con.execute(q, ((kind, limit) if kind else (limit,))).fetchall()
            return [dict(r) for r in rows]
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return []


STATUS_ICON = {"bad": "✖", "warn": "⚠", "ok": "✔"}


def status_text(row: dict) -> str:
    """The History list's Status column for one run: the worst finding in a few words ("✖ sky over half the linear
    range", "⚠ read-noise-limited", "3 field stars saturated": notes carry no icon), or for older runs without it
    the status word with its icon."""
    status = row.get("status") or ""
    try:
        summary = json.loads(row.get("summary") or "{}")
    except (TypeError, ValueError):
        summary = {}
    worst = summary.get("worst") if isinstance(summary, dict) else None
    if isinstance(worst, str) and worst.strip():
        return worst.strip()
    icon = STATUS_ICON.get(status, "")
    return f"{icon} {status}".strip()
