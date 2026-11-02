"""The system report (0.5): one PDF describing a telescope + camera from its system profile, camera profile and run
history: what FLUX measured, where every number came from, how the system performs against a photon budget, how
it has changed over time, and the methods and caveats behind the numbers.

gather(system_path, ...) collects everything into a plain dict (no plotting: the tests check it directly);
write_pdf(info, path, share=...) lays it out on US-letter pages with matplotlib (no extra library).

Pages: 1 summary (setup, data range, key numbers); 2 camera (each value with its source, the maker's spec and any
other value for it, e.g. FLUX measured 1.89 against the operator's 1.904); 3 on sky, per band (numbers and four
trend plots); 4 throughput, measured against the photon budget; 5 over time (zero point and seeing, with a before /
after split at an optional event date such as a mirror cleaning); 6 methods and caveats.

share=True makes a copy to send to someone else: the site (latitude, longitude, elevation) and every file path on
this computer are left out, and the report says so."""
from __future__ import annotations

import math
import os
import re
import tempfile
import textwrap
from datetime import datetime, timezone

import history
import onsky
import shobs_profile as sp

APP_DIR = os.path.dirname(os.path.abspath(__file__))
BUILTIN_SPECS = os.path.join(APP_DIR, "builtin_specs")

H = 6.62607015e-34          # J s
JY = 1e-26                  # W m⁻² Hz⁻¹
# Photons s⁻¹ m⁻² from a magnitude-0 star above the atmosphere, through each catalog band:
# Johnson–Cousins (Vega) from Bessell, Castelli & Plez 1998 (photons s⁻¹ cm⁻² Å⁻¹ at mag 0 × the band's width);
# Sloan (AB: 3631 Jy) from the band's effective wavelength and width (SDSS u′g′r′i′z′).
_VEGA = {"U": (756.0, 680.0), "B": (1392.0, 980.0), "V": (995.5, 890.0), "R": (702.0, 1380.0), "I": (452.0, 1490.0)}
_SLOAN = {"SU": (355.7, 59.9), "SG": (482.5, 137.9), "SR": (626.1, 138.2), "SI": (767.2, 153.5), "SZ": (909.7, 137.0)}
PHOTONS_MAG0 = {b: f * w * 1e4 for b, (f, w) in _VEGA.items()}
# Each catalog band's span in nm, for reading the maker's QE curve over the band (a top-hat, centre ± width / 2):
# Johnson–Cousins from Bessell 1990/1998's effective wavelength and the width above (Å → nm); Sloan as above.
_VEGA_CENTRE = {"U": 366.0, "B": 438.0, "V": 545.0, "R": 641.0, "I": 798.0}
BAND_RANGE_NM = {b: (_VEGA_CENTRE[b] - w / 20.0, _VEGA_CENTRE[b] + w / 20.0) for b, (_f, w) in _VEGA.items()}
BAND_RANGE_NM.update({b: (lam - w / 2.0, lam + w / 2.0) for b, (lam, w) in _SLOAN.items()})
PHOTONS_MAG0.update({b: 3631.0 * JY * (w / lam) / H for b, (lam, w) in _SLOAN.items()})
BAND_SYSTEM = {**{b: "Vega (Bessell 1998)" for b in _VEGA}, **{b: "AB (3631 Jy)" for b in _SLOAN}}

# Optics the photon budget needs, kept in the system profile's telescope block (plain numbers, set in the report
# window); what is assumed when it is not known.
OPTICS = (
    ("central_obstruction_fraction", "Central obstruction (diameter fraction)", 0.40,
     "typical of a Ritchey–Chrétien or corrected Dall–Kirkham"),
    ("mirrors", "Mirrors in the light path", 2, "primary and secondary"),
    ("mirror_reflectivity", "Reflectivity of each mirror", 0.90, "aluminium a few years old"),
    ("corrector_transmission", "Corrector / reducer transmission", 1.0, "none"),
    ("filter_transmission", "Filter transmission in the band", 0.90, "a good interference filter"),
    ("qe_in_band", "Camera QE in the band", None, "the camera's peak QE"),
)
CURVE_HOW = "maker's QE curve"
# One-shot-color bands: FLUX counts only the pixels of that color (RGGB: half are green, a quarter red, a quarter
# blue), so only that share of the light falling in the aperture can become counted electrons.
BAYER_SHARE = {"TG": 0.5, "TR": 0.25, "TB": 0.25}
# where each budget input came from, in the report's words (a value typed in the report window is kept in the system
# profile, so "profile" is a typed value too)
COAT_WORDS = {"user": "entered by hand", "profile": "entered by hand", "assumed": "assumed"}
NO_MIRRORS = "0 mirrors (refractor or lens: no mirror losses)"

CAMERA_ROWS = (("gain_e_per_adu", "Gain"), ("read_noise_e", "Read noise"), ("full_well_e", "Full well"),
               ("saturation_adu", "Saturation"), ("linear_limit_adu", "Linear limit"),
               ("bias_level_adu", "Bias level"), ("dark_current_e_per_px_s", "Dark current"),
               ("exposure_offset_s", "Exposure-timing offset"), ("qe_peak", "Peak QE"))
BAND_ROWS = (("zero_point", "Zero point at airmass 1"), ("extinction_k", "Extinction k"),
             ("fwhm_in_focus", "Seeing in focus (FWHM)"), ("fwhm_defocus", "Star size defocused"),
             ("sky_brightness", "Sky brightness"), ("scintillation_cy", "Scintillation C_Y"))
SPEC_CURVE = {"gain_e_per_adu": "gain_e_per_adu", "read_noise_e": "read_noise_e", "full_well_e": "full_well_e"}


def prepare_fonts() -> None:
    """Call on the main thread before write_pdf runs in a worker: real text in the PDF (TrueType, searchable), and
    matplotlib's font cache built here, not in two threads at once on a first run."""
    import matplotlib
    from matplotlib import font_manager
    matplotlib.rcParams["pdf.fonttype"] = 42
    font_manager.findfont(font_manager.FontProperties(family=matplotlib.rcParams["font.family"]))


class ReportError(Exception):
    """The report cannot be made (the message is a plain sentence)."""


def _f(v):
    return sp._finite(v)


def num(v, nd: int = 4) -> str:
    """A number for the report: about nd significant figures, never in e-notation, and with thousands separators
    from 1,000 up (15,800 · 65,535); '—' when unknown."""
    v = _f(v)
    if v is None:
        return "—"
    if abs(v) >= 1000:
        return f"{v:,.0f}"
    if v == 0:
        return "0"
    txt = f"{v:.{nd}g}"
    if "e" in txt:                                     # 2.2e-05 → 0.000022
        dec = nd - 1 - math.floor(math.log10(abs(v)))
        txt = f"{v:.{dec}f}".rstrip("0").rstrip(".")
    return txt


def fmt(v, unit: str = "", nd: int = 4, sigma=None) -> str:
    """A number to about nd significant figures with its ± and unit; '—' when unknown."""
    v = _f(v)
    if v is None:
        return "—"
    txt = num(v, nd)
    s = _f(sigma)
    if s is not None and s > 0:
        txt += f" ± {num(s, 2)}"
    return f"{txt} {unit}".strip()


def rec_words(rec) -> str:
    if not sp.is_record(rec) or sp.value_of(rec) is None:
        return "—"
    return fmt(rec.get("value"), rec.get("unit") or "", sigma=rec.get("sigma"))


# The report is read by other people too: "typed by you" would name the reader, so a typed value is "entered by hand".
# Short words, so the Source columns never split a word ("manufacturer" / "'s spec" in 0.5.0).
REPORT_SOURCE_WORDS = {**sp.SOURCE_WORDS, "user": "entered by hand", "spec": "maker's spec",
                       "header": "FITS header"}


def source_words(rec) -> str:
    if not sp.is_record(rec):
        return "not known"
    return REPORT_SOURCE_WORDS.get(rec.get("source"), str(rec.get("source")))


# ---- gathering -------------------------------------------------------------------------------------------------------

def site_for(prefs: dict | None, cam_id: str, focal) -> dict:
    """The site the Sky page holds for this setup (lat, lon, elevation_m); {} when it holds none. Never in profiles."""
    f = _f(focal)
    for t in (prefs or {}).get("sky_telescopes") or []:
        if not isinstance(t, dict) or t.get("camera_id") != cam_id:
            continue
        tf = _f(t.get("focal"))
        if f is None or tf is None or abs(tf - f) <= 0.03 * max(tf, f):
            return {k: t.get(k) for k in ("lat", "lon", "elevation_m") if _f(t.get(k)) is not None}
    return {}


def earlier_values(cam_path: str | None, key: str, now_rec) -> list[str]:
    """Other values this camera profile has held for key, from its earlier revisions, when they differ from the value
    now: "1.89 ± 0.03 e-/ADU measured (revision 3, 2026-10-30)". Newest first, one per value and source."""
    if not cam_path:
        return []
    kind, pid = os.path.basename(os.path.dirname(cam_path)), os.path.basename(cam_path)[:-5]
    out, seen = [], set()
    now_key = (sp.value_of(now_rec), (now_rec or {}).get("source")) if sp.is_record(now_rec) else None
    for hp in list(reversed(sp.history_of(kind, pid)))[:20]:          # the newest 20 are plenty (each save makes one)
        try:
            doc = sp.load(hp)
        except sp.ProfileError:
            continue
        rec = (doc.get("values") or {}).get(key)
        if not sp.is_record(rec) or sp.value_of(rec) is None or rec.get("source") in ("estimate", "none"):
            continue                                       # a first guess says nothing about the camera
        k = (round(float(rec["value"]), 6), rec.get("source"))
        if k in seen or (now_key and (round(float(now_key[0]), 6) if now_key[0] is not None else None,
                                      now_key[1]) == k):
            continue
        seen.add(k)
        out.append(f"{rec_words(rec)} {source_words(rec)} (revision {doc.get('revision')}, "
                   f"{str(rec.get('date') or doc.get('updated') or '')[:10]})")
    return out[:3]


_FIND = object()


def _curve_points(curves: dict, name: str):
    pts = curves.get(name)
    if not isinstance(pts, list):
        return None
    out = sorted((float(p[0]), float(p[1])) for p in pts if isinstance(p, (list, tuple)) and len(p) == 2
                 and _f(p[0]) is not None and _f(p[1]) is not None)
    return out if len(out) >= 2 else None


def band_qe(spec: dict | None, band: str, cam: dict | None = None) -> dict | None:
    """The camera's QE averaged over one band, from the maker's QE curve in the spec ("qe_relative": per Bayer color,
    in percent of the peak, times the peak QE; or one curve for a mono sensor). The band's light is taken as a
    top-hat over its catalog band (V: 500–590 nm); a one-shot-color band (TG / TR / TB) reads its own color's curve,
    a clear or filter band on a one-shot-color camera the RGGB mix (R + 2 G + B) / 4. None when the spec has no
    curve, the curve does not reach the band, or a relative curve has no peak QE to scale it.
    Returns {"value", "color", "range_nm", "peak", "words"}."""
    import numpy as np
    curves = (spec or {}).get("qe_relative") or (spec or {}).get("qe") or {}
    if not isinstance(curves, dict):
        return None
    cat = onsky.BAND_CATALOG.get(band, band)
    rng = BAND_RANGE_NM.get(cat)
    if rng is None:
        return None
    color = (onsky.BAND_COLOR.get(band) or (None,))[0]
    names = [k for k in curves if _curve_points(curves, k)]
    if not names:
        return None
    if color in ("R", "G", "B") and color in names:
        use, label = [(color, 1.0)], f"the {color} curve"
    elif all(c in names for c in ("R", "G", "B")):
        use, label = [("R", 0.25), ("G", 0.5), ("B", 0.25)], "the RGGB mix of the curves"
    else:
        one = next((k for k in ("L", "mono", "Mono", "QE", "qe") if k in names), names[0] if len(names) == 1 else None)
        if one is None:
            return None
        use, label = [(one, 1.0)], "the curve"
    lo, hi = rng
    for name, _w in use:                                  # the part of the band every curve used covers
        pts = _curve_points(curves, name)
        lo, hi = max(lo, pts[0][0]), min(hi, pts[-1][0])
    if hi - lo < 0.75 * (rng[1] - rng[0]):
        return None                                       # the curve misses too much of the band
    xs = np.linspace(lo, hi, 181)
    total = np.zeros_like(xs)
    top = 0.0
    for name, w in use:
        pts = _curve_points(curves, name)
        total += w * np.interp(xs, [p[0] for p in pts], [p[1] for p in pts])
        top = max(top, max(p[1] for p in pts))
    avg = float(np.mean(total))
    unit = str(curves.get("unit") or "").lower()
    if "percent" in unit or "%" in unit or top > 1.5:
        avg /= 100.0
    peak = None
    if "relative" in unit or "of the" in unit:
        peak = _f(sp.camera_value(cam, "qe_peak")[0]) if cam else None
        if peak is None:
            peak = _f(((spec or {}).get("points") or {}).get("qe_peak"))
        if peak is None or not 0 < peak <= 1:
            return None
        avg *= peak
    if not 0 < avg <= 1:
        return None
    words = (label + (f" × peak QE {peak:g}" if peak is not None else "")
             + f", averaged over {lo:.0f}–{hi:.0f} nm ({cat} band"
             + (f", the part of {rng[0]:.0f}–{rng[1]:.0f} nm the curve covers" if (lo, hi) != rng else "") + ")")
    return {"value": round(avg, 3), "color": use[0][0] if len(use) == 1 else "RGGB", "range_nm": (lo, hi),
            "peak": peak, "words": words}


def camera_spec(cam: dict | None) -> dict | None:
    """The maker's spec for a camera profile's camera (the user's specs folder first, then the built-in ones)."""
    inst = ((cam or {}).get("camera") or {}).get("instrume")
    return sp.find_spec(inst, (BUILTIN_SPECS,)) if inst else None


def optics_values(doc: dict, cam: dict | None, overrides: dict | None = None, spec=_FIND,
                  bands: list | None = None) -> dict:
    """Each budget input: {key: {"value", "how" ("profile" / "user" / "assumed" / "maker's QE curve" …), "words"}}.
    The camera QE in the band, when not typed, is worked out per band from the maker's QE curve (band_qe): its
    entry then has "by_band" {band: value} and "curve" {band: band_qe(...)}, and "value" is the first band's.
    spec: the camera's spec (found from the camera profile when not given); bands: the bands to work it out for (the
    system profile's, else the camera's one-shot colors, else V)."""
    tel = dict(doc.get("telescope") or {})
    overrides = {k: v for k, v in (overrides or {}).items() if v is not None}
    tel.update(overrides)
    osc = ((cam or {}).get("camera") or {}).get("color") == "OSC" or \
        str(((cam or {}).get("camera") or {}).get("bayer_pattern") or "").upper() in ("RGGB", "BGGR", "GRBG", "GBRG")
    out = {}
    for key, words, default, why in OPTICS:
        v = _f(tel.get(key))
        if v is not None:
            out[key] = {"value": v, "how": "user" if key in overrides else "profile", "words": words}
            continue
        if key == "filter_transmission" and osc:
            out[key] = {"value": 1.0, "how": "one-shot color", "words": words,
                        "why": "the Bayer filter is part of the camera's QE"}
            continue
        if key == "qe_in_band":
            sp_doc = camera_spec(cam) if spec is _FIND else spec
            want = list(bands or (doc.get("bands") or {}).keys()) or (["TG", "TR", "TB"] if osc else ["V"])
            curve = {b: q for b in want for q in (band_qe(sp_doc, b, cam),) if q}
            if curve:
                first = next(b for b in want if b in curve)
                out[key] = {"value": curve[first]["value"], "how": CURVE_HOW, "words": words,
                            "by_band": {b: q["value"] for b, q in curve.items()}, "curve": curve,
                            "why": "; ".join(f"{onsky.BAND_WORDS.get(b, b)} {q['value']:.3g}"
                                             for b, q in curve.items()) + " (averaged over each band)"}
                continue
            qe, rec = sp.camera_value(cam, "qe_peak")
            if _f(qe) is not None:
                out[key] = {"value": float(qe), "how": "assumed", "words": words,
                            "why": f"the camera's peak QE ({source_words(rec)}); no maker's QE curve on file, and "
                                   "the average over a band is lower"}
                continue
            default, why = 0.6, "not known: a typical back-illuminated sensor averaged over a band"
        out[key] = {"value": default, "how": "assumed", "words": words, "why": why}
    return out


def budget(band: str, zp1, k, aperture_mm, optics: dict) -> dict | None:
    """The photon budget for one band: what a perfect telescope of this aperture would collect, what the optics and
    camera should deliver, and what the zero point says it does. None without a zero point or an aperture.
      ideal_zp      mag for 1 e⁻/s above the atmosphere with every photon on the full aperture counted
      measured_zp0  the zero point moved above the atmosphere (ZP at airmass 1 + k)
      measured_eff  10^(-0.4 (ideal − measured_zp0)): the fraction of photons that become electrons
      predicted_eff (1 − obstruction²) × R^mirrors × corrector × filter × QE (× the share of pixels of that color
                    for a one-shot-color band: half for green, a quarter for red or blue)
      predicted_zp1 the zero point at airmass 1 the budget predicts
      shortfall_mag predicted_zp1 − measured ZP (positive: the system is fainter than the budget)"""
    cat = onsky.BAND_CATALOG.get(band, band)
    n0 = PHOTONS_MAG0.get(cat)
    zp1, k, d = _f(zp1), _f(k), _f(aperture_mm)
    if n0 is None or zp1 is None or d is None or d <= 0:
        return None
    k = k if k is not None else onsky.TYPICAL_K.get(cat, 0.15)
    area = math.pi * (d / 2000.0) ** 2
    ideal = 2.5 * math.log10(n0 * area)
    zp0 = zp1 + k
    eff = 10 ** (-0.4 * (ideal - zp0))
    o = {key: optics[key]["value"] for key in optics}
    qe = optics["qe_in_band"]
    qe_v = (qe.get("by_band") or {}).get(band, o["qe_in_band"])
    if qe["how"] == CURVE_HOW and band in (qe.get("curve") or {}):
        qe_note = "maker's QE curve: " + qe["curve"][band]["words"]
    else:
        qe_note = COAT_WORDS.get(qe["how"], qe["how"])
    ob = optics["central_obstruction_fraction"]
    n_m = int(round(o["mirrors"]))
    parts = [("Clear aperture (1 − obstruction²)", 1.0 - o["central_obstruction_fraction"] ** 2,
              f"obstruction {num(ob['value'])}" + (" (assumed)" if ob["how"] == "assumed" else "")),
             (f"Mirrors ({n_m} × {o['mirror_reflectivity']:.2f})" if n_m else "Mirrors (none)",
              o["mirror_reflectivity"] ** n_m, "" if n_m else NO_MIRRORS),
             ("Corrector / reducer", o["corrector_transmission"], ""),
             ("Filter", o["filter_transmission"], ""),
             ("Camera QE", qe_v, qe_note)]
    if band in BAYER_SHARE:
        parts.append((f"Pixels of this color (one-shot color: {BAYER_SHARE[band]:.0%} of them)", BAYER_SHARE[band],
                      ""))
    pred = 1.0
    for _n, v, _w in parts:
        pred *= v
    pred_zp1 = ideal + 2.5 * math.log10(pred) - k if pred > 0 else None
    return {"band": band, "catalog_band": cat, "system": BAND_SYSTEM.get(cat, ""), "photons_mag0": n0,
            "area_m2": area, "ideal_zp": ideal, "k": k, "measured_zp1": zp1, "measured_zp0": zp0,
            "measured_eff": eff, "parts": parts, "predicted_eff": pred, "predicted_zp1": pred_zp1,
            "shortfall_mag": (pred_zp1 - zp1) if pred_zp1 is not None else None,
            "ratio": eff / pred if pred > 0 else None}


def gather(system_path: str, prefs: dict | None = None, event: str = "", optics_override: dict | None = None,
           date_from: str = "", date_to: str = "") -> dict:
    """Everything the report shows, from the saved profiles and the run history. ReportError when the system
    profile cannot be read."""
    try:
        doc = sp.load(system_path)
    except sp.ProfileError as exc:
        raise ReportError(f"The system profile cannot be read: {exc}.") from None
    if doc.get("format") != sp.SYSTEM_FORMAT:
        raise ReportError("That file is not a system profile.")
    sid = doc.get("profile_id") or os.path.basename(system_path)[:-5]
    tel = doc.get("telescope") or {}
    cam_id = doc.get("camera_profile") or ""
    cam_path = sp.profile_path("cameras", cam_id, make=False) if cam_id else None
    cam = None
    if cam_path and os.path.exists(cam_path):
        try:
            cam = sp.load(cam_path)
        except sp.ProfileError:
            cam = None
    if cam is None:
        cam_path = None
    camera = (cam or {}).get("camera") or {}
    mode = (cam or {}).get("mode") or {}
    spec = sp.find_spec(camera.get("instrume"), (BUILTIN_SPECS,)) if camera.get("instrume") else None
    gain_set = mode.get("gain_setting")

    cam_rows = []
    for key, words in CAMERA_ROWS:
        val, rec = sp.camera_value(cam, key)
        if rec is None and key not in ("dark_current_e_per_px_s",):
            continue
        spec_v = sp.spec_at(spec, SPEC_CURVE[key], gain_set) if key in SPEC_CURVE else None
        cam_rows.append({"key": key, "words": words, "rec": rec, "value": rec_words(rec), "source": source_words(rec),
                         "conditions": (rec or {}).get("conditions") or "", "note": (rec or {}).get("note") or "",
                         "spec": spec_v, "earlier": earlier_values(cam_path, key, rec)})
    per_channel = {}
    for ch, vals in ((cam or {}).get("per_channel") or {}).items():
        got = {k: rec_words(r) for k, r in (vals or {}).items() if sp.is_record(r) and sp.value_of(r) is not None}
        if got:
            per_channel[ch] = got

    pts_all = history.series_points("system", sid, date_from=date_from, date_to=date_to)
    bands_doc = doc.get("bands") or {}
    band_names = list(bands_doc) + sorted({p["channel"] for p in pts_all if p.get("channel")
                                           and p["channel"] not in bands_doc})
    bands = []
    for b in band_names:
        recs = bands_doc.get(b) or {}
        rows = []
        for key, words in BAND_ROWS:
            rec = recs.get(key)
            if key == "fwhm_defocus":
                if not isinstance(rec, dict) or sp.is_record(rec):
                    continue                                      # not defocused yet: no row
                for dk, dr in rec.items():
                    rows.append({"key": key, "words": f"{words} ({dk})", "rec": dr, "value": rec_words(dr),
                                 "source": source_words(dr), "conditions": (dr or {}).get("conditions") or ""})
                continue
            if key == "extinction_k" and not sp.is_record(rec):
                cat = onsky.BAND_CATALOG.get(b, b)
                typ = onsky.TYPICAL_K.get(cat)
                rows.append({"key": key, "words": words, "rec": None,
                             "value": fmt(typ, "mag/airmass") if typ is not None else "—",
                             "source": "typical for the band (not measured)" if typ is not None else "not known",
                             "conditions": ""})
                continue
            rows.append({"key": key, "words": words, "rec": rec, "value": rec_words(rec),
                         "source": source_words(rec), "conditions": (rec or {}).get("conditions") or ""})
        pts = [p for p in pts_all if p.get("channel") == b]
        bands.append({"band": b, "words": onsky.BAND_WORDS.get(b, b), "rows": rows, "points": pts, "recs": recs})

    optics = optics_values(doc, cam, optics_override, spec=spec, bands=[b["band"] for b in bands])
    budgets = []
    for bd in bands:
        zp = bd["recs"].get("zero_point")
        k = bd["recs"].get("extinction_k")
        bg = budget(bd["band"], sp.value_of(zp), sp.value_of(k), tel.get("aperture_mm"), optics)
        if bg:
            bg["k_measured"] = sp.value_of(k) is not None
            budgets.append(bg)

    dates = sorted(p["date"] for p in pts_all)
    cam_dates = sorted(str(r["rec"].get("date"))[:10] for r in cam_rows if r["rec"] and r["rec"].get("date"))
    nights = sorted({(p["date"], p.get("target") or "") for p in pts_all if p["kind"] == "onsky"})
    info = {
        "system_id": sid, "system_path": system_path, "doc": doc, "telescope": tel, "camera_id": cam_id,
        "camera_path": cam_path, "cam": cam, "camera": camera, "mode": mode, "spec": spec,
        "spec_name": (spec or {}).get("model") or "", "cam_rows": cam_rows, "per_channel": per_channel,
        "bands": bands, "optics": optics, "budgets": budgets, "points": pts_all,
        "nights": nights, "date_range": (dates[0], dates[-1]) if dates else None,
        "camera_dates": (cam_dates[0], cam_dates[-1]) if cam_dates else None,
        "site": site_for(prefs, cam_id, tel.get("focal_mm")), "event": event.strip() if event else "",
        "plate_scale": sp.value_of(((doc.get("plate_scale") or {}).get("raw_arcsec_per_px"))),
        "revision": doc.get("revision"), "updated": str(doc.get("updated") or "")[:10],
        "date_from": date_from, "date_to": date_to,
    }
    info["caveats"] = caveats(info)
    return info


def caveats(info: dict) -> list[str]:
    """Plain sentences on what to keep in mind when reading the numbers, from the data themselves."""
    out = []
    n_nights = len({d for d, _t in info["nights"]})
    if n_nights == 0:
        out.append("No on-sky night is in the run history for this system yet; the on-sky numbers are the profile's "
                   "own, without the nights behind them.")
    elif n_nights < 3:
        out.append(f"Only {n_nights} night{'s' if n_nights != 1 else ''} so far: the on-sky numbers will firm up as "
                   "more nights are measured.")
    for bd in info["bands"]:
        w = bd["words"]
        recs = bd["recs"]
        if not sp.is_record(recs.get("extinction_k")):
            out.append(f"{w}: the extinction was not measured (a night needs an airmass span of 0.25 or more on a "
                       "photometric night); the typical value for the band was used to move the zero point to "
                       "airmass 1 and above the atmosphere.")
        zp = recs.get("zero_point")
        if sp.is_record(zp) and (zp.get("n") or 0) == 1:
            out.append(f"{w}: the zero point rests on one photometric night.")
        if not sp.is_record(recs.get("scintillation_cy")) or sp.value_of(recs.get("scintillation_cy")) is None:
            out.append(f"{w}: scintillation (C_Y) was not measured: with a large aperture the stars' photon noise "
                       "hides it.")
        sky = recs.get("sky_brightness")
        if sp.is_record(sky) and "none with a dark Moon" in str(sky.get("conditions") or ""):
            out.append(f"{w}: no night had a dark Moon yet, so the sky brightness is a moonlit one.")
        if any(p.get("marginal") for p in bd["points"]):
            out.append(f"{w}: some nights were marginal (the zero point drifted through the night); they are shown "
                       "in orange and left out of the zero point when a clean night exists.")
    typed = [r["words"] for r in info["cam_rows"] if r["rec"] and r["rec"].get("source") == "user"]
    if typed:
        out.append("Camera values typed in rather than measured by FLUX: " + ", ".join(typed) + ". They come from "
                   "the operator or the maker and are used as given.")
    with_budget = {b["band"] for b in info["budgets"]}
    for bd in info["bands"]:
        if sp.value_of(bd["recs"].get("zero_point")) is not None and bd["band"] not in with_budget:
            why = ("the telescope's aperture is not known" if _f(info["telescope"].get("aperture_mm")) is None else
                   "no photon zero point is known for this band")
            out.append(f"{bd['words']}: no photon budget: {why}.")
    assumed = [v["words"] for v in info["optics"].values() if v["how"] == "assumed"]
    if assumed and info["budgets"]:
        out.append("Photon budget inputs assumed, not known: " + ", ".join(assumed).lower() + ". Type the real values "
                   "in the report window for a closer budget.")
    if any(b["band"] in ("TG", "TR", "TB") for b in info["budgets"]):
        out.append("One-shot-color bands (TG, TR, TB) are calibrated against the nearest catalog band (V, R, B); the "
                   "Bayer filters are wider and differently shaped, so their budget is a rough guide.")
    return out


# ---- the PDF ---------------------------------------------------------------------------------------------------------

PAGE_W, PAGE_H = 8.5, 11.0
MARGIN = 0.6
INK = "#1c2b33"
HINT = "#5d6d76"
LINE = "#c9d6de"
HEAD_BG = "#e7eef2"
ACCENT = "#1f5f99"


class Page:
    """One page laid out top to bottom in inches (a figure with no axes but those placed for plots)."""

    def __init__(self, pdf, info, share, version, number, title):
        from matplotlib.figure import Figure
        self.pdf, self.info, self.share, self.version = pdf, info, share, version
        self.fig = Figure(figsize=(PAGE_W, PAGE_H))
        self.y = PAGE_H - MARGIN
        self.fig.text(MARGIN / PAGE_W, 1 - 0.35 / PAGE_H, report_title(info, share), fontsize=8, color=HINT,
                      ha="left", va="top")
        self.fig.text(1 - MARGIN / PAGE_W, 0.3 / PAGE_H, f"Page {number}   ·   Created {history.dtg()} · "
                      f"SHOBS-FLUX {version}", fontsize=7, color=HINT, ha="right", va="bottom")
        self.heading(title, size=15)

    def _t(self, x_in, y_in, text, **kw):
        if self.share:
            text = scrub(text)
        return self.fig.text(x_in / PAGE_W, y_in / PAGE_H, text, **kw)

    def heading(self, text, size=12):
        self.y -= size / 72 * 1.5
        self._t(MARGIN, self.y, text, fontsize=size, color=INK, fontweight="bold", va="baseline")
        self.y -= 0.12

    def para(self, text, size=9, color=INK, width_chars=None, indent=0.0, gap=0.06):
        width_chars = width_chars or int((PAGE_W - 2 * MARGIN - indent) * 72 / (size * 0.52))
        for line in textwrap.wrap(text, width_chars) or [""]:
            self.y -= size / 72 * 1.35
            self._t(MARGIN + indent, self.y, line, fontsize=size, color=color, va="baseline")
        self.y -= gap

    def bullets(self, items, size=9):
        for it in items:
            width = int((PAGE_W - 2 * MARGIN - 0.2) * 72 / (size * 0.52))
            lines = textwrap.wrap(it, width) or [""]
            for i, line in enumerate(lines):
                self.y -= size / 72 * 1.35
                if i == 0:
                    self._t(MARGIN + 0.02, self.y, "•", fontsize=size, color=ACCENT, va="baseline")
                self._t(MARGIN + 0.2, self.y, line, fontsize=size, color=INK, va="baseline")
            self.y -= 0.05

    def table(self, header, rows, widths, size=8.5) -> int:
        """widths in inches; long cells wrap within their column. Returns how many rows fitted on this page (the
        caller carries the rest to a new page: see write_pdf's table())."""
        from matplotlib.patches import Rectangle
        x0 = MARGIN
        total = sum(widths)
        lh = size / 72 * 1.3

        def wrap_row(cells):
            return [textwrap.wrap(str(c), max(4, int((w - 0.1) * 72 / (size * 0.55)))) or [""]
                    for c, w in zip(cells, widths)]
        if any(str(h).strip() for h in header):
            hdr = wrap_row(header)
            hh = max(len(c) for c in hdr) * lh + 0.08
            self.fig.patches.append(Rectangle((x0 / PAGE_W, (self.y - hh) / PAGE_H), total / PAGE_W, hh / PAGE_H,
                                              transform=self.fig.transFigure, facecolor=HEAD_BG, edgecolor="none"))
            x = x0
            for c, w in zip(hdr, widths):
                for i, line in enumerate(c):
                    self._t(x + 0.05, self.y - 0.05 - (i + 0.8) * lh, line, fontsize=size, color=INK,
                            fontweight="bold")
                x += w
            self.y -= hh
        else:
            self.fig.lines.append(_hline(self.fig, x0, x0 + total, self.y))
        for n, row in enumerate(rows):
            cells = wrap_row(row)
            rh = max(len(c) for c in cells) * lh + 0.06
            if self.y - rh < MARGIN + 0.3:
                return n                                  # the caller starts a new page for the rest
            x = x0
            for c, w in zip(cells, widths):
                for i, line in enumerate(c):
                    self._t(x + 0.05, self.y - 0.03 - (i + 0.8) * lh, line, fontsize=size, color=INK)
                x += w
            self.y -= rh
            self.fig.lines.append(_hline(self.fig, x0, x0 + total, self.y))
        self.y -= 0.12
        return len(rows)

    def axes(self, height_in, left=0.75, right=0.35, bottom_pad=0.45, top_pad=0.4):
        """A plot area height_in inches tall, the full width (top_pad leaves room for its title)."""
        b = self.y - height_in
        ax = self.fig.add_axes(((MARGIN + left) / PAGE_W, (b + bottom_pad) / PAGE_H,
                                (PAGE_W - 2 * MARGIN - left - right) / PAGE_W,
                                (height_in - bottom_pad - top_pad) / PAGE_H))
        self.y = b
        return ax

    def grid(self, height_in, ncols=2, nrows=2):
        """ncols × nrows plot areas filling height_in inches."""
        out = []
        cw = (PAGE_W - 2 * MARGIN) / ncols
        rh = height_in / nrows
        top = self.y
        for r in range(nrows):
            for c in range(ncols):
                x = MARGIN + c * cw
                y = top - (r + 1) * rh
                out.append(self.fig.add_axes(((x + 0.6) / PAGE_W, (y + 0.45) / PAGE_H, (cw - 0.8) / PAGE_W,
                                              (rh - 0.85) / PAGE_H)))
        self.y = top - height_in
        return out

    def room(self) -> float:
        return self.y - MARGIN - 0.3

    def done(self):
        self.pdf.savefig(self.fig)


def _hline(fig, x0, x1, y):
    from matplotlib.lines import Line2D
    return Line2D([x0 / PAGE_W, x1 / PAGE_W], [y / PAGE_H, y / PAGE_H], transform=fig.transFigure, color=LINE,
                  linewidth=0.6)


def system_words(info) -> str:
    tel = info["telescope"]
    cam = info["camera"].get("instrume") or info["camera_id"].split("__")[0]
    return f"{tel.get('name') or info['system_id'].split('__')[0]} + {cam}"


def report_title(info, share) -> str:
    return f"SHOBS-FLUX system report: {system_words(info)}" + ("   (shared copy)" if share else "")


def default_name(info, share) -> str:
    safe = "".join(c if c.isalnum() or c in " .+-_()" else "-" for c in system_words(info)).strip()
    return f"System report - {safe} - {datetime.now():%d %b %Y}" + (" (shared)" if share else "") + ".pdf"


def _band_table_rows(bd):
    return [[r["words"], r["value"], r["source"], r["conditions"]] for r in bd["rows"]]


# A file location: a root (C:\ · \\\\server · /home/ · /Users/ · ~/ …), then folders whose names may hold spaces
# ("Panel 1-1"), then a last name that may hold spaces before a part starting with a digit or a capital
# ("…\\Raw\\Panel 1-1", "…\\M045 Pleiades"), or a file name with spaces up to its extension ("Andor spec sheet.pdf");
# otherwise it ends at a space before a lower-case word, or at ; , ( ) ' or ". When unsure it takes too much, never
# too little.
_SEG = r"[^\\/:*?\"<>|;\r\n]"
_LAST = r"[^\s\\/:*?\"<>|;,()']"
PATH_RE = re.compile(r"(?:[A-Za-z]:[\\/]|\\\\|/home/|/Users/|/mnt/|/media/|/Volumes/|/tmp/|~[\\/])"
                     rf"(?:{_SEG}+[\\/])*(?:(?:{_LAST}+ ){{1,5}}{_LAST}*\.[A-Za-z0-9]{{1,5}}(?![A-Za-z0-9])"
                     rf"|{_LAST}+(?: (?=[0-9A-Z]){_LAST}+)*)?")


def scrub(text: str) -> str:
    """A shared copy never shows a file location on the observer's computer (whatever note or name holds one)."""
    return PATH_RE.sub("(a file on the observer's computer)", str(text))


def write_pdf(info: dict, path: str, share: bool = False, version: str = "") -> int:
    """Write the report; returns the number of pages. OSError when the file cannot be written (open elsewhere)."""
    from matplotlib.backends.backend_pdf import PdfPages

    import threading

    import trend_plots
    if threading.current_thread() is threading.main_thread():
        prepare_fonts()                       # in a worker, the window already did (ReportDialog.make)
    tel, cam, mode = info["telescope"], info["camera"], info["mode"]
    pages = [0]

    def new_page(pdf, title):
        pages[0] += 1
        return Page(pdf, info, share, version, pages[0], title)

    def table(pdf, page, title, header, rows, widths):
        """A table that carries on over as many pages as it needs; returns the page it ended on."""
        while True:
            n = page.table(header, rows, widths)
            if n >= len(rows):
                return page
            page.done()
            page = new_page(pdf, f"{title} (continued)")
            rows = rows[n:] if n else rows[1:]             # a row taller than a page is dropped, never looped on

    fd, tmp = tempfile.mkstemp(prefix=".report_", suffix=".pdf.part", dir=os.path.dirname(os.path.abspath(path)))
    os.close(fd)
    try:
        _write(info, tmp, share, version, pages, new_page, table, tel, cam, mode, trend_plots, PdfPages)
        os.replace(tmp, path)                          # fails when the old PDF is open in a viewer (Windows)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
    return pages[0]


def pdf_date(when: datetime) -> str:
    """A PDF date string. An aware time in UTC → 'D:20261102143000Z'; any other aware time with its own offset at
    that moment ('D:20261102093000-05'00''); a naive time is taken as local and given the offset it had then (after
    DST ends that is −05'00', not the −04'00' matplotlib's own writer took from time.altzone)."""
    if when.tzinfo is None:
        when = when.astimezone()
    off = when.utcoffset()
    r = when.strftime("D:%Y%m%d%H%M%S")
    mins = int(round(off.total_seconds() / 60.0))
    if mins == 0:
        return r + "Z"
    sign = "+" if mins > 0 else "-"
    mins = abs(mins)
    return r + f"{sign}{mins // 60:02d}'{mins % 60:02d}'"


class _PdfDate(datetime):
    """A datetime that writes itself into the PDF with pdf_date (matplotlib checks the metadata is a datetime, then
    uses an object's own pdfRepr when it has one)."""

    def pdfRepr(self):
        from matplotlib.backends.backend_pdf import pdfRepr
        return pdfRepr(pdf_date(self))


def pdf_metadata(info: dict, share: bool, version: str, now: datetime | None = None) -> dict:
    """The PDF's document properties. A shared copy's dates are in UTC ('Z': nothing about where it was made); a full
    copy's carry the computer's real UTC offset at that moment."""
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.astimezone()
    when = now.astimezone(timezone.utc) if share else now.astimezone()
    d = _PdfDate(when.year, when.month, when.day, when.hour, when.minute, when.second, tzinfo=when.tzinfo)
    return {"Title": report_title(info, share), "Creator": f"SHOBS-FLUX {version}", "CreationDate": d, "ModDate": d}


def date_span(a: str, b: str) -> str:
    """'2026-09-01' when both ends are the same day, else '2026-09-01 to 2026-10-07'."""
    return a if a == b else f"{a} to {b}"


def optics_value_words(key: str, v: dict) -> str:
    """A budget input's value as the report prints it: '0.4 (assumed)', '0 mirrors (refractor or lens: …)',
    '0.83 (green (TG)) · 0.42 (red (TR))' for a QE worked out per band."""
    if key == "mirrors" and int(round(v["value"])) == 0:
        return NO_MIRRORS
    if key == "qe_in_band" and v.get("by_band") and len(v["by_band"]) > 1:
        return " · ".join(f"{num(q, 3)} {onsky.BAND_WORDS.get(b, b)}" for b, q in v["by_band"].items())
    txt = f"{int(round(v['value']))}" if key == "mirrors" else num(v["value"])
    return txt + (" (assumed)" if v["how"] == "assumed" else "")


def _write(info, tmp, share, version, pages, new_page, table, tel, cam, mode, trend_plots, PdfPages):
    """The pages themselves (write_pdf handles the file)."""
    with PdfPages(tmp, metadata=pdf_metadata(info, share, version)) as pdf:
        # 1 summary
        p = new_page(pdf, "System report")
        p.para(system_words(info), size=13, color=ACCENT)
        made = f"Made {history.dtg()} by SHOBS-FLUX {version} from system profile revision {info['revision']}"
        made += f" ({info['updated']})" if info["updated"] else ""
        p.para(made + ".", color=HINT)
        if share:
            p.para("Shared copy: the site (latitude, longitude, elevation) and the file locations on the observer's "
                   "computer are left out.", color=HINT)
        p.heading("The setup")
        ap, fl = _f(tel.get("aperture_mm")), _f(tel.get("focal_mm"))
        ob = info["optics"]["central_obstruction_fraction"]
        nm = info["optics"]["mirrors"]
        rows = [["Telescope", tel.get("name") or "—"],
                ["Aperture", fmt(ap, "mm")],
                ["Focal length", fmt(fl, "mm") + (f"  (f/{fl / ap:.2f})" if ap and fl else "")],
                ["Central obstruction", f"{num(ob['value'])} of the diameter"
                 + (" (assumed)" if ob["how"] == "assumed" else f" ({ob['value'] * 100:.0f} %)")],
                ["Mirrors", optics_value_words("mirrors", nm)]]
        if tel.get("mount"):
            rows.append(["Mount and optics", tel["mount"]])
        rows += [["Camera", cam.get("instrume") or info["camera_id"]],
                 ["Sensor", " · ".join(str(x) for x in (cam.get("sensor"), cam.get("type"), cam.get("color")) if x)
                  or "—"],
                 ["Pixels", (fmt(_f(cam.get("pixel_um")), "µm") + (f", {cam.get('width_px')} × {cam.get('height_px')}"
                                                                    if cam.get("width_px") else ""))],
                 ["Plate scale", fmt(info["plate_scale"], "″/px")],
                 ["Camera mode", ", ".join(x for x in (
                     f"gain setting {mode['gain_setting']:g}" if _f(mode.get("gain_setting")) is not None else "",
                     f"offset {mode['offset']:g}" if _f(mode.get("offset")) is not None else "",
                     str(mode.get("readout_mode") or ""),
                     f"bin {mode.get('binning')}" if mode.get("binning") else "",
                     f"{mode['set_temp_c']:g} °C" if _f(mode.get("set_temp_c")) is not None else "") if x) or "—"],
                 ["Bands", ", ".join(b["words"] for b in info["bands"]) or "none measured yet"]]
        site = info["site"]
        if not share:
            rows.append(["Site", ", ".join(x for x in (
                f"latitude {site['lat']:.4f}°" if "lat" in site else "",
                f"longitude {site['lon']:.4f}°" if "lon" in site else "",
                f"elevation {num(site['elevation_m'])} m" if "elevation_m" in site else "") if x)
                or "not set on the Sky page"])
        dr = info["date_range"]
        n_n = len({d for d, _t in info["nights"]})
        rows.append(["On-sky data", f"{n_n} night{'s' if n_n != 1 else ''}, {date_span(*dr)}" if dr else
                     "none in the run history yet"])
        if info["camera_dates"]:
            rows.append(["Camera values dated", date_span(*info["camera_dates"])])
        p = table(pdf, p, "System report", ["", ""], rows, [1.9, PAGE_W - 2 * MARGIN - 1.9])
        p.heading("Key numbers")
        key_rows = []
        for r in info["cam_rows"]:
            if r["key"] in ("gain_e_per_adu", "read_noise_e", "dark_current_e_per_px_s", "linear_limit_adu"):
                key_rows.append([r["words"], r["value"], r["source"]])
        for bd in info["bands"]:
            for r in bd["rows"]:
                if r["key"] in ("zero_point", "extinction_k", "fwhm_in_focus", "sky_brightness"):
                    key_rows.append([f"{bd['words']}: {r['words']}", r["value"], r["source"]])
        for bg in info["budgets"]:
            key_rows.append([f"{onsky.BAND_WORDS.get(bg['band'], bg['band'])}: overall throughput",
                             f"{bg['measured_eff'] * 100:.1f} %", f"budget {bg['predicted_eff'] * 100:.1f} %"])
        p = table(pdf, p, "System report", ["Value", "Now", "Source"], key_rows, [3.4, 2.0, PAGE_W - 2 * MARGIN - 5.4])
        p.done()

        # 2 camera
        p = new_page(pdf, "Camera")
        p.para(f"{cam.get('instrume') or info['camera_id']}" + (f", maker's spec: {info['spec_name']}"
                                                                 if info["spec_name"] else ", no maker's spec on file")
               + ". Each value says where it came from; other values the profile has held for it, and the spec at "
                 "this gain setting, are shown beside it.", color=HINT)
        rows = []
        for r in info["cam_rows"]:
            other = list(r["earlier"])
            if _f(r["spec"]) is not None:
                g = _f(mode.get("gain_setting"))
                other.insert(0, f"spec {num(r['spec'])}" + (f" at gain {g:g}" if g is not None else ""))
            rows.append([r["words"], r["value"], r["source"], "; ".join(other) or "—",
                         r["conditions"] + (f" ({r['note']})" if r["note"] else "")])
        p = table(pdf, p, "Camera", ["Value", "Now", "Source", "Other values", "Conditions"], rows,
                  [1.2, 1.45, 1.2, 1.7, 1.75])
        if info["per_channel"]:
            p.heading("Per color")
            keys = [k for k, _w in CAMERA_ROWS if any(k in v for v in info["per_channel"].values())]
            words = dict(CAMERA_ROWS)
            prow = [[words.get(k, k)] + [info["per_channel"][ch].get(k, "—") for ch in info["per_channel"]]
                    for k in keys]
            w0 = 2.2
            wc = (PAGE_W - 2 * MARGIN - w0) / max(1, len(info["per_channel"]))
            p = table(pdf, p, "Camera", ["Value"] + list(info["per_channel"]), prow,
                      [w0] + [wc] * len(info["per_channel"]))
        lin = (info["cam"] or {}).get("linearity") or {}
        if _f(lin.get("max_deviation_pct")) is not None:
            frac = _f(lin.get("measured_to_fraction_of_full_scale"))
            p.para(f"Linearity: within {lin['max_deviation_pct']:g} %"
                   + (f" up to {frac * 100:.0f} % of full scale" if frac is not None else "")
                   + f" ({lin.get('source')}, {lin.get('date')}).")
        p.done()

        # 3 on sky, one page per band
        for bd in info["bands"]:
            p = new_page(pdf, f"On sky: {bd['words']}")
            pts = bd["points"]
            n = len({q['date'] for q in pts})
            p.para(f"{n} night{'s' if n != 1 else ''} in the run history for this band." if n else
                   "No night for this band is in the run history; the numbers are the profile's own.", color=HINT)
            p = table(pdf, p, f"On sky: {bd['words']}", ["Value", "Now", "Source", "From"], _band_table_rows(bd),
                      [2.2, 1.6, 1.4, 2.1])
            if pts and p.room() > 4.0:
                axes = p.grid(min(p.room(), 6.2))
                for ax, (key, unit) in zip(axes, (("bands.zero_point", "mag"), ("bands.fwhm_in_focus", "″"),
                                                   ("bands.sky_brightness", "mag/″²"),
                                                   ("bands.extinction_k", "mag/airmass"))):
                    kp = [q for q in pts if q["key"] == key]
                    if key == "bands.fwhm_in_focus":
                        kp += [dict(q, photometric=False) for q in pts if q["key"] == "bands.fwhm_defocus"]
                    now = bd["recs"].get(key.split(".", 1)[1])
                    trend_plots.draw_trend(None, kp, history.VALUE_WORDS.get(key, key), unit,
                                           now=now if sp.is_record(now) else None, ax=ax, compact=True,
                                           title=history.VALUE_WORDS.get(key, key) +
                                           (" (hollow = defocused)" if key == "bands.fwhm_in_focus" else ""))
            p.done()

        # 4 throughput
        p = new_page(pdf, "Throughput: measured against the photon budget")
        p.para("The zero point says how many electrons a star of a given magnitude gives each second. A perfect "
               "telescope of this aperture, with no obstruction and every photon detected, would give the ideal "
               "zero point; the measured one, moved above the atmosphere with the extinction, is fainter by the "
               "system's overall throughput. The budget multiplies what each part should pass.", color=HINT)
        rows = []
        for key, v in info["optics"].items():
            how = COAT_WORDS.get(v["how"], v["how"])
            if key == "mirrors" and int(round(v["value"])) == 0:
                why = ""
            elif v["how"] == CURVE_HOW:
                why = ": " + "; ".join(f"{onsky.BAND_WORDS.get(b, b)}: {q['words']}" for b, q in v["curve"].items())
            else:
                why = f": {v['why']}" if v.get("why") and v["how"] not in ("profile", "user") else ""
            rows.append([v["words"], optics_value_words(key, v), how + why])
        p = table(pdf, p, "Throughput", ["Budget input", "Value", "From"], rows, [2.5, 1.5, PAGE_W - 2 * MARGIN - 4.0])
        if not info["budgets"]:
            p.para("No band has both a zero point and an aperture yet, so there is no budget to compare.")
        for bg in info["budgets"]:
            n0 = bg["photons_mag0"]
            n0 = round(n0, -max(0, len(str(int(n0))) - 3))         # 3 figures: 8,860,000,000
            rows = [["Photons from a magnitude-0 star", f"{num(n0)} /s/m²",
                     f"catalog band {bg['catalog_band']}, {bg['system']}"],
                    ["Full aperture area", f"{bg['area_m2']:.4g} m²", fmt(tel.get("aperture_mm"), "mm")],
                    ["Ideal zero point (above the atmosphere)", f"{bg['ideal_zp']:.3f}", "every photon counted"],
                    ["Measured zero point at airmass 1", f"{bg['measured_zp1']:.3f}", "system profile"],
                    ["Extinction used", f"{bg['k']:.3f} mag/airmass",
                     "measured" if bg["k_measured"] else "typical for the band (not measured)"],
                    ["Measured zero point above the atmosphere", f"{bg['measured_zp0']:.3f}", "ZP + k"]]
            for name, v, note in bg["parts"]:
                rows.append([f"Budget: {name}", f"{v:.3f}", note])
            rows += [["Budget throughput", f"{bg['predicted_eff'] * 100:.1f} %",
                      f"predicts a zero point of {bg['predicted_zp1']:.3f} at airmass 1" if bg["predicted_zp1"]
                      else ""],
                     ["Measured throughput", f"{bg['measured_eff'] * 100:.1f} %",
                      f"{bg['ratio']:.2f} of the budget" if bg["ratio"] else ""]]
            if bg["shortfall_mag"] is not None:
                sf = bg["shortfall_mag"]
                rows.append(["Measured against the budget", f"{-sf:+.2f} mag",
                             "fainter than the budget" if sf > 0 else "brighter than the budget"])
            if p.room() < 1.5:
                p.done()
                p = new_page(pdf, "Throughput (continued)")
            p.heading(onsky.BAND_WORDS.get(bg["band"], bg["band"]))
            p = table(pdf, p, "Throughput", ["", "Value", "Note"], rows, [2.8, 1.5, PAGE_W - 2 * MARGIN - 4.3])
        if info["budgets"] and p.room() > 2.6:
            ax = p.axes(min(2.8, p.room()))
            import sky_plots
            sky_plots._style(ax)
            names = [onsky.BAND_WORDS.get(b["band"], b["band"]) for b in info["budgets"]]
            import numpy as np
            xs = np.arange(len(names))
            ax.bar(xs - 0.18, [b["predicted_eff"] * 100 for b in info["budgets"]], 0.34, color="#b7c7d1",
                   label="budget")
            ax.bar(xs + 0.18, [b["measured_eff"] * 100 for b in info["budgets"]], 0.34, color=ACCENT,
                   label="measured")
            ax.set_xticks(xs)
            ax.set_xticklabels(names)
            ax.set_ylabel("throughput (%)")
            ax.legend(frameon=False, fontsize=8)
        p.done()

        # 5 over time
        p = new_page(pdf, "Over time")
        ev = info["event"]
        p.para("Every night's zero point and seeing from the run history" +
               (f", split at {ev}: the mean before that date against the mean from it on." if ev else
                ". Give an event date in the report window (a mirror cleaning, a collimation) to compare before and "
                "after."), color=HINT)
        any_plot = False
        for bd in info["bands"]:
            zp = [q for q in bd["points"] if q["key"] == "bands.zero_point"]
            fw = [q for q in bd["points"] if q["key"] == "bands.fwhm_in_focus"]
            if not (zp or fw) or p.room() < 2.6:
                continue
            any_plot = True
            for key, pts_k, unit in (("bands.zero_point", zp, "mag"), ("bands.fwhm_in_focus", fw, "″")):
                if not pts_k or p.room() < 2.6:
                    continue
                p.y -= 0.1
                ax = p.axes(2.8)
                now = bd["recs"].get(key.split(".", 1)[1])
                trend_plots.draw_trend(None, pts_k, history.VALUE_WORDS[key], unit,
                                       now=now if sp.is_record(now) else None, event=ev or None, ax=ax,
                                       title=f"{bd['words']}: {history.VALUE_WORDS[key]}")
                if ev:
                    ba = trend_plots.before_after(pts_k, ev)
                    if ba:
                        e = ba["change_err"]
                        p.para(f"{bd['words']}, {history.VALUE_WORDS[key].lower()}: before {ba['before']['mean']:.3f} "
                               f"({ba['before']['n']} nights), after {ba['after']['mean']:.3f} "
                               f"({ba['after']['n']} nights), change {ba['change']:+.3f}"
                               + (f" ± {e:.3f}" if math.isfinite(e) else "") + f" {unit}.", size=8.5)
                    else:
                        p.para(f"{bd['words']}: no night on one side of {ev} yet, so no before / after.", size=8.5)
        if not any_plot:
            p.para("No night is in the run history yet.")
        p.done()

        # 6 methods and caveats
        p = new_page(pdf, "Methods and caveats")
        p.heading("How the numbers are measured")
        p.bullets([
            "Camera (bench): flat pairs and bias pairs in a central region, per color for a one-shot-color camera. "
            "Gain from the photon transfer curve (variance of the difference of each pair against its level), read "
            "noise from the bias pairs, full well and the safe linear limit from where the curve bends.",
            "Dark current: from the darks' level against exposure and, independently, from their noise; a rate too "
            "small to measure is given as an upper limit.",
            "Zero point: every catalogued field star (APASS DR9, Gaia DR3 synthetic photometry for z′) measured in "
            "electrons per second with the camera's gain; saturated stars, stars over the linear limit, crowded "
            "stars, known variables (VSX) and stars whose catalog value disagrees with the rest are left out. "
            "Magnitude for 1 e⁻/s at airmass 1.",
            "A night is photometric when its zero point holds steady in 10-minute bins; only photometric nights set "
            "the zero point and the extinction, and a night whose zero point drifts steadily (0.03 mag or more) is "
            "marginal and used only when no clean night exists.",
            "Extinction from the zero point against airmass on a photometric night with an airmass span of 0.25 or "
            "more. Seeing is the median FWHM per night, in focus and per defocus setting. Scintillation is Young's "
            "C_Y (Osborn et al. 2015).",
            "Photon budget: the photons a magnitude-0 star sends through the band (Vega for Johnson–Cousins, AB for "
            "Sloan), on the full aperture, times each part's throughput; the measured zero point is moved above the "
            "atmosphere with the extinction before comparing.",
            "Camera QE in the band, unless typed in: the maker's QE curve from the camera's spec (relative curves "
            "times the peak QE), averaged over the band as a top-hat: the catalog band's centre ± half its width "
            "(V 500–590 nm, B 389–487 nm, R 572–710 nm; Sloan bands likewise). A one-shot-color band (TG, TR, TB) "
            "uses its own color's curve, a filter band on a one-shot-color camera the RGGB mix. With no curve on "
            "file the peak QE (or 0.6) is used and marked assumed.",
        ])
        p.heading("Caveats")
        p.bullets(info["caveats"] or ["None found in the data."])
        if not share:
            p.heading("Files")
            p.para(f"System profile: {info['system_path']}", size=7.5, color=HINT)
            if info["camera_path"]:
                p.para(f"Camera profile: {info['camera_path']}", size=7.5, color=HINT)
            p.para(f"Run history: {history.db_path()}", size=7.5, color=HINT)
        p.done()
