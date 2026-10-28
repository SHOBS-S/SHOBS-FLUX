"""Check image: evaluate one frame for photon counts, saturation and the things that matter while observing.

For one FITS/XISF frame it works out, with the camera values in use (instrument.py, each with its source):
  * the sky in each color: ADU, electrons per pixel, electrons per pixel per second, and how much of the linear
    range it uses; whether the exposure is sky-limited or read-noise-limited;
  * saturated pixels, and stars whose brightest raw pixel is over the linear limit or saturated;
  * for the brightest stars: the light in a 95 %-light aperture (electrons), SNR, FWHM (or the size of a defocused
    "donut"), the brightest raw pixel against the linear limit and full well, and the exposure at which the star
    would reach the linear limit;
  * a noise check (measured sky noise against what the gain and read noise predict) when the bias level is known.
Each finding is a line with ✔ / ⚠ / ✖ (always the icon AND the color: red–green color blindness), or a plain note
with no icon. ✖ is kept for problems with the frame itself (the sky over half the linear range, nothing to measure),
⚠ for serious things that need action (a guessed gain, a read-noise-limited sky); routine things, such as a few
saturated field stars, are plain notes. The run's status follows the icons: 'bad' only with a ✖ line, 'warn' only
with a ⚠ line.

A processed image (stacked, calibrated or stretched by other software) is recognised (frames.is_processed): its stars
and their sizes are still measured, but electron counts, saturation and the linear limit are not judged on it.

Stars are found and measured on the frame as it comes from the camera: one-shot-color frames on 2×2 superpixels
(each the sum of one Bayer cell, so four real pixels), mono frames as they are. Raw peaks are read on the real pixels.
No calibration frames are needed: the bias level comes from the camera profile or is worked out from the sky noise."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

import frames as fr
import instrument
import shobs_profile as sp

MAX_STARS = 40               # brightest stars measured in full
MIN_EDGE = 16                # superpixels kept clear of the frame edge
MIN_FWHM = 1.2               # measurement pixels: a Gaussian fit narrower than this is noise, not a star
R95_PER_FWHM = 1.04          # a Gaussian holds 95 % of its light within sqrt(-2 ln 0.05)·σ = 2.448 σ = 1.04 FWHM
DONUT_R95_RATIO = 4.0        # a fit whose 95 %-light radius is over 4× what its FWHM implies: defocused (a real
                             # star with strong Moffat wings, β ≈ 2, reaches about 3.3×; a fit on noise, far more)
SATURATION_KEYS = ("SATURATE", "MAXADU", "DATAMAX")     # header keywords that say where the camera saturates
SKY_GREY = 0.20              # the picture's sky level (0 black, 1 white) for a well-exposed frame
SKY_GREY_NOISY = 0.16        # and for a noise-limited one (a softer stretch: the noise stays quiet)


@dataclass
class StarResult:
    n: int
    x: float                 # measurement-image pixels (superpixels for OSC)
    y: float
    flux_adu: float = float("nan")
    flux_e: float = float("nan")
    snr: float = float("nan")
    r95: float = float("nan")          # 95 %-light radius, measurement pixels
    fwhm_px: float = float("nan")
    donut: bool = False
    raw_peak: float = float("nan")      # brightest real pixel, ADU (bias included, like SHOBS-P's raw peaks)
    peak_channel: str = ""
    sky_adu: float = float("nan")      # per measurement pixel
    t_linear: float = float("nan")     # exposure at which the raw peak would reach the linear limit, s
    status: str = "ok"                 # ok / warn (over the linear limit) / bad (saturated)


@dataclass
class CheckResult:
    path: str
    frame: object
    cam: object
    lines: list = field(default_factory=list)          # (kind, text) in reading order
    stars: list = field(default_factory=list)
    channels: dict = field(default_factory=dict)       # channel → {"sky_adu", "noise_adu", "sky_e", "sky_rate"}
    bias: tuple = (None, "none", "")                   # (ADU, source, note)
    scale_arcsec: float | None = None                  # per measurement pixel
    px_raw: int = 1                                    # real pixels per measurement pixel
    preview: np.ndarray | None = None                  # measurement image, for the page's picture
    sat_pixels: int = 0
    over_linear_pixels: int = 0
    processed: str = ""                                # why the frame is a processed image ("" for a raw frame)
    read_noise_limited: bool = False                   # the sky noise is below the read noise (softer picture)
    whys: list = field(default_factory=list)           # (kind, short words) for the History list's Status column
    summary: dict = field(default_factory=dict)        # numbers for the run history

    def add(self, kind: str, text: str, why: str | None = None) -> None:
        """One finding. kind: 'bad' (text starts with ✖), 'warn' (⚠), 'ok' (✔) or 'info' (a plain note, no icon).
        why = a few words for the History list when this finding is the worst of the run."""
        self.lines.append((kind, text))
        if why:
            self.whys.append((kind, why))

    @property
    def status(self) -> str:
        """'bad' when a ✖ line exists, 'warn' when a ⚠ line exists, else 'ok'."""
        texts = [t.lstrip() for _k, t in self.lines]
        if any(t.startswith("✖") for t in texts):
            return "bad"
        if any(t.startswith("⚠") for t in texts):
            return "warn"
        return "ok"

    def worst(self) -> str:
        """The worst finding in a few words ('✖ sky over half the linear range', '3 field stars saturated'), for the
        History list; '' when nothing stands out."""
        for kind, icon in (("bad", "✖ "), ("warn", "⚠ ")):
            for k, w in self.whys:
                if k == kind:
                    return icon + w
        for k, w in self.whys:
            if k == "info":
                return w
        return ""


# ---- small measures ------------------------------------------------------------------------------------------------

def robust_sigma(a: np.ndarray) -> float:
    a = a[np.isfinite(a)]
    if a.size == 0:
        return float("nan")
    med = np.median(a)
    return float(1.4826 * np.median(np.abs(a - med)))


def clipped_median(a: np.ndarray, iters: int = 3) -> float:
    a = a[np.isfinite(a)].ravel()
    if a.size > 2_000_000:
        a = a[:: a.size // 2_000_000 + 1]
    for _ in range(iters):
        if a.size < 10:
            break
        med = np.median(a)
        s = 1.4826 * np.median(np.abs(a - med))
        if s <= 0:
            break
        a = a[np.abs(a - med) < 3 * s]
    return float(np.median(a)) if a.size else float("nan")


def pixel_noise(plane: np.ndarray) -> float:
    """The noise of one pixel (ADU) from differences of neighbours of the same color: gradients, vignetting and the
    flat-field pattern cancel; stars are clipped by the robust estimate."""
    d = (plane[:, 1:] - plane[:, :-1]).ravel()
    if d.size > 4_000_000:
        d = d[:: d.size // 4_000_000 + 1]
    return robust_sigma(d) / math.sqrt(2.0)


def detect_sources(img: np.ndarray, max_sources: int = 120, nsigma: float = 8.0, min_sep: float = 8.0):
    """Star-like peaks (SHOBS-P's detector): local maxima of a lightly smoothed image, nsigma above the background,
    with halo and spike points of brighter stars left out. [(x, y, height)] brightest first."""
    from scipy import ndimage

    data = np.array(img, dtype=np.float32, copy=True)
    finite = np.isfinite(data)
    if not finite.any():
        return []
    med = float(np.median(data[finite][:: max(1, data.size // 2_000_000)]))
    data[~finite] = med
    sample = data[:: max(1, data.shape[0] // 400), :: max(1, data.shape[1] // 400)]
    sigma = robust_sigma(sample) or float(np.std(sample)) or 1.0
    smooth = ndimage.uniform_filter(data, size=3)
    peaks = (smooth == ndimage.maximum_filter(smooth, size=9)) & (smooth > med + nsigma * sigma / 3.0)
    e = MIN_EDGE
    peaks[:e, :] = peaks[-e:, :] = False
    peaks[:, :e] = peaks[:, -e:] = False
    ys, xs = np.nonzero(peaks)
    if xs.size == 0:
        return []
    order = np.argsort(smooth[ys, xs])[::-1][: max_sources * 20]
    picked = []
    for index in order:
        x, y = float(xs[index]), float(ys[index])
        value = float(smooth[ys[index], xs[index]]) - med
        ok = True
        for px_, py_, pv in picked:
            d2 = (x - px_) ** 2 + (y - py_) ** 2
            if d2 <= min_sep ** 2:
                ok = False
                break
            ratio = pv / max(value, 1e-6)
            if ratio > 10 and d2 < min(80.0, 5.0 * math.sqrt(ratio)) ** 2:
                ok = False
                break
        if ok:
            picked.append((x, y, value))
            if len(picked) >= max_sources:
                break
    return picked


def _window(img, x, y, r):
    h, w = img.shape
    x0, x1 = max(0, int(x - r)), min(w, int(x + r) + 1)
    y0, y1 = max(0, int(y - r)), min(h, int(y + r) + 1)
    return img[y0:y1, x0:x1], x0, y0


def measure_star(img: np.ndarray, x: float, y: float, max_r: float = 40.0) -> dict:
    """Light-weighted centre, sky (median of a ring outside the star), the growth curve, and the radius holding
    95 % of the light; works for sharp stars and defocused donuts alike. {} when there is no star."""
    r_out = 10.0
    out = {}
    for _ in range(4):
        sky_in, sky_w = r_out + 3.0, 6.0
        win, x0, y0 = _window(img, x, y, sky_in + sky_w + 1)
        if win.size < 50:
            return {}
        yy, xx = np.mgrid[0: win.shape[0], 0: win.shape[1]]
        cx, cy = x - x0, y - y0
        rr = np.hypot(xx - cx, yy - cy)
        ring = win[(rr >= sky_in) & (rr < sky_in + sky_w)]
        if ring.size < 20:
            return {}
        sky = clipped_median(ring)
        noise = robust_sigma(ring)
        # light-weighted centre inside r_out (twice, moving the centre)
        for _k in range(2):
            m = rr < r_out
            wts = np.clip(win - sky, 0, None) * m
            tot = float(wts.sum())
            if tot <= 0:
                return {}
            cx, cy = float((wts * xx).sum() / tot), float((wts * yy).sum() / tot)
            rr = np.hypot(xx - cx, yy - cy)
        radii = np.arange(1.0, r_out + 0.5, 0.5)
        sub = win - sky
        grow = np.array([float(sub[rr < r].sum()) for r in radii])
        total = float(grow[-1])
        if total <= 0:
            return {}
        # still growing at the edge (by > 1.5 % over the last 2 px)? look further out
        k2 = max(0, len(radii) - 5)
        if grow[k2] > 0 and (total - grow[k2]) / total > 0.015 and r_out < max_r:
            r_out = min(max_r, r_out * 1.6)
            continue
        idx = int(np.searchsorted(grow, 0.95 * total))
        r95 = float(radii[min(idx, len(radii) - 1)])
        # radial profile: a donut's brightest ring sits off the centre
        prof = [float(np.mean(sub[(rr >= a) & (rr < a + 1)])) if np.any((rr >= a) & (rr < a + 1)) else 0.0
                for a in np.arange(0, max(3.0, r95), 1.0)]
        donut = len(prof) >= 3 and int(np.argmax(prof)) >= 2 and prof[0] < 0.8 * max(prof)
        out = {"x": cx + x0, "y": cy + y0, "sky": sky, "noise": noise, "flux": total, "r95": max(r95, 1.0),
               "npix": float(np.sum(rr < max(r95, 1.0))), "donut": bool(donut), "r_out": r_out}
        break
    return out


def gaussian_fwhm(img: np.ndarray, x: float, y: float, sky: float, half: int = 8) -> float:
    """FWHM (measurement pixels) from a round 2-D Gaussian fit; NaN when the fit fails."""
    from scipy.optimize import least_squares

    win, x0, y0 = _window(img, x, y, half)
    if win.shape[0] < 7 or win.shape[1] < 7:
        return float("nan")
    data = win.astype(float) - sky
    yy, xx = np.mgrid[0: data.shape[0], 0: data.shape[1]]
    cx, cy = x - x0, y - y0
    a0 = float(np.nanmax(data))
    if not math.isfinite(a0) or a0 <= 0:
        return float("nan")

    def resid(p):
        a, xc, yc, s, b = p
        return (a * np.exp(-((xx - xc) ** 2 + (yy - yc) ** 2) / (2 * s * s)) + b - data).ravel()

    try:
        fit = least_squares(resid, [a0, cx, cy, 1.5, 0.0],
                            bounds=([0, cx - 3, cy - 3, 0.3, -np.inf], [np.inf, cx + 3, cy + 3, half, np.inf]))
    except Exception:
        return float("nan")
    if not fit.success:
        return float("nan")
    f = 2.3548 * float(fit.x[3])
    return f if MIN_FWHM < f < 2 * half else float("nan")


def size_of(img: np.ndarray, m: dict) -> tuple[float, bool]:
    """(FWHM, defocused) for a star measured by measure_star: the Gaussian FWHM when it is believable, else NaN.
    A fit far narrower than the star's 95 %-light radius implies (a fit that landed on noise or on one side of a
    donut's ring) marks the star as defocused: its size is then the 95 %-light radius, as for a donut."""
    if m["donut"]:
        return float("nan"), True
    f = gaussian_fwhm(img, m["x"], m["y"], m["sky"], half=int(max(6, min(20, 2.5 * m["r95"]))))
    if math.isfinite(f) and f * R95_PER_FWHM * DONUT_R95_RATIO < m["r95"]:
        return float("nan"), True
    return f, False


def header_saturation(hdr: dict, data_max: float) -> tuple[float, str] | None:
    """(ADU, keyword) where the header says the camera saturates (SATURATE, MAXADU or DATAMAX, the first present,
    finite and above zero), or None. DATAMAX is believed only when no pixel is above it (some software writes the
    frame's own largest value there, which is not a saturation level)."""
    for key in SATURATION_KEYS:
        v = fr.hfloat(hdr, key)
        if v is None or v <= 0:
            continue
        if key == "DATAMAX" and data_max > v:
            continue
        return float(v), key
    return None


def display_stretch(img: np.ndarray, read_noise_limited: bool = False) -> np.ndarray:
    """The picture's brightness, 0..1, for the Check image page (display only: no measurement uses it).

    Black point 2σ below the sky (σ = the robust sky noise), white point at the brightest stars (but never closer
    than 25σ to the sky, so noise alone can never fill the range), and an asinh stretch whose softness is chosen so
    the sky sits at a quiet dark grey (SKY_GREY): the noise stays small and smooth and faint stars still show. When
    the frame is noise-limited (a read-noise-limited sky, or no star far above the noise) the sky is set darker
    (a softer stretch) and a light 3×3 smoothing is applied."""
    from scipy import ndimage

    a = np.asarray(img, dtype=np.float32)
    finite = np.isfinite(a)
    if not finite.any():
        return np.zeros(a.shape, dtype=np.float32)
    sample = a[finite][:: max(1, int(finite.sum()) // 1_000_000)]
    med = float(np.median(sample))
    sig = robust_sigma(sample)
    if not math.isfinite(sig) or sig <= 0:
        sig = float(np.std(sample)) or 1.0
    a = np.where(finite, a, med)
    smooth = ndimage.uniform_filter(a, size=3)
    peak = float(np.percentile(smooth[:: max(1, smooth.shape[0] // 1000), :: max(1, smooth.shape[1] // 1000)],
                               99.99))
    noisy = read_noise_limited or (peak - med) < 150.0 * sig
    if noisy:
        a = smooth
    lo = med - 2.0 * sig
    span = max(peak - med, 25.0 * sig) + 2.0 * sig
    target = SKY_GREY_NOISY if noisy else SKY_GREY

    def sky_grey(b: float) -> float:
        return math.asinh(2.0 * sig / b) / math.asinh(span / b)

    # sky_grey falls as b grows: bisect (in log b) for the softness that puts the sky at the target grey
    b_lo, b_hi = 0.02 * sig, 50.0 * sig
    for _ in range(40):
        b = math.sqrt(b_lo * b_hi)
        if sky_grey(b) > target:
            b_lo = b
        else:
            b_hi = b
    b = math.sqrt(b_lo * b_hi)
    z = np.arcsinh(np.clip(a - lo, 0, None) / b) / math.asinh(span / b)
    return np.clip(z, 0.0, 1.0).astype(np.float32)


def raw_peak(frame_data: np.ndarray, pattern, x: float, y: float, r: float, k: int) -> tuple[float, str]:
    """The brightest real pixel within r measurement pixels of (x, y) and its color ('' for mono)."""
    scale = 2 if k == 4 else 1
    rr = r * scale + scale
    cx, cy = x * scale + (scale - 1) / 2.0, y * scale + (scale - 1) / 2.0
    win, x0, y0 = _window(frame_data, cx, cy, rr)
    if win.size == 0:
        return float("nan"), ""
    yy, xx = np.mgrid[0: win.shape[0], 0: win.shape[1]]
    m = np.hypot(xx + x0 - cx, yy + y0 - cy) <= rr
    vals = np.where(m, win, -np.inf)
    i = int(np.argmax(vals))
    py, pxx = divmod(i, win.shape[1])
    ch = ""
    if pattern in fr.BAYER_PATTERNS:
        ch = pattern[((py + y0) % 2) * 2 + ((pxx + x0) % 2)]
    return float(vals.flat[i]), ch


def time_to_linear(peak: float, bias: float, lin: float, t_exp) -> float:
    """The exposure at which a pixel now at peak ADU reaches the linear limit: star AND sky both grow with time,
    so everything above the bias scales (the sky is not a constant floor)."""
    if not t_exp or not math.isfinite(peak) or peak <= bias or lin <= bias:
        return float("nan")
    return t_exp * (lin - bias) / (peak - bias)


def plate_scale(setup: dict, k: int):
    """Arcseconds per measurement pixel from the header (XPIXSZ, XBINNING, FOCALLEN), or None."""
    pix, f, b = setup.get("pixel_um"), setup.get("focal_mm"), setup.get("binning") or 1
    if not pix or not f or f <= 0:
        return None
    return 206.265 * pix * b * (2 if k == 4 else 1) / f


def fmt_e(v: float) -> str:
    if not math.isfinite(v):
        return "—"
    if abs(v) >= 1e6:
        return f"{v / 1e6:.2f} M"
    if abs(v) >= 1e4:
        return f"{v / 1e3:.0f} k"
    if abs(v) >= 100:
        return f"{v:.0f}"
    return f"{v:.1f}"


def fmt_s(t: float) -> str:
    if not math.isfinite(t):
        return "—"
    if t >= 120:
        return f"{t / 60:.1f} min"
    if t >= 10:
        return f"{t:.0f} s"
    if t >= 1:
        return f"{t:.1f} s"
    return f"{t:.2f} s"


# ---- the check -----------------------------------------------------------------------------------------------------

def check_image(path: str, step=None, fill: dict | None = None) -> CheckResult:
    """Read and evaluate one frame. step(words) is called between stages (for the busy bar); fill = header keywords
    you gave once for frames that lack them ({"INSTRUME": "..."}), used only where the header has none. FrameError
    when the file cannot be read."""
    say = step or (lambda _w: None)
    say("Reading the frame")
    frame = fr.read_frame(path)
    if fill:
        upper = {str(k).upper() for k in frame.header}
        added = {k: v for k, v in fill.items() if k.upper() not in upper and v not in (None, "")}
        if added:
            frame.header.update(added)
            frame.setup = sp.setup_key(frame.header)
            frame.notes.append("from your earlier answer: " + ", ".join(f"{k} = {v}" for k, v in added.items()))
    data = frame.data
    hdr = {str(k).upper(): v for k, v in frame.header.items()}
    finite = np.isfinite(data)
    if not finite.any():
        raise fr.FrameError("the frame holds no numbers (every pixel is blank or NaN)")
    mx = float(np.max(data[finite])) if not finite.all() else float(data.max())
    file_max = 65535.0 - (frame.adu_step - 1) if mx <= 65535 else mx
    temp = fr.hfloat(hdr, "CCD-TEMP", "CCD_TEMP", "SENSOR-TEMP", "TEMPERAT")
    processed, why_processed = fr.is_processed(frame)
    hsat = None if processed else header_saturation(hdr, mx)
    cam = instrument.camera_in_use(frame.setup, file_max_adu=file_max, temp_c=temp, header_saturation=hsat)
    res = CheckResult(path=path, frame=frame, cam=cam, processed=why_processed if processed else "")
    if cam.source("saturation_adu") == "header" and cam.source("linear_limit_adu") == "estimate":
        cam.records["linear_limit_adu"] = sp.record(
            round(instrument.LINEAR_FRACTION * cam.value("saturation_adu")), "ADU", "estimate",
            note=f"{instrument.LINEAR_FRACTION:.0%} of the saturation the header gives ({hsat[1]})")
    # a camera that saturates below the file's top (many CCDs, 12-bit cameras written unscaled): pixels pile up at
    # (or just under) the frame's maximum
    if not processed and cam.source("saturation_adu") == "estimate" \
            and frame.kind in ("light", "unknown", "flat") and mx < file_max - 2 * frame.adu_step:
        plateau, n_top = None, 0
        n_at = int(np.count_nonzero(data >= mx - 0.5 * frame.adu_step))
        if n_at >= 3 and mx >= 255:
            plateau, n_top = float(mx), n_at                      # clipped pixels sitting exactly at the top
        elif mx >= 0.6 * file_max:
            n_top = int(np.count_nonzero(data >= 0.97 * mx))
            if n_top >= 50:
                plateau = float(round(0.97 * mx))                 # a soft pile-up just under the top
        if plateau is not None:
            cam.records["saturation_adu"] = sp.record(
                plateau, "ADU", "estimate",
                note=f"inferred: {n_top} pixels pile up at {mx:.0f} ADU, under the file's top ({file_max:.0f})")
            if cam.source("linear_limit_adu") == "estimate":
                cam.records["linear_limit_adu"] = sp.record(
                    round(instrument.LINEAR_FRACTION * plateau), "ADU", "estimate",
                    note=f"{instrument.LINEAR_FRACTION:.0%} of the inferred saturation")
            cam.notes.append(f"This camera seems to saturate at {plateau:.0f} ADU, below the file's top "
                             f"({file_max:.0f}): {n_top} pixels pile up there. Saturation is taken from this frame "
                             "until a bench run measures it.")
    gain = cam.value("gain_e_per_adu", instrument.DEFAULT_GAIN)
    rn = cam.value("read_noise_e", instrument.DEFAULT_READ_NOISE)
    sat = cam.value("saturation_adu", file_max)
    lin = cam.value("linear_limit_adu", instrument.LINEAR_FRACTION * sat)
    full_well = cam.value("full_well_e")
    dark = cam.value("dark_current_e_per_px_s", 0.0) or 0.0
    t_exp = frame.exptime
    pattern = frame.bayer

    # ---- frame line
    s = frame.setup
    bits = [f"{frame.kind}", f"{t_exp:g} s" if t_exp is not None else "no exposure time"]
    if s.get("gain_setting") is not None:
        bits.append(f"gain {s['gain_setting']:g}")
    if s.get("offset") is not None:
        bits.append(f"offset {s['offset']:g}")
    bits.append(f"bin {s.get('binning') or 1}")
    if temp is not None:
        bits.append(f"{temp:.0f} °C")
    bits.append(f"one-shot color ({pattern})" if pattern else "mono")
    filt = hdr.get("FILTER")
    if isinstance(filt, str) and filt.strip():
        bits.append(f"filter {filt.strip()}")
    res.add("info", f"{s.get('instrume') or 'Unknown camera'}: " + ", ".join(bits))
    if processed:
        res.lines.insert(0, ("info", f"This is a processed image ({why_processed}), not a frame as the camera wrote "
                                     "it: electron counts, saturation and the linear limit do not apply to it, so "
                                     "they are not judged. Its stars and their sizes are still measured."))
        res.whys.append(("info", "processed image: stars and sizes only"))
    for n in frame.notes:
        res.add("info", f"Note: {n}")
    missing = sp.setup_missing(frame.setup)
    if "FOCALLEN" in missing or "XPIXSZ" in missing:
        res.add("info", "The header has no " + " or ".join(k for k in ("FOCALLEN", "XPIXSZ") if k in missing) +
                ": the plate scale (″ per pixel) is not known, so sizes are given in pixels only.")
    if not processed and ("INSTRUME" in missing or "GAIN" in missing):
        res.add("info", "The header has no " + " or ".join(k for k in ("INSTRUME", "GAIN") if k in missing) +
                ": a saved camera profile cannot be matched to this frame.")
    if not processed:
        res.add("info", "Camera values: " + ("your saved profile" if cam.profile_path else
                                             "no saved profile yet: header and spec values" +
                                             (f" ({cam.spec_model})" if cam.spec_model else "")))
        for n in cam.notes:
            if n.startswith("⚠"):
                res.add("warn", n, why="gain is a guess" if "gain" in n.lower() else n[2:40])
            elif n.startswith("✖"):
                res.add("bad", n, why=n[2:40])
            else:
                res.add("info", n)

    # ---- pixels at or over the limits
    say("Counting saturated pixels")
    res.sat_pixels = int(np.count_nonzero(data >= sat))
    res.over_linear_pixels = int(np.count_nonzero(data >= lin))

    # ---- sky and noise per color, in the middle of the frame
    say("Measuring the sky")
    cy, cx = fr.central_region(data.shape, 0.5)
    centre = data[cy, cx]
    pl = fr.planes(centre, pattern)
    for ch, plane in pl.items():
        res.channels[ch] = {"sky_adu": clipped_median(plane), "noise_adu": pixel_noise(plane)}
    if frame.kind in ("bias", "dark", "flat"):
        _calibration_frame(res, data, lin, sat, gain)
        img, k = fr.superpixel_sum(data, pattern)
        res.px_raw, res.preview = k, img
        res.scale_arcsec = plate_scale(frame.setup, k)
        res.summary = {"kind": frame.kind, "saturated_pixels": res.sat_pixels,
                       "levels": {ch: round(c["sky_adu"], 1) for ch, c in res.channels.items()},
                       "noise_adu": {ch: round(c["noise_adu"], 2) for ch, c in res.channels.items()},
                       "gain": gain, "gain_source": cam.source("gain_e_per_adu"), "linear_limit": lin,
                       "worst": res.worst() or None}
        return res
    if processed:
        return _processed_image(res, data, pattern)
    bias_v = cam.value("bias_level_adu")
    if bias_v is not None:
        res.bias = (float(bias_v), cam.source("bias_level_adu"), "from the camera profile")
    else:
        # sky above bias from the noise: var(ADU²) = S/g + (rn/g)²  →  S = g·var − rn²/g, per color, then the bias
        # the frame implies; the lowest believable one is kept (a star-free channel gives the cleanest value)
        cands = []
        for ch, c in res.channels.items():
            var = c["noise_adu"] ** 2
            S = max(0.0, gain * var - rn * rn / gain)
            if dark and t_exp:
                S = max(0.0, S - dark * t_exp / gain)
            cands.append(c["sky_adu"] - S)
        top = min(c["sky_adu"] for c in res.channels.values())     # the bias can't be above the sky
        b = max(0.0, min(min(cands), top) if cands else top)
        res.bias = (b, "derived", "worked out from the sky noise and the gain and read noise in use "
                                  "(bias frames will measure it)")
    bias = res.bias[0]
    for ch, c in res.channels.items():
        c["sky_e"] = max(0.0, (c["sky_adu"] - bias) * gain)
        c["sky_rate"] = c["sky_e"] / t_exp if t_exp else float("nan")
        c["pred_noise_adu"] = math.sqrt(c["sky_e"] + rn * rn + dark * (t_exp or 0)) / gain

    names = {"R": "red", "G": "green", "B": "blue", "L": "sky"}
    sky_parts = []
    for ch, c in res.channels.items():
        rate = f", {c['sky_rate']:.2f} e⁻/px/s" if math.isfinite(c["sky_rate"]) else ""
        sky_parts.append(f"{names[ch]} {c['sky_adu']:.0f} ADU = {fmt_e(c['sky_e'])} e⁻/px{rate}")
    how = {"measured": "measured", "derived": "worked out from the sky noise, approximate: bias frames will "
           "measure it"}.get(res.bias[1], "from the profile")
    res.add("info", f"Sky in the middle of the frame (bias {bias:.0f} ADU, {how}): " + "; ".join(sky_parts))
    worst_sky = max(c["sky_adu"] for c in res.channels.values())
    head = lin - bias
    frac = (worst_sky - bias) / head if head > 0 else float("nan")
    if math.isfinite(frac):
        if frac > 0.5:
            res.add("bad", f"✖ The sky fills {frac:.0%} of the linear range: little room left for stars. Shorter "
                           "exposures (or a darker sky).", why="sky over half the linear range")
        elif frac > 0.25:
            res.add("info", f"The sky fills {frac:.0%} of the linear range (bias to linear limit). Shorter "
                            "exposures leave more room for the stars.", why=f"sky {frac:.0%} of the linear range")
        else:
            res.add("ok", f"✔ The sky uses {frac:.1%} of the linear range.")
    # sky- or read-noise-limited (the faintest color decides)
    min_sky_e = min(c["sky_e"] for c in res.channels.values())
    ratio = min_sky_e / (rn * rn) if rn > 0 else float("inf")
    if frame.kind == "light" or frame.kind == "unknown":
        if ratio >= 10:
            res.add("ok", f"✔ Sky-limited: sky noise² is {ratio:.0f}× the read noise² (read noise "
                          f"{rn:.2g} e⁻, {sp.SOURCE_WORDS[cam.source('read_noise_e')]}). Longer exposures would "
                          "not lower the noise per hour.")
        elif ratio >= 3:
            res.add("ok", f"✔ Mostly sky-limited (sky noise² {ratio:.1f}× the read noise²).")
        else:
            res.read_noise_limited = True
            res.add("warn", f"⚠ Read-noise-limited: the sky gives only {min_sky_e:.0f} e⁻/px against a read noise "
                            f"of {rn:.2g} e⁻ ({ratio:.1f}×). For faint stars, longer exposures (or binning) help.",
                    why="read-noise-limited")
    # the noise check needs a known bias (else the bias came from the noise itself)
    if res.bias[1] not in ("derived", "none", "estimate"):
        bad = [(ch, c["noise_adu"] / c["pred_noise_adu"]) for ch, c in res.channels.items()
               if c["pred_noise_adu"] > 0]
        off = [(ch, r) for ch, r in bad if not (0.75 <= r <= 1.33)]
        if off:
            res.add("warn", "⚠ Sky noise does not match the gain and read noise in use: measured / predicted = " +
                    ", ".join(f"{names[ch]} {r:.2f}" for ch, r in off) + ". Check the gain (e⁻/ADU).",
                    why="sky noise does not match the gain")
        elif bad:
            res.add("ok", "✔ Sky noise matches the gain and read noise in use (measured / predicted " +
                    ", ".join(f"{r:.2f}" for _ch, r in bad) + ").")

    # ---- stars
    say("Finding stars")
    img, k = fr.superpixel_sum(data, pattern)
    res.px_raw = k
    res.scale_arcsec = plate_scale(frame.setup, k)
    say("Measuring stars")
    stars = _measure_stars(img, data, pattern, k, gain=gain, rn=rn, dark=dark, bias=bias, sat=sat, lin=lin,
                           t_exp=t_exp)
    res.stars = stars
    say("Summing up")
    n_sat = sum(1 for st in stars if st.status == "bad")
    n_lin = sum(1 for st in stars if st.status == "warn")
    src_lin = sp.SOURCE_WORDS[cam.source("linear_limit_adu")]
    if not stars:
        res.add("bad", "✖ No stars found to measure (clouds, a calibration frame, or very out of focus?).",
                why="no stars found")
    else:
        # field stars over the limits are a note, not a problem with the frame: a target this bright needs shorter
        # exposures, the rest of the field is fine
        if n_sat:
            res.add("info", f"{n_sat} field star{'s' if n_sat != 1 else ''} saturated (raw peak at {sat:.0f} ADU; "
                            f"{'its' if n_sat == 1 else 'their'} light can't be measured; a target this bright needs "
                            "shorter exposures).",
                    why=f"{n_sat} field star{'s' if n_sat != 1 else ''} saturated")
        if n_lin:
            res.add("info", f"{n_lin} field star{'s' if n_lin != 1 else ''} over the linear limit ({lin:.0f} ADU, "
                            f"{src_lin}) but not saturated: fine to look at, not for photometry.",
                    why=f"{n_lin} field star{'s' if n_lin != 1 else ''} over the linear limit")
        if not n_sat and not n_lin:
            res.add("ok", f"✔ All {len(stars)} brightest stars stay under the linear limit ({lin:.0f} ADU, "
                          f"{src_lin}).")
        if res.sat_pixels and not n_sat:
            res.add("info", f"{res.sat_pixels} saturated pixel{'s' if res.sat_pixels != 1 else ''}, none in the "
                            "stars measured (hot pixels or a star off the list).")
        elif res.sat_pixels:
            res.add("info", f"{res.sat_pixels} saturated pixels in the frame.")
        good = [st for st in stars if st.status == "ok"]
        if good:
            top = max(good, key=lambda st: st.raw_peak)
            pct = (top.raw_peak - bias) / (lin - bias) if lin > bias else float("nan")
            well = ((top.raw_peak - bias) * gain / full_well) if full_well else float("nan")
            words = (f"✔ Brightest usable star (#{top.n}) peaks at {top.raw_peak:.0f} ADU: {pct:.0%} of the linear "
                     f"range" + (f", {well:.0%} of full well" if math.isfinite(well) else ""))
            if math.isfinite(top.t_linear):
                words += f"; it would reach the linear limit at about {fmt_s(top.t_linear)}"
            res.add("ok", words + ".")
            snr100 = sum(1 for st in good if st.snr >= 100)
            res.add("ok", f"✔ {snr100} of {len(good)} usable stars reach SNR 100 or more in this one frame "
                          f"(best {max(st.snr for st in good):.0f}, light {fmt_e(top.flux_e)} e⁻ in #{top.n}).")
        _size_line(res, stars, k)
    res.summary = {
        "n_stars": len(stars), "n_saturated_stars": n_sat, "n_over_linear": n_lin,
        "saturated_pixels": res.sat_pixels, "bias_adu": round(bias, 1), "bias_source": res.bias[1],
        "sky": {ch: {"adu": round(c["sky_adu"], 1), "e": round(c["sky_e"], 1),
                     "e_per_s": (round(c["sky_rate"], 4) if math.isfinite(c["sky_rate"]) else None),
                     "noise_adu": round(c["noise_adu"], 2)} for ch, c in res.channels.items()},
        "fwhm_px": summary_fwhm(stars), "donut_d95_px": summary_donut(stars),
        "scale_arcsec": round(res.scale_arcsec, 4) if res.scale_arcsec else None,
        "gain": gain, "gain_source": cam.source("gain_e_per_adu"), "linear_limit": lin,
        "linear_source": cam.source("linear_limit_adu"), "saturation": sat,
        "saturation_source": cam.source("saturation_adu"),
        "worst": res.worst() or None,
    }
    res.preview = img
    return res


def defocused(stars: list) -> bool:
    """Is the frame defocused: donut stars make up half the stars (at least 3), or every sized star is one?"""
    donuts = sum(1 for st in stars if st.donut)
    sharp = sum(1 for st in stars if math.isfinite(st.fwhm_px) and not st.donut and st.status != "bad")
    return bool(donuts) and (donuts >= max(3, len(stars) // 2) or not sharp)


def summary_fwhm(stars: list) -> float | None:
    """The run's FWHM (measurement pixels): the median of the accepted Gaussian fits of unsaturated, focused stars;
    None when there are none or the frame is defocused (it has a 95 %-light size instead)."""
    if defocused(stars):
        return None
    good = [st.fwhm_px for st in stars if math.isfinite(st.fwhm_px) and not st.donut and st.status != "bad"]
    return round(float(np.median(good)), 3) if good else None


def summary_donut(stars: list) -> float | None:
    """The 95 %-light diameter (measurement pixels) of a defocused frame's donut stars, or None."""
    if not defocused(stars):
        return None
    return round(float(np.median([2 * st.r95 for st in stars if st.donut])), 2)


def _measure_stars(img, data, pattern, k: int, gain: float, rn: float, dark: float, bias: float, sat: float | None,
                   lin: float | None, t_exp) -> list[StarResult]:
    """Find and measure up to MAX_STARS stars. sat / lin None (a processed image): no limit judgment per star."""
    sources = detect_sources(img, max_sources=MAX_STARS * 3)
    stars: list[StarResult] = []
    for x, y, _h in sources:
        if len(stars) >= MAX_STARS:
            break
        m = measure_star(img, x, y)
        if not m:
            continue
        if any(math.hypot(o.x - m["x"], o.y - m["y"]) < max(o.r95, m["r95"]) for o in stars):
            continue                     # the same star again (two peaks on one donut's ring)
        st = StarResult(n=len(stars) + 1, x=m["x"], y=m["y"], flux_adu=m["flux"], r95=m["r95"], donut=m["donut"],
                        sky_adu=m["sky"])
        st.fwhm_px, st.donut = size_of(img, m)
        st.raw_peak, st.peak_channel = raw_peak(data, pattern, st.x, st.y, st.r95, k)
        if sat is not None and lin is not None:
            st.flux_e = st.flux_adu * gain
            sky_e_raw = max(0.0, (m["sky"] / k - bias) * gain)          # per real pixel
            noise2 = st.flux_e + m["npix"] * k * (sky_e_raw + rn * rn + dark * (t_exp or 0))
            st.snr = st.flux_e / math.sqrt(noise2) if noise2 > 0 and st.flux_e > 0 else float("nan")
            st.t_linear = time_to_linear(st.raw_peak, bias, lin, t_exp)
            if st.raw_peak >= sat:
                st.status = "bad"
            elif st.raw_peak >= lin:
                st.status = "warn"
        stars.append(st)
    return stars


def _size_line(res: CheckResult, stars: list, k: int) -> None:
    """The seeing (median FWHM) or, for a defocused frame, the donut size."""
    donuts = [st for st in stars if st.donut]
    sharp = [st.fwhm_px for st in stars if math.isfinite(st.fwhm_px) and not st.donut and st.status != "bad"]
    sc = res.scale_arcsec
    if defocused(stars):
        d = float(np.median([2 * st.r95 for st in donuts]))
        res.add("info", f"Defocused (donut stars): 95 %-light diameter {d:.1f} px" +
                (f" = {d * sc:.1f}″" if sc else "") + f" ({len(donuts)} stars).")
    elif sharp:
        f = float(np.median(sharp))
        res.add("info", f"Seeing: FWHM {f:.2f} px" + (f" = {f * sc:.2f}″" if sc else "") +
                f" (median of {len(sharp)} stars" + (", superpixels" if k == 4 else "") + ").")


def _processed_image(res: CheckResult, data, pattern) -> CheckResult:
    """A processed image: stars and their sizes only (no electrons, saturation or linear limit)."""
    img, k = fr.superpixel_sum(data, pattern)
    res.px_raw, res.preview = k, img
    res.scale_arcsec = plate_scale(res.frame.setup, k)
    res.sat_pixels = res.over_linear_pixels = 0
    res.bias = (0.0, "none", "not used on a processed image")
    for c in res.channels.values():
        c["sky_e"], c["sky_rate"], c["pred_noise_adu"] = float("nan"), float("nan"), float("nan")
    stars = _measure_stars(img, data, pattern, k, gain=1.0, rn=0.0, dark=0.0, bias=0.0, sat=None, lin=None,
                           t_exp=None)
    res.stars = stars
    if not stars:
        res.add("bad", "✖ No stars found to measure.", why="no stars found")
    else:
        res.add("info", f"{len(stars)} stars measured.")
        _size_line(res, stars, k)
    res.summary = {"processed": res.processed, "n_stars": len(stars), "fwhm_px": summary_fwhm(stars),
                   "donut_d95_px": summary_donut(stars),
                   "scale_arcsec": round(res.scale_arcsec, 4) if res.scale_arcsec else None,
                   "worst": res.worst() or None}
    return res


def _calibration_frame(res: CheckResult, data, lin: float, sat: float, gain: float) -> None:
    """Flats, bias and darks: the level and noise of each color, and for flats how far up the range they sit."""
    names = {"R": "red", "G": "green", "B": "blue", "L": "level"}
    kind = res.frame.kind
    parts = [f"{names[ch]} {c['sky_adu']:.0f} ADU (noise {c['noise_adu']:.1f} ADU)" for ch, c in res.channels.items()]
    res.add("info", f"{kind.capitalize()} levels in the middle of the frame: " + "; ".join(parts))
    if res.sat_pixels:
        if kind == "flat" and res.sat_pixels >= 1000:
            res.add("bad", f"✖ {res.sat_pixels} saturated pixels in the flat.", why="flat has saturated pixels")
        else:
            res.add("info", f"{res.sat_pixels} saturated pixel{'s' if res.sat_pixels != 1 else ''}"
                    + (" (hot pixels)." if res.sat_pixels < 1000 else "."))
    if kind == "flat":
        top = max(c["sky_adu"] for c in res.channels.values())
        low = min(c["sky_adu"] for c in res.channels.values())
        f_top, f_low = top / lin, low / lin
        if f_top >= 1.0:
            res.add("bad", f"✖ The brightest color is at {f_top:.0%} of the linear limit ({lin:.0f} ADU): "
                           "over it. Shorten the flat exposure.", why="flat over the linear limit")
        elif f_top > 0.85:
            res.add("warn", f"⚠ The brightest color sits at {f_top:.0%} of the linear limit: close to the top.",
                    why="flat close to the linear limit")
        elif f_low < 0.1:
            res.add("warn", f"⚠ The faintest color is only {f_low:.0%} of the linear limit: noisy. Lengthen "
                            "the flat exposure or brighten the panel.", why="flat too faint")
        else:
            res.add("ok", f"✔ Flat level fine: colors from {f_low:.0%} to {f_top:.0%} of the linear limit "
                          f"({lin:.0f} ADU).")
    else:
        res.add("info", f"Bias and darks are measured in bench runs (Camera page, next version). The read noise "
                        f"here would be about {min(c['noise_adu'] for c in res.channels.values()) * gain:.2f} e⁻ "
                        "if this is a bias frame and the gain in use is right.")


def star_detail(res: CheckResult, st: StarResult) -> list[tuple[str, str]]:
    """(label, value) lines for one star (the page's star panel)."""
    cam = res.cam
    gain = cam.value("gain_e_per_adu", instrument.DEFAULT_GAIN)
    lin = cam.value("linear_limit_adu")
    fw = cam.value("full_well_e")
    bias = res.bias[0]
    sc = res.scale_arcsec
    k = res.px_raw
    out = [("Position", f"x {st.x * (2 if k == 4 else 1):.0f}, y {st.y * (2 if k == 4 else 1):.0f}")]
    if res.processed:
        out.append(("Brightest pixel", f"{st.raw_peak:.4g} (processed image: no limits)"))
        out.append(("Aperture radius", f"{st.r95:.1f} px" + (f" = {st.r95 * sc:.1f}″" if sc else "") + " (95 %)"))
        if st.donut:
            out.append(("Shape", "defocused (donut)"))
        elif math.isfinite(st.fwhm_px):
            out.append(("FWHM", f"{st.fwhm_px:.2f} px" + (f" = {st.fwhm_px * sc:.2f}″" if sc else "")))
        return out
    icon = {"ok": "✔", "warn": "⚠", "bad": "✖"}[st.status]
    word = {"ok": "linear", "warn": "over linear", "bad": "saturated"}[st.status]
    out.append(("Brightest pixel", f"{icon} {st.raw_peak:.0f} ADU, {word}"
                + (f" ({st.peak_channel})" if st.peak_channel else "")))
    if lin and lin > bias:
        out.append(("Of linear range", f"{(st.raw_peak - bias) / (lin - bias):.0%}"))
    if fw:
        out.append(("Of full well", f"{(st.raw_peak - bias) * gain / fw:.0%}"))
    out.append(("Light in aperture", f"{fmt_e(st.flux_e)} e⁻"))
    out.append(("Aperture radius", f"{st.r95:.1f} px" + (f" = {st.r95 * sc:.1f}″" if sc else "") + " (95 %)"))
    if st.donut:
        out.append(("Shape", "defocused (donut)"))
    elif math.isfinite(st.fwhm_px):
        out.append(("FWHM", f"{st.fwhm_px:.2f} px" + (f" = {st.fwhm_px * sc:.2f}″" if sc else "")))
    out.append(("SNR (this frame)", f"{st.snr:.0f}" if math.isfinite(st.snr) else "—"))
    out.append(("Sky under it", f"{fmt_e(max(0.0, (st.sky_adu / k - bias) * gain))} e⁻/px"))
    if math.isfinite(st.t_linear):
        out.append(("Linear limit at", fmt_s(st.t_linear)))
    return out


def star_near(res: CheckResult, x: float, y: float, max_d: float = 25.0):
    """The measured star nearest (x, y) in measurement pixels, or None."""
    best, bd = None, max_d
    for st in res.stars:
        d = math.hypot(st.x - x, st.y - y)
        if d < bd:
            best, bd = st, d
    return best


def measure_at(res: CheckResult, x: float, y: float) -> StarResult | None:
    """Measure a star the user clicked that is not on the list (added to res.stars with the next number)."""
    img = res.preview
    if img is None:
        return None
    win, x0, y0 = _window(img, x, y, 6)
    if win.size == 0:
        return None
    iy, ix = divmod(int(np.argmax(win)), win.shape[1])
    m = measure_star(img, ix + x0, iy + y0)
    if not m:
        return None
    cam, frame = res.cam, res.frame
    gain = cam.value("gain_e_per_adu", instrument.DEFAULT_GAIN)
    rn = cam.value("read_noise_e", instrument.DEFAULT_READ_NOISE)
    sat = cam.value("saturation_adu", 65535.0)
    lin = cam.value("linear_limit_adu", 0.8 * sat)
    dark = cam.value("dark_current_e_per_px_s", 0.0) or 0.0
    k, bias, t_exp = res.px_raw, res.bias[0], frame.exptime
    st = StarResult(n=len(res.stars) + 1, x=m["x"], y=m["y"], flux_adu=m["flux"], r95=m["r95"], donut=m["donut"],
                    sky_adu=m["sky"])
    st.fwhm_px, st.donut = size_of(img, m)
    st.raw_peak, st.peak_channel = raw_peak(frame.data, frame.bayer, st.x, st.y, st.r95, k)
    if not res.processed:
        st.flux_e = st.flux_adu * gain
        sky_e_raw = max(0.0, (m["sky"] / k - bias) * gain)
        noise2 = st.flux_e + m["npix"] * k * (sky_e_raw + rn * rn + dark * (t_exp or 0))
        st.snr = st.flux_e / math.sqrt(noise2) if noise2 > 0 and st.flux_e > 0 else float("nan")
        st.t_linear = time_to_linear(st.raw_peak, bias, lin, t_exp)
        st.status = "bad" if st.raw_peak >= sat else ("warn" if st.raw_peak >= lin else "ok")
    res.stars.append(st)
    return st
