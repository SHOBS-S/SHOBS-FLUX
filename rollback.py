"""Profile roll-back (Profiles page, 0.5): put an earlier revision of a camera or system profile back.

Every save keeps the previous revision in the data folder's history/ (shobs_profile.save_profile). Rolling back
saves the chosen revision's values as a NEW revision: the profile in use now goes to history/ like any other save, so
a roll-back can itself be undone, and the revision number only ever goes up. The rolled-back profile says what it
came from in "rolled_back" ({"to_revision", "from_revision", "as_revision", "on"}), shown on the Profiles page while
that revision is the one in use (a later save keeps the key but has a higher revision, so it is no longer shown)."""
from __future__ import annotations

import os
import re

import shobs_profile as sp


# Plain names for the values in a profile (the Roll back window; the Profiles page can use the same map).
VALUE_NAMES = {
    "gain_e_per_adu": "Gain", "read_noise_e": "Read noise", "full_well_e": "Full well", "saturation_adu": "Saturation",
    "linear_limit_adu": "Linear limit", "bias_level_adu": "Bias level", "dark_current_e_per_px_s": "Dark current",
    "qe_peak": "Peak QE", "exposure_offset_s": "Exposure-timing offset", "hot_pixel_ppm": "Hot pixels",
    "hot_pixel_rate_e_per_s": "Hot-pixel rate", "glow_e_per_px_s": "Edge glow",
    "zero_point": "Zero point at airmass 1", "zero_point_at_x": "Zero point at the night's airmass",
    "extinction_k": "Extinction k", "fwhm_in_focus": "Seeing in focus (FWHM)",
    "fwhm_imaging": "Seeing, imaging focus", "fwhm_defocus": "Star size defocused (FWHM)",
    "sky_brightness": "Sky brightness", "scintillation_cy": "Scintillation C_Y",
    "overhead_s": "Time lost between frames", "interval_s": "Time between frame starts",
    "raw_arcsec_per_px": "Plate scale",
    # the telescope's plain fields in a system profile
    "name": "Telescope name", "aperture_mm": "Aperture", "focal_mm": "Focal length",
    "focal_header": "Focal length in the header", "telescop_header": "TELESCOP in the header",
    "telescop_header_ignored": "TELESCOP not used", "central_obstruction_fraction": "Central obstruction",
    "mount": "Mount and optics", "mirrors": "Mirrors", "mirror_reflectivity": "Mirror reflectivity",
    "corrector_transmission": "Corrector transmission", "filter_transmission": "Filter transmission",
    "qe_in_band": "Camera QE in the band",
}
def value_name(path) -> str:
    """'values.gain_e_per_adu' → 'Gain'; 'per_channel.G.read_noise_e' → 'Read noise (G)';
    'bands.TG.zero_point' → 'Zero point at airmass 1 (green (TG))'; 'bands.TG.fwhm_defocus.+125 steps' →
    'Star size defocused (FWHM) (green (TG), +125 steps)'. An unknown key is shown as it is."""
    import onsky
    parts = [str(x) for x in (path.split(".") if isinstance(path, str) else path)]
    if not parts:
        return ""
    if parts[0] == "bands" and len(parts) >= 3:
        extra = [onsky.BAND_WORDS.get(parts[1], parts[1])] + parts[3:]
        return f"{VALUE_NAMES.get(parts[2], parts[2])} ({', '.join(extra)})"
    if parts[0] == "per_channel" and len(parts) >= 3:
        return f"{VALUE_NAMES.get(parts[2], parts[2])} ({parts[1]})"
    name = VALUE_NAMES.get(parts[-1], parts[-1])
    rest = parts[1:-1]
    return name + (f" ({', '.join(rest)})" if rest else "")


def number_words(v) -> str:
    """65535 → '65,535'; 1.89 → '1.89'; 0.0004242 → '0.0004242' (a plain number, never in e-notation)."""
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return str(v)
    if abs(v) >= 1000:
        return f"{v:,.0f}" if abs(v - round(v)) < 0.05 or abs(v) >= 1e5 else f"{v:,.1f}"
    txt = f"{v:.4g}"
    if "e" in txt:
        txt = f"{v:.8f}".rstrip("0").rstrip(".")
    return txt


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
        sp.rederive(doc)                 # as it would be put back: a worked-out value follows its inputs (0.5.1)
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
    txt = number_words(v) if isinstance(v, (int, float)) else str(v)
    if isinstance(rec.get("sigma"), (int, float)):
        txt += f" ± {number_words(rec['sigma'])}"
    return f"{txt} {rec.get('unit') or ''}".strip() + f" ({sp.SOURCE_WORDS.get(rec.get('source'), rec.get('source'))})"


def differences(old: dict, new: dict) -> list[tuple[str, str, str]]:
    """The value records that differ between two revisions: [(key path, old words, new words)], e.g.
    ('values.gain_e_per_adu', '1 e-/ADU (estimate)', '1.904 e-/ADU (typed by you)'); value_name(key) gives the plain
    name. Only the number, its ± and its source count (a later date on the same value is not a change)."""
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
                    out.append((f"{part}.{k}", "—" if ta.get(k) is None else number_words(ta.get(k)),
                                "—" if tb.get(k) is None else number_words(tb.get(k))))
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
    doc["created"] = now.get("created") or old.get("created") or sp.now_iso()
    doc["revision"] = int(now.get("revision") or 1) + 1
    doc["rolled_back"] = {"to_revision": old.get("revision"), "from_revision": now.get("revision"),
                          "as_revision": doc["revision"], "on": sp.now_iso()}
    doc["updated"] = sp.now_iso()
    if written_by:
        doc["written_by"] = written_by
    try:
        saved = sp.save_profile(doc, written_by)
    except sp.ProfileError as exc:
        raise RollbackError(f"That revision is not a valid profile ({exc}).") from None
    return sp.load(saved)
