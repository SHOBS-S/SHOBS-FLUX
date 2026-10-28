"""The "folder of lights" run: FLUX's Sky page without SHOBS-P. A folder of raw light frames becomes an on-sky log
(format "shobs-onsky-log", exactly as SHOBS-P writes one), which onsky.py then learns from like any other night.

WHAT IS MEASURED. One band and one camera setup per run. A color (OSC) camera is measured in one Bayer color: each
2×2 cell gives one measured pixel holding the SUM of that color's raw pixels in it (green: both greens). A mono camera
is measured on its raw pixels. Nothing is subtracted from the frames: the sky is taken locally from a ring around each
star, so bias, dark and sky all cancel. A star's flux is the raw ADU of its color inside a fixed circular aperture
minus the ring's level (log flag flux_is_adu_sum: one unit = one ADU = gain electrons). Its raw peak is the brightest
single camera pixel of that color in the aperture (ADU as written, bias included), which onsky compares with the
camera's linear limit. The frame's sky is the ring level minus the bias (from the camera profile, else worked out from
the sky noise and the gain: approximate).

WHERE THE STARS ARE. A plate solution in the header (ASIAIR writes one, with SIP terms) is used on the raw pixels. A
measured pixel (xc, yc) sits at raw (2 xc + dx, 2 yc + dy), 0-based, where (dx, dy) is the mean place of the color's
pixels in the cell (green 0.5, 0.5; red and blue their own corner). Without a solution the first frame is matched to
the catalog blind: centre from RA/DEC (or OBJCTRA/OBJCTDEC), pixel scale from XPIXSZ and FOCALLEN, every rotation in 1°
steps and both mirror states, voting on the offsets between the brightest detected and catalogued stars, then a
least-squares fit that must hold at least 8 stars within 2 pixels. Later frames without their own solution are
followed from the first by the shift (and small turn) of its brightest stars.

THE STARS. One catalog query for the field (catalogs.fetch_field: APASS DR9, Gaia DR3 for the rest). Kept: inside the
frame with room for the sky ring, no other star within 2.5 aperture radii brighter than 2.5 mag fainter than itself,
not a known variable (VSX, within 5″), the 60 brightest. The aperture radius is twice the first frame's median FWHM
(at least 3 measured pixels), the ring from 4 to 12 pixels beyond it, fixed for the run.

Per frame: mid-exposure JD (DATE-AVG, else DATE-OBS + EXPTIME/2), airmass (AIRMASS, else from the solved centre and
the site, Kasten & Young 1989), focus, pier side (frames after a change of side are segment "flip"), FWHM (median
Gaussian fit of up to 20 bright stars), sky. A frame whose stars cannot be found, or where fewer than 30 % of them show
(clouds), is left out with a line saying so."""
from __future__ import annotations

import math
import os
import secrets
import warnings
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

import numpy as np

import catalogs
import frames as fr
import image_check as ic
import onsky
import shobs_profile as sp

MAX_STARS = 60                # catalogued stars measured
CROWD_RADII = 2.5             # a brighter neighbour within this many aperture radii makes a star crowded
CROWD_MAG = 2.5               # "brighter" = brighter than the star's magnitude + this
VSX_ARCSEC = 5.0
SKY_GAP, SKY_WIDTH = 4.0, 8.0  # the sky ring starts this far beyond the aperture and is this wide (measured px)
MIN_APERTURE = 3.0
APERTURE_FWHM = 2.5           # aperture radius in FWHMs (each frame's own: ~99 % of a star's light)
CENTROID_PX = 3.0             # a star's centre may move this far from its predicted place
FOUND_FRACTION = 0.30         # a frame where fewer of its stars show is clouded
BLIND_MIN_MATCH = 8
MATCH_TOL_PX = 2.0
N_DETECT_BLIND, N_CAT_BLIND = 40, 80
N_TRACK = 30                  # brightest first-frame stars followed from frame to frame
TRACK_SEARCH_PX = 30.0
FWHM_STARS = 20
LINEAR_GUESS_ADU = onsky.LINEAR_GUESS_ADU
OSC_BANDS = {"G": "TG", "R": "TR", "B": "TB"}


class SkyFolderError(Exception):
    """A folder FLUX cannot make an on-sky log from (the message is a plain sentence)."""


@dataclass
class FolderResult:
    doc: dict | None = None
    lines: list = field(default_factory=list)      # (kind, text): bad / warn / ok / info
    n_frames: int = 0                              # frames in the log
    n_matched: int = 0                             # catalogued stars measured
    band: str = ""
    target: str = ""
    night: str = ""
    n_lights: int = 0                              # light frames found in the folder (before subsampling)
    skipped: list = field(default_factory=list)    # (file name, why)


# ---- headers ---------------------------------------------------------------------------------------------------------

def _str(h: dict, *keys):
    for k in keys:
        v = h.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return None


def has_wcs(h: dict) -> bool:
    c1, c2 = str(h.get("CTYPE1") or "").upper(), str(h.get("CTYPE2") or "").upper()
    return c1.startswith("RA--") and c2.startswith("DEC-")


def wcs_of(h: dict):
    """astropy WCS from a header dict (celestial part), or None."""
    if not has_wcs(h):
        return None
    try:
        from astropy.io import fits
        from astropy.wcs import WCS, FITSFixedWarning
        hdr = fits.Header()
        for k, v in h.items():
            if isinstance(v, (str, int, float, bool)) and len(k) <= 8 and k not in ("COMMENT", "HISTORY"):
                try:
                    hdr[k] = v
                except (ValueError, KeyError):
                    pass
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", FITSFixedWarning)
            warnings.simplefilter("ignore")
            w = WCS(hdr, relax=True, naxis=2)
        return w.celestial if w.has_celestial else None
    except Exception:
        return None


def pointing_of(h: dict):
    """(ra, dec) degrees from RA/DEC (degrees) or OBJCTRA/OBJCTDEC (sexagesimal), or None."""
    ra, dec = fr.hfloat(h, "RA"), fr.hfloat(h, "DEC")
    if ra is not None and dec is not None and 0 <= ra < 360 and -90 <= dec <= 90:
        return ra, dec
    for kr, kd in (("OBJCTRA", "OBJCTDEC"), ("RA", "DEC"), ("RA_OBJ", "DEC_OBJ")):
        r = catalogs.parse_angle(h.get(kr), hours=True)
        d = catalogs.parse_angle(h.get(kd))
        if r is not None and d is not None and -90 <= d <= 90:
            return r % 360.0, d
    return None


def jd_mid_of(h: dict):
    """Mid-exposure JD: DATE-AVG, else DATE-OBS + EXPTIME/2 (UTC), or None."""
    from astropy.time import Time
    for key, add in (("DATE-AVG", 0.0), ("DATE-OBS", (fr.exptime_of(h) or 0.0) / 2.0)):
        v = h.get(key)
        if not isinstance(v, str) or not v.strip():
            continue
        s = v.strip().replace(" ", "T")
        if "T" not in s and h.get("TIME-OBS"):
            s = f"{s}T{str(h.get('TIME-OBS')).strip()}"
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                return float(Time(s, format="isot", scale="utc").jd) + add / 86400.0
        except Exception:
            try:
                return float(Time(s, scale="utc").jd) + add / 86400.0
            except Exception:
                continue
    return None


def airmass_kasten_young(alt_deg):
    """Kasten & Young (1989): X = 1 / (sin h + 0.50572 (h + 6.07995)^−1.6364), h in degrees; NaN below −6°."""
    h = np.asarray(alt_deg, dtype=float)
    with np.errstate(invalid="ignore"):
        x = 1.0 / (np.sin(np.radians(h)) + 0.50572 * (h + 6.07995) ** -1.6364)
    return np.where(h > -6.0, x, np.nan)


def airmass_of(ra, dec, jds, lat, lon, elevation_m=0.0) -> np.ndarray:
    """Airmass of (ra, dec) at each JD from a site (degrees, metres)."""
    from astropy import units as u
    from astropy.coordinates import AltAz, EarthLocation, SkyCoord
    from astropy.time import Time
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        loc = EarthLocation(lat=lat * u.deg, lon=lon * u.deg, height=(elevation_m or 0.0) * u.m)
        t = Time(np.asarray(jds, dtype=float), format="jd", scale="utc")
        alt = SkyCoord(ra * u.deg, dec * u.deg).transform_to(AltAz(obstime=t, location=loc)).alt.deg
    return airmass_kasten_young(alt)


def _calibrated(h: dict, history: list, float_samples: bool) -> bool:
    """Does the header say the frame was calibrated (bias/dark/flat taken out) by other software?"""
    if _str(h, "CALSTAT"):
        return True
    for line in history:
        low = str(line).lower()
        if ("dark" in low or "flat" in low or "bias" in low or "calibrat" in low) and \
                ("subtract" in low or "divid" in low or "calibrat" in low or "applied" in low):
            return True
    return False


def _stacked(h: dict) -> str:
    hdr = {str(k).upper(): v for k, v in h.items()}
    for key in fr.STACK_KEYS:
        n = fr.hfloat(hdr, key)
        if n is not None and n > 1:
            return f"stacked ({key} = {hdr.get(key)})"
    return ""


# ---- scan -----------------------------------------------------------------------------------------------------------

def _lights(folder: str):
    """[(path, header)] of the light frames (headers only), and Counter of the other kinds."""
    lights, others = [], Counter()
    for p in fr.list_frames(folder):
        try:
            h = fr.read_header(p)
        except fr.FrameError:
            others["unreadable"] += 1
            continue
        kind = fr.kind_of(h, p)
        if kind == "unknown" and os.path.basename(p).lower().startswith("light"):
            kind = "light"
        if kind != "light":
            others[kind] += 1
            continue
        if _stacked(h):
            others["stacked"] += 1
            continue
        lights.append((p, h))
    return lights, others


def scan(folder: str) -> dict:
    """A quick look at a folder (headers only) for the Sky page before Run: {"folder", "n_files", "n_lights",
    "others" {kind: count}, "osc" (True / False), "bayer", "filters" {FILTER: count}, "bands" {band: count},
    "band" (mono: the band a run measures; None for color, where the page's color choice decides),
    "n_wcs" (lights with a plate solution), "has_wcs" = "solved" (all of them), "has_pointing", "target", "night",
    "exposures" [s], "camera", "lines" [(kind, text)]}. SkyFolderError when there are no light frames."""
    if not folder or not os.path.isdir(folder):
        raise SkyFolderError("That folder is not there.")
    files = fr.list_frames(folder)
    if not files:
        raise SkyFolderError("There are no FITS or XISF frames in that folder.")
    lights, others = _lights(folder)
    if not lights:
        what = ", ".join(f"{n} {k}" for k, n in others.items()) or "nothing usable"
        raise SkyFolderError(f"There are no light frames in that folder (it holds {what}).")
    heads = [h for _, h in lights]
    bayers = Counter(fr.bayer_of(h) or "mono" for h in heads)
    bayer = bayers.most_common(1)[0][0]
    osc = bayer != "mono"
    filters = Counter(str(h.get("FILTER") or "").strip() or "none" for h in heads)
    bands = Counter("TG" if osc else catalogs.band_of_filter(h.get("FILTER")) for h in heads)
    n_wcs = sum(1 for h in heads if has_wcs(h))
    jds = [j for j in (jd_mid_of(h) for h in heads[:1] + heads[-1:]) if j]
    night = onsky.night_of(min(jds)) if jds else ""
    target = Counter(_str(h, "OBJECT") or "" for h in heads).most_common(1)[0][0] or os.path.basename(
        os.path.normpath(folder))
    exps = sorted({round(e, 3) for e in (fr.exptime_of(h) for h in heads) if e is not None})
    cam = Counter(_str(h, "INSTRUME") or "unknown camera" for h in heads).most_common(1)[0][0]
    pointing = any(pointing_of(h) for h in heads[:3])
    lines = [("info", f"{len(lights)} light frame{'s' if len(lights) != 1 else ''} of {target} from {cam} "
                      f"({'color, ' + bayer if osc else 'mono'})" + (f", night of {night}" if night else "") + ".")]
    if others:
        lines.append(("info", "Not used: " + ", ".join(f"{n} {k}" for k, n in sorted(others.items())) + "."))
    if not osc and len(bands) > 1:
        b, n = bands.most_common(1)[0]
        lines.append(("warn", f"⚠ The frames hold {len(bands)} filters; the run uses the most common one "
                              f"({onsky.BAND_WORDS.get(b, b)}, {n} frames)."))
    if n_wcs == len(heads):
        lines.append(("info", "Every frame has a plate solution in its header."))
    elif not pointing:
        lines.append(("bad", "✖ The frames have no plate solution and no RA/DEC: FLUX cannot tell where they point."))
    else:
        lines.append(("info", "No plate solution in the headers: the stars will be matched to the catalog."))
    return {"folder": folder, "n_files": len(files), "n_lights": len(lights), "others": dict(others), "osc": osc,
            "bayer": None if not osc else bayer, "filters": dict(filters), "bands": dict(bands),
            "band": None if osc else bands.most_common(1)[0][0], "n_wcs": n_wcs, "has_wcs": n_wcs == len(heads),
            "solved": n_wcs == len(heads), "has_pointing": bool(pointing), "target": target, "night": night,
            "exposures": exps, "camera": cam, "lines": lines}


# ---- matching -------------------------------------------------------------------------------------------------------

def _rot(theta_deg: float, parity: int) -> np.ndarray:
    c, s = math.cos(math.radians(theta_deg)), math.sin(math.radians(theta_deg))
    return np.array([[c, -s], [s, c]]) @ np.diag([float(parity), 1.0])


def _vote(dst: np.ndarray, src_t: np.ndarray, bin_px: float):
    """(votes, offset): the most common offset dst − src_t (2-D histogram, two grids half a bin apart)."""
    d = (dst[:, None, :] - src_t[None, :, :]).reshape(-1, 2)
    best = (0, None)
    for shift in (0.0, 0.5):
        k = np.floor(d / bin_px + shift).astype(np.int64)
        key = (k[:, 0] + 1_000_000) * 4_000_000 + (k[:, 1] + 1_000_000)
        u, inv, cnt = np.unique(key, return_inverse=True, return_counts=True)
        i = int(np.argmax(cnt))
        if cnt[i] > best[0]:
            best = (int(cnt[i]), d[inv.ravel() == i].mean(axis=0))
    return best


def _fit(src, dst, parity: int, affine: bool):
    """dst ≈ A src + b: a similarity (turn, scale, shift; mirror fixed by parity) or a full affine."""
    if affine:
        X = np.column_stack([src, np.ones(len(src))])
        P, *_ = np.linalg.lstsq(X, dst, rcond=None)
        return P[:2].T, P[2]
    u, v = src[:, 0] * parity, src[:, 1]
    n = len(src)
    M = np.zeros((2 * n, 4))
    M[:n] = np.column_stack([u, -v, np.ones(n), np.zeros(n)])
    M[n:] = np.column_stack([v, u, np.zeros(n), np.ones(n)])
    rhs = np.concatenate([dst[:, 0], dst[:, 1]])
    (a, c, bx, by), *_ = np.linalg.lstsq(M, rhs, rcond=None)
    return np.array([[a, -c], [c, a]]) @ np.diag([float(parity), 1.0]), np.array([bx, by])


def _pairs(src, dst, A, b, tol, tree):
    pred = src @ A.T + b
    d, j = tree.query(pred, distance_upper_bound=tol)
    ok = np.isfinite(d)
    idx = np.where(ok)[0]
    idx = idx[np.argsort(d[idx])]
    seen, keep = set(), []
    for i in idx:
        if j[i] not in seen:
            seen.add(j[i])
            keep.append(i)
    keep = np.array(sorted(keep), dtype=int)
    return keep, j[keep] if keep.size else np.array([], dtype=int), d[keep] if keep.size else np.array([])


def _refine(src, dst, A, b, tol0: float, final_tol: float = MATCH_TOL_PX, affine_ok: bool = True):
    """Iterative matching: nearest pairs within a tolerance that shrinks to final_tol, refitting each time.
    (A, b, n matched within final_tol, rms) or None."""
    from scipy.spatial import cKDTree
    tree = cKDTree(dst)
    parity = 1 if np.linalg.det(A) >= 0 else -1
    tol = max(tol0, final_tol)
    for it in range(14):
        si, di, _ = _pairs(src, dst, A, b, tol, tree)
        if si.size < 3:
            return None
        affine = affine_ok and tol <= final_tol and si.size >= 10
        A, b = _fit(src[si], dst[di], parity, affine)
        if tol <= final_tol and it >= 8:
            break
        tol = max(final_tol, tol * 0.6)
    si, di, d = _pairs(src, dst, A, b, final_tol, tree)
    if si.size < 3:
        return None
    return A, b, int(si.size), float(np.sqrt(np.mean(d ** 2)))


def match(src, dst, angles, parities=(1, -1), bin_px: float = 8.0, min_match: int = BLIND_MIN_MATCH,
          final_tol: float = MATCH_TOL_PX, n_try: int = 6):
    """Find dst ≈ A src + b by trying every angle and mirror state (offset voting), then refining the best few.
    (A, b, n matched, rms) or None."""
    src, dst = np.asarray(src, float), np.asarray(dst, float)
    if len(src) < 3 or len(dst) < 3:
        return None
    cands = []
    for p in parities:
        for th in angles:
            R = _rot(th, p)
            votes, off = _vote(dst, src @ R.T, bin_px)
            if off is not None:
                cands.append((votes, th, p, R, off))
    cands.sort(key=lambda c: -c[0])
    best = None
    for votes, th, p, R, off in cands[:n_try]:
        if votes < 3:
            break
        r = _refine(src, dst, R, off, tol0=bin_px, final_tol=final_tol)
        if r is not None and r[2] >= min_match and (best is None or r[2] > best[2]):
            best = r
    return best


def _tangent(ra, dec, ra0, dec0):
    """Gnomonic standard coordinates (ξ east, η north), radians."""
    a, d = np.radians(np.asarray(ra, float)), np.radians(np.asarray(dec, float))
    a0, d0 = math.radians(ra0), math.radians(dec0)
    cosc = math.sin(d0) * np.sin(d) + math.cos(d0) * np.cos(d) * np.cos(a - a0)
    xi = np.cos(d) * np.sin(a - a0) / cosc
    eta = (math.cos(d0) * np.sin(d) - math.sin(d0) * np.cos(d) * np.cos(a - a0)) / cosc
    return np.column_stack([xi, eta])


def tan_wcs(ra0, dec0, A_per_rad, b, f: int, off):
    """A TAN WCS on RAW pixels from a solution in measured pixels: x_meas = A q + b, q = (ξ, η) radians, and
    raw = f x_meas + off (f = 2 for color cells, off = the color's place in the cell)."""
    from astropy.wcs import WCS
    Minv = np.linalg.inv(A_per_rad)
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra0, dec0]
    w.wcs.crpix = [f * b[0] + off[0] + 1.0, f * b[1] + off[1] + 1.0]
    w.wcs.cd = np.degrees(Minv) / f
    return w


# ---- the measured image -------------------------------------------------------------------------------------------

class Image:
    """One frame as measured: the image (one value per measured pixel), the raw color planes (for the peak), and
    how measured pixels sit on the raw ones."""

    def __init__(self, data: np.ndarray, pattern: str | None, color: str):
        if pattern in fr.BAYER_PATTERNS:
            h, w = data.shape
            d = data[: h - h % 2, : w - w % 2]
            offs = fr.channel_offsets(pattern)[color]
            self.planes = [d[dy::2, dx::2] for dy, dx in offs]
            self.img = self.planes[0] if len(self.planes) == 1 else (self.planes[0] + self.planes[1])
            self.f = 2
            self.off = (float(np.mean([dx for _, dx in offs])), float(np.mean([dy for dy, _ in offs])))
            self.n_sum = len(offs)
        else:
            self.planes = [data]
            self.img = data
            self.f, self.off, self.n_sum = 1, (0.0, 0.0), 1

    @property
    def shape(self):
        return self.img.shape

    def to_raw(self, xy):
        xy = np.asarray(xy, float)
        return xy * self.f + np.array(self.off)

    def from_raw(self, xy):
        xy = np.asarray(xy, float)
        return (xy - np.array(self.off)) / self.f


def _centroid(img, x, y, r, sky, noise):
    """Light-weighted centre within r of (x, y) (twice); None when there is no light."""
    R = int(math.ceil(r)) + 1
    h, w = img.shape
    x0, y0 = int(round(x)) - R, int(round(y)) - R
    if x0 < 0 or y0 < 0 or x0 + 2 * R + 1 > w or y0 + 2 * R + 1 > h:
        return None
    win = img[y0:y0 + 2 * R + 1, x0:x0 + 2 * R + 1].astype(float) - sky - noise
    yy, xx = np.mgrid[0:win.shape[0], 0:win.shape[1]]
    cx, cy = x - x0, y - y0
    for _ in range(2):
        m = np.hypot(xx - cx, yy - cy) <= r
        wts = np.clip(win, 0, None) * m
        tot = float(wts.sum())
        if tot <= 0:
            return None
        cx, cy = float((wts * xx).sum() / tot), float((wts * yy).sum() / tot)
    return cx + x0, cy + y0


def photometry(im: Image, x: float, y: float, r: float, rin: float, rout: float):
    """{flux, peak_adu, x, y, sky, noise, found} at a predicted place, or None when the ring leaves the frame."""
    R = int(math.ceil(rout)) + 2
    h, w = im.shape
    xi, yi = int(round(x)), int(round(y))
    if xi - R < 0 or yi - R < 0 or xi + R + 1 > w or yi + R + 1 > h:
        return None
    sl = (slice(yi - R, yi + R + 1), slice(xi - R, xi + R + 1))
    win = im.img[sl].astype(float)
    yy, xx = np.mgrid[yi - R: yi + R + 1, xi - R: xi + R + 1]
    rr = np.hypot(xx - x, yy - y)
    ring = win[(rr >= rin) & (rr < rout)]
    sky = ic.clipped_median(ring)
    noise = ic.robust_sigma(ring)
    if not math.isfinite(sky):
        return None
    c = _centroid(im.img, x, y, max(CENTROID_PX, 0.5 * r), sky, noise if math.isfinite(noise) else 0.0)
    cx, cy = x, y
    if c is not None and math.hypot(c[0] - x, c[1] - y) <= CENTROID_PX:
        cx, cy = c
    rr = np.hypot(xx - cx, yy - cy)
    ap = rr <= r
    ring_m = (rr >= rin) & (rr < rout)
    ring = win[ring_m]
    sky = ic.clipped_median(ring)
    noise = ic.robust_sigma(ring)
    npix = int(ap.sum())
    flux = float(win[ap].sum() - npix * sky)
    peak = max(float(p[sl][ap].max()) for p in im.planes)
    err = math.sqrt(max(npix, 1)) * (noise if math.isfinite(noise) else 0.0)
    return {"flux": flux, "peak_adu": peak, "x": cx, "y": cy, "sky": sky, "noise": noise,
            "found": bool(flux > 3.0 * err and flux > 0)}


def _detect(im: Image, n: int):
    """The n brightest star-like peaks, centred: (N, 2) measured pixels."""
    src = ic.detect_sources(im.img, max_sources=n)
    out = []
    for x, y, _ in src:
        c = _centroid(im.img, x, y, 3.0, _local_sky(im.img, x, y), 0.0)
        out.append(c if c is not None and math.hypot(c[0] - x, c[1] - y) < 3 else (x, y))
    return np.array(out, dtype=float).reshape(-1, 2)


def _local_sky(img, x, y, r=12):
    win, _, _ = ic._window(img, x, y, r)
    return float(np.median(win)) if win.size else 0.0


def _first_fwhm(im: Image, det: np.ndarray, lin: float):
    """Median Gaussian FWHM (measured px) of up to 20 bright, unsaturated, isolated first-frame stars."""
    vals = []
    for i, (x, y) in enumerate(det[:60]):
        others = np.delete(det, i, axis=0)
        if others.size and np.min(np.hypot(*(others - (x, y)).T)) < 15:
            continue
        win, x0, y0 = ic._window(im.planes[0], x, y, 3)
        if win.size and max(float(ic._window(p, x, y, 3)[0].max()) for p in im.planes) >= lin:
            continue
        m = ic.measure_star(im.img, x, y)
        if not m:
            continue
        f = ic.gaussian_fwhm(im.img, m["x"], m["y"], m["sky"], half=int(max(6, min(20, 2.5 * m["r95"]))))
        if math.isfinite(f):
            vals.append(f)
        if len(vals) >= FWHM_STARS:
            break
    return float(np.median(vals)) if len(vals) >= 3 else float("nan")


def _track(im: Image, ref: np.ndarray, prev, r: float):
    """(A, b) mapping first-frame measured pixels → this frame's, from its brightest stars, or None."""
    A, b = prev
    pred = ref @ A.T + b
    S = int(max(TRACK_SEARCH_PX, 3 * r))
    h, w = im.shape
    diffs = []
    for x, y in pred:
        xi, yi = int(round(x)), int(round(y))
        x0, x1, y0, y1 = max(0, xi - S), min(w, xi + S + 1), max(0, yi - S), min(h, yi + S + 1)
        if x1 - x0 < 8 or y1 - y0 < 8:
            continue
        win = im.img[y0:y1, x0:x1]
        k = int(np.argmax(win))
        py, px = divmod(k, win.shape[1])
        diffs.append((px + x0 - x, py + y0 - y))
    if len(diffs) >= 4:
        d = np.array(diffs)
        votes, off = _vote(d, np.zeros((1, 2)), 3.0)
        if votes >= max(4, 0.4 * len(diffs)):
            src, dst = [], []
            for (x, y), (rx, ry) in zip(pred + off, ref):
                c = _centroid(im.img, x, y, max(CENTROID_PX, 0.5 * r), _local_sky(im.img, x, y, int(r) + 8), 0.0)
                if c is not None and math.hypot(c[0] - x, c[1] - y) <= CENTROID_PX:
                    src.append((rx, ry))
                    dst.append(c)
            if len(src) >= 4:
                res = _refine(np.array(src), np.array(dst), A, b + off, tol0=4.0, affine_ok=False)
                if res is not None and res[2] >= max(4, 0.5 * len(src)):
                    return res[0], res[1]
    # a big jump (re-pointing, a meridian flip): match the whole frame again, straight or turned half way round
    det = _detect(im, 80)
    if len(det) < 6:
        return None
    ext = float(np.max(np.ptp(ref, axis=0))) if len(ref) else 100.0
    angles = list(range(-3, 4)) + list(range(177, 184))
    res = match(ref, det, angles, parities=(1,), bin_px=max(4.0, 0.02 * ext), min_match=6)
    return (res[0], res[1]) if res is not None else None


# ---- the run ---------------------------------------------------------------------------------------------------------

def _subsample(items: list, n: int) -> list:
    if n <= 0 or len(items) <= n:
        return list(items)
    idx = np.unique(np.round(np.linspace(0, len(items) - 1, n)).astype(int))
    return [items[i] for i in idx]


def _bias_estimate(im: Image, gain, rn_e):
    """The bias level (ADU per raw pixel) a frame implies: its sky level minus the sky the noise says is there."""
    ys, xs = fr.central_region(im.shape, 0.5)
    vals = []
    for p in im.planes:
        c = p[ys, xs]
        sky = ic.clipped_median(c)
        noise = ic.pixel_noise(c)
        if not (math.isfinite(sky) and math.isfinite(noise)):
            continue
        S = max(0.0, gain * noise * noise - (rn_e or 0.0) ** 2 / gain)
        vals.append(min(max(0.0, sky - S), sky))
    return float(min(vals)) if vals else None


def _rnd(v, nd):
    if v is None:
        return None
    v = float(v)
    return round(v, nd) if math.isfinite(v) else None


def run_folder(folder: str, channel: str = "G", step=None, catalog=None, cam_lookup=None, max_frames: int = 150,
               site: dict | None = None, elevation_m: float | None = None, variables=None) -> FolderResult:
    """Measure a folder of light frames and return its on-sky log (FolderResult.doc). channel: the Bayer color for a
    color camera (G, R or B, or the band codes TG, TR, TB; ignored for mono, where FILTER decides).
    catalog(ra, dec, radius_deg, cat_band) → dict as catalogs.fetch_field returns (default:
    that). variables(ra, dec, radius_deg) → [{name, ra, dec}] known variables (default: VSX when the catalog is the
    default one, none otherwise). site {lat, lon, elevation} for airmass when the frames have no AIRMASS or SITELAT.
    SkyFolderError (plain sentence) when nothing usable comes out."""
    res = FolderResult()
    L = res.lines
    say = step or (lambda _t: None)
    cam_lookup = cam_lookup or sp.find_camera_profile
    say("Reading the folder")
    if not folder or not os.path.isdir(folder):
        raise SkyFolderError("That folder is not there.")
    lights, others = _lights(folder)
    if not lights:
        what = ", ".join(f"{n} {k}" for k, n in others.items()) or "no frames at all"
        raise SkyFolderError(f"There are no light frames in that folder (it holds {what}).")
    res.n_lights = len(lights)

    # one setup and one band
    bayer = Counter(fr.bayer_of(h) or "mono" for _, h in lights).most_common(1)[0][0]
    osc = bayer != "mono"
    lights = [(p, h) for p, h in lights if (fr.bayer_of(h) or "mono") == bayer]
    channel = str(channel or "G").strip().upper()
    channel = {"TG": "G", "TR": "R", "TB": "B", "GREEN": "G", "RED": "R", "BLUE": "B"}.get(channel, channel)
    if osc:
        if channel not in OSC_BANDS:
            channel = "G"
        band = OSC_BANDS[channel]
    else:
        bands = Counter(catalogs.band_of_filter(h.get("FILTER")) for _, h in lights)
        band = bands.most_common(1)[0][0]
        if len(bands) > 1:
            L.append(("warn", f"⚠ The folder holds {len(bands)} filters; only the most common, "
                              f"{onsky.BAND_WORDS.get(band, band)} ({bands[band]} frames), is measured."))
        lights = [(p, h) for p, h in lights if catalogs.band_of_filter(h.get("FILTER")) == band]
    cams = Counter(sp.setup_key(h)["camera_id"] for _, h in lights)
    cam_id = cams.most_common(1)[0][0]
    if len(cams) > 1:
        L.append(("warn", f"⚠ The frames come from {len(cams)} camera settings; only the most common "
                          f"({cam_id}, {cams[cam_id]} frames) is measured."))
        lights = [(p, h) for p, h in lights if sp.setup_key(h)["camera_id"] == cam_id]
    lights.sort(key=lambda t: (jd_mid_of(t[1]) or 0.0, t[0]))
    cadence = cadence_of(lights)                  # from EVERY light's header, before any are skipped for speed
    if len(lights) > max_frames:
        L.append(("info", f"{len(lights)} light frames: every {len(lights) / max_frames:.1f}th of them is measured "
                          f"({max_frames})."))
        lights = _subsample(lights, max_frames)
    cat_band = catalogs.catalog_band(band)
    res.band = band
    color = channel if osc else "M"

    h0 = lights[0][1]
    key0 = sp.setup_key(h0)
    try:
        cam = cam_lookup(cam_id)
    except Exception:
        cam = None
    nums = onsky.camera_numbers(cam, color, key0.get("e_per_adu"))
    lin = nums["linear_limit_adu"] or LINEAR_GUESS_ADU
    gain = nums["gain"]
    binning = key0.get("binning") or 1
    pixel_um = (key0.get("pixel_um") or 0.0) * binning or None
    focal = key0.get("focal_mm")
    target = _str(h0, "OBJECT") or os.path.basename(os.path.normpath(folder))
    res.target = target

    # ---- the first frame: where it points, the stars, the aperture
    say(f"Reading frame 1 of {len(lights)}")
    f0 = None
    first_i = 0
    for i, (p, _h) in enumerate(lights[:5]):
        try:
            f0 = fr.read_frame(p)
            first_i = i
            break
        except fr.FrameError as exc:
            res.skipped.append((os.path.basename(p), str(exc)))
    if f0 is None:
        raise SkyFolderError("The first light frames cannot be read.")
    lights = lights[first_i:]
    im0 = Image(f0.data, f0.bayer if osc else None, channel if osc else "L")
    precal = _calibrated(f0.header, f0.history, f0.float_samples)
    if precal:
        L.append(("warn", "⚠ These frames were calibrated by other software (the header says so): their flux scale "
                          "is not known, so they give seeing and sky shape only, no zero point."))
    det0 = _detect(im0, 120)
    if len(det0) < 5:
        raise SkyFolderError("The first frame shows too few stars to measure (clouds, or the wrong folder?).")
    fwhm0 = _first_fwhm(im0, det0, lin)
    if not math.isfinite(fwhm0):
        raise SkyFolderError("The stars in the first frame could not be measured (too few round stars).")
    r_ap = round(max(MIN_APERTURE, APERTURE_FWHM * fwhm0), 2)
    rin, rout = r_ap + SKY_GAP, r_ap + SKY_GAP + SKY_WIDTH
    meas_arcsec = 206.264806 * pixel_um / focal * im0.f if pixel_um and focal else None

    h_px, w_px = im0.shape
    wcs0 = wcs_of(f0.header)
    cat_fn = catalog or catalogs.fetch_field
    half_diag_deg = (0.5 * math.hypot(h_px, w_px) * meas_arcsec / 3600.0) if meas_arcsec else None
    solved_how = ""
    field_doc = None
    if wcs0 is not None:
        cx, cy = im0.to_raw([[(w_px - 1) / 2.0, (h_px - 1) / 2.0]])[0]
        ra_c, dec_c = (float(v) for v in wcs0.wcs_pix2world([[cx, cy]], 0)[0])
        if half_diag_deg is None:
            try:
                from astropy.wcs.utils import proj_plane_pixel_scales
                sc = float(np.mean(proj_plane_pixel_scales(wcs0))) * im0.f
                meas_arcsec = meas_arcsec or sc * 3600.0
                half_diag_deg = 0.5 * math.hypot(h_px, w_px) * sc
            except Exception:
                half_diag_deg = 0.5
        solved_how = "the plate solution in the header"
    else:
        pt = pointing_of(f0.header)
        if pt is None:
            raise SkyFolderError("The frames have no plate solution and no RA/DEC in their header, so FLUX cannot "
                                 "tell where they point. Plate-solve them (or let the capture program do it) first.")
        if not meas_arcsec:
            raise SkyFolderError("The frames have no plate solution, and the header lacks the pixel size (XPIXSZ) or "
                                 "the focal length (FOCALLEN) needed to match the stars to the catalog.")
        ra_c, dec_c = pt
    say("Asking the star catalog")
    radius = half_diag_deg * (1.05 if wcs0 is not None else 1.15)
    try:
        field_doc = cat_fn(ra_c, dec_c, radius, cat_band)
    except catalogs.CatalogError as exc:
        raise SkyFolderError(str(exc)[:1].upper() + str(exc)[1:] + ".") from None
    stars_cat = list(field_doc.get("stars") or [])
    all_cat = list(field_doc.get("all") or stars_cat)
    if field_doc.get("incomplete"):
        L.append(("warn", f"⚠ The catalog answer may be incomplete ({field_doc['incomplete']})."))
    if len(stars_cat) < 3:
        raise SkyFolderError(f"The catalog has too few stars with {onsky.BAND_WORDS.get(cat_band, cat_band)} "
                             "magnitudes in this field.")

    if wcs0 is None:
        say("Matching the stars to the catalog")
        allpos = sorted([s for s in all_cat if s.get("bright") is not None], key=lambda s: s["bright"])
        allpos = allpos[:N_CAT_BLIND] if len(allpos) >= 10 else sorted(stars_cat, key=lambda s: s["mag"])[:N_CAT_BLIND]
        scale_rad = math.radians(meas_arcsec / 3600.0)
        q = _tangent([s["ra"] for s in allpos], [s["dec"] for s in allpos], ra_c, dec_c) / scale_rad
        det = det0[:N_DETECT_BLIND]
        R = 0.5 * math.hypot(h_px, w_px)
        sol = match(q, det, range(0, 360), parities=(1, -1), bin_px=max(4.0, 0.025 * R))
        if sol is None:
            raise SkyFolderError("Could not match the stars to the catalog: check that RA/DEC, XPIXSZ and FOCALLEN in "
                                 "the header are right, or plate-solve the frames first.")
        A, b, n_m, rms = sol
        # all stars now, to pin the solution down
        q_all = _tangent([s["ra"] for s in all_cat], [s["dec"] for s in all_cat], ra_c, dec_c) / scale_rad
        r2 = _refine(q_all, det0, A, b, tol0=MATCH_TOL_PX)
        if r2 is not None and r2[2] >= n_m:
            A, b, n_m, rms = r2
        wcs0 = tan_wcs(ra_c, dec_c, A / scale_rad, b, im0.f, im0.off)
        cd = wcs0.wcs.cd
        mirrored = np.linalg.det(cd) > 0     # a normal (unmirrored) image has east left: det(CD) < 0
        turn = math.degrees(math.atan2((-1.0 if mirrored else 1.0) * cd[1, 0], cd[1, 1]))
        solved_how = (f"matching {n_m} stars to the catalog (within {rms:.1f} px; rotation {turn % 360:.0f}°"
                      f"{', mirrored' if mirrored else ''})")
        cx, cy = im0.to_raw([[(w_px - 1) / 2.0, (h_px - 1) / 2.0]])[0]
        ra_c, dec_c = (float(v) for v in wcs0.wcs_pix2world([[cx, cy]], 0)[0])
    L.append(("info", f"Stars placed from {solved_how}; catalog: {field_doc.get('service') or 'catalog'}."))

    def cat_xy(wcs, ra, dec):
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            raw = wcs.all_world2pix(np.column_stack([ra, dec]), 0)
        return im0.from_raw(raw)

    # choose the stars
    xy_s = cat_xy(wcs0, [s["ra"] for s in stars_cat], [s["dec"] for s in stars_cat])
    margin = rout + 5
    inside = (xy_s[:, 0] >= margin) & (xy_s[:, 0] <= w_px - 1 - margin) & \
             (xy_s[:, 1] >= margin) & (xy_s[:, 1] <= h_px - 1 - margin) & np.all(np.isfinite(xy_s), axis=1)
    xy_a = cat_xy(wcs0, [s["ra"] for s in all_cat], [s["dec"] for s in all_cat])
    br_a = np.array([s.get("bright") if s.get("bright") is not None else np.nan for s in all_cat], dtype=float)
    self_px = 1.5 / (meas_arcsec or 1.0)
    notes_vsx: list = []
    variables_fn = variables
    if variables_fn is None and catalog is None and "cache" in str(field_doc.get("service") or "").lower() \
            and "did not answer" in str(field_doc.get("service") or ""):
        notes_vsx.append("VizieR did not answer, so known variable stars (VSX) could not be left out; the "
                         "night's own check (stars that vary) still catches them.")
    elif variables_fn is None and catalog is None:
        variables_fn = (lambda ra, dec, rad: catalogs.fetch_variables(ra, dec, rad, notes=notes_vsx))
    vsx = []
    if variables_fn is not None:
        try:
            vsx = list(variables_fn(ra_c, dec_c, radius) or [])
        except Exception:
            vsx = []
            notes_vsx.append("The variable-star list (VSX) could not be read, so known variables are not left out.")
    for n in notes_vsx:
        L.append(("info", n))
    xy_v = cat_xy(wcs0, [v["ra"] for v in vsx], [v["dec"] for v in vsx]) if vsx else np.zeros((0, 2))
    vsx_px = VSX_ARCSEC / (meas_arcsec or 1.0)
    chosen, n_crowd, n_var = [], 0, 0
    for i in np.argsort([s["mag"] for s in stars_cat], kind="stable"):
        if not inside[i]:
            continue
        s = stars_cat[i]
        d = np.hypot(*(xy_a - xy_s[i]).T)
        near = (d > self_px) & (d < CROWD_RADII * r_ap) & ~(br_a >= s["mag"] + CROWD_MAG)
        if near.any():
            n_crowd += 1
            continue
        if chosen and np.min(np.hypot(*(xy_s[chosen] - xy_s[i]).T)) < 2 * self_px:
            continue                          # the same star again (two catalogs' entries not merged): once only
        if len(xy_v) and np.min(np.hypot(*(xy_v - xy_s[i]).T)) < vsx_px:
            n_var += 1
            continue
        chosen.append(i)
        if len(chosen) >= MAX_STARS:
            break
    if len(chosen) < 3:
        raise SkyFolderError("Too few uncrowded catalogued stars fall inside the frame to measure.")
    sel = [stars_cat[i] for i in chosen]
    names = [s["name"] for s in sel]
    sel_ra = np.array([s["ra"] for s in sel])
    sel_dec = np.array([s["dec"] for s in sel])
    ref_xy = xy_s[chosen]
    words = [f"{len(sel)} catalogued stars measured"]
    if n_crowd:
        words.append(f"{n_crowd} left out as crowded")
    if n_var:
        words.append(f"{n_var} known variable{'s' if n_var != 1 else ''} left out")
    L.append(("info", "; ".join(words) + f" (aperture {r_ap:.1f} px, sky ring {rin:.1f}–{rout:.1f} px)."))

    # bias, for the sky level
    bias = None
    if cam:
        bv, _ = sp.camera_value(cam, "bias_level_adu")
        bias = onsky._f(bv)
    if bias is None:
        if gain:
            bias = _bias_estimate(im0, gain, nums["read_noise_e"])
        L.append(("info", "Sky brightness is approximate: there is no camera profile with a measured bias level, so "
                          "the bias was worked out from the sky noise and the gain."))
    bias_px = (bias or 0.0) * im0.n_sum

    # ---- every frame
    ref_det = det0[:N_TRACK]
    T = (np.eye(2), np.zeros(2))
    pier0 = _str(f0.header, "PIERSIDE")
    rot0 = fr.hfloat(f0.header, "ROTATOR", "ROTATANG", "ROTANGLE")
    out_frames = []
    n_lights = len(lights)
    for k, (p, hk) in enumerate(lights):
        say(f"Reading frame {k + 1} of {n_lights}")
        if k == 0:
            f = f0
            im = im0
        else:
            try:
                f = fr.read_frame(p)
            except fr.FrameError as exc:
                res.skipped.append((os.path.basename(p), str(exc)))
                continue
            if f.data.shape != f0.data.shape:
                res.skipped.append((f.name, "a different frame size"))
                continue
            im = Image(f.data, f.bayer if osc else None, channel if osc else "L")
        why = _stacked(f.header)
        if why:
            res.skipped.append((f.name, why))
            continue
        cal = _calibrated(f.header, f.history, f.float_samples)
        if not cal:
            proc, why = fr.is_processed(f)
            if proc:
                res.skipped.append((f.name, why))
                continue
        exptime = f.exptime
        jd = jd_mid_of(f.header)
        if not exptime or not jd:
            res.skipped.append((f.name, "no exposure time or date in the header"))
            continue
        flipped = False
        meas = None
        tries = []
        wk = wcs0 if k == 0 else (wcs_of(f.header) if has_wcs(f.header) else None)
        if wk is not None:
            tries.append("wcs")
        if k > 0:
            tries.append("track")
        for how in tries:
            if how == "wcs":
                xy = cat_xy(wk, sel_ra, sel_dec)
            else:
                Tk = _track(im, ref_det, T, r_ap)
                if Tk is None:
                    continue
                T = Tk
                xy = ref_xy @ Tk[0].T + Tk[1]
                flipped = abs(math.degrees(math.atan2(Tk[0][1, 0], Tk[0][0, 0]))) > 90
            m = {}
            for name, (x, y) in zip(names, xy):
                if not (math.isfinite(x) and math.isfinite(y)):
                    continue
                ph = photometry(im, float(x), float(y), r_ap, rin, rout)
                if ph is not None:
                    m[name] = ph
            n_found = sum(1 for v in m.values() if v["found"])
            if m and n_found >= max(3, FOUND_FRACTION * len(m)):
                meas = m
                break
            meas = None
        if meas is None:
            why = "its stars could not be found (clouds, or the telescope moved)" if tries else "no stars"
            res.skipped.append((f.name, why))
            continue
        # FWHM: bright, unsaturated stars
        fw = []
        for name in names:
            v = meas.get(name)
            if v is None or not v["found"] or v["peak_adu"] >= lin:
                continue
            g = ic.gaussian_fwhm(im.img, v["x"], v["y"], v["sky"], half=int(max(6, min(20, 2.5 * fwhm0))))
            if math.isfinite(g):
                fw.append(g)
            if len(fw) >= FWHM_STARS:
                break
        # the aperture follows this frame's seeing (a fixed one loses light as the seeing grows through the night,
        # which would read as extinction): measured again when the frame's stars are larger than the first's
        r_k, rin_k, rout_k = r_ap, rin, rout
        if fw:
            want = round(max(MIN_APERTURE, APERTURE_FWHM * float(np.median(fw))), 2)
            if want > r_ap * 1.15:
                r_k = want
                rin_k, rout_k = r_k + SKY_GAP, r_k + SKY_GAP + SKY_WIDTH
                again = {}
                for name, v in meas.items():
                    ph = photometry(im, float(v["x"]), float(v["y"]), r_k, rin_k, rout_k)
                    if ph is not None:
                        again[name] = ph
                if again:
                    meas = again
        skies = [v["sky"] for v in meas.values() if math.isfinite(v["sky"])]
        sky = (float(np.median(skies)) - bias_px) if skies else None
        pier = _str(f.header, "PIERSIDE")
        rot = fr.hfloat(f.header, "ROTATOR", "ROTATANG", "ROTANGLE")
        seg = ""
        if (pier0 and pier and pier.lower() != pier0.lower()) or flipped or \
                (rot0 is not None and rot is not None and abs(((rot - rot0) + 180) % 360 - 180) > 150):
            seg = "flip"
        stars_k = {}
        for name, v in meas.items():
            fl = v["flux"]
            stars_k[name] = {"flux": _rnd(fl, 3),
                             "inst_mag": _rnd(-2.5 * math.log10(fl / exptime), 5) if fl > 0 else None,
                             "peak_adu": _rnd(v["peak_adu"], 0), "x": _rnd(v["x"], 2), "y": _rnd(v["y"], 2)}
        out_frames.append({"file": f.name, "jd_mid": round(float(jd), 6), "airmass": fr.hfloat(f.header, "AIRMASS"),
                           "exptime_s": float(exptime), "focuspos": fr.hfloat(f.header, "FOCUSPOS", "FOCPOS"),
                           "pierside": pier, "ccd_temp_c": fr.hfloat(f.header, "CCD-TEMP"), "segment": seg,
                           "fwhm_px": _rnd(np.median(fw), 3) if fw else None, "sky": _rnd(sky, 3), "flag": "",
                           "aperture_px": r_k,
                           "precalibrated": bool(cal), "stars": stars_k,
                           "_site": (fr.hfloat(f.header, "SITELAT", "LAT-OBS", "OBSLAT"),
                                     fr.hfloat(f.header, "SITELONG", "LONG-OBS", "OBSLONG"),
                                     fr.hfloat(f.header, "SITEELEV", "ALT-OBS", "OBSALT"))})
    say("Writing the log")
    if not out_frames:
        raise SkyFolderError("None of the frames could be measured (their stars could not be found).")

    # airmass where the header gives none
    need = [i for i, fm in enumerate(out_frames) if fm["airmass"] is None]
    if need:
        lat = lon = elev = None
        for fm in out_frames:
            la, lo, el = fm["_site"]
            if la is not None and lo is not None:
                lat, lon, elev = la, lo, el
                break
        if lat is None and site:
            lat, lon = onsky._f(site.get("lat")), onsky._f(site.get("lon"))
            elev = onsky._f(site.get("elevation"))
        if elevation_m is not None:
            elev = elevation_m
        if lat is not None and lon is not None:
            try:
                xs = airmass_of(ra_c, dec_c, [out_frames[i]["jd_mid"] for i in need], lat, lon, elev or 0.0)
                for i, x in zip(need, xs):
                    out_frames[i]["airmass"] = _rnd(x, 4)
                L.append(("info", f"Airmass worked out from the field's position and the site "
                                  f"({lat:.2f}°, {lon:.2f}°)."))
            except Exception as exc:
                L.append(("warn", f"⚠ The airmass could not be worked out ({exc})."))
        else:
            L.append(("warn", "⚠ The frames have no AIRMASS and no site position (SITELAT/SITELONG): type the site "
                              "on the Sky page so the airmass, and so the extinction, can be worked out."))
    for fm in out_frames:
        fm.pop("_site", None)

    if res.skipped:
        why = Counter(w for _, w in res.skipped)
        L.append(("warn" if len(res.skipped) > 0.2 * n_lights else "info",
                  ("⚠ " if len(res.skipped) > 0.2 * n_lights else "") +
                  f"{len(res.skipped)} frame{'s' if len(res.skipped) != 1 else ''} left out: " +
                  "; ".join(f"{n} × {w}" for w, n in why.most_common()) + "."))
    if others:
        L.append(("info", "Not light frames, so not used: " +
                  ", ".join(f"{n} {k}" for k, n in sorted(others.items())) + "."))

    import settings
    night = onsky.night_of(min(fm["jd_mid"] for fm in out_frames))
    run_id = f"flux-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{secrets.token_hex(2)}"
    setup = {"instrume": key0.get("instrume"), "gain_setting": key0.get("gain_setting"), "offset": key0.get("offset"),
             "camera_binning": binning, "camera_id": cam_id, "focal_header_mm": focal,
             "telescop_header": key0.get("telescop_header"), "pixel_um": pixel_um,
             "e_per_adu_header": key0.get("e_per_adu"), "bayer_pattern": bayer if osc else None}
    measurement = {"debayer_mode": None, "software_binning": 1, "raw_px_per_px": im0.f,
                   "aperture_radius_px": r_ap, "sky_inner_px": round(rin, 2), "sky_outer_px": round(rout, 2),
                   "flux_units": "raw ADU summed over the band's pixels in the aperture, sky subtracted "
                                 "(SHOBS-FLUX folder run)",
                   "flux_is_adu_sum": True, "color": color, "bias_adu": _rnd(bias, 2)}
    stars_list = [{"name": target, "role": "target", "catalog_mag": None}] + \
        [{"name": s["name"], "role": "field", "catalog_mag": _rnd(s["mag"], 4), "catalog": s.get("catalog"),
          "ra": _rnd(s["ra"], 7), "dec": _rnd(s["dec"], 7)} for s in sel]
    doc = {"format": sp.ONSKY_FORMAT, "format_version": 1, "run_id": run_id,
           "written_by": f"SHOBS-FLUX {settings.APP_VERSION}", "written": sp.now_iso(), "night": night,
           "target": target, "mode": "folder", "folder": os.path.abspath(folder), "setup": setup, "band": band,
           "catalog_band": cat_band, "catalog_service": field_doc.get("service") or "",
           "field_centre": {"ra": _rnd(ra_c, 6), "dec": _rnd(dec_c, 6)}, "measurement": measurement,
           "precalibrated": bool(precal), "stars": stars_list, "frames": out_frames}
    if cadence:
        doc["cadence"] = cadence
    res.doc = doc
    res.n_frames = len(out_frames)
    res.n_matched = len(sel)
    res.night = night
    L.insert(0, ("ok", f"✔ {len(out_frames)} of {n_lights} frames measured in {onsky.BAND_WORDS.get(band, band)}, "
                       f"{len(sel)} catalogued stars, night of {night}."))
    return res


def cadence_of(lights: list) -> dict | None:
    """Time from one light to the next, from all the folder's headers (frames measured may be every Nth):
    {"interval_s", "exptime_s", "n"} for the most common exposure, or None. Gaps over 2.5 × the median left out."""
    rows = [(jd_mid_of(h), fr.exptime_of(h)) for _p, h in lights]
    rows = sorted((j, e) for j, e in rows if j and e)
    if len(rows) < 6:
        return None
    exp = Counter(round(e, 3) for _j, e in rows).most_common(1)[0][0]
    dt = [(b[0] - a[0]) * 86400.0 for a, b in zip(rows, rows[1:])
          if round(a[1], 3) == exp and round(b[1], 3) == exp and b[0] > a[0]]
    if len(dt) < 5:
        return None
    med = float(np.median(dt))
    dt = [d for d in dt if d <= 2.5 * med]
    if len(dt) < 5:
        return None
    return {"interval_s": round(float(np.median(dt)), 3), "exptime_s": float(exp), "n": len(dt)}


def save_log(doc: dict) -> str:
    """Write the log into the SHOBS data folder's onsky/ folder (where SHOBS-P puts its own); the path."""
    path = os.path.join(sp.onsky_dir(), f"{doc['night']}__{sp.slug(doc['target'])}__{doc['run_id']}.json")
    sp.write_json(path, doc)
    return path
