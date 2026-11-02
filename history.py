"""SHOBS-FLUX run history: one SQLite database (flux_runs.sqlite in the SHOBS data folder).

Every run FLUX makes (a Check image, later a bench run, darks, an on-sky run) is one row in runs, with its inputs and
results as JSON; every value a run measures is one row in run_values, so the History page (M5) can plot any value
over time and trace it back to its run. SHOBS-P's on-sky log files are recorded in onsky_files when FLUX reads them,
so no night is counted twice.

The schema has a version (PRAGMA user_version); a newer FLUX adds tables or columns and never drops data.
Version 2 (FLUX 0.5) adds runs.removed: a run taken off the History page is only marked (it leaves the list and the
trend plots, and can be put back), so a log once read is never read again by mistake and nothing is lost."""
from __future__ import annotations

import json
import os
import sqlite3
import time

import shobs_profile as sp

SCHEMA_VERSION = 2

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
    summary     TEXT,                        -- JSON
    removed     INTEGER NOT NULL DEFAULT 0   -- 1 = taken off the History page (schema 2)
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
        cols = {r[1] for r in con.execute("PRAGMA table_info(runs)").fetchall()}
        if "removed" not in cols:                              # a version-1 database: add the column, keep every row
            try:
                con.execute("ALTER TABLE runs ADD COLUMN removed INTEGER NOT NULL DEFAULT 0")
            except sqlite3.OperationalError:                   # another FLUX added it at the same moment
                cols = {r[1] for r in con.execute("PRAGMA table_info(runs)").fetchall()}
                if "removed" not in cols:
                    raise
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


def onsky_file_known(path: str, size: int, mtime: float) -> bool:
    """True when this on-sky log, at this size and modification time, is already in the history (a log SHOBS-P has
    written again since counts as new). Never raises (False when the database cannot be read)."""
    try:
        con = connect()
        try:
            row = con.execute("SELECT size, mtime FROM onsky_files WHERE path = ?",
                              (os.path.normcase(os.path.abspath(path)),)).fetchone()
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return False
    return row is not None and row[0] == int(size) and row[1] is not None and abs(float(row[1]) - float(mtime)) < 1e-3


def mark_onsky_file(path: str, size: int, mtime: float, run_key: str | None = None) -> bool:
    """Remember that this on-sky log (at this size and modification time) is in the history, with the runs.id of the
    run that recorded it. Returns False when the database could not be written (never raises)."""
    try:
        con = connect()
        try:
            rid = None
            if run_key:
                row = con.execute("SELECT id FROM runs WHERE run_key = ?", (run_key,)).fetchone()
                rid = row[0] if row else None
            con.execute("INSERT OR REPLACE INTO onsky_files (path, size, mtime, read_at, run_id) VALUES (?,?,?,?,?)",
                        (os.path.normcase(os.path.abspath(path)), int(size), float(mtime), sp.now_iso(), rid))
            con.commit()
        finally:
            con.close()
        return True
    except (sqlite3.Error, OSError, TypeError, ValueError):
        return False


def recent_runs(limit: int = 50, kind: str | None = None, removed: bool = False) -> list[dict]:
    """The newest runs first. removed=True also lists the runs taken off the History page."""
    try:
        con = connect()
        try:
            con.row_factory = sqlite3.Row
            where = [] if removed else ["removed = 0"]
            if kind:
                where.append("kind = ?")
            q = "SELECT * FROM runs" + (" WHERE " + " AND ".join(where) if where else "") + " ORDER BY id DESC LIMIT ?"
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


def values_for(keys: tuple, profile_ids: list | None = None, limit: int = 2000) -> list[dict]:
    """Measured values from earlier runs (newest first): key, channel, value, sigma, unit, date, profile_id, and the
    run's run_key, kind and started time. profile_ids: only those camera profiles (None: all)."""
    if not keys:
        return []
    try:
        con = connect()
        try:
            con.row_factory = sqlite3.Row
            q = ("SELECT v.key, v.channel, v.value, v.sigma, v.unit, v.date, v.profile_id, r.run_key, r.kind, "
                 "r.started FROM run_values v JOIN runs r ON r.id = v.run_id WHERE r.removed = 0 AND v.key IN (" +
                 ",".join("?" * len(keys)) + ")")
            args = list(keys)
            if profile_ids is not None:
                if not profile_ids:
                    return []
                q += " AND v.profile_id IN (" + ",".join("?" * len(profile_ids)) + ")"
                args += list(profile_ids)
            q += " ORDER BY r.id DESC LIMIT ?"
            args.append(limit)
            return [dict(r) for r in con.execute(q, args).fetchall()]
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return []


# ---- the History page (0.5): setups, trend series, removing a run ----------------------------------------------------

# What each value is called on the History page and in the system report, in the order the drop-down lists them.
VALUE_WORDS = {
    "bands.zero_point": "Zero point at airmass 1",
    "bands.zero_point_at_x": "Zero point at the night's airmass",
    "bands.extinction_k": "Extinction k",
    "bands.fwhm_in_focus": "Seeing in focus (FWHM)",
    "bands.fwhm_defocus": "Star size defocused (FWHM)",
    "bands.sky_brightness": "Sky brightness",
    "bands.scintillation_cy": "Scintillation C_Y",
    "cadence.overhead_s": "Time lost between frames",
    "cadence.interval_s": "Time between frame starts",
    "gain_e_per_adu": "Gain",
    "read_noise_e": "Read noise",
    "full_well_e": "Full well",
    "linear_limit_adu": "Linear limit",
    "bias_level_adu": "Bias level",
    "exposure_offset_s": "Exposure-timing offset",
    "dark_current_e_per_px_s": "Dark current",
    "hot_pixel_ppm": "Hot pixels",
    "glow_e_per_px_s": "Edge glow",
}
SYSTEM_KINDS = ("onsky",)              # one row per night; "sky" runs are the combined values a save wrote
CAMERA_KINDS = ("bench", "darks")


def value_words(key: str, channel: str | None = None) -> str:
    """'Zero point at airmass 1 (green (TG))', 'Read noise (R)', 'Dark current (-10 °C)'."""
    import onsky
    words = VALUE_WORDS.get(key, key)
    if not channel:
        return words
    ch = str(channel)
    if ch.endswith("C") and ch[:-1].lstrip("-").replace(".", "", 1).isdigit():
        return f"{words} ({ch[:-1]} °C)"
    return f"{words} ({onsky.BAND_WORDS.get(ch, ch)})"


def parse_system_id(pid: str | None):
    """'ioptron-14-rc-2903mm__zwo…' → ('ioptron-14-rc', 2903.0, 'zwo…'); anything else → (None, None, None)."""
    import re
    m = re.match(r"^(.*?)-(\d+(?:\.\d+)?)mm__(.+)$", str(pid or ""))
    if not m:
        return None, None, None
    return m.group(1), float(m.group(2)), m.group(3)


def same_system(a: str | None, b: str | None, tolerance: float = 0.03) -> bool:
    """Two system ids for the same setup: the same camera id and focal lengths within 3 % (the telescope's name is
    not compared: a night saved before the name was typed, or under an old name, still belongs to the setup)."""
    if not a or not b:
        return False
    if a == b:
        return True
    _ta, fa, ca = parse_system_id(a)
    _tb, fb, cb = parse_system_id(b)
    if ca is None or cb is None or ca != cb or not fa or not fb:
        return False
    return abs(fa - fb) <= tolerance * max(fa, fb)


def _rows(q: str, args=()) -> list[dict]:
    try:
        con = connect()
        try:
            con.row_factory = sqlite3.Row
            return [dict(r) for r in con.execute(q, args).fetchall()]
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return []


def setups() -> list[dict]:
    """Every setup the History page can show: each saved system and camera profile, and any setup the run history
    knows without a profile. [{"kind": "system"|"camera", "id", "words", "path" or None}], systems first."""
    out: list[dict] = []
    seen_sys: list[str] = []
    for path in sp.list_profiles("systems"):
        try:
            doc = sp.load(path)
        except sp.ProfileError:
            continue
        pid = doc.get("profile_id") or os.path.basename(path)[:-5]
        tel = doc.get("telescope") or {}
        cam = doc.get("camera_profile") or ""
        words = f"{tel.get('name') or pid.split('__')[0]} + {cam.split('__')[0]}"
        if tel.get("focal_mm"):
            words += f" ({tel['focal_mm']:g} mm)"
        out.append({"kind": "system", "id": pid, "words": words, "path": path})
        seen_sys.append(pid)
    for r in _rows("SELECT DISTINCT system_id FROM runs WHERE system_id IS NOT NULL AND removed = 0"):
        sid = r["system_id"]
        if sid and not any(same_system(sid, s) for s in seen_sys):
            _t, f, _c = parse_system_id(sid)
            note = "no profile saved" + ("; no focal length in the headers" if not f else "")
            out.append({"kind": "system", "id": sid, "words": f"{sid} ({note})", "path": None})
            seen_sys.append(sid)
    seen_cam = []
    for path in sp.list_profiles("cameras"):
        pid = os.path.basename(path)[:-5]
        out.append({"kind": "camera", "id": pid, "words": f"Camera {pid}", "path": path})
        seen_cam.append(pid)
    for r in _rows("SELECT DISTINCT camera_id FROM runs WHERE camera_id IS NOT NULL AND removed = 0 AND kind IN "
                   "('check', 'bench', 'darks')"):
        cid = r["camera_id"]
        if cid and cid not in seen_cam:
            out.append({"kind": "camera", "id": cid, "words": f"Camera {cid} (no profile saved)", "path": None})
            seen_cam.append(cid)
    return out


def _setup_rows(kind: str, sid: str, include_removed: bool = False) -> list[dict]:
    """Every value row of one setup with its run (newest run first)."""
    kinds = SYSTEM_KINDS if kind == "system" else CAMERA_KINDS
    q = ("SELECT v.key, v.channel, v.value, v.sigma, v.unit, v.source, v.date, v.profile_id, r.run_key, r.kind, "
         "r.started, r.source AS run_source, r.status, r.system_id, r.camera_id, r.summary, r.removed "
         "FROM run_values v JOIN runs r ON r.id = v.run_id WHERE r.kind IN (" + ",".join("?" * len(kinds)) + ")")
    args: list = list(kinds)
    if not include_removed:
        q += " AND r.removed = 0"
    q += " ORDER BY r.id DESC"
    rows = _rows(q, args)
    if kind == "system":
        return [r for r in rows if same_system(r.get("system_id") or r.get("profile_id"), sid)]
    return [r for r in rows if (r.get("profile_id") or r.get("camera_id")) == sid]


def available(kind: str, sid: str) -> list[tuple]:
    """The values one setup has a trend for: [(key, channel, words, n points)], in VALUE_WORDS order."""
    count: dict = {}
    for r in series_points(kind, sid):
        count[(r["key"], r["channel"])] = count.get((r["key"], r["channel"]), 0) + 1
    order = list(VALUE_WORDS)
    return sorted(((k, c, value_words(k, c), n) for (k, c), n in count.items()),
                  key=lambda t: (order.index(t[0]) if t[0] in order else 99, str(t[1] or "")))


def _night_of(row) -> str:
    """The date a point is plotted at: the night for on-sky rows, the run's (or its data's) date otherwise."""
    d = str(row.get("date") or row.get("started") or "")[:10]
    return d


def series_points(kind: str, sid: str, key: str | None = None, channel: str | None = "*", date_from: str = "",
                  date_to: str = "", include_removed: bool = False) -> list[dict]:
    """The points of one setup's trends, oldest first: date, value, sigma, unit, key, channel, run_key, kind, started,
    target (on-sky nights), photometric, removed. key / channel narrow it (channel "*" = any). One point per night,
    value and band: when a night was read more than once (a re-run, or the same log after SHOBS-P wrote it again) the
    newest run counts. Dates are YYYY-MM-DD and inclusive."""
    out: dict = {}
    rows = _setup_rows(kind, sid, include_removed)
    rows.sort(key=lambda r: bool(r.get("removed")))        # stable: live rows (newest first) before removed ones
    for r in rows:
        if key is not None and r["key"] != key:
            continue
        if channel != "*" and (r["channel"] or None) != (channel or None):
            continue
        if r.get("value") is None:
            continue
        day = _night_of(r)
        if date_from and day < date_from or date_to and day > date_to:
            continue
        try:
            summ = json.loads(r.get("summary") or "{}")
        except (TypeError, ValueError):
            summ = {}
        if not isinstance(summ, dict):
            summ = {}
        ident = (day, r["key"], r["channel"], summ.get("defocus") or "") if r["kind"] == "onsky" else \
            (r["run_key"], r["key"], r["channel"])
        if ident in out:                               # rows come newest run first: the first one seen counts
            continue
        out[ident] = {"date": day, "value": r["value"], "sigma": r.get("sigma"), "unit": r.get("unit") or "",
                      "key": r["key"], "channel": r["channel"], "run_key": r["run_key"], "kind": r["kind"],
                      "started": r["started"], "target": summ.get("target") or "",
                      "photometric": summ.get("photometric"), "marginal": summ.get("marginal"),
                      "defocus": summ.get("defocus") or "", "source_path": r.get("run_source") or "",
                      "removed": bool(r.get("removed"))}
    return sorted(out.values(), key=lambda p: (p["date"], p["started"] or ""))


def set_removed(run_key: str, removed: bool = True) -> bool:
    """Take a run off the History page (or put it back). Its rows stay in the database, and an on-sky log it read
    stays known, so the next Sky run does not read that night again. Returns False when it could not be written."""
    try:
        con = connect()
        try:
            cur = con.execute("UPDATE runs SET removed = ? WHERE run_key = ?", (1 if removed else 0, run_key))
            con.commit()
            return cur.rowcount > 0
        finally:
            con.close()
    except (sqlite3.Error, OSError):
        return False


def _saved_profiles_texts(sid: str) -> list[str]:
    """Every value's date and conditions in the system profiles of one setup: the current files and every revision
    kept in history/ (a revision's file name starts with "systems__")."""
    paths = list(sp.list_profiles("systems"))
    hdir = sp.sub_dir("history", make=False)
    if os.path.isdir(hdir):
        paths += sorted(os.path.join(hdir, n) for n in os.listdir(hdir) if n.startswith("systems__")
                        and n.endswith(".json"))
    out = []
    for path in paths:
        try:
            doc = sp.load(path)
        except sp.ProfileError:
            continue
        if doc.get("format") != sp.SYSTEM_FORMAT or not same_system(doc.get("profile_id"), sid):
            continue
        for _where, rec in sp.iter_records(doc):
            for k in ("date", "conditions"):
                if rec.get(k):
                    out.append(str(rec[k]))
    return out


def _names_night(text: str, night: str) -> bool:
    """A value's date or conditions names a night: the date itself, or a range "A to B" that holds it."""
    import re
    if night in text:
        return True
    for a, b in re.findall(r"(\d{4}-\d{2}-\d{2}) to (\d{4}-\d{2}-\d{2})", text):
        if a <= night <= b:
            return True
    return False


def unsaved_folder_runs() -> list[dict]:
    """On-sky runs FLUX 0.5.0 recorded for a folder of lights at Run time and that were never saved: the source is a
    FLUX folder log (its file name holds "__flux-") and no system profile of the same setup, current or any
    revision, has a value whose date or conditions names that run's night. [{"id", "run_key", "night", "log"}]."""
    import re
    rows = _rows("SELECT id, run_key, system_id, source, summary FROM runs WHERE kind = 'onsky' AND removed = 0")
    texts: dict = {}
    out = []
    for r in rows:
        log = re.split(r"[\\/]", str(r.get("source") or ""))[-1]
        if "__flux-" not in log.lower():
            continue
        try:
            summ = json.loads(r.get("summary") or "{}")
        except (TypeError, ValueError):
            summ = {}
        night = str((summ if isinstance(summ, dict) else {}).get("night") or log[:10])
        sid = r.get("system_id") or ""
        if sid not in texts:
            texts[sid] = _saved_profiles_texts(sid) if sid else []
        if any(_names_night(t, night) for t in texts[sid]):
            continue
        out.append({"id": r["id"], "run_key": r["run_key"], "night": night, "log": log})
    return out


def remove_unsaved_folder_runs() -> list[dict]:
    """The 0.5.1 clean-up: mark every unsaved folder run removed (nothing is deleted; History can put one back).
    Returns the runs marked."""
    got = unsaved_folder_runs()
    return [r for r in got if set_removed(r["run_key"], True)]


CSV_COLUMNS = ("date", "value", "sigma", "unit", "value_name", "band_or_channel", "target", "photometric", "run",
               "kind", "recorded")


def write_csv(path: str, points: list[dict], setup_words: str, version: str) -> None:
    """The plotted points as CSV, with a comment line saying what, for which setup, and when it was made (DTG)."""
    import csv
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(f"# {setup_words}\n# Created {dtg()} by SHOBS-FLUX {version}\n")
        w = csv.writer(fh)
        w.writerow(CSV_COLUMNS)
        for p in points:
            w.writerow([p["date"], f"{p['value']:.6g}", "" if p.get("sigma") is None else f"{p['sigma']:.4g}",
                        p.get("unit") or "", VALUE_WORDS.get(p["key"], p["key"]), p.get("channel") or "",
                        p.get("target") or "", "" if p.get("photometric") is None else
                        ("marginal" if p.get("marginal") else ("yes" if p["photometric"] else "no")),
                        p.get("run_key") or "", p.get("kind") or "", p.get("started") or ""])


def dtg(when=None) -> str:
    """A military Date Time Group, Zulu, as SHOBS-P stamps its files: "010345Z NOV 26"."""
    from datetime import datetime, timezone
    now = when or datetime.now(timezone.utc)
    month = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")[now.month - 1]
    return now.strftime("%d%H%MZ ") + f"{month} {now:%y}"            # month in English whatever Windows' language
