"""Profile roll-back (Profiles page, 0.5): put an earlier revision of a camera or system profile back.

Every save keeps the previous revision in the data folder's history/ (shobs_profile.save_profile). Rolling back
saves the chosen revision's values as a NEW revision: the profile in use now goes to history/ like any other save, so
a roll-back can itself be undone, and the revision number only ever goes up. The rolled-back profile says what it
came from in "rolled_back" ({"to_revision", "from_revision", "on"}), shown on the Profiles page."""
from __future__ import annotations

import os
import re

import shobs_profile as sp


class RollbackError(Exception):
    """A revision that cannot be put back (the message is a plain sentence)."""


def _kind_pid(path: str) -> tuple[str, str]:
    return os.path.basename(os.path.dirname(path)), os.path.basename(path)[:-5]


def revisions(path: str) -> list[dict]:
    """The earlier revisions of the profile at path, newest first: {"path", "revision", "saved" (when it was replaced,
    'YYYY-MM-DD HH:MM'), "updated" (its own last update), "written_by", "changes" (values that differ from the profile
    now), "doc"}. A file that cannot be read is listed with "error" and no doc."""
    kind, pid = _kind_pid(path)
    try:
        now = sp.load(path)
    except sp.ProfileError:
        now = None
    out = []
    for hp in sp.history_of(kind, pid):
        m = re.search(r"__rev(\d+)__(\d{8})-(\d{6})", os.path.basename(hp))
        saved = f"{m.group(2)[:4]}-{m.group(2)[4:6]}-{m.group(2)[6:]} {m.group(3)[:2]}:{m.group(3)[2:4]}" if m else ""
        item = {"path": hp, "revision": int(m.group(1)) if m else None, "saved": saved}
        try:
            doc = sp.load(hp)
        except sp.ProfileError as exc:
            item.update(error=str(exc), doc=None, changes=[], updated="", written_by="")
            out.append(item)
            continue
        item.update(doc=doc, updated=str(doc.get("updated") or "")[:16].replace("T", " "),
                    written_by=doc.get("written_by") or "",
                    changes=differences(doc, now) if now else [])
        if isinstance(doc.get("revision"), int):
            item["revision"] = doc["revision"]
        out.append(item)
    out.reverse()
    return out


def _fmt(rec) -> str:
    if not sp.is_record(rec):
        return "—"
    v = rec.get("value")
    if v is None or rec.get("source") == "none":
        return "—"
    txt = f"{v:g}" if isinstance(v, (int, float)) else str(v)
    if isinstance(rec.get("sigma"), (int, float)):
        txt += f" ± {rec['sigma']:g}"
    return f"{txt} {rec.get('unit') or ''}".strip() + f" ({sp.SOURCE_WORDS.get(rec.get('source'), rec.get('source'))})"


def differences(old: dict, new: dict) -> list[tuple[str, str, str]]:
    """The value records that differ between two revisions: [(name, old words, new words)]. Only the number, its
    ± and its source count (a later date on the same value is not a change)."""
    a = {".".join(str(p) for p in path): rec for path, rec in sp.iter_records(old)}
    b = {".".join(str(p) for p in path): rec for path, rec in sp.iter_records(new)}
    out = []
    for name in list(a) + [n for n in b if n not in a]:
        ra, rb = a.get(name), b.get(name)

        def key(r):
            if not sp.is_record(r):
                return None
            return (r.get("value"), r.get("sigma"), r.get("source"))
        if key(ra) != key(rb):
            out.append((name, _fmt(ra), _fmt(rb)))
    for part in ("telescope",):                                  # system profiles: the telescope's plain fields
        ta, tb = old.get(part) or {}, new.get(part) or {}
        if isinstance(ta, dict) and isinstance(tb, dict):
            for k in list(ta) + [k for k in tb if k not in ta]:
                if ta.get(k) != tb.get(k) and not isinstance(ta.get(k), dict):
                    out.append((f"{part}.{k}", "—" if ta.get(k) is None else str(ta.get(k)),
                                "—" if tb.get(k) is None else str(tb.get(k))))
    return out


def roll_back(path: str, revision_path: str, written_by: str = "") -> dict:
    """Save the revision at revision_path as the newest revision of the profile at path. Returns the saved profile.
    RollbackError when the revision is not the same profile, cannot be read or is not valid."""
    kind, pid = _kind_pid(path)
    try:
        old = sp.load(revision_path)
        now = sp.load(path)
    except sp.ProfileError as exc:
        raise RollbackError(f"That revision cannot be read: {exc}.") from None
    if old.get("format") != now.get("format") or (old.get("profile_id") or pid) != (now.get("profile_id") or pid):
        raise RollbackError("That file is a revision of a different profile.")
    doc = dict(old)
    doc["profile_id"] = now.get("profile_id") or pid
    doc["created"] = now.get("created") or old.get("created")
    doc["revision"] = int(now.get("revision") or 1) + 1
    doc["rolled_back"] = {"to_revision": old.get("revision"), "from_revision": now.get("revision"),
                          "on": sp.now_iso()}
    doc["updated"] = sp.now_iso()
    if written_by:
        doc["written_by"] = written_by
    try:
        saved = sp.save_profile(doc, written_by)
    except sp.ProfileError as exc:
        raise RollbackError(f"That revision is not a valid profile ({exc}).") from None
    return sp.load(saved)
