"""On-sky characterization (milestone 4): what the telescope + camera + sky do, learned from ordinary nights.

INPUT. Every night comes in as an ON-SKY LOG (format "shobs-onsky-log", one JSON file per reduction in the SHOBS data
folder's onsky/ folder). SHOBS-P 2.2.19.1 and later write one after every reduction; FLUX's own "folder of lights" run
(sky_folder.py) writes the same kind of file, so both paths are learned from in exactly the same way. Each log is
turned into a Dataset (load_dataset): fluxes become electrons per second with the camera profile's measured gain for
the band's color (header EGAIN when there is no profile), pixels become arcseconds.

ONE NIGHT (analyse). Per frame and catalogued star: zero point = catalog mag + 2.5 log10(e⁻/s). FLUX ignores the
reduction's comp/check roles and uses every catalogued star (the target is always left out: it is usually the
variable). Stars are set aside, with the reason, when they are saturated (raw peak at or above the linear limit),
when their catalog magnitude disagrees with the rest (offset from the ensemble beyond 0.15 mag and 4 robust sigma: a
wrong catalog value or a blend) or when they vary (their own scatter more than three times the ensemble's). The
frames' zero points are fitted against airmass (3-sigma clipping): ZP(X) = ZP1 − k (X − 1).
  Photometric?  The residuals binned in 10-minute bins must stay within 0.025 mag rms (thin cloud or haze moves
                them; photon noise and scintillation average out in the bins).
  Extinction k  only from a photometric night with an airmass span of at least 0.25 and a physical slope (0–1).
                Otherwise ZP1 uses the profile's measured k, or a typical value for the band (labelled).
  Seeing        FWHM in arcsec per frame (median for the night). A night whose median is over 7″, or more than
                2.2× the best in-focus run that night, is DEFOCUSED; when the same night has an in-focus run, the
                defocus is keyed by the focuser offset ("+125 steps").
  Sky           mag/arcsec² from each frame's sky level and its own zero point (median), with the Moon's
                illuminated fraction at mid-night.
  Scintillation Young's C_Y (modified Young approximation, Osborn et al. 2015, as SHOBS-P uses it) from the
                frame-to-frame scatter of each star against the others, after taking out photon, sky and read noise.
                Needs the telescope aperture and the site elevation (typed on the Sky page; the site is never stored
                in a profile).
  Cadence       median time between frames minus the exposure = the per-frame overhead (gaps and flips left out).

LEARNING (combine). Only the newest log of each night + target + band counts (a re-run replaces the first). Nights
are combined per band: zero point and extinction from photometric nights only, each night once, 3-sigma clipped
across nights (a night that disagrees is set aside, not averaged in); seeing in focus and per defocus key; sky from
the darkest (Moon under 25 % lit) nights when there are any; C_Y and the overhead as medians. The result is written
into the system profile (format v1, shobs_profile.py) by apply_to_profile, after changes_for shows what changes.

Standard library + numpy (+ astropy for the Moon, optional)."""
from __future__ import annotations

import copy
import math
import os
from dataclasses import dataclass, field

import numpy as np

import shobs_profile as sp

# ---- bands ---------------------------------------------------------------------------------------------------------

# the catalog band each observing band is calibrated against (SHOBS-P's codes; OSC channels TG/TR/TB, clear CV)
BAND_CATALOG = {"TG": "V", "V": "V", "CV": "V", "TB": "B", "B": "B", "TR": "R", "R": "R", "I": "I",
                "SG": "SG", "SR": "SR", "SI": "SI", "SZ": "SZ", "SU": "SU"}
BAND_WORDS = {"TG": "green (TG)", "TB": "blue (TB)", "TR": "red (TR)", "CV": "clear (CV)", "V": "V", "B": "B",
              "R": "R", "I": "I", "SG": "g′", "SR": "r′", "SI": "i′", "SZ": "z′", "SU": "u′"}
# typical extinction (mag/airmass) at a low site, used only until the night measures its own (labelled "typical")
TYPICAL_K = {"B": 0.30, "V": 0.20, "R": 0.13, "I": 0.08, "SU": 0.55, "SG": 0.20, "SR": 0.12, "SI": 0.08, "SZ": 0.06}
# the Bayer color a band is measured in, and how many raw pixels of that color one 2×2 cell holds
BAND_COLOR = {"TG": ("G", 2), "TR": ("R", 1), "TB": ("B", 1), "CV": ("L", 4)}
DEBAYER_COLOR = {"green": ("G", 2), "red": ("R", 1), "blue": ("B", 1), "luminance": ("L", 4)}

# ---- the rules (numbers named once, quoted in the findings) ----------------------------------------------------------
SAT_FRACTION = 0.2            # a star saturated in more than this fraction of frames is set aside
CAT_OFFSET_MAG = 0.15         # catalog disagreement: beyond this AND beyond CAT_OFFSET_SIGMA robust sigma
CAT_OFFSET_SIGMA = 4.0
VARY_FACTOR = 3.0             # a star whose own scatter is this many times the ensemble's varies
MIN_STARS = 2                 # catalogued stars a night needs for a zero point
MIN_FRAMES = 10               # frames a night needs
BIN_DAYS = 10.0 / 1440.0      # 10-minute bins for the photometric test
PHOT_BIN_RMS = 0.025          # binned residual rms (mag) a photometric night stays within
K_MIN_SPAN = 0.25             # airmass span a night needs to measure extinction
K_RANGE = (0.0, 1.0)          # a physical extinction coefficient
DEFOCUS_ARCSEC = 7.0          # a night's median FWHM above this is defocused
DEFOCUS_RATIO = 2.2           # or this many times the best in-focus run the same night
DARK_MOON = 0.25              # Moon illuminated fraction below which a night counts as dark for the sky
SCALE_HEIGHT_M = 8000.0       # Osborn et al. 2015
SCINT_MIN_FRACTION = 0.3      # a star informs C_Y only when scintillation is at least this share of its variance
CLIP_SIGMA = 3.0              # across nights (the profile's learning rule)
ZP_FLOOR = 0.01               # mag: the least uncertainty one night's zero point is given
MAG = 2.5 / math.log(10.0)    # 1.0857: relative flux error → magnitudes


class OnSkyError(Exception):
    """A log or folder FLUX cannot learn from (the message is a plain sentence)."""


# ---- one night as FLUX sees it --------------------------------------------------------------------------------------

@dataclass
class Dataset:
    path: str
    source: str                         # "SHOBS-P" (its on-sky log) or "SHOBS-FLUX" (a folder of lights)
    written_by: str
    written: str
    run_id: str
    night: str                          # the night's date (UTC date of JD − 12 h), YYYY-MM-DD
    target: str
    band: str                           # observing band code (TG, SI…)
    catalog_band: str                   # what the catalog magnitudes are (V, SI…)
    setup: dict
    camera_id: str
    color: str                          # R / G / B / L (luminance) / M (mono)
    gain: float | None                  # e⁻/ADU used
    gain_source: str                    # "measured" (camera profile) / "header" (EGAIN) / "none"
    e_per_unit: float | None            # electrons per unit of the log's flux and sky
    raw_px_per_px: int                  # raw camera pixels along one side of the measured pixel
    px_arcsec: float | None             # one measured pixel, arcsec
    linear_limit_adu: float | None
    read_noise_e: float | None
    aperture_px: float | None
    names: list                         # catalogued field stars used (target left out)
    cat: np.ndarray                     # their catalog magnitudes
    jd: np.ndarray
    airmass: np.ndarray
    exptime: np.ndarray
    focuspos: np.ndarray
    fwhm_px: np.ndarray
    sky: np.ndarray                     # per frame, log units per measured pixel
    flux: np.ndarray                    # frames × stars, log units (NaN = not measured)
    peak: np.ndarray                    # frames × stars, raw ADU
    n_frames_in_log: int = 0
    precalibrated: bool = False
    notes: list = field(default_factory=list)

    @property
    def key(self) -> tuple:
        return (self.night, sp.slug(self.target), self.band, self.camera_id)

    @property
    def label(self) -> str:
        return f"{self.night} {self.target} ({BAND_WORDS.get(self.band, self.band)})"


@dataclass
class NightResult:
    ds: Dataset
    usable: bool = False
    why_not: str = ""                   # when not usable at all
    lines: list = field(default_factory=list)          # (kind, text): bad / warn / ok / info
    stars_used: list = field(default_factory=list)
    stars_out: dict = field(default_factory=dict)      # name → reason
    n_frames: int = 0
    zp_frame: np.ndarray | None = None                 # per frame (NaN = left out)
    zp_mean: float = float("nan")                      # at x_mean
    x_mean: float = float("nan")
    x_span: float = 0.0
    zp1: float = float("nan")                          # at airmass 1
    zp1_sigma: float = float("nan")
    k: float = float("nan")                            # measured this night (NaN = not)
    k_sigma: float = float("nan")
    k_used: float = float("nan")
    k_used_source: str = ""                            # "this night" / "profile" / "typical" / "all nights"
    rms: float = float("nan")                          # per-frame residual rms
    bin_rms: float = float("nan")                      # 10-minute binned residual rms
    photometric: bool = False
    fwhm_arcsec: float = float("nan")
    fwhm_sigma: float = float("nan")
    focus: float = float("nan")                        # median FOCUSPOS
    defocused: bool = False
    defocus_key: str = ""
    sky_mag: float = float("nan")                      # mag/arcsec²
    moon_illum: float = float("nan")
    cy: float = float("nan")
    cy_n: int = 0
    overhead_s: float = float("nan")
    overhead_n: int = 0
    exptime_s: float = float("nan")

    @property
    def worst(self) -> str:
        for kind in ("bad", "warn"):
            for k, t in self.lines:
                if k == kind:
                    return t
        return ""


# ---- reading ----------------------------------------------------------------------------------------------------------

def _f(v):
    if isinstance(v, bool) or v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def log_files(folder: str | None = None) -> list[str]:
    d = folder or sp.onsky_dir(make=False)
    if not os.path.isdir(d):
        return []
    return sorted(os.path.join(d, n) for n in os.listdir(d) if n.endswith(".json") and not n.startswith("."))


def camera_numbers(cam_profile: dict | None, color: str, header_gain) -> dict:
    """gain, read noise and linear limit for one color: the camera profile's own for that color when the bench
    measured it, else its camera-wide value, else the header's EGAIN (gain only)."""
    out = {"gain": None, "gain_source": "none", "read_noise_e": None, "linear_limit_adu": None}
    ch = color if color in ("R", "G", "B") else None
    if cam_profile:
        g, rec = sp.camera_value(cam_profile, "gain_e_per_adu", ch)
        if g is not None and g > 0:
            out["gain"], out["gain_source"] = float(g), rec.get("source", "measured") if rec else "measured"
        rn, _ = sp.camera_value(cam_profile, "read_noise_e", ch)
        out["read_noise_e"] = _f(rn)
        lin, _ = sp.camera_value(cam_profile, "linear_limit_adu", ch)
        out["linear_limit_adu"] = _f(lin)
    if out["gain"] is None and _f(header_gain):
        out["gain"], out["gain_source"] = float(header_gain), "header"
    return out


def load_dataset(path_or_doc, cam_lookup=None, path: str = "") -> Dataset:
    """One on-sky log as a Dataset. cam_lookup(camera_id) → camera profile or None (default: the saved one).
    OnSkyError when it cannot be learned from."""
    if isinstance(path_or_doc, dict):
        doc = path_or_doc
    else:
        path = path_or_doc
        try:
            doc = sp.load(path)
        except sp.ProfileError as exc:
            raise OnSkyError(f"it cannot be read: {exc}") from None
    if doc.get("format") != sp.ONSKY_FORMAT:
        raise OnSkyError("it is not an on-sky log")
    cam_lookup = cam_lookup or sp.find_camera_profile
    setup = dict(doc.get("setup") or {})
    meas = doc.get("measurement") or {}
    band = str(doc.get("band") or "").upper() or "CV"
    cat_band = str(doc.get("catalog_band") or BAND_CATALOG.get(band, "V")).upper()
    pattern = setup.get("bayer_pattern")
    mono = not pattern or str(pattern).upper() in ("MONO", "NONE")
    if mono:
        color, n_color = "M", 1
    else:
        color, n_color = DEBAYER_COLOR.get(str(meas.get("debayer_mode") or "").lower(),
                                           BAND_COLOR.get(band, ("L", 4)))
    cam_id = setup.get("camera_id") or sp.camera_id(setup.get("instrume") or "unknown camera",
                                                    setup.get("gain_setting"), setup.get("offset"),
                                                    setup.get("camera_binning") or 1)
    try:
        cam = cam_lookup(cam_id)
    except Exception:
        cam = None
    nums = camera_numbers(cam, color, setup.get("e_per_adu_header"))
    soft_bin = int(_f(meas.get("software_binning")) or 1)
    rpp = _f(meas.get("raw_px_per_px"))
    rpp = int(rpp) if rpp else (soft_bin if mono else 2 * soft_bin)
    # electrons per log unit: SHOBS-P's pixel is the AVERAGE of the color's raw pixels in the cell (and of the
    # software-binned cells), so its value × (pixels averaged) × gain is the electrons it holds. FLUX's own folder
    # runs store sums of raw ADU (flux_is_adu_sum), one ADU = gain electrons.
    if meas.get("flux_is_adu_sum"):
        per_unit_px = 1.0
    else:
        per_unit_px = float(n_color * soft_bin * soft_bin)
    e_per_unit = nums["gain"] * per_unit_px if nums["gain"] else None
    pix_um, focal = _f(setup.get("pixel_um")), _f(setup.get("focal_header_mm") or setup.get("focal_mm"))
    px_arcsec = 206.264806 * pix_um / focal * rpp if pix_um and focal else None

    stars = doc.get("stars") or []
    names, cats = [], []
    for s in stars:
        if not isinstance(s, dict) or s.get("role") == "target":
            continue
        m = _f(s.get("catalog_mag"))
        if m is None or not s.get("name"):
            continue
        names.append(str(s["name"]))
        cats.append(m)
    frames_in = [f for f in (doc.get("frames") or []) if isinstance(f, dict)]
    pre = bool(doc.get("precalibrated"))
    keep = [f for f in frames_in if not (f.get("flag") or "") and not f.get("precalibrated")
            and _f(f.get("jd_mid")) and _f(f.get("exptime_s"))]
    nf, ns = len(keep), len(names)
    flux = np.full((nf, ns), np.nan)
    peak = np.full((nf, ns), np.nan)
    idx = {n: i for i, n in enumerate(names)}
    for fi, fr in enumerate(keep):
        for n, m in (fr.get("stars") or {}).items():
            j = idx.get(n)
            if j is None or not isinstance(m, dict):
                continue
            v = _f(m.get("flux"))
            flux[fi, j] = v if v is not None else np.nan
            p = _f(m.get("peak_adu"))
            peak[fi, j] = p if p is not None else np.nan

    def col(key):
        return np.array([_f(f.get(key)) if _f(f.get(key)) is not None else np.nan for f in keep], dtype=float)

    ds = Dataset(
        path=path, source="SHOBS-FLUX" if str(doc.get("written_by", "")).startswith("SHOBS-FLUX") else "SHOBS-P",
        written_by=str(doc.get("written_by") or ""), written=str(doc.get("written") or ""),
        run_id=str(doc.get("run_id") or ""), night=str(doc.get("night") or ""),
        target=str(doc.get("target") or "unknown"), band=band, catalog_band=cat_band, setup=setup,
        camera_id=cam_id, color=color, gain=nums["gain"], gain_source=nums["gain_source"], e_per_unit=e_per_unit,
        raw_px_per_px=rpp, px_arcsec=px_arcsec, linear_limit_adu=nums["linear_limit_adu"],
        read_noise_e=nums["read_noise_e"], aperture_px=_f(meas.get("aperture_radius_px")), names=names,
        cat=np.array(cats, dtype=float), jd=col("jd_mid"), airmass=col("airmass"), exptime=col("exptime_s"),
        focuspos=col("focuspos"), fwhm_px=col("fwhm_px"), sky=col("sky"), flux=flux, peak=peak,
        n_frames_in_log=len(frames_in), precalibrated=pre)
    if not ds.night and nf:
        ds.night = night_of(float(np.nanmin(ds.jd)))
    return ds


def night_of(jd: float) -> str:
    """The night's date as SHOBS-P names it: the UTC date of JD − 12 h (the evening's date)."""
    from datetime import datetime, timedelta, timezone
    t = datetime(2000, 1, 1, 12, tzinfo=timezone.utc) + timedelta(days=jd - 2451545.0 - 0.5)
    return t.strftime("%Y-%m-%d")


def newest_per_night(datasets: list) -> tuple[list, list]:
    """(kept, replaced): only the newest log of each night + target + band + camera counts (a re-run of a night in
    SHOBS-P writes a second log; counting both would weigh that night twice)."""
    best: dict = {}
    for ds in datasets:
        k = ds.key
        cur = best.get(k)
        if cur is None or (ds.written, ds.run_id) > (cur.written, cur.run_id):
            best[k] = ds
    kept = [d for d in datasets if best.get(d.key) is d]
    return kept, [d for d in datasets if best.get(d.key) is not d]


# ---- small statistics ----------------------------------------------------------------------------------------------

def rsigma(a) -> float:
    a = np.asarray(a, dtype=float)
    a = a[np.isfinite(a)]
    if a.size < 2:
        return float("nan")
    return float(1.4826 * np.median(np.abs(a - np.median(a))))


def clipped(values, sigmas=None, nsig: float = CLIP_SIGMA, iters: int = 5):
    """Indices kept after iterative clipping around the median (robust sigma; with sigmas, a point is also kept when
    it agrees within nsig of its own error)."""
    v = np.asarray(values, dtype=float)
    ok = np.isfinite(v)
    for _ in range(iters):
        if ok.sum() < 3:
            break
        med = np.median(v[ok])
        s = rsigma(v[ok])
        if not math.isfinite(s) or s == 0:
            s = float(np.std(v[ok])) or 0.0
        lim = nsig * s
        dev = np.abs(v - med)
        new = np.isfinite(v) & (dev <= lim if sigmas is None else (dev <= lim) |
                                (dev <= nsig * np.asarray(sigmas, dtype=float)))
        if lim == 0:
            new = np.isfinite(v)
        if (new == ok).all():
            break
        ok = new
    return ok


def wmean(values, sigmas):
    v = np.asarray(values, dtype=float)
    s = np.asarray(sigmas, dtype=float)
    good = np.isfinite(v) & np.isfinite(s) & (s > 0)
    if not good.any():
        good = np.isfinite(v)
        if not good.any():
            return float("nan"), float("nan")
        return float(np.mean(v[good])), float(np.std(v[good]) / math.sqrt(good.sum())) if good.sum() > 1 else float("nan")
    w = 1.0 / s[good] ** 2
    m = float(np.sum(w * v[good]) / np.sum(w))
    err = math.sqrt(1.0 / float(np.sum(w)))
    if good.sum() > 1:                               # never claim less than the nights' own scatter allows
        err = max(err, float(np.std(v[good], ddof=1)) / math.sqrt(good.sum()))
    return m, err


def linfit(x, y, nsig: float = 3.0, iters: int = 5):
    """y = a + b (x − 1), clipped. (a, b, sa, sb, rms, keep mask) or None."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    ok = np.isfinite(x) & np.isfinite(y)
    res = None
    for _ in range(iters):
        if ok.sum() < 3:
            return res
        X = np.vstack([np.ones(ok.sum()), x[ok] - 1.0]).T
        coef, *_ = np.linalg.lstsq(X, y[ok], rcond=None)
        r = y - (coef[0] + coef[1] * (x - 1.0))
        rms = float(np.sqrt(np.mean(r[ok] ** 2)))
        n = ok.sum()
        cov = np.linalg.pinv(X.T @ X) * (np.sum(r[ok] ** 2) / max(n - 2, 1))
        res = (float(coef[0]), float(coef[1]), float(math.sqrt(max(cov[0, 0], 0))),
               float(math.sqrt(max(cov[1, 1], 0))), rms, ok.copy())
        s = rsigma(r[ok]) or rms
        new = np.isfinite(x) & np.isfinite(y) & (np.abs(r) <= nsig * max(s, 1e-4))
        if (new == ok).all():
            break
        ok = new
    return res


def binned_rms(jd, resid, width: float = BIN_DAYS) -> tuple[float, int]:
    """rms of the residuals averaged in bins of `width` days (bins with ≥ 3 points), and the number of bins."""
    jd = np.asarray(jd, dtype=float)
    r = np.asarray(resid, dtype=float)
    ok = np.isfinite(jd) & np.isfinite(r)
    if ok.sum() < 3:
        return float("nan"), 0
    jd, r = jd[ok], r[ok]
    b = np.floor((jd - jd.min()) / width).astype(int)
    means = [float(np.mean(r[b == i])) for i in np.unique(b) if (b == i).sum() >= 3]
    if len(means) < 2:
        return float(np.std(r) / math.sqrt(max(len(r), 1))), len(means)
    m = np.asarray(means)
    return float(np.sqrt(np.mean((m - np.mean(m)) ** 2))), len(means)


def moon_illumination(jd: float) -> float:
    """The Moon's illuminated fraction (0–1) at a JD, geocentric; NaN when astropy is not there."""
    try:
        from astropy.coordinates import get_body
        from astropy.time import Time
        import warnings
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            t = Time(jd, format="jd")
            sun, moon = get_body("sun", t), get_body("moon", t)
            elong = sun.separation(moon).radian
        return float((1.0 - math.cos(elong)) / 2.0)
    except Exception:
        return float("nan")


def scint_factor(aperture_m: float, exptime_s, airmass, elevation_m: float) -> np.ndarray:
    """σ_scint / C_Y (relative flux) per frame: Osborn et al. 2015 eq. 7, σ² = 1e-5 C_Y² D^(-4/3) t^-1 X^3 exp(-2h/H)."""
    t = np.asarray(exptime_s, dtype=float)
    x = np.asarray(airmass, dtype=float)
    return np.sqrt(1e-5 * aperture_m ** (-4.0 / 3.0) / t * x ** 3 * math.exp(-2.0 * elevation_m / SCALE_HEIGHT_M))


# ---- one night -------------------------------------------------------------------------------------------------------

def analyse(ds: Dataset, k_prior: tuple | None = None, aperture_mm: float | None = None,
            elevation_m: float | None = None) -> NightResult:
    """What one night says. k_prior = (k, source words) used when the night cannot measure its own extinction."""
    res = NightResult(ds=ds)
    L = res.lines
    if ds.precalibrated:
        res.why_not = "its frames were calibrated before SHOBS-P read them, so their flux scale is not known"
        L.append(("bad", f"✖ Not used: {res.why_not}."))
        return res
    if ds.e_per_unit is None:
        res.why_not = "no gain is known for this camera (no camera profile and no EGAIN in the header)"
        L.append(("bad", f"✖ Not used: {res.why_not}."))
        return res
    nf = len(ds.jd)
    res.n_frames = nf
    if nf < MIN_FRAMES:
        res.why_not = f"only {nf} usable frame{'s' if nf != 1 else ''} (at least {MIN_FRAMES} needed)"
        L.append(("bad", f"✖ Not used: {res.why_not}."))
        return res
    if ds.gain_source == "header":
        L.append(("info", f"Gain {ds.gain:.4f} e⁻/ADU from the frames' header (EGAIN): there is no camera profile "
                          f"for {ds.camera_id}; a bench run makes the zero point exact."))
    res.exptime_s = float(np.nanmedian(ds.exptime))
    _seeing(res)
    _cadence(res)

    ns = len(ds.names)
    if ns == 0:
        res.why_not = "no catalogued field stars in it"
        L.append(("bad", f"✖ No zero point: {res.why_not}. Seeing and cadence are still used."))
        res.usable = True
        return res
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = ds.flux * ds.e_per_unit / ds.exptime[:, None]
        zp = ds.cat[None, :] + 2.5 * np.log10(np.where(rate > 0, rate, np.nan))
    out = res.stars_out
    lin = ds.linear_limit_adu
    if lin:
        sat = np.isfinite(ds.peak) & (ds.peak >= lin)
        zp[sat] = np.nan
        frac = sat.sum(axis=0) / max(nf, 1)
        for j in np.where(frac > SAT_FRACTION)[0]:
            out[ds.names[j]] = f"saturated (raw peak ≥ the linear limit {lin:,.0f} ADU in {frac[j]:.0%} of frames)"
            zp[:, j] = np.nan
    for j in range(ns):
        if ds.names[j] not in out and np.isfinite(zp[:, j]).sum() < max(3, nf // 3):
            out[ds.names[j]] = "measured in too few frames"
            zp[:, j] = np.nan
    good = [j for j in range(ns) if ds.names[j] not in out]
    # catalog disagreement and variability, against the ensemble of the others
    for _ in range(3):
        if len(good) < 2:
            break
        ens = np.nanmedian(zp[:, good], axis=1)
        offs = np.array([np.nanmedian(zp[:, j] - ens) for j in good])
        scat = np.array([rsigma(zp[:, j] - ens) for j in good])
        s_off = rsigma(offs) if len(good) >= 3 else float("nan")
        med_off = float(np.nanmedian(offs))
        med_scat = float(np.nanmedian(scat))
        drop = []
        for i, j in enumerate(good):
            d = offs[i] - med_off
            lim = max(CAT_OFFSET_MAG, CAT_OFFSET_SIGMA * s_off if math.isfinite(s_off) else 0.0)
            if len(good) >= 3 and abs(d) > lim:
                drop.append((j, f"its catalog magnitude disagrees with the other stars by {d:+.2f} mag (a wrong "
                                f"catalog value, a blend or a variable)"))
            elif len(good) >= 4 and math.isfinite(scat[i]) and med_scat > 0 and scat[i] > VARY_FACTOR * med_scat \
                    and scat[i] > 0.01:
                drop.append((j, f"it varies ({scat[i]:.3f} mag scatter against the others, {scat[i] / med_scat:.1f}× "
                                "the typical star)"))
        if not drop:
            break
        worst = max(drop, key=lambda t: abs(np.nanmedian(zp[:, t[0]] - ens)))   # one at a time, worst first
        out[ds.names[worst[0]]] = worst[1]
        good.remove(worst[0])
    res.stars_used = [ds.names[j] for j in good]
    if len(good) < MIN_STARS:
        res.why_not = (f"only {len(good)} usable catalogued star{'s' if len(good) != 1 else ''} "
                       f"(at least {MIN_STARS} needed)")
        L.append(("warn", f"⚠ No zero point: {res.why_not}. Seeing and cadence are still used."))
        res.usable = True
        _sky(res, None)
        return res
    # each star's zero point shifted onto the ensemble (removes catalog offsets), then averaged per frame
    ens = np.nanmedian(zp[:, good], axis=1)
    offs = np.array([np.nanmedian(zp[:, j] - ens) for j in good])
    shifted = zp[:, good] - (offs - np.nanmean(offs))[None, :]
    zf = np.nanmean(shifted, axis=1)
    res.zp_frame = zf
    ok = np.isfinite(zf) & np.isfinite(ds.airmass)
    if ok.sum() < MIN_FRAMES:
        res.why_not = "too few frames with both a zero point and an airmass"
        L.append(("warn", f"⚠ No zero point: {res.why_not}."))
        res.usable = True
        return res
    x = ds.airmass
    res.x_span = float(np.nanmax(x[ok]) - np.nanmin(x[ok]))
    fit = linfit(x, zf)
    if fit is None:
        res.why_not = "the zero points could not be fitted"
        L.append(("warn", f"⚠ No zero point: {res.why_not}."))
        res.usable = True
        return res
    a, b, sa, sb, rms, keep = fit
    k_fit, k_fit_s = -b, sb
    resid = zf - (a + b * (x - 1.0))
    res.rms = rms
    res.bin_rms, nbins = binned_rms(ds.jd[keep], resid[keep])
    res.photometric = bool(math.isfinite(res.bin_rms) and res.bin_rms <= PHOT_BIN_RMS and keep.sum() >= MIN_FRAMES
                           and keep.mean() >= 0.8)
    k_ok = res.photometric and res.x_span >= K_MIN_SPAN and K_RANGE[0] <= k_fit <= K_RANGE[1] and \
        k_fit_s < 0.1
    res.x_mean = float(np.mean(x[keep]))
    zmean = float(np.mean(zf[keep]))
    res.zp_mean = zmean
    if k_ok:
        res.k, res.k_sigma = k_fit, k_fit_s
        res.k_used, res.k_used_source = k_fit, "this night"
        res.zp1, res.zp1_sigma = a, max(sa, ZP_FLOOR)
    else:
        kp, src = k_prior if k_prior and k_prior[0] is not None and math.isfinite(k_prior[0]) else \
            (TYPICAL_K.get(ds.catalog_band, 0.2), "typical")
        res.k_used, res.k_used_source = kp, src
        res.zp1 = zmean + kp * (res.x_mean - 1.0)
        res.zp1_sigma = max(rms / math.sqrt(keep.sum()), ZP_FLOOR) + 0.02 * abs(res.x_mean - 1.0)
    res.usable = True

    # findings
    L.append(("ok" if res.photometric else "warn",
              (f"✔ Photometric: the zero point held within {res.bin_rms:.3f} mag (10-minute bins, {nbins} bins)."
               if res.photometric else
               f"⚠ Not photometric: the zero point moved {res.bin_rms:.3f} mag between 10-minute bins (limit "
               f"{PHOT_BIN_RMS}); clouds or haze. Kept out of the zero point and extinction.")))
    if k_ok:
        L.append(("ok", f"✔ Extinction measured: k = {k_fit:.3f} ± {k_fit_s:.3f} mag/airmass over airmass "
                        f"{np.nanmin(x[keep]):.2f}–{np.nanmax(x[keep]):.2f}."))
    elif res.photometric and res.x_span < K_MIN_SPAN:
        L.append(("info", f"Extinction not measured: airmass only spanned {res.x_span:.2f} (needs {K_MIN_SPAN}); "
                          f"k = {res.k_used:.2f} used ({res.k_used_source})."))
    elif res.photometric:
        L.append(("warn", f"⚠ The fitted extinction ({k_fit:.3f} ± {k_fit_s:.3f}) is not physical; "
                          f"k = {res.k_used:.2f} used ({res.k_used_source})."))
    L.append(("info", f"Zero point {res.zp1:.3f} ± {res.zp1_sigma:.3f} mag for 1 e⁻/s at airmass 1 "
                      f"({res.zp_mean:.3f} at airmass {res.x_mean:.2f}); {len(good)} catalogued stars, "
                      f"{int(keep.sum())} frames, residual {rms:.3f} mag per frame."))
    for n, why in out.items():
        L.append(("info", f"Left out {n}: {why}."))
    _sky(res, zf)
    if aperture_mm and elevation_m is not None:
        _scintillation(res, zp, good, aperture_mm / 1000.0, elevation_m)
    elif not aperture_mm:
        L.append(("info", "Scintillation not worked out: type the telescope aperture on the Sky page."))
    else:
        L.append(("info", "Scintillation not worked out: type the site elevation on the Sky page."))
    return res


def _seeing(res: NightResult) -> None:
    ds = res.ds
    if not ds.px_arcsec:
        res.lines.append(("info", "Seeing not worked out: the pixel size or focal length is not in the log."))
        return
    f = ds.fwhm_px * ds.px_arcsec
    f = f[np.isfinite(f) & (f > 0)]
    if f.size < 3:
        return
    res.fwhm_arcsec = float(np.median(f))
    res.fwhm_sigma = rsigma(f)
    fp = ds.focuspos[np.isfinite(ds.focuspos)]
    res.focus = float(np.median(fp)) if fp.size else float("nan")
    res.defocused = res.fwhm_arcsec > DEFOCUS_ARCSEC


def _cadence(res: NightResult) -> None:
    ds = res.ds
    order = np.argsort(ds.jd)
    t = ds.jd[order] * 86400.0
    e = ds.exptime[order]
    if t.size < 6:
        return
    dt = np.diff(t)
    same = np.isclose(e[1:], e[:-1], rtol=1e-3)
    dt = dt[same & (dt > 0)]
    if dt.size < 5:
        return
    med = float(np.median(dt))
    dt = dt[dt <= 2.5 * med]                          # gaps, meridian flips, refocus
    if dt.size < 5:
        return
    oh = float(np.median(dt)) - float(np.median(e))
    if oh >= -0.5:
        res.overhead_s, res.overhead_n = max(oh, 0.0), int(dt.size)


def _sky(res: NightResult, zf) -> None:
    ds = res.ds
    jd_mid = float(np.nanmedian(ds.jd)) if ds.jd.size else float("nan")
    if math.isfinite(jd_mid):
        res.moon_illum = moon_illumination(jd_mid)
    if zf is None or not ds.px_arcsec or ds.e_per_unit is None:
        return
    area = ds.px_arcsec ** 2
    with np.errstate(divide="ignore", invalid="ignore"):
        rate = ds.sky * ds.e_per_unit / ds.exptime / area
        mu = zf - 2.5 * np.log10(np.where(rate > 0, rate, np.nan))
    mu = mu[np.isfinite(mu)]
    if mu.size >= 3:
        res.sky_mag = float(np.median(mu))


def _scintillation(res: NightResult, zp, good, aperture_m: float, elevation_m: float) -> None:
    """C_Y from each bright star's frame-to-frame scatter against the mean of the others."""
    ds = res.ds
    if len(good) < 3 or ds.read_noise_e is None and ds.gain is None:
        return
    order = np.argsort(ds.jd)
    t = ds.exptime[order]
    x = ds.airmass[order]
    e_flux = ds.flux[order][:, good] * ds.e_per_unit                      # electrons in the aperture
    sky_e = ds.sky[order] * ds.e_per_unit                                 # electrons per measured pixel
    npix = math.pi * (ds.aperture_px or 8.0) ** 2
    raw_per_px = ds.e_per_unit / ds.gain if ds.gain else 1.0              # raw pixels summed per measured pixel
    rn2 = (ds.read_noise_e or 0.0) ** 2 * raw_per_px
    with np.errstate(divide="ignore", invalid="ignore"):
        var_ph = (e_flux + npix * (sky_e[:, None] + rn2)) / e_flux ** 2  # relative variance per star per frame
    m = zp[order][:, good]
    fac = scint_factor(aperture_m, t, x, elevation_m)                     # σ_scint / C_Y per frame
    est = []
    n = len(good)
    for i in range(n):
        others = [k for k in range(n) if k != i]
        d = m[:, i] - np.nanmean(m[:, others], axis=1)
        dd = np.diff(d)
        ok = np.isfinite(dd)
        if ok.sum() < 10:
            continue
        var_obs = (rsigma(dd[ok]) / MAG) ** 2 / 2.0                       # point-to-point: slow drifts cancel
        ph = np.nanmedian(var_ph[:, i]) + np.nanmedian(np.nansum(var_ph[:, others], axis=1)) / len(others) ** 2
        f2 = float(np.nanmedian(fac ** 2))
        scint_share = 1.0 + 1.0 / len(others)
        excess = var_obs - ph
        if not math.isfinite(excess) or excess <= 0 or excess < SCINT_MIN_FRACTION * var_obs:
            continue
        est.append(math.sqrt(excess / scint_share / f2))
    if len(est) >= 2:
        res.cy = float(np.median(est))
        res.cy_n = len(est)
        res.lines.append(("info", f"Scintillation C_Y {res.cy:.2f} from {len(est)} stars (Osborn et al. 2015; "
                                  f"aperture {aperture_m * 1000:.0f} mm, site {elevation_m:.0f} m)."))
    else:
        res.lines.append(("info", "Scintillation not measured: photon noise hides it in these exposures."))


# ---- across nights ----------------------------------------------------------------------------------------------------

@dataclass
class BandSummary:
    band: str
    zp1: float = float("nan")
    zp1_sigma: float = float("nan")
    zp_nights: list = field(default_factory=list)       # nights used
    zp_out: dict = field(default_factory=dict)          # night → why set aside
    k: float = float("nan")
    k_sigma: float = float("nan")
    k_nights: list = field(default_factory=list)
    k_source: str = ""
    fwhm_in_focus: float = float("nan")
    fwhm_in_focus_sigma: float = float("nan")
    fwhm_in_focus_n: int = 0
    fwhm_defocus: dict = field(default_factory=dict)    # key → (value, sigma, n)
    sky: float = float("nan")
    sky_sigma: float = float("nan")
    sky_n: int = 0
    sky_dark: bool = False
    cy: float = float("nan")
    cy_sigma: float = float("nan")
    cy_n: int = 0
    last_night: str = ""


@dataclass
class Summary:
    nights: list                                        # NightResult, newest night first
    replaced: list                                      # Dataset (older runs of the same night)
    bands: dict                                         # band → BandSummary
    overhead_s: float = float("nan")
    overhead_sigma: float = float("nan")
    overhead_n: int = 0
    camera_ids: list = field(default_factory=list)
    lines: list = field(default_factory=list)


def _by_night(results, value, sigma):
    """One value per night (several runs that night: weighted mean). [(night, v, s, [results])]."""
    groups: dict = {}
    for r in results:
        groups.setdefault(r.ds.night, []).append(r)
    out = []
    for night, rs in sorted(groups.items()):
        v = [value(r) for r in rs]
        s = [sigma(r) for r in rs]
        m, e = wmean(v, s)
        if math.isfinite(m):
            out.append((night, m, e if math.isfinite(e) else max(s) if s else float("nan"), rs))
    return out


def _defocus_keys(results: list) -> None:
    """Defocused runs keyed by the focuser offset from the same night's in-focus run (else "defocused")."""
    nights: dict = {}
    for r in results:
        if math.isfinite(r.fwhm_arcsec):
            nights.setdefault(r.ds.night, []).append(r)
    for rs in nights.values():
        infocus = [r for r in rs if not r.defocused]
        best = min((r.fwhm_arcsec for r in infocus), default=float("nan"))
        for r in rs:
            if not r.defocused and math.isfinite(best) and r.fwhm_arcsec > DEFOCUS_RATIO * best:
                r.defocused = True
        infocus = [r for r in rs if not r.defocused]
        for r in rs:
            if not r.defocused:
                continue
            ref = [q.focus for q in infocus if math.isfinite(q.focus)]
            if ref and math.isfinite(r.focus):
                off = r.focus - float(np.median(ref))
                r.defocus_key = f"{int(round(off / 5.0) * 5):+d} steps"
            else:
                r.defocus_key = "defocused"


def combine(results: list, replaced: list | None = None, old_profile: dict | None = None) -> Summary:
    """The nights together, per band (see the module text)."""
    _defocus_keys(results)
    usable = [r for r in results if r.usable]
    bands: dict = {}
    for r in usable:
        bands.setdefault(r.ds.band, []).append(r)
    out = {}
    for band, rs in bands.items():
        bs = BandSummary(band=band)
        bs.last_night = max((r.ds.night for r in rs), default="")
        # extinction first (the zero point of nights without their own k uses it)
        kn = _by_night([r for r in rs if math.isfinite(r.k)], lambda r: r.k, lambda r: r.k_sigma)
        if kn:
            keep = clipped([v for _, v, _, _ in kn], [s for _, _, s, _ in kn])
            bs.k, bs.k_sigma = wmean([v for (_, v, _, _), k in zip(kn, keep) if k],
                                     [s for (_, _, s, _), k in zip(kn, keep) if k])
            bs.k_nights = [n for (n, _, _, _), k in zip(kn, keep) if k]
            bs.k_source = "measured"
            for r in rs:
                if not math.isfinite(r.k) and math.isfinite(r.zp_mean):
                    r.k_used, r.k_used_source = bs.k, "all nights"
                    r.zp1 = r.zp_mean + bs.k * (r.x_mean - 1.0)
                    r.zp1_sigma = max(r.rms / math.sqrt(max(r.n_frames, 1)), ZP_FLOOR) + \
                        (bs.k_sigma if math.isfinite(bs.k_sigma) else 0.02) * abs(r.x_mean - 1.0)
        # zero point: photometric nights only, each night once, clipped across nights
        for r in rs:
            if math.isfinite(r.zp1) and not r.photometric:
                bs.zp_out[r.ds.night] = "not photometric (clouds or haze)"
        zn = _by_night([r for r in rs if r.photometric and math.isfinite(r.zp1)], lambda r: r.zp1,
                       lambda r: r.zp1_sigma)
        if zn:
            keep = clipped([v for _, v, _, _ in zn], [s for _, _, s, _ in zn])
            for (n, v, _, _), k in zip(zn, keep):
                if not k:
                    bs.zp_out[n] = f"disagrees with the other nights ({v:.2f})"
            bs.zp1, bs.zp1_sigma = wmean([v for (_, v, _, _), k in zip(zn, keep) if k],
                                         [s for (_, _, s, _), k in zip(zn, keep) if k])
            bs.zp_nights = [n for (n, _, _, _), k in zip(zn, keep) if k]
        # seeing
        inf = [r.fwhm_arcsec for r in rs if math.isfinite(r.fwhm_arcsec) and not r.defocused]
        if inf:
            bs.fwhm_in_focus, bs.fwhm_in_focus_sigma, bs.fwhm_in_focus_n = float(np.median(inf)), rsigma(inf), len(inf)
        keys: dict = {}
        for r in rs:
            if math.isfinite(r.fwhm_arcsec) and r.defocused:
                keys.setdefault(r.defocus_key or "defocused", []).append(r.fwhm_arcsec)
        bs.fwhm_defocus = {k: (float(np.median(v)), rsigma(v), len(v)) for k, v in keys.items()}
        # sky: the dark nights when there are any
        sk = [(r.sky_mag, r.moon_illum) for r in rs if math.isfinite(r.sky_mag)]
        dark = [s for s, m in sk if math.isfinite(m) and m < DARK_MOON]
        use = dark or [s for s, _ in sk]
        if use:
            bs.sky, bs.sky_sigma, bs.sky_n, bs.sky_dark = float(np.median(use)), rsigma(use), len(use), bool(dark)
        cys = [r.cy for r in rs if math.isfinite(r.cy)]
        if cys:
            bs.cy, bs.cy_sigma, bs.cy_n = float(np.median(cys)), rsigma(cys), len(cys)
        out[band] = bs
    s = Summary(nights=sorted(results, key=lambda r: (r.ds.night, r.ds.written), reverse=True),
                replaced=list(replaced or []), bands=out)
    oh = [(r.overhead_s, r.overhead_n) for r in usable if math.isfinite(r.overhead_s)]
    if oh:
        s.overhead_s = float(np.median([v for v, _ in oh]))
        s.overhead_sigma = rsigma([v for v, _ in oh])
        s.overhead_n = int(sum(n for _, n in oh))
    s.camera_ids = sorted({r.ds.camera_id for r in results})
    return s


def k_prior_from(profile: dict | None, band: str) -> tuple | None:
    """The system profile's measured extinction for a band, as (k, "profile"), or None."""
    rec = (((profile or {}).get("bands") or {}).get(band) or {}).get("extinction_k")
    v = sp.value_of(rec)
    if v is not None and rec.get("source") in ("measured", "user"):
        return float(v), "profile"
    return None


def run_all(datasets: list, profile: dict | None = None, aperture_mm=None, elevation_m=None,
            step=None) -> Summary:
    """Analyse every dataset (newest per night only) and combine."""
    kept, replaced = newest_per_night(datasets)
    results = []
    for i, ds in enumerate(sorted(kept, key=lambda d: d.night)):
        if step:
            step(f"Night {i + 1} of {len(kept)}: {ds.label}")
        results.append(analyse(ds, k_prior_from(profile, ds.band), aperture_mm, elevation_m))
    return combine(results, replaced, profile)


def read_logs(paths: list, cam_lookup=None) -> tuple[list, list]:
    """(datasets, problems [(path, why)]) for a list of log files."""
    good, bad = [], []
    for p in paths:
        try:
            good.append(load_dataset(p, cam_lookup))
        except OnSkyError as exc:
            bad.append((p, str(exc)))
        except Exception as exc:                          # a damaged file never stops the run
            bad.append((p, f"{type(exc).__name__}: {exc}"))
    return good, bad


# ---- the system profile -----------------------------------------------------------------------------------------------

def _rnd(v, nd):
    return round(float(v), nd) if v is not None and math.isfinite(v) else None


def _nights_word(nights: list) -> str:
    if not nights:
        return ""
    if len(nights) == 1:
        return f"1 night ({nights[0]})"
    return f"{len(nights)} nights ({min(nights)} to {max(nights)})"


def measured_records(s: Summary, run_key: str | None = None) -> tuple[dict, dict]:
    """({band: {key: record or {defocus key: record}}}, {"overhead_s": record}) for what the nights measured."""
    bands = {}
    for band, b in s.bands.items():
        recs = {}
        if math.isfinite(b.zp1):
            recs["zero_point"] = sp.record(
                _rnd(b.zp1, 3), "mag for 1 e-/s", "measured", sigma=_rnd(b.zp1_sigma, 3), n=len(b.zp_nights),
                date=max(b.zp_nights), conditions=_nights_word(b.zp_nights), run=run_key,
                note="at airmass 1; photometric nights only, catalogued field stars (APASS DR9 / Gaia DR3)")
        if math.isfinite(b.k):
            recs["extinction_k"] = sp.record(_rnd(b.k, 3), "mag/airmass", "measured", sigma=_rnd(b.k_sigma, 3),
                                             n=len(b.k_nights), date=max(b.k_nights),
                                             conditions=_nights_word(b.k_nights), run=run_key)
        if math.isfinite(b.sky):
            recs["sky_brightness"] = sp.record(
                _rnd(b.sky, 2), "mag/arcsec2", "measured", sigma=_rnd(b.sky_sigma, 2), n=b.sky_n, date=b.last_night,
                conditions="Moon under 25 % lit" if b.sky_dark else "all nights (none with a dark Moon yet)",
                run=run_key, note="median of the nights' medians")
        if math.isfinite(b.fwhm_in_focus):
            recs["fwhm_in_focus"] = sp.record(_rnd(b.fwhm_in_focus, 2), "arcsec", "measured",
                                              sigma=_rnd(b.fwhm_in_focus_sigma, 2), n=b.fwhm_in_focus_n,
                                              date=b.last_night, conditions="median of the nights' medians",
                                              run=run_key)
        if b.fwhm_defocus:
            recs["fwhm_defocus"] = {k: sp.record(_rnd(v, 2), "arcsec", "measured", sigma=_rnd(sg, 2), n=n,
                                                 date=b.last_night, conditions="median of the nights' medians",
                                                 run=run_key, note="defocus offset from the same night's in-focus run"
                                                 if k != "defocused" else "offset from focus not known")
                                    for k, (v, sg, n) in b.fwhm_defocus.items()}
        if math.isfinite(b.cy):
            recs["scintillation_cy"] = sp.record(_rnd(b.cy, 2), "Young C_Y", "measured", sigma=_rnd(b.cy_sigma, 2),
                                                 n=b.cy_n, date=b.last_night,
                                                 conditions="Osborn et al. 2015 modified Young", run=run_key)
        if recs:
            bands[band] = recs
    cad = {}
    if math.isfinite(s.overhead_s):
        cad["overhead_s"] = sp.record(_rnd(s.overhead_s, 2), "s", "measured", sigma=_rnd(s.overhead_sigma, 2),
                                      n=s.overhead_n, date=max((r.ds.night for r in s.nights), default=None),
                                      run=run_key, note="time between frames minus the exposure (gaps left out)")
    return bands, cad


LABELS = {"zero_point": ("Zero point", "mag (1 e⁻/s)", 3), "extinction_k": ("Extinction", "mag/airmass", 3),
          "sky_brightness": ("Sky", "mag/″²", 2), "fwhm_in_focus": ("Seeing in focus", "″", 2),
          "fwhm_defocus": ("Defocused", "″", 2), "scintillation_cy": ("Scintillation C_Y", "", 2),
          "overhead_s": ("Overhead per frame", "s", 2)}


def changes_for(s: Summary, doc: dict | None) -> list:
    """[(label, old value, old source, new value, unit, decimals)] for the save dialog."""
    bands, cad = measured_records(s)
    out = []
    for band, recs in bands.items():
        old_b = (((doc or {}).get("bands") or {}).get(band) or {})
        for key, r in recs.items():
            label, unit, nd = LABELS.get(key, (key, "", 3))
            if key == "fwhm_defocus":
                for dk, dr in r.items():
                    o = (old_b.get("fwhm_defocus") or {}).get(dk) or {}
                    out.append((f"{BAND_WORDS.get(band, band)}: {label} {dk}", o.get("value"),
                                o.get("source", "none"), dr["value"], unit, nd))
                continue
            o = old_b.get(key) or {}
            out.append((f"{BAND_WORDS.get(band, band)}: {label}", o.get("value"), o.get("source", "none"),
                        r["value"], unit, nd))
    for key, r in cad.items():
        label, unit, nd = LABELS[key]
        o = (((doc or {}).get("cadence") or {}).get(key)) or {}
        out.append((label, o.get("value"), o.get("source", "none"), r["value"], unit, nd))
    return out


def new_system_profile(system_id: str, cam_id: str, telescope: dict, written_by: str, setup: dict | None = None) -> dict:
    """A new, empty system profile (format v1)."""
    now = sp.now_iso()
    focal = sp._finite(telescope.get("focal_mm"))
    pix = sp._finite((setup or {}).get("pixel_um"))
    doc = {
        "format": sp.SYSTEM_FORMAT, "format_version": sp.FORMAT_VERSION, "profile_id": system_id, "revision": 1,
        "written_by": written_by, "created": now, "updated": now, "camera_profile": cam_id,
        "telescope": {"name": telescope.get("name") or "", "aperture_mm": sp._finite(telescope.get("aperture_mm")),
                      "focal_mm": focal, "focal_header": sp._finite(telescope.get("focal_header")),
                      "telescop_header": telescope.get("telescop_header"),
                      "telescop_header_ignored": "mount name, not a telescope" if telescope.get("telescop_header")
                      else None,
                      "central_obstruction_fraction": None, "mount": None},
        "site": "uses each app's own site setting (never stored here)",
        "plate_scale": {}, "bands": {}, "cadence": {},
        "learning": {"outlier_rejection": f"sigma-clip {CLIP_SIGMA} across nights",
                     "min_nights_for_measured": 1},
    }
    if focal and pix:
        doc["plate_scale"]["raw_arcsec_per_px"] = sp.record(
            round(206.264806 * pix / focal, 4), "arcsec/px", "derived",
            note=f"206.265 x {pix:g} um / {focal:g} mm")
    return doc


def apply_to_profile(s: Summary, doc: dict | None, written_by: str, run_key: str | None = None,
                     keep_user: bool = False, system_id: str = "", cam_id: str = "", telescope: dict | None = None,
                     setup: dict | None = None) -> dict:
    """The system profile with these nights in it (a new one when doc is None). keep_user keeps values you typed.
    A value the nights did not measure is left as it was."""
    if doc is None:
        doc = new_system_profile(system_id, cam_id, telescope or {}, written_by, setup)
    else:
        doc = copy.deepcopy(doc)
        tel = doc.setdefault("telescope", {})
        for k in ("name", "aperture_mm"):
            if telescope and telescope.get(k) not in (None, "") and tel.get(k) in (None, ""):
                tel[k] = telescope[k]
    bands, cad = measured_records(s, run_key)
    for band, recs in bands.items():
        slot = doc.setdefault("bands", {}).setdefault(band, {})
        for key, r in recs.items():
            if key == "fwhm_defocus":
                fd = slot.setdefault("fwhm_defocus", {})
                for dk, dr in r.items():
                    if keep_user and (fd.get(dk) or {}).get("source") == "user":
                        continue
                    fd[dk] = dr
                continue
            if keep_user and (slot.get(key) or {}).get("source") == "user":
                continue
            slot[key] = r
    for key, r in cad.items():
        c = doc.setdefault("cadence", {})
        if keep_user and (c.get(key) or {}).get("source") == "user":
            continue
        c[key] = r
    doc.setdefault("learning", {})["last_onsky_run"] = run_key
    return doc


def night_values(r: NightResult, system_id: str) -> list:
    """One night's numbers for the run history (date = the night), so the History page can trend them."""
    rows = []

    def add(key, v, unit, sigma=None):
        if v is not None and math.isfinite(v):
            rows.append({"key": key, "value": float(v), "sigma": sigma if sigma is None or math.isfinite(sigma)
                         else None, "unit": unit, "source": "measured", "channel": r.ds.band,
                         "profile_id": system_id, "date": r.ds.night})
    if r.photometric:
        add("bands.zero_point", r.zp1, "mag for 1 e-/s", r.zp1_sigma)
    add("bands.zero_point_at_x", r.zp_mean, "mag for 1 e-/s")
    add("bands.extinction_k", r.k, "mag/airmass", r.k_sigma)
    add("bands.sky_brightness", r.sky_mag, "mag/arcsec2")
    add("bands.fwhm_defocus" if r.defocused else "bands.fwhm_in_focus", r.fwhm_arcsec, "arcsec", r.fwhm_sigma)
    add("bands.scintillation_cy", r.cy, "Young C_Y")
    add("cadence.overhead_s", r.overhead_s, "s")
    return rows


def night_summary(r: NightResult) -> dict:
    def n(v, nd=4):
        return round(v, nd) if v is not None and math.isfinite(v) else None
    worst = r.worst
    return {"night": r.ds.night, "target": r.ds.target, "band": r.ds.band, "log": os.path.basename(r.ds.path),
            "written_by": r.ds.written_by, "frames": r.n_frames, "stars_used": r.stars_used,
            "stars_out": r.stars_out, "zp1": n(r.zp1), "zp1_sigma": n(r.zp1_sigma), "zp_at_x": n(r.zp_mean),
            "x_mean": n(r.x_mean, 3), "k": n(r.k), "k_used": n(r.k_used), "k_used_source": r.k_used_source,
            "photometric": r.photometric, "bin_rms": n(r.bin_rms), "fwhm_arcsec": n(r.fwhm_arcsec, 3),
            "defocus": r.defocus_key if r.defocused else "", "sky": n(r.sky_mag, 3), "moon": n(r.moon_illum, 3),
            "cy": n(r.cy, 3), "overhead_s": n(r.overhead_s, 3), "why_not": r.why_not,
            "worst": worst if worst else ("⚠ not photometric" if r.usable and not r.photometric else "")}
