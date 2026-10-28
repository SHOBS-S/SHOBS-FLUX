"""The bench run: a photon transfer curve (PTC) from flat pairs and bias pairs, per color.

What the user shoots (one gain, offset, binning and temperature): two flats at each of 10–20 exposures from very
short to past saturation, and 10–20 bias pairs with the camera capped (no light). What FLUX measures, in a central
region of the frame (amp glow and vignetting stay out), for each color of a one-shot-color camera (R, G, B; the two
greens together) or once for a mono camera:

  bias level and read noise      from bias pairs: the mean, and the spread of their difference / √2
  gain (e⁻ per ADU)              the PTC: for each flat pair, signal S (mean above bias) against the temporal
                                 variance V (the variance of the pair's difference / 2). Shot noise gives
                                 V = S / gain + read noise², so the slope of V against S is 1 / gain. Taking the
                                 DIFFERENCE of two frames cancels the flat-field pattern (vignetting, dust, pixel
                                 response); scaling the second frame to the first cancels a panel that flickered
                                 between them.
  full well                      where the PTC turns over (the variance falls as pixels fill), or, when the ADC
                                 tops out first, (saturation − bias) × gain
  linearity and linear limit     the signal against exposure, S = rate × (t + offset), fitted on the low part of
                                 the ladder; every pair's deviation from that line in percent. The linear limit is
                                 the highest raw level (ADU, bias included, as a pixel reads) still within ±1 %.
  exposure-timing offset         the offset in that line: the time a camera really exposes beyond (or short of)
                                 the exposure it was given
  panel stability                the two frames of each pair, and pairs repeated at one exposure, must agree

Every result comes with its uncertainty, how many pairs it rests on, and plain-words findings. Nothing here draws or
writes; the Camera page shows the result and apply_to_profile() turns it into a camera profile."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import numpy as np

import frames as fr
import shobs_profile as sp

LIN_TOL_PCT = 1.0            # the linear limit: the highest level still within this many percent of the line
SAT_FRACTION = 0.001         # a pair with more than this fraction of region pixels at saturation is saturated
PAIR_RATIO_TOL = 0.01        # the two frames of a pair must agree to 1 % (panel flicker / drift)
REGIONS = {"Central 25 %": 0.25, "Central 50 %": 0.5, "Central 10 %": 0.10}
DEFAULT_REGION = "Central 25 %"


class BenchError(Exception):
    """A folder FLUX cannot run a bench on (the message is a plain sentence)."""


# ---- the folder --------------------------------------------------------------------------------------------------

@dataclass
class FrameInfo:
    path: str
    kind: str                  # flat / bias / dark / light / unknown
    exptime: float | None
    header: dict
    shape: tuple | None
    bayer: str | None
    when: str                  # DATE-OBS or the file name: shooting order

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass(eq=False)
class Pair:
    kind: str                  # flat / bias
    exptime: float
    a: FrameInfo
    b: FrameInfo
    order: int                 # shooting order of the pair (0 = first)


@dataclass
class Scan:
    folder: str
    frames: list = field(default_factory=list)
    flat_pairs: list = field(default_factory=list)
    bias_pairs: list = field(default_factory=list)
    setup: dict = field(default_factory=dict)
    bayer: str | None = None
    shape: tuple | None = None
    notes: list = field(default_factory=list)          # (kind, text): kind ok / warn / bad / info
    problems: list = field(default_factory=list)       # stop the run (plain sentences)


def _exp_key(t: float) -> float:
    return round(float(t), 6)


def _setup_word(h: dict) -> tuple:
    return (fr.hfloat(h, "GAIN"), fr.hfloat(h, "OFFSET", "BLKLEVEL"), fr.hfloat(h, "XBINNING", "BINNING") or 1.0)


def scan_folder(folder: str, step=None, bias_folder: str | None = None) -> Scan:
    """Read the headers of every frame in the folder (not subfolders) and pair them. bias_folder: bias frames shot
    separately (they replace any bias frames in folder). Problems that stop a bench run (no flat pairs, frames from
    different gains or sizes) go in scan.problems; everything else is a note."""
    sc = Scan(folder=folder)
    paths = fr.list_frames(folder)
    if not paths:
        sc.problems.append("there are no FITS or XISF frames in this folder")
        return sc
    bias_paths = []
    if bias_folder and os.path.normcase(os.path.abspath(bias_folder)) != os.path.normcase(os.path.abspath(folder)):
        bias_paths = fr.list_frames(bias_folder)
        if not bias_paths:
            sc.notes.append(("warn", "⚠ The bias folder holds no FITS or XISF frames; bias frames in the flats "
                                     "folder are used, if any."))
    infos = []
    paths = paths + bias_paths
    for i, p in enumerate(paths):
        if step and i % 10 == 0:
            step(f"Reading headers… {i} of {len(paths)}")
        try:
            h = fr.read_header(p)
        except fr.FrameError as exc:
            sc.notes.append(("warn", f"⚠ {os.path.basename(p)} left out: {exc}."))
            continue
        nx, ny = fr.hfloat(h, "NAXIS1"), fr.hfloat(h, "NAXIS2")
        shape = (int(ny), int(nx)) if nx and ny else None
        kind = fr.kind_of(h, p)
        t = fr.exptime_of(h)
        when = str(h.get("DATE-OBS") or h.get("DATE") or "")
        if p in bias_paths and kind in ("dark", "unknown", "light") and (t is None or t <= 0.01):
            kind = "bias"                      # in the bias folder, a very short frame is a bias whatever it says
        infos.append(FrameInfo(p, kind, t, h, shape, fr.bayer_of(h), when or os.path.basename(p)))
    if bias_paths:
        separate = [f for f in infos if f.path in set(bias_paths) and f.kind == "bias"]
        if separate:
            dropped = [f for f in infos if f.kind == "bias" and f.path not in set(bias_paths)]
            infos = [f for f in infos if f not in dropped]
            if dropped:
                sc.notes.append(("info", f"The {len(dropped)} bias frames in the flats folder are left out: the "
                                         f"{len(separate)} in the bias folder are used instead."))
    sc.frames = infos
    if not infos:
        sc.problems.append("none of the frames in this folder could be read")
        return sc

    # one setup only: gain, offset, binning, frame size
    used = [f for f in infos if f.kind in ("flat", "bias")]
    others = [f for f in infos if f.kind not in ("flat", "bias")]
    if others:
        kinds = sorted({f.kind for f in others})
        sc.notes.append(("info", f"{len(others)} frame{'s' if len(others) != 1 else ''} that are not flats or "
                                 f"biases ({', '.join(kinds)}) left out" + (" (darks come in the next version)."
                                                                             if "dark" in kinds else ".")))
    if not used:
        sc.problems.append("no flat or bias frames found (FLUX reads IMAGETYP, or 'Flat' / 'Bias' in the file name)")
        return sc
    words = {}
    for f in used:
        words.setdefault(_setup_word(f.header), []).append(f)
    if len(words) > 1:
        desc = "; ".join(f"gain {k[0]:g}, offset {k[1]:g}, bin {k[2]:g}: {len(v)} frames" if None not in k else
                         f"{len(v)} frames with gain/offset not given" for k, v in words.items())
        sc.problems.append(f"the frames are from more than one camera setting ({desc}); a bench run needs one gain, "
                           "offset and binning: put each setting in its own folder")
        return sc
    shapes = {f.shape for f in used}
    if len(shapes) > 1:
        sc.problems.append(f"the frames are not all the same size ({', '.join(f'{s[1]}×{s[0]}' for s in shapes if s)})")
        return sc
    sc.shape = next(iter(shapes))
    bayers = {f.bayer for f in used}
    sc.bayer = next(iter(bayers)) if len(bayers) == 1 else None
    if len(bayers) > 1:
        sc.notes.append(("warn", "⚠ The frames disagree about the Bayer pattern; treated as mono."))
    sc.setup = sp.setup_key(used[0].header)
    temps = [fr.hfloat(f.header, "CCD-TEMP", "CCD_TEMP", "SENSOR-TEMP") for f in used]
    temps = [t for t in temps if t is not None]
    if temps and max(temps) - min(temps) > 5.0:
        sc.notes.append(("warn", f"⚠ The sensor temperature changed by {max(temps) - min(temps):.1f} °C during the "
                                 "frames: the bias level and dark current move with it. Let the cooler settle first."))
    elif temps and max(temps) - min(temps) > 1.0:
        sc.notes.append(("info", f"The sensor temperature varied by {max(temps) - min(temps):.1f} °C across the "
                                 "frames (small; noted)."))

    # pairs: frames of one kind and exposure, in shooting order, taken two at a time
    groups: dict = {}
    for f in used:
        t = f.exptime if f.exptime is not None else 0.0
        groups.setdefault((f.kind, _exp_key(t)), []).append(f)
    singles = 0
    for (kind, t), fs in sorted(groups.items(), key=lambda kv: (kv[0][0], kv[0][1])):
        fs.sort(key=lambda f: (f.when, f.name))
        for i in range(0, len(fs) - 1, 2):
            pr = Pair(kind, t, fs[i], fs[i + 1], 0)
            (sc.flat_pairs if kind == "flat" else sc.bias_pairs).append(pr)
        singles += len(fs) % 2
    allp = sorted(sc.flat_pairs + sc.bias_pairs, key=lambda p: (p.a.when, p.a.name))
    for i, p in enumerate(allp):
        p.order = i
    if singles:
        sc.notes.append(("info", f"{singles} frame{'s' if singles != 1 else ''} without a partner at the same "
                                 "exposure left out (pairs only)."))
    if not sc.flat_pairs:
        sc.problems.append("no flat pairs (two flats at the same exposure) were found")
    elif len({p.exptime for p in sc.flat_pairs}) < 4:
        sc.problems.append(f"only {len({p.exptime for p in sc.flat_pairs})} different flat exposures: a photon "
                           "transfer curve needs at least 4 (10–20 is best)")
    if not sc.bias_pairs:
        sc.notes.append(("warn", "⚠ No bias frames: the gain can still be measured, but the read noise cannot, and "
                                 "the bias level is worked out from the flats."))
    bias_t = [p.exptime for p in sc.bias_pairs]
    if bias_t and max(bias_t) > 0.1:
        sc.notes.append(("warn", f"⚠ The 'bias' frames were exposed for {max(bias_t):g} s: those are darks; bias "
                                 "frames use the shortest exposure the camera allows."))
    return sc


# ---- reading a region -----------------------------------------------------------------------------------------------

def region_of(shape: tuple, fraction: float) -> tuple:
    return fr.central_region(shape, fraction)


def read_region(path: str, region: tuple) -> np.ndarray:
    """The pixels of one region of a frame (float32, file ADU), reading only that part of a FITS file."""
    ys, xs = region
    if not path.lower().endswith(".xisf"):
        try:
            import warnings
            from astropy.io import fits
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                with fits.open(path, memmap=True, do_not_scale_image_data=True) as hdul:
                    hdu = next((h for h in hdul if getattr(h, "header", None) is not None
                                and h.header.get("NAXIS", 0) >= 2), None)
                    if hdu is None:
                        raise fr.FrameError("the FITS file holds no image")
                    hd = hdu.header
                    raw = hdu.data[ys, xs] if hd.get("NAXIS") == 2 else hdu.data[0, ys, xs]
                    out = np.asarray(raw, dtype=np.float64)
                    bs, bz = float(hd.get("BSCALE", 1.0) or 1.0), float(hd.get("BZERO", 0.0) or 0.0)
                    if bs != 1.0 or bz != 0.0:
                        out = out * bs + bz
                    return out.astype(np.float32)
        except fr.FrameError:
            raise
        except (OSError, ValueError, TypeError, IndexError) as exc:
            msg = str(exc).lower()
            if "truncat" in msg or "too small" in msg or "reshape" in msg or "mmap" in msg:
                raise fr.FrameError("the FITS file is cut short (an incomplete copy?)") from None
            # fall back to a whole read (unusual FITS layouts)
    frame = fr.read_frame(path)
    return frame.data[ys, xs]


# ---- statistics --------------------------------------------------------------------------------------------------

def clipped_stats(a: np.ndarray, nsig: float = 4.5, iters: int = 4) -> tuple[float, float, int]:
    """Mean, standard deviation and count after clipping hot pixels and cosmic rays (robust start, then
    iterative)."""
    v = np.asarray(a, dtype=np.float64).ravel()
    v = v[np.isfinite(v)]
    if v.size < 10:
        return float("nan"), float("nan"), int(v.size)
    med = np.median(v)
    mad = np.median(np.abs(v - med)) * 1.4826
    s = mad if mad > 0 else np.std(v)
    m = med
    keep = v
    for _ in range(iters):
        if s <= 0:
            break
        keep = v[np.abs(v - m) <= nsig * s]
        if keep.size < 10:
            break
        m2, s2 = float(np.mean(keep)), float(np.std(keep, ddof=1))
        if abs(m2 - m) < 1e-6 * max(1.0, abs(m)) and abs(s2 - s) < 1e-6 * max(1.0, s):
            m, s = m2, s2
            break
        m, s = m2, s2
    return float(m), float(s), int(keep.size)


def channel_planes(data: np.ndarray, bayer: str | None) -> dict:
    """{'R','G','B'} raw pixels for color (greens together), {'L'} for mono."""
    return fr.planes(data, bayer)


@dataclass
class PairStat:
    exptime: float
    order: int
    names: tuple
    mean_raw: float            # mean of the two frames (ADU, bias included)
    signal: float              # mean_raw − bias
    var: float                 # temporal variance per frame (ADU²): var(A − B·k) / 2, quantization removed
    var_err: float
    ratio: float               # mean(A − bias) / mean(B − bias): the panel between the two frames
    sat_frac: float            # fraction of region pixels at saturation (either frame)
    saturated: bool = False
    used_gain: bool = False
    used_lin: bool = False
    deviation_pct: float = float("nan")


@dataclass
class ChannelResult:
    ch: str
    bias_adu: float = float("nan")
    bias_source: str = "none"          # measured (bias pairs) / derived (from the PTC) / none
    read_noise_adu: float = float("nan")
    gain: float = float("nan")         # e-/ADU (file units)
    gain_sigma: float = float("nan")
    read_noise_e: float = float("nan")
    full_well_e: float = float("nan")
    full_well_how: str = ""            # "well" (the pixels fill first) / "ADC top" / ""
    plateau_adu: float = float("nan")  # the mean level the saturated pairs stop at
    lin_intercept: float = float("nan")  # c in M = c + rate·t (ADU): bias + rate × timing offset
    linear_is_bound: bool = False      # the limit is only "at least": no pair was seen bending
    saturation_adu: float = float("nan")
    rate: float = float("nan")         # ADU/s above bias
    t_offset: float = float("nan")     # s
    t_offset_sigma: float = float("nan")
    linear_limit_adu: float = float("nan")
    linear_limit_how: str = ""
    max_dev_pct: float = float("nan")  # largest |deviation| below the linear limit
    pairs: list = field(default_factory=list)
    n_gain: int = 0
    n_lin: int = 0
    ptc_intercept: float = float("nan")


@dataclass
class BenchResult:
    folder: str
    region: tuple
    region_name: str
    shape: tuple
    bayer: str | None
    adu_step: int
    setup: dict
    channels: dict = field(default_factory=dict)       # ch → ChannelResult
    lines: list = field(default_factory=list)          # (kind, text) findings, worst first is NOT implied
    gain: float = float("nan")                         # camera-wide
    gain_sigma: float = float("nan")
    read_noise_e: float = float("nan")
    full_well_e: float = float("nan")
    saturation_adu: float = float("nan")
    linear_limit_adu: float = float("nan")
    linear_is_bound: bool = False
    bias_adu: float = float("nan")
    t_offset: float = float("nan")
    t_offset_sigma: float = float("nan")
    drift_pct: float = float("nan")
    n_flat_pairs: int = 0
    n_bias_pairs: int = 0
    lit_bias: bool = False
    bias_light: dict = field(default_factory=dict)     # ch → (correlation with the flat pattern, ADU implied)
    temp_c: float | None = None
    date: str = ""
    thumb: np.ndarray | None = None                   # a small picture of one flat, for the region box
    thumb_scale: int = 1
    summary: dict = field(default_factory=dict)
    shorts: dict = field(default_factory=dict)          # line index → the few words the History list shows

    def add(self, kind: str, text: str, short: str | None = None) -> None:
        self.lines.append((kind, text))
        if short and kind in ("bad", "warn"):
            self.shorts[len(self.lines) - 1] = short

    @property
    def status(self) -> str:
        kinds = {k for k, _ in self.lines}
        return "bad" if "bad" in kinds else ("warn" if "warn" in kinds else "ok")

    @property
    def worst(self) -> str:
        """The worst finding in a few words (for the History list)."""
        import re
        for want in ("bad", "warn"):
            for i, (k, t) in enumerate(self.lines):
                if k == want:
                    if i in self.shorts:
                        return f"{'✖' if k == 'bad' else '⚠'} {self.shorts[i]}"
                    return re.split(r"[:(;.]", t)[0].strip()[:70]
        return ""


# ---- the run -------------------------------------------------------------------------------------------------------

def _wls(x, y, w):
    """Weighted straight line y = a + b·x: (a, b, cov 2×2, chi2 per dof)."""
    x, y, w = (np.asarray(v, dtype=float) for v in (x, y, w))
    W = np.sum(w)
    xm, ym = np.sum(w * x) / W, np.sum(w * y) / W
    sxx = np.sum(w * (x - xm) ** 2)
    if sxx <= 0:
        return float("nan"), float("nan"), np.full((2, 2), np.nan), float("nan")
    b = np.sum(w * (x - xm) * (y - ym)) / sxx
    a = ym - b * xm
    dof = max(1, len(x) - 2)
    chi2 = float(np.sum(w * (y - a - b * x) ** 2) / dof)
    var_b = 1.0 / sxx
    var_a = 1.0 / W + xm * xm / sxx
    cov_ab = -xm / sxx
    cov = np.array([[var_a, cov_ab], [cov_ab, var_b]]) * max(chi2, 1.0)
    return float(a), float(b), cov, chi2


def _saturation_level(arrs: list, file_top: float, step: int) -> float:
    """The level pixels pile up at in the brightest frames: the frame maximum when many pixels share it (an ADC
    top or full-well clip), else the file's top."""
    best = None
    for a in arrs:
        m = float(np.max(a))
        n_at = int(np.count_nonzero(a >= m - 0.5 * step))
        if n_at >= max(20, a.size * 0.0005):
            best = m if best is None else max(best, m)
    return best if best is not None else file_top


def run_bench(folder: str, region_name: str = DEFAULT_REGION, step=None, scan: Scan | None = None,
              bias_folder: str | None = None) -> BenchResult:
    """The whole bench run on a folder (and, optionally, bias frames from a folder of their own). BenchError (plain
    words) when it cannot be done."""
    sc = scan or scan_folder(folder, step, bias_folder)
    if sc.problems:
        raise BenchError(sc.problems[0])
    frac = REGIONS.get(region_name, REGIONS[DEFAULT_REGION])
    reg = region_of(sc.shape, frac)
    res = BenchResult(folder=folder, region=reg, region_name=region_name, shape=sc.shape, bayer=sc.bayer,
                      adu_step=1, setup=sc.setup)
    chans = ("R", "G", "B") if sc.bayer in fr.BAYER_PATTERNS else ("L",)
    for c in chans:
        res.channels[c] = ChannelResult(c)
    for k, t in sc.notes:
        res.add(k, t)
    temps = [fr.hfloat(f.header, "CCD-TEMP", "CCD_TEMP") for f in sc.frames]
    temps = [t for t in temps if t is not None]
    res.temp_c = float(np.median(temps)) if temps else None
    res.date = (str(sc.frames[0].header.get("DATE-OBS") or "")[:10]) or sp.now_iso()[:10]
    total = 2 * (len(sc.flat_pairs) + len(sc.bias_pairs))
    done = [0]

    def load(f: FrameInfo) -> np.ndarray:
        done[0] += 1
        if step:
            step(f"Reading frames… {done[0]} of {total}")
        return read_region(f.path, reg)

    # ADU step from the first flat: a 12/14-bit camera scaled to 16 bits
    first = load(sc.flat_pairs[0].a)
    res.adu_step = fr.adu_step(first)
    q = res.adu_step ** 2 / 12.0                      # quantization variance per frame
    top_hdr = fr.hfloat(sc.flat_pairs[0].a.header, "DATAMAX", "SATURATE", "MAXADU")
    file_top = top_hdr if top_hdr and top_hdr > 0 else 65535.0

    # bias pairs
    bias_lv = {c: [] for c in chans}
    bias_rn = {c: [] for c in chans}
    bias_blocks = {c: [] for c in chans}
    for p in sc.bias_pairs:
        A, B = load(p.a), load(p.b)
        pa, pb = channel_planes(A, sc.bayer), channel_planes(B, sc.bayer)
        for c in chans:
            ma, _, _ = clipped_stats(pa[c])
            mb, _, _ = clipped_stats(pb[c])
            _, sd, _ = clipped_stats(pa[c] - pb[c])
            bias_lv[c].append(0.5 * (ma + mb))
            bias_rn[c].append(math.sqrt(max(sd * sd / 2.0 - q, 0.0)))
            bias_blocks[c].append(block_mean(0.5 * (pa[c].astype(np.float64) + pb[c])))
    res.n_bias_pairs = len(sc.bias_pairs)

    # flat pairs: raw means and the difference variance (bias not yet subtracted: it cancels in the difference,
    # and the ratio correction is redone once the bias is known)
    ordered = sorted(sc.flat_pairs, key=lambda p: p.exptime)
    # the brightest pairs first: the level the pixels pile up at (saturation) is needed for every pair
    tops = ordered[-2:]
    cache = {}
    for p in tops:
        cache[id(p)] = (first if p.a is sc.flat_pairs[0].a else load(p.a), load(p.b))
    sat_level = _saturation_level([x for ab in cache.values() for x in ab], file_top, res.adu_step)
    sat_at = sat_level - 0.5 * res.adu_step
    raw = []
    for i, p in enumerate(ordered):
        if id(p) in cache:
            A, B = cache.pop(id(p))
        else:
            A = first if p.a is sc.flat_pairs[0].a else load(p.a)
            B = load(p.b)
        pa, pb = channel_planes(A, sc.bayer), channel_planes(B, sc.bayer)
        raw.append((p, {c: pair_moments(pa[c], pb[c], sat_at) for c in chans}))
        if res.thumb is None and i == len(ordered) // 3:
            res.thumb, res.thumb_scale = _thumb(p.a.path, sc.shape)
    res.saturation_adu = sat_level

    # the bias level per channel: measured from the bias pairs, unless those saw light
    lit = False
    if sc.bias_pairs:
        lv = {c: float(np.median(bias_lv[c])) for c in chans}
        rn = {c: float(np.median(bias_rn[c])) for c in chans}
        if len(chans) == 3:
            spread = max(lv.values()) - min(lv.values())
            if spread > max(15.0, 4.0 * max(rn.values())):
                lit = True
        dim = min((raw[0][1][c].ma for c in chans), default=float("inf"))
        if any(v > dim for v in lv.values()):
            lit = True
        # the flats' pattern in the bias frames: a mid-ladder pair well below saturation
        res.bias_light = {}
        for c in chans:
            mid = [m for _, mom in raw for m in [mom[c]] if m.n_sat == 0 and 0.5 * (m.ma + m.mb) - lv[c] >
                   20 * max(rn[c], 1.0) and 0.5 * (m.ma + m.mb) < lv[c] + 0.6 * (sat_level - lv[c])]
            if not mid or not bias_blocks[c]:
                continue
            fb = max(mid, key=lambda m: m.ma).blocks
            bb = np.mean(bias_blocks[c], axis=0)
            r, slope = light_in_bias(bb, fb)
            res.bias_light[c] = (r, slope)
            if r > 0.6 and slope > max(2.0, 0.5 * rn[c]):
                lit = True
        if lit:
            res.lit_bias = True
            res.add("bad", "✖ The bias frames saw light (they carry the flats' pattern; levels " +
                    ", ".join(f"{c} {lv[c]:.0f}" for c in chans) + " ADU), so the panel or room light reached the "
                    "sensor. Read noise cannot be measured from them, and the bias level is worked out from the "
                    "flats instead. Shoot bias pairs again with the panel OFF and the scope capped.",
                    short="bias frames saw light")
        else:
            for c in chans:
                ch = res.channels[c]
                ch.bias_adu, ch.bias_source = lv[c], "measured"
                ch.read_noise_adu = rn[c]

    # per channel PTC
    for c in chans:
        _channel(res.channels[c], raw, c, q, sat_level, res.adu_step)
    # a bias worked out from the flats: one level for all colors (one ADC), each color's own estimate is noisier
    derived = [res.channels[c].bias_adu for c in chans if res.channels[c].bias_source == "derived"]
    if len(chans) > 1 and len(derived) == len(chans):
        common = float(np.median(derived))
        for c in chans:
            ch = res.channels[c]
            ch.bias_adu = common
            _channel(ch, raw, c, q, sat_level, res.adu_step, derive=False)
            ch.bias_source = "derived"
    for c in chans:
        ch = res.channels[c]
        if not math.isnan(ch.gain) and not math.isnan(ch.read_noise_adu):
            ch.read_noise_e = ch.read_noise_adu * ch.gain

    # camera-wide values
    good = [res.channels[c] for c in chans if math.isfinite(res.channels[c].gain)]
    if not good:
        raise BenchError("no flat pairs fell in the straight part of the curve, so the gain cannot be measured "
                         "(are they all saturated, or all very dark?)")
    w = np.array([1.0 / max(ch.gain_sigma, 1e-9) ** 2 for ch in good])
    g = np.array([ch.gain for ch in good])
    res.gain = float(np.sum(w * g) / np.sum(w))
    res.gain_sigma = float(max(1.0 / math.sqrt(np.sum(w)), np.std(g) / math.sqrt(len(g)) if len(g) > 1 else 0.0))
    rne = [ch.read_noise_e for ch in good if math.isfinite(ch.read_noise_e)]
    res.read_noise_e = float(np.median(rne)) if rne else float("nan")
    res.bias_adu = float(np.mean([ch.bias_adu for ch in good])) if all(
        ch.bias_source == "measured" for ch in good) else float("nan")
    fw = [ch.full_well_e for ch in good if math.isfinite(ch.full_well_e)]
    res.full_well_e = float(min(fw)) if fw else float("nan")
    # one ADC reads every color: a level any color stayed linear at holds for all, unless a color bent sooner
    bends = [ch.linear_limit_adu for ch in good if math.isfinite(ch.linear_limit_adu) and not ch.linear_is_bound]
    bounds = [ch.linear_limit_adu for ch in good if math.isfinite(ch.linear_limit_adu) and ch.linear_is_bound]
    if bends:
        res.linear_limit_adu = float(min(bends))
        res.linear_is_bound = False
    elif bounds:
        res.linear_limit_adu = float(max(bounds))
        res.linear_is_bound = True
    off = [(ch.t_offset, ch.t_offset_sigma) for ch in good if math.isfinite(ch.t_offset)]
    if off:
        wo = np.array([1.0 / max(s, 1e-6) ** 2 for _, s in off])
        res.t_offset = float(np.sum(wo * np.array([o for o, _ in off])) / np.sum(wo))
        res.t_offset_sigma = float(1.0 / math.sqrt(np.sum(wo)))
    res.n_flat_pairs = len(sc.flat_pairs)
    res.drift_pct = _drift(res, raw)
    _findings(res, sc)
    res.summary = summary_of(res)
    return res


def _thumb(path: str, shape: tuple):
    try:
        f = max(1, int(math.ceil(max(shape) / 600)))
        frame = fr.read_frame(path)
        d = frame.data
        h, w = d.shape[0] // f * f, d.shape[1] // f * f
        return d[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3)).astype(np.float32), f
    except (fr.FrameError, ValueError, MemoryError):
        return None, 1


@dataclass
class Moments:
    """What one pair leaves behind in one color (so no pixels are kept): the two clipped means, the variances of
    each frame and their covariance on the same clipped pixels, and the top of each frame's pixel values."""
    ma: float
    mb: float
    va: float
    vb: float
    cab: float
    n: int
    n_sat: int                      # pixels at saturation (the larger count of the two frames)
    size: int
    blocks: np.ndarray | None = None  # the pair's average in 16×16 blocks: its large-scale pattern


BLOCK = 16


def block_mean(a: np.ndarray, n: int = BLOCK) -> np.ndarray:
    h, w = a.shape[0] // n * n, a.shape[1] // n * n
    if h == 0 or w == 0:
        return np.zeros((0, 0))
    return a[:h, :w].reshape(h // n, n, w // n, n).mean(axis=(1, 3))


def pair_moments(A: np.ndarray, B: np.ndarray, sat_at: float) -> Moments:
    a = np.asarray(A, dtype=np.float64).ravel()
    b = np.asarray(B, dtype=np.float64).ravel()
    ma0, mb0 = float(np.median(a)), float(np.median(b))
    k0 = ma0 / mb0 if mb0 > 0 else 1.0
    d = a - k0 * b
    _, sd, _ = clipped_stats(d)
    md = float(np.median(d))
    keep = np.isfinite(d) & (np.abs(d - md) <= 5.0 * sd) if sd > 0 else np.isfinite(d)
    a, b = a[keep], b[keep]
    ma, mb = float(a.mean()), float(b.mean())
    va = float(np.mean((a - ma) ** 2))
    vb = float(np.mean((b - mb) ** 2))
    cab = float(np.mean((a - ma) * (b - mb)))
    n_sat = max(int(np.count_nonzero(A >= sat_at)), int(np.count_nonzero(B >= sat_at)))
    blocks = block_mean(0.5 * (np.asarray(A, dtype=np.float64) + np.asarray(B, dtype=np.float64)))
    return Moments(ma, mb, va, vb, cab, int(a.size), n_sat, int(A.size), blocks)


def light_in_bias(bias_blocks: np.ndarray, flat_blocks: np.ndarray) -> tuple[float, float]:
    """Did light reach the bias frames? Their large-scale pattern is compared with a flat's (vignetting, dust): a
    bias that saw the panel carries the flat's pattern. Returns (correlation, light level in ADU implied)."""
    if bias_blocks.size < 12 or bias_blocks.shape != flat_blocks.shape:
        return float("nan"), float("nan")
    f = flat_blocks / max(float(np.mean(flat_blocks)), 1e-9) - 1.0
    b = bias_blocks - float(np.mean(bias_blocks))
    sf, sb = float(np.std(f)), float(np.std(b))
    if sf <= 0 or sb <= 0:
        return 0.0, 0.0
    r = float(np.mean(f * b) / (sf * sb))
    slope = float(np.mean(f * b) / (sf * sf))          # ADU of bias pattern per unit of relative flat pattern
    return r, slope


def _channel(ch: ChannelResult, raw: list, c: str, q: float, sat_level: float, step: int,
             derive: bool = True) -> None:
    """Fill one color's PTC, linearity and timing from the flat pairs (raw: [(Pair, {ch: Moments})]). derive=False:
    keep ch.bias_adu as given (a common level for all colors)."""
    stats = []
    for p, mom in raw:
        m = mom[c]
        sat_frac = m.n_sat / max(m.size, 1)
        stats.append((p, m, sat_frac))

    def measure(bias):
        out = []
        for p, m, sat_frac in stats:
            sa, sb = m.ma - bias, m.mb - bias
            k = sa / sb if sb > 0 else 1.0
            vd = m.va + k * k * m.vb - 2.0 * k * m.cab
            var = vd / (1.0 + k * k) - q
            mean_raw = 0.5 * (m.ma + m.mb)
            ps = PairStat(p.exptime, p.order, (p.a.name, p.b.name), mean_raw, mean_raw - bias, var,
                          abs(var) * math.sqrt(2.0 / max(m.n, 2)), (sa / sb) if sb > 0 else float("nan"), sat_frac)
            ps.saturated = sat_frac > SAT_FRACTION or mean_raw >= sat_level - 2 * step
            out.append(ps)
        return out

    def fit(pts, top):
        _fit_gain(ch, pts, top)
        if _mark_full(ch, pts, top):
            _fit_gain(ch, pts, top)

    bias = ch.bias_adu if math.isfinite(ch.bias_adu) else 0.0     # provisional 0: the PTC slope does not care
    pts = measure(bias)
    fit(pts, sat_level - bias)
    if derive and ch.bias_source != "measured" and math.isfinite(ch.gain) and math.isfinite(ch.ptc_intercept):
        # V = (M − bias)/g + RN²  →  intercept on raw means = −bias/g + RN²; RN² is small next to bias/g
        rn2 = ch.read_noise_adu ** 2 if math.isfinite(ch.read_noise_adu) else 0.0
        b_est = (rn2 - ch.ptc_intercept) * ch.gain
        lo = min(s[1].ma for s in stats)
        if 0 <= b_est < lo:
            ch.bias_adu, ch.bias_source = b_est, "derived"
            pts = measure(b_est)
            fit(pts, sat_level - b_est)
    ch.pairs = pts
    ch.saturation_adu = sat_level
    _fit_linearity(ch, pts, sat_level)
    if math.isfinite(ch.bias_adu):
        # the gain again, on the straight part only (a bending response bends the variance three times as fast)
        if math.isfinite(ch.linear_limit_adu):
            lin_top = ch.linear_limit_adu - ch.bias_adu
            for tol in (0.3, 0.5):
                straight = [p for p in pts if not p.saturated and 0 < p.signal <= lin_top and p.var > 0 and
                            (not math.isfinite(p.deviation_pct) or abs(p.deviation_pct) <= tol)]
                if len(straight) >= 4:
                    _fit_gain(ch, pts, sat_level - ch.bias_adu, only=straight)
                    break
        _full_well(ch, pts, sat_level)


def _mark_full(ch: ChannelResult, pts: list, top: float) -> bool:
    """Pairs whose pixels are full (the well, not the ADC, stopped them, so no pile-up at one value): the variance
    has collapsed far below what the gain predicts. Marked saturated; True when any were newly marked."""
    if not math.isfinite(ch.gain):
        return False
    new = False
    for p in pts:
        if not p.saturated and p.signal > 0.3 * top and p.var < 0.5 * p.signal / ch.gain:
            p.saturated = True
            new = True
    return new


def _fit_gain(ch: ChannelResult, pts: list, top: float, only: list | None = None) -> None:
    """The straight part of the PTC: pairs between 2 % and 70 % of the range (not saturated), refitted after
    dropping points more than 4 σ off the line (a flickering pair, a pixel defect). only: use just these pairs."""
    ok = only if only is not None else [p for p in pts if not p.saturated and p.var > 0
                                        and 0.02 * top <= p.signal <= 0.70 * top]
    if only is None and len(ok) < 3:
        ok = [p for p in pts if not p.saturated and p.var > 0 and p.signal > 0]
        ok = sorted(ok, key=lambda p: p.signal)[: max(3, len(ok) * 2 // 3)]
    for p in pts:
        p.used_gain = False
    if len(ok) < 3:
        ch.gain = ch.gain_sigma = float("nan")
        return
    for _ in range(3):
        x = np.array([p.signal for p in ok])
        y = np.array([p.var for p in ok])
        w = 1.0 / np.array([max(p.var_err, 1e-6) for p in ok]) ** 2
        a, b, cov, chi2 = _wls(x, y, w)
        if not (b > 0):
            ch.gain = ch.gain_sigma = float("nan")
            return
        resid = (y - a - b * x) / np.sqrt(1.0 / w) / math.sqrt(max(chi2, 1.0))
        bad = np.abs(resid) > 4.0
        if not bad.any() or len(ok) - bad.sum() < 3:
            break
        ok = [p for p, bd in zip(ok, bad) if not bd]
    for p in ok:
        p.used_gain = True
    ch.gain = 1.0 / b
    ch.gain_sigma = math.sqrt(cov[1, 1]) / (b * b)
    ch.ptc_intercept = a
    ch.n_gain = len(ok)


def _fit_linearity(ch: ChannelResult, pts: list, sat_level: float) -> None:
    """The raw mean level against exposure, M = c + rate·t, fitted on the pairs between 2 % and 30 % of the range;
    each pair's deviation from that line, in percent of its exposure's signal (rate·t); the linear limit = the highest
    raw level still within ±LIN_TOL_PCT (interpolated to the first pair beyond it). Working on raw levels keeps the
    linearity independent of the bias: c is the bias plus rate × timing offset, and the offset comes out only when
    the bias was measured."""
    good = [p for p in pts if not p.saturated and p.exptime > 0]
    if len(good) < 3:
        return
    lo_raw = min(p.mean_raw for p in good)
    rng = sat_level - (ch.bias_adu if math.isfinite(ch.bias_adu) else lo_raw)
    floor = ch.bias_adu if math.isfinite(ch.bias_adu) else 0.0
    base = [p for p in good if 0.02 * rng <= p.mean_raw - floor <= 0.30 * rng]
    if len({p.exptime for p in base}) < 3:
        base = sorted(good, key=lambda p: p.exptime)[: max(3, len(good) // 2)]
    if len({p.exptime for p in base}) < 2:
        return
    t = np.array([p.exptime for p in base])
    m = np.array([p.mean_raw for p in base])
    w = 1.0 / np.maximum((m - floor) * 0.002, 1.0) ** 2            # ~0.2 % per pair: panel and photon noise
    c, b, cov, _ = _wls(t, m, w)
    if not (b > 0):
        return
    ch.rate = b
    if ch.bias_source == "measured":
        ch.t_offset = (c - ch.bias_adu) / b
        ch.t_offset_sigma = math.sqrt(cov[0, 0] / b ** 2 + (c - ch.bias_adu) ** 2 * cov[1, 1] / b ** 4
                                      - 2 * (c - ch.bias_adu) * cov[0, 1] / b ** 3) \
            if np.all(np.isfinite(cov)) else float("nan")
    ch.lin_intercept = c
    for p in base:
        p.used_lin = True
    ch.n_lin = len(base)
    for p in pts:
        if p.exptime > 0:
            p.deviation_pct = 100.0 * (p.mean_raw - c - b * p.exptime) / (b * p.exptime)
    # walk up the ladder: the last pair within tolerance before the first one outside it
    ladder = sorted([p for p in pts if p.exptime > 0 and math.isfinite(p.deviation_pct)], key=lambda p: p.signal)
    last_ok, first_bad = None, None
    for p in ladder:
        if p.saturated:
            first_bad = first_bad or p
            break
        if abs(p.deviation_pct) <= LIN_TOL_PCT:
            last_ok = p
        else:
            # one stray pair (flicker) does not end the linear range when the next one is back in
            nxt = [q for q in ladder if q.signal > p.signal and not q.saturated]
            if nxt and abs(nxt[0].deviation_pct) <= LIN_TOL_PCT and p.mean_raw - floor < 0.5 * rng:
                continue
            first_bad = p
            break
    if last_ok is None:
        return
    lim = last_ok.mean_raw
    how = "the brightest pair still within ±{:g} %".format(LIN_TOL_PCT)
    if first_bad is not None and not first_bad.saturated and math.isfinite(first_bad.deviation_pct):
        d0, d1 = abs(last_ok.deviation_pct), abs(first_bad.deviation_pct)
        if d1 > d0:
            f = (LIN_TOL_PCT - d0) / (d1 - d0)
            lim = last_ok.mean_raw + f * (first_bad.mean_raw - last_ok.mean_raw)
            how = f"where the curve leaves ±{LIN_TOL_PCT:g} %"
    elif first_bad is not None and first_bad.saturated:
        how = (f"straight up to the brightest unsaturated pair ({last_ok.mean_raw:,.0f} ADU); the next pair "
               "saturated, so the limit is at least that")
        ch.linear_is_bound = True
    else:
        how = "straight up to the brightest pair shot; the limit may be higher"
        ch.linear_is_bound = True
    ch.linear_limit_adu = float(lim)
    ch.linear_limit_how = how
    below = [abs(p.deviation_pct) for p in ladder if p.mean_raw <= lim and math.isfinite(p.deviation_pct)]
    ch.max_dev_pct = float(max(below)) if below else float("nan")


def _full_well(ch: ChannelResult, pts: list, sat_level: float) -> None:
    """Full well: the level the mean signal stops at (the plateau of the saturated pairs) times the gain. When that
    plateau is the ADC's top (pixels pile up at one value), the ADC stops first and the sensor's true well may be
    deeper: said so."""
    if not math.isfinite(ch.gain):
        return
    full = [p for p in pts if p.saturated]
    if not full:
        return
    plateau = max(p.mean_raw for p in full)
    ch.full_well_e = (plateau - ch.bias_adu) * ch.gain
    ch.full_well_how = "ADC top" if plateau >= sat_level - 0.01 * (sat_level - ch.bias_adu) else "well"
    ch.plateau_adu = plateau


def _drift(res: BenchResult, raw: list) -> float:
    """Panel drift, %: pairs repeated at one exposure compared (in the brightest linear channel); the worst pair's
    frame-to-frame ratio when no exposure was repeated."""
    c = max(res.channels, key=lambda k: res.channels[k].rate if math.isfinite(res.channels[k].rate) else -1)
    ch = res.channels[c]
    by_t: dict = {}
    for p in ch.pairs:
        if not p.saturated and p.signal > 0:
            by_t.setdefault(p.exptime, []).append(p)
    rep = [sorted(v, key=lambda p: p.order) for v in by_t.values() if len(v) > 1]
    if rep:
        worst = max(rep, key=lambda v: abs(v[-1].signal / v[0].signal - 1))
        return 100.0 * (worst[-1].signal / worst[0].signal - 1.0)
    return float("nan")


def _findings(res: BenchResult, sc: Scan) -> None:
    chans = list(res.channels)
    g = res.gain
    eg = fr.hfloat(sc.flat_pairs[0].a.header, "EGAIN")
    unit = "e⁻/ADU" + (f" (file units; × {res.adu_step} for the camera's own {fr.native_bits(res.adu_step)}-bit ADU)"
                        if res.adu_step > 1 else "")
    gl = "Gain " + ", ".join(f"{c} {res.channels[c].gain:.4f}" for c in chans if math.isfinite(res.channels[c].gain))
    res.add("ok", f"✔ {gl} {unit}; camera {g:.4f} ± {res.gain_sigma:.4f}." +
            (f" The header's EGAIN says {eg:.4f} ({100 * (g / eg - 1):+.1f} %)." if eg else ""))
    gains = [res.channels[c].gain for c in chans if math.isfinite(res.channels[c].gain)]
    if len(gains) > 1 and (max(gains) - min(gains)) / g > 0.04:
        res.add("warn", f"⚠ The colors disagree on the gain by {100 * (max(gains) - min(gains)) / g:.0f} % (it is one "
                        "sensor, so they should agree within a few %): the panel may have drifted, or one color "
                        "has too few pairs in its straight part.",
                short="colors disagree on the gain")
    if math.isfinite(res.read_noise_e):
        res.add("ok", f"✔ Read noise {res.read_noise_e:.2f} e⁻ (" + ", ".join(
            f"{c} {res.channels[c].read_noise_e:.2f}" for c in chans if math.isfinite(res.channels[c].read_noise_e))
            + f"); bias level {res.bias_adu:,.1f} ADU from {res.n_bias_pairs} bias pairs.")
    else:
        b = [res.channels[c].bias_adu for c in chans if math.isfinite(res.channels[c].bias_adu)]
        res.add("info", "Read noise: not measured (needs bias pairs shot with no light)." +
                (f" Bias level worked out from the flats: about {np.mean(b):,.0f} ADU." if b else ""))
    if math.isfinite(res.full_well_e):
        hows = {res.channels[c].full_well_how for c in chans if res.channels[c].full_well_how}
        res.add("ok", f"✔ Full well {res.full_well_e:,.0f} e⁻ (" + (
            "the pixels fill before the ADC tops out" if hows == {"well"} else
            "the ADC tops out first, so this is the most the camera can record; the sensor's well may be deeper") + f"); saturation {res.saturation_adu:,.0f} ADU.")
    else:
        res.add("info", "Full well: not reached (no pair went to saturation). Add a few longer exposures past the "
                        "top to measure it.")
    if math.isfinite(res.linear_limit_adu):
        per = ", ".join(f"{c} {res.channels[c].linear_limit_adu:,.0f}" + ("+" if res.channels[c].linear_is_bound else "")
                        for c in chans if math.isfinite(res.channels[c].linear_limit_adu))
        if res.linear_is_bound:
            res.add("ok", f"✔ Linear limit at least {res.linear_limit_adu:,.0f} ADU: every color stayed within "
                          f"±{LIN_TOL_PCT:g} % of a straight line right up to its last unsaturated pair ({per}). One "
                          "ADC reads all colors, so the highest of those holds for the camera. A few pairs between "
                          f"{res.linear_limit_adu:,.0f} and {res.saturation_adu:,.0f} ADU would find where it bends.")
        else:
            worst = min((c for c in chans if math.isfinite(res.channels[c].linear_limit_adu)
                         and not res.channels[c].linear_is_bound), key=lambda c: res.channels[c].linear_limit_adu)
            res.add("ok", f"✔ Linear limit {res.linear_limit_adu:,.0f} ADU (±{LIN_TOL_PCT:g} %; {per}). "
                          f"{worst}: {res.channels[worst].linear_limit_how}.")
    else:
        res.add("warn", "⚠ The linear limit could not be found: too few unsaturated pairs at different exposures.",
                short="linear limit not found")
    if math.isfinite(res.t_offset):
        sig = res.t_offset_sigma if math.isfinite(res.t_offset_sigma) else 0.0
        word = ("the camera exposes as long as it is told" if abs(res.t_offset) < max(3 * sig, 0.002) else
                ("each exposure is really longer than asked" if res.t_offset > 0 else
                 "each exposure is really shorter than asked"))
        res.add("ok" if abs(res.t_offset) < max(3 * sig, 0.01) else "info",
                (("✔ " if abs(res.t_offset) < max(3 * sig, 0.01) else "") +
                 f"Exposure-timing offset {1000 * res.t_offset:+.1f} ± {1000 * sig:.1f} ms: {word}."))
    if math.isfinite(res.drift_pct):
        if abs(res.drift_pct) > 1.0:
            res.add("warn", f"⚠ The panel changed by {res.drift_pct:+.1f} % between repeated exposures: linearity "
                            "numbers carry that much doubt. Let the panel warm up, and shoot a drift pair at the start "
                            "and end.", short="panel drifted")
        else:
            res.add("ok", f"✔ Panel steady: repeated exposures agree to {res.drift_pct:+.2f} %.")
    flick = []
    for c in chans:
        for p in res.channels[c].pairs:
            if not p.saturated and math.isfinite(p.ratio) and abs(p.ratio - 1) > PAIR_RATIO_TOL:
                flick.append(p.exptime)
    if flick:
        res.add("info", f"The two frames of {len(set(flick))} pair{'s' if len(set(flick)) != 1 else ''} differ by "
                        f"more than {100 * PAIR_RATIO_TOL:g} % (panel flicker); corrected by scaling one to the other.")
    n_sat = sum(1 for p in next(iter(res.channels.values())).pairs if p.saturated)
    usable = res.n_flat_pairs - n_sat
    if n_sat and n_sat > res.n_flat_pairs / 2:
        res.add("warn", f"⚠ {n_sat} of the {res.n_flat_pairs} flat pairs are saturated: the ladder's top half is "
                        "wasted. The panel is brighter than planned; use the Shooting plan to fit the exposures.",
                short="most flat pairs saturated")
    elif n_sat:
        res.add("info", f"{n_sat} saturated pair{'s' if n_sat != 1 else ''} at the top of the ladder (good: that is "
                        "how full well and saturation are found).")
    if usable < 6:
        res.add("warn", f"⚠ Only {usable} unsaturated flat pairs: the curve rests on few points.",
                short="few unsaturated pairs")


def summary_of(res: BenchResult) -> dict:
    def f(v, nd=4):
        return round(float(v), nd) if v is not None and math.isfinite(v) else None
    out = {"kind": "bench", "region": res.region_name, "adu_step": res.adu_step, "bayer": res.bayer,
           "gain_e_per_adu": f(res.gain), "gain_sigma": f(res.gain_sigma, 5), "read_noise_e": f(res.read_noise_e, 3),
           "full_well_e": f(res.full_well_e, 0), "saturation_adu": f(res.saturation_adu, 0),
           "linear_limit_adu": f(res.linear_limit_adu, 0), "linear_limit_at_least": res.linear_is_bound,
           "bias_adu": f(res.bias_adu, 1),
           "exposure_offset_s": f(res.t_offset, 5), "drift_pct": f(res.drift_pct, 3), "lit_bias": res.lit_bias,
           "n_flat_pairs": res.n_flat_pairs, "n_bias_pairs": res.n_bias_pairs, "temp_c": res.temp_c,
           "worst": res.worst, "channels": {}}
    for c, ch in res.channels.items():
        out["channels"][c] = {"gain": f(ch.gain), "gain_sigma": f(ch.gain_sigma, 5),
                              "read_noise_e": f(ch.read_noise_e, 3), "bias_adu": f(ch.bias_adu, 1),
                              "bias_source": ch.bias_source, "full_well_e": f(ch.full_well_e, 0),
                              "linear_limit_adu": f(ch.linear_limit_adu, 0), "rate_adu_s": f(ch.rate, 2),
                              "t_offset_s": f(ch.t_offset, 5), "max_dev_pct": f(ch.max_dev_pct, 3),
                              "n_gain": ch.n_gain, "n_lin": ch.n_lin}
    return out


def run_values(res: BenchResult, profile_id: str, run_key: str | None = None) -> list:
    """The measured values as rows for the run database (History page)."""
    date = res.date
    rows = []

    def add(key, v, unit, sigma=None, ch=None):
        if v is not None and math.isfinite(v):
            rows.append({"key": key, "value": float(v), "sigma": sigma, "unit": unit, "source": "measured",
                         "channel": ch, "profile_id": profile_id, "date": date})
    add("gain_e_per_adu", res.gain, "e-/ADU", res.gain_sigma)
    add("read_noise_e", res.read_noise_e, "e-")
    add("full_well_e", res.full_well_e, "e-")
    add("linear_limit_adu", res.linear_limit_adu, "ADU")
    add("bias_level_adu", res.bias_adu, "ADU")
    add("exposure_offset_s", res.t_offset, "s", res.t_offset_sigma)
    for c, ch in res.channels.items():
        add("gain_e_per_adu", ch.gain, "e-/ADU", ch.gain_sigma, c)
        add("read_noise_e", ch.read_noise_e, "e-", None, c)
        add("linear_limit_adu", ch.linear_limit_adu, "ADU", None, c)
    return rows


# ---- writing the profile ----------------------------------------------------------------------------------------

def _rec(v, unit, sigma=None, n=None, date=None, conditions=None, run=None, note=None, nd=4):
    if v is None or not math.isfinite(v):
        return None
    return sp.record(round(float(v), nd), unit, "measured", sigma=None if sigma is None or not math.isfinite(sigma)
                     else round(float(sigma), nd + 1), n=n, date=date, conditions=conditions, run=run, note=note)


def measured_records(res: BenchResult, run_key: str | None = None) -> tuple[dict, dict, dict]:
    """(camera-wide values, per-channel values, linearity block) as format-v1 records; only what was measured."""
    cond = (f"gain {res.setup.get('gain_setting')}, offset {res.setup.get('offset')}"
            + (f", {res.temp_c:g} °C" if res.temp_c is not None else "") + f", {res.region_name.lower()}")
    d = res.date
    vals = {}
    for key, v, unit, sig, n, nd, note in (
            ("gain_e_per_adu", res.gain, "e-/ADU", res.gain_sigma, res.n_flat_pairs, 4,
             "photon transfer curve, flat pairs" + (f"; per file ADU (× {res.adu_step} native)" if res.adu_step > 1
                                                     else "")),
            ("read_noise_e", res.read_noise_e, "e-", None, res.n_bias_pairs, 3, "bias pairs"),
            ("full_well_e", res.full_well_e, "e-", None, None, 0,
             "where the pixels fill" if {c.full_well_how for c in res.channels.values() if c.full_well_how} == {"well"}
             else "ADC top: (saturation − bias) × gain"),
            ("saturation_adu", res.saturation_adu, "ADU", None, None, 0, "level the brightest flats pile up at"),
            ("linear_limit_adu", res.linear_limit_adu, "ADU", None, None, 0,
             (f"at least: straight (±{LIN_TOL_PCT:g} %) to the brightest unsaturated pair" if res.linear_is_bound
              else f"highest raw level within ±{LIN_TOL_PCT:g} % of the straight line (lowest color)")),
            ("bias_level_adu", res.bias_adu, "ADU", None, res.n_bias_pairs, 1, "bias pairs"),
            ("exposure_offset_s", res.t_offset, "s", res.t_offset_sigma, None, 4, "linearity fit")):
        r = _rec(v, unit, sig, n, d, cond, run_key, note, nd)
        if r is not None:
            vals[key] = r
    per = {}
    for c, ch in res.channels.items():
        per[c] = {"gain_e_per_adu": _rec(ch.gain, "e-/ADU", ch.gain_sigma, ch.n_gain, d, cond, run_key),
                  "read_noise_e": _rec(ch.read_noise_e, "e-", None, res.n_bias_pairs, d, cond, run_key, nd=3),
                  "linear_limit_adu": _rec(ch.linear_limit_adu, "ADU", None, None, d, cond, run_key, nd=0)}
    lead = res.channels.get("G") or next(iter(res.channels.values()))
    curve = [[round(p.mean_raw, 1), round(p.deviation_pct, 3)] for p in sorted(lead.pairs, key=lambda p: p.signal)
             if math.isfinite(p.deviation_pct) and not p.saturated]
    top = res.saturation_adu if math.isfinite(res.saturation_adu) else 65535.0
    lin = {"curve_adu": curve, "max_deviation_pct": None if not math.isfinite(lead.max_dev_pct) else
           round(lead.max_dev_pct, 3),
           "measured_to_fraction_of_full_scale": round(max((p[0] for p in curve), default=0.0) / top, 3) if curve else None,
           "source": "measured", "date": d}
    return vals, per, lin


def changes_for(res: BenchResult, doc: dict | None) -> list[tuple[str, object, str, object]]:
    """[(key, old value, old source, new value)] that saving would make (camera-wide values)."""
    vals, _, _ = measured_records(res)
    out = []
    for k, r in vals.items():
        old = ((doc or {}).get("values") or {}).get(k) or {}
        out.append((k, old.get("value"), old.get("source", "none"), r["value"]))
    return out


def apply_to_profile(res: BenchResult, doc: dict | None, written_by: str, run_key: str | None = None,
                     keep_user: bool = False, instrume_fallback: str | None = None) -> dict:
    """The camera profile with this run's measured values in it: doc (an existing profile) is updated, or a new one
    is made from the frames' setup. keep_user=True keeps values the user typed (source 'user'). Values the run
    could not measure keep what the profile had."""
    import copy
    vals, per, lin = measured_records(res, run_key)
    s = res.setup
    if doc is None:
        now = sp.now_iso()
        color = "OSC" if res.bayer in fr.BAYER_PATTERNS else "mono"
        doc = {
            "format": sp.CAMERA_FORMAT, "format_version": sp.FORMAT_VERSION,
            "profile_id": s.get("camera_id") or sp.camera_id(instrume_fallback, s.get("gain_setting"),
                                                             s.get("offset"), s.get("binning")),
            "revision": 1, "written_by": written_by, "created": now, "updated": now, "spec_file": None,
            "camera": {"instrume": s.get("instrume") or instrume_fallback, "sensor": None, "type": None,
                       "color": color, "bayer_pattern": res.bayer if color == "OSC" else None,
                       "pixel_um": s.get("pixel_um"), "width_px": res.shape[1], "height_px": res.shape[0],
                       "adc_bits": fr.native_bits(res.adu_step), "file_bits": 16, "adu_step": res.adu_step},
            "mode": {"gain_setting": s.get("gain_setting"), "offset": s.get("offset"), "readout_mode": None,
                     "binning": s.get("binning") or 1, "set_temp_c": s.get("set_temp_c")},
            "values": {}, "per_channel": {}, "linearity": {},
        }
    else:
        doc = copy.deepcopy(doc)
    doc.setdefault("values", {})
    for k, r in vals.items():
        old = doc["values"].get(k)
        if keep_user and isinstance(old, dict) and old.get("source") == "user":
            continue
        doc["values"][k] = r
    pc = doc.get("per_channel")
    if not isinstance(pc, dict):
        pc = doc["per_channel"] = {}
    for c, recs in per.items():
        slot = pc.get(c) if isinstance(pc.get(c), dict) else {}
        for k, r in recs.items():
            if r is not None:
                old = slot.get(k)
                if keep_user and isinstance(old, dict) and old.get("source") == "user":
                    continue
                slot[k] = r
            else:
                slot.setdefault(k, None)
        pc[c] = slot
    doc["linearity"] = lin
    cam = doc.get("camera") if isinstance(doc.get("camera"), dict) else {}
    cam["adu_step"] = res.adu_step
    if res.adu_step > 1:
        cam["adc_bits"] = fr.native_bits(res.adu_step)
    doc["camera"] = cam
    return doc


# ---- the shooting plan --------------------------------------------------------------------------------------------

NICE = (0.001, 0.002, 0.005, 0.01, 0.015, 0.02, 0.03, 0.04, 0.05, 0.06, 0.075, 0.09, 0.1, 0.125, 0.15, 0.175, 0.2, 0.25,
        0.3, 0.35, 0.4, 0.45, 0.5, 0.6, 0.7, 0.75, 0.8, 0.9, 1.0, 1.1, 1.2, 1.25, 1.4, 1.5, 1.75, 2.0, 2.25, 2.5, 2.75,
        3.0, 3.5, 4.0, 4.5, 5.0, 6.0, 7.0, 7.5, 8.0, 9.0, 10.0, 12.0, 15.0, 20.0, 25.0, 30.0, 40.0, 50.0, 60.0, 90.0,
        120.0)


def _nice(t: float) -> float:
    return min(NICE, key=lambda n: abs(math.log(n / t)))


def plan_ladder(rates: dict, bias: float, sat_adu: float = 65535.0, steps: int = 16, knee_pairs: int = 4,
                min_exp: float = 0.001) -> dict:
    """A flat-pair ladder for a panel whose signal rate per color is known (ADU/s above bias, e.g. from a test flat
    or from a bench run): exposures spread evenly in log from ~1 % to ~120 % of the brightest color's range, plus
    extra steps near each color's top (60–100 % of its range), where linearity bends. Returns {'exposures': [...],
    'drift': t, 'notes': [...]}."""
    rates = {c: r for c, r in rates.items() if r and r > 0}
    if not rates:
        raise BenchError("no signal rate to plan from")
    rng = sat_adu - bias
    rmax, rmin = max(rates.values()), min(rates.values())
    t_lo, t_hi = 0.01 * rng / rmax, 1.2 * rng / rmin
    ts = set()
    for i in range(steps):
        ts.add(_nice(t_lo * (t_hi / t_lo) ** (i / max(1, steps - 1))))
    for c, r in rates.items():
        for f in np.linspace(0.6, 1.0, knee_pairs):
            ts.add(_nice(f * rng / r))
    ts = sorted(t for t in ts if t >= min_exp)
    notes = []
    if t_lo < min_exp * 2:
        notes.append(f"The panel is bright: the shortest useful exposure ({t_lo * 1000:.1f} ms) is near the camera's "
                     "minimum. Dim the panel (or add a layer of paper) for a better ladder.")
    drift = _nice(0.4 * rng / rmax)
    return {"exposures": ts, "drift": drift, "notes": notes}


def plan_text(plan: dict, gain_setting=None, offset=None, temp=None, bias_pairs: int = 20) -> str:
    ex = plan["exposures"]

    def fmt(t):
        return f"{t * 1000:g} ms" if t < 1 else f"{t:g} s"
    lines = ["SHOBS-FLUX bench ladder: shooting plan",
             f"Camera: gain {gain_setting if gain_setting is not None else '(as for your lights)'}, offset "
             f"{offset if offset is not None else '(as for your lights)'}, "
             f"{temp if temp is not None else 'your usual'} °C, bin 1. Same settings for every frame.",
             "",
             f"1. Bias: panel OFF, scope capped, shortest exposure the camera allows: {2 * bias_pairs} frames "
             f"({bias_pairs} pairs).",
             f"2. Drift check: panel ON, let it warm up 5 minutes; 2 flats at {fmt(plan['drift'])}.",
             f"3. Flat pairs, 2 frames at each exposure ({len(ex)} exposures, {2 * len(ex)} frames):"]
    for i in range(0, len(ex), 6):
        lines.append("     " + ", ".join(fmt(t) for t in ex[i:i + 6]))
    lines += [f"4. Drift check again: 2 flats at {fmt(plan['drift'])}.",
              "",
              "ASIAIR: use the flat frame type with the exposure typed by hand (not auto-flat), or a Plan with one "
              "line per exposure. Don't touch the panel or the scope during the ladder.",
              "Put every frame in one folder (the bias frames may go in a folder of their own), then Camera page → "
              "Flats folder… (and Bias folder…) → Run."]
    lines += [""] + plan["notes"] if plan["notes"] else []
    return "\n".join(lines)


def rates_from_test_flat(path: str, bias: float | None = None) -> tuple[dict, float, float]:
    """A test flat's signal rate per color (ADU/s above bias), the bias used, and its exposure."""
    frame = fr.read_frame(path)
    if not frame.exptime:
        raise BenchError("the test flat has no exposure time in its header")
    reg = fr.central_region(frame.data.shape, 0.25)
    pl = channel_planes(frame.data[reg], frame.bayer)
    if bias is None:
        off = fr.hfloat(frame.header, "OFFSET")
        bias = 10.0 * off if off is not None else 0.0          # ZWO: about 10 ADU per offset step at 16 bits
    rates = {c: (clipped_stats(v)[0] - bias) / frame.exptime for c, v in pl.items()}
    return rates, bias, frame.exptime


