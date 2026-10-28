"""The dark run: dark current, hot pixels and edge glow, from a folder of darks (and bias frames), per color.

What the user points FLUX at: a dark library, the way it is usually kept: darks at one or more exposures, either all
in one folder or each exposure in a subfolder of its own ("20 Sec", "180 sec"…), at one gain, offset and binning,
with or without bias frames (in a subfolder, here, or in a bias folder of their own). FLUX finds the frames itself,
leaves out what does not belong (flats, lights, master darks, frames cut short in copying, frames shot before the
cooler settled) and says why.

Two independent ways to the dark current, in a central region (amp glow stays out), per color:

  from the mean level    the level of each exposure's darks (mean of clipped frame means) against the exposure:
                         the slope is the dark current in ADU/s, × gain → e⁻/px/s. With one exposure only, the
                         bias frames give the zero point instead. The bias level drifts a little between sessions
                         (darks shot on different days), which limits this way on a cold modern sensor.
  from the noise         the temporal variance of each exposure (the difference of two darks / 2, which cancels the
                         fixed pattern and the bias level, drift and all), in e⁻²: V = read noise² + dark × t. The
                         slope is the dark current again (dark current is shot noise). Much more sensitive when the
                         dark signal is a fraction of an electron.

The more precise of the two is the result; both are shown, and a value smaller than twice its uncertainty is given as
an upper limit ("at most …"), never as a number it is not. Hot pixels come from a median stack of the longest
exposure (minus the bias pattern); edge glow from whole frames, shrunk, longest exposure minus bias (or the
shortest darks). Darks at several sensor temperatures are measured temperature by temperature, and the doubling
temperature comes out when two or more are measured.

Nothing here draws or writes; the Camera page shows the result and apply_to_profile() puts it in a camera profile."""
from __future__ import annotations

import math
import os
from dataclasses import dataclass, field

import numpy as np

import bench
import frames as fr
import shobs_profile as sp

MAX_PER_EXPOSURE = 8          # darks read per exposure (more only slows the run: the means are already precise)
MAX_BIAS = 16                 # bias frames read
GLOW_FRAMES = 3               # whole frames read for the glow map (each side)
SETTLE_C = 1.5                # a dark more than this far from its set temperature was shot before the cooler settled
TEMP_GROUP_C = 2.0            # darks without SET-TEMP: sensor temperatures within this go together
SESSION_DRIFT_ADU = 0.5       # the bias level's typical move between sessions (ZWO / Sony CMOS, 16-bit files)
SIGNIFICANT = 2.0             # a dark current below this many sigma is an upper limit
HOT_SIGMA = 10.0              # hot pixel: this many stack-noise sigmas above the region's level
GLOW_MIN_E = 2.0              # edge glow worth a line: this many electrons above the centre over the exposure
BIN = 8                       # whole frames are shrunk by this before the glow map (hot pixels then median out)


class DarkError(Exception):
    """A folder FLUX cannot run a dark measurement on (the message is a plain sentence)."""


# ---- the folder --------------------------------------------------------------------------------------------------

@dataclass
class DarkFrame:
    path: str
    kind: str                  # dark / bias
    exptime: float
    header: dict
    shape: tuple | None
    bayer: str | None
    when: str
    temp: float | None         # CCD-TEMP
    set_temp: float | None     # SET-TEMP

    @property
    def name(self) -> str:
        return os.path.basename(self.path)


@dataclass
class DarkScan:
    folder: str
    darks: dict = field(default_factory=dict)        # temperature group (°C) → {exposure: [DarkFrame], kept}
    found: dict = field(default_factory=dict)        # temperature group → {exposure: how many found}
    biases: list = field(default_factory=list)
    setup: dict = field(default_factory=dict)
    bayer: str | None = None
    shape: tuple | None = None
    notes: list = field(default_factory=list)        # (kind, text)
    left_out: list = field(default_factory=list)     # (file name, why)
    problems: list = field(default_factory=list)
    n_read: int = 0


def _hidden(name: str) -> bool:
    # ".75 Sec" is John's 0.75 s darks folder, not a hidden one
    return name.startswith(".") and not name[1:2].isdigit()


PROCESSED_FOLDER_WORDS = ("debayer", "calibrat", "processed", "master", "stack", "integrat", "registered")


def find_frames(folder: str, depth: int = 2, skipped: list | None = None) -> list[str]:
    """Frames in the folder and its subfolders, down to depth levels (a dark library: '20 Sec', '180 sec', or
    'night/DARK' and 'night/BIAS'). Subfolders of processed copies ('Debayered', 'SHOBS-P masters', 'Calibrated'…)
    are not searched; their names go in skipped."""
    out = list(fr.list_frames(folder))
    if depth <= 0:
        return out
    try:
        subs = sorted(n for n in os.listdir(folder) if not _hidden(n) and os.path.isdir(os.path.join(folder, n)))
    except OSError:
        return out
    for n in subs:
        if any(w in n.lower() for w in PROCESSED_FOLDER_WORDS):
            if skipped is not None:
                skipped.append(os.path.join(folder, n))
            continue
        out += find_frames(os.path.join(folder, n), depth - 1, skipped)
    return out


def _data_bytes(h: dict) -> int | None:
    try:
        n = abs(int(h.get("BITPIX"))) // 8
        dims = [int(h.get(f"NAXIS{i}")) for i in range(1, int(h.get("NAXIS", 0)) + 1)]
    except (TypeError, ValueError):
        return None
    if not dims:
        return None
    return n * int(np.prod(dims))


def _is_master(h: dict, path: str) -> str | None:
    base = os.path.basename(path).lower()
    t = str(h.get("IMAGETYP") or h.get("FRAME") or "").lower()
    if "master" in base or "master" in t:
        return "a master (stacked) frame"
    for k in fr.STACK_KEYS:
        v = h.get(k)
        if v not in (None, False, 0, 1, "1", "") and not (isinstance(v, str) and not v.strip()):
            return f"a stacked frame ({k} {v})"
    try:
        if int(h.get("BITPIX", 16)) < 0:
            return "a processed frame (floating-point pixels, not as the camera wrote it)"
    except (TypeError, ValueError):
        pass
    return None


def _folder_kind(path: str, root: str) -> str | None:
    """'dark' or 'bias' when a folder on the way from root says so (DARK, Bias 28Oct26, Darks…)."""
    rel = os.path.relpath(os.path.dirname(path), root)
    for part in rel.replace("\\", "/").split("/"):
        p = part.lower()
        if "bias" in p or "offset" in p:
            return "bias"
        if "dark" in p:
            return "dark"
    return None


def _setup_word(h: dict) -> tuple:
    return (fr.hfloat(h, "GAIN"), fr.hfloat(h, "OFFSET", "BLKLEVEL"), fr.hfloat(h, "XBINNING", "BINNING") or 1.0)


def _pick(frames_: list, n: int) -> list:
    """n frames spread evenly through the shooting order (the first dark after a cool-down is not favoured)."""
    if len(frames_) <= n:
        return list(frames_)
    idx = np.unique(np.round(np.linspace(0, len(frames_) - 1, n)).astype(int))
    return [frames_[i] for i in idx]


def scan_darks(folder: str, bias_folder: str | None = None, step=None, max_per_exp: int = MAX_PER_EXPOSURE) -> DarkScan:
    """Find the darks and bias frames (headers only), sort them by sensor temperature and exposure, and keep up to
    max_per_exp darks per exposure. Problems that stop the run go in scan.problems; the rest are notes."""
    sc = DarkScan(folder=folder)
    skipped: list = []
    paths = find_frames(folder, skipped=skipped)
    if skipped:
        names = sorted({os.path.basename(p) for p in skipped})
        sc.notes.append(("info", f"{len(skipped)} subfolder{'s' if len(skipped) != 1 else ''} of processed copies not "
                                 f"searched ({', '.join(names)}): only frames as the camera wrote them are measured."))
    bias_paths = []
    if bias_folder and os.path.normcase(os.path.abspath(bias_folder)) != os.path.normcase(os.path.abspath(folder)):
        bias_paths = find_frames(bias_folder, depth=1)
        if not bias_paths:
            sc.notes.append(("info", "The bias folder holds no FITS or XISF frames."))
    all_paths = paths + [p for p in bias_paths if p not in paths]
    if not all_paths:
        sc.problems.append("there are no FITS or XISF frames in this folder or its subfolders")
        return sc
    other: dict = {}
    infos = []
    for i, p in enumerate(all_paths):
        if step and i % 20 == 0:
            step(f"Reading headers… {i} of {len(all_paths)}")
        try:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")            # astropy's "may have been truncated": checked below
                h = fr.read_header(p)
        except fr.FrameError as exc:
            sc.left_out.append((os.path.basename(p), f"could not be read ({exc})"))
            continue
        sc.n_read += 1
        kind = fr.kind_of(h, p)
        t = fr.exptime_of(h)
        if kind in ("unknown", "light"):
            fk = _folder_kind(p, bias_folder if p in bias_paths and bias_folder else folder)
            if fk and (kind == "unknown" or fk == "bias"):
                kind = fk
        if p in bias_paths and kind in ("dark", "unknown") and (t is None or t <= 0.01):
            kind = "bias"                      # in the bias folder, a very short frame is a bias whatever it says
        if kind not in ("dark", "bias"):
            other[kind] = other.get(kind, 0) + 1
            continue
        why = _is_master(h, p)
        if why:
            sc.left_out.append((os.path.basename(p), why))
            continue
        if not p.lower().endswith(".xisf"):
            need = _data_bytes(h)
            try:
                size = os.path.getsize(p)
            except OSError:
                size = None
            if need and size is not None and size < need + 2880:
                sc.left_out.append((os.path.basename(p), f"cut short: {size / 1e6:.1f} MB of the "
                                                         f"{(need + 2880) / 1e6:.1f} MB it should be (an incomplete copy?)"))
                continue
        if kind == "dark" and (t is None or t <= 0):
            sc.left_out.append((os.path.basename(p), "a dark with no exposure time in its header"))
            continue
        nx, ny = fr.hfloat(h, "NAXIS1"), fr.hfloat(h, "NAXIS2")
        shape = (int(ny), int(nx)) if nx and ny else None
        when = str(h.get("DATE-OBS") or h.get("DATE") or "")
        infos.append(DarkFrame(p, kind, float(t or 0.0), h, shape, fr.bayer_of(h), when or os.path.basename(p),
                               fr.hfloat(h, "CCD-TEMP", "CCD_TEMP", "SENSOR-TEMP"), fr.hfloat(h, "SET-TEMP")))
    if other:
        sc.notes.append(("info", "Left out, not darks or bias frames: " +
                         ", ".join(f"{n} {k if k != 'unknown' else 'unlabelled'}" for k, n in sorted(other.items()))
                         + " (FLUX reads IMAGETYP, or 'Dark' / 'Bias' in the file or folder name)."))
    darks = [f for f in infos if f.kind == "dark"]
    if not darks:
        sc.problems.append("no dark frames were found (FLUX reads IMAGETYP, or 'Dark' in the file or folder name)")
        return sc
    # one camera setting and one frame size
    words: dict = {}
    for f in infos:
        words.setdefault(_setup_word(f.header), []).append(f)
    if len(words) > 1:
        main = max(words.values(), key=len)
        lead = _setup_word(main[0].header)
        odd = [f for f in infos if _setup_word(f.header) != lead]
        if len([f for f in main if f.kind == "dark"]) == 0:
            sc.problems.append("the frames are from more than one camera setting; a dark run needs one gain, offset "
                               "and binning: put each setting in its own folder")
            return sc
        for f in odd:
            sc.left_out.append((f.name, f"another camera setting (gain {_setup_word(f.header)[0]}, offset "
                                        f"{_setup_word(f.header)[1]})"))
        infos = main
    shapes: dict = {}
    for f in infos:
        shapes.setdefault(f.shape, []).append(f)
    if len(shapes) > 1:
        main = max(shapes.values(), key=len)
        for f in infos:
            if f.shape != main[0].shape:
                sc.left_out.append((f.name, f"another frame size ({f.shape[1]}×{f.shape[0]})" if f.shape else
                                    "no image size in its header"))
        infos = main
    sc.shape = infos[0].shape
    if sc.shape is None:
        sc.problems.append("the frames' headers give no image size (NAXIS1 / NAXIS2)")
        return sc
    bayers: dict = {}
    for f in infos:
        bayers.setdefault(f.bayer, []).append(f)
    if len(bayers) > 1:
        # a copy without the pattern (debayered, processed) next to the camera's own frames: keep the camera's
        lead = max(bayers, key=lambda b: (b is not None, len(bayers[b])))
        for b, fs in bayers.items():
            if b != lead:
                for f in fs:
                    sc.left_out.append((f.name, f"Bayer pattern {b or 'none'}, not {lead or 'none'} like the rest "
                                                "(a debayered or processed copy?)"))
        infos = bayers[lead]
        sc.bayer = lead
    else:
        sc.bayer = next(iter(bayers))
    darks = [f for f in infos if f.kind == "dark"]
    if not darks:
        sc.problems.append("no dark frames are left once the odd ones are set aside")
        return sc
    sc.setup = sp.setup_key(darks[0].header)

    # temperature groups: SET-TEMP when the camera writes it, else the sensor temperature in 2 °C bins
    groups: dict = {}
    for f in darks:
        if f.set_temp is not None:
            if f.temp is not None and abs(f.temp - f.set_temp) > SETTLE_C:
                sc.left_out.append((f.name, f"shot at {f.temp:.1f} °C, not its set {f.set_temp:g} °C (the cooler "
                                            "had not settled)"))
                continue
            g = float(round(f.set_temp))
        elif f.temp is not None:
            g = float(round(f.temp / TEMP_GROUP_C) * TEMP_GROUP_C)
        else:
            g = float("nan")
        groups.setdefault(g, []).append(f)
    if not groups:
        sc.problems.append("every dark was shot before the cooler settled")
        return sc
    nan_keys = [k for k in groups if math.isnan(k)]
    if nan_keys and len(groups) > len(nan_keys):
        for k in nan_keys:
            for f in groups.pop(k):
                sc.left_out.append((f.name, "no sensor temperature in its header (other darks have one)"))
    for g, fs in groups.items():
        by_t: dict = {}
        for f in fs:
            by_t.setdefault(bench._exp_key(f.exptime), []).append(f)
        sc.found[g] = {t: len(v) for t, v in by_t.items()}
        sc.darks[g] = {t: _pick(sorted(v, key=lambda f: (f.when, f.name)), max_per_exp) for t, v in by_t.items()}
    sc.biases = _pick(sorted([f for f in infos if f.kind == "bias"], key=lambda f: (f.when, f.name)), MAX_BIAS)
    n_found = sum(sum(v.values()) for v in sc.found.values())
    n_used = sum(len(x) for v in sc.darks.values() for x in v.values())
    if n_used < n_found:
        sc.notes.append(("info", f"{n_used} of the {n_found} darks are read (up to {max_per_exp} per exposure, spread "
                                 "through the night: the levels are already precise, more would only slow the run)."))
    for g, v in sc.darks.items():
        thin = [t for t, fs in v.items() if len(fs) < 2]
        if thin:
            sc.notes.append(("info", f"Only one dark at {', '.join(_exp(t) for t in sorted(thin))}"
                                     f"{' (' + _t_word(g) + ')' if len(sc.darks) > 1 else ''}: it adds to the level "
                                     "line but not to the noise line (that needs two)."))
    return sc


def _exp(t: float) -> str:
    return f"{t * 1000:g} ms" if t < 1 else f"{t:g} s"


def _t_word(g: float) -> str:
    return "an unknown temperature" if g is None or math.isnan(g) else f"{g:g} °C"


# ---- results -------------------------------------------------------------------------------------------------------

@dataclass
class ExposureStat:
    exptime: float
    n: int                       # darks read
    n_found: int
    level: dict = field(default_factory=dict)        # ch → mean level (ADU, bias included)
    level_se: dict = field(default_factory=dict)     # ch → its standard error from the frames' spread
    var: dict = field(default_factory=dict)          # ch → temporal variance per frame (ADU²) from one pair
    var_se: dict = field(default_factory=dict)
    temp: float | None = None                        # median CCD-TEMP
    dates: tuple = ()                                # first and last DATE-OBS (session)


@dataclass
class DarkChannel:
    ch: str
    gain: float = float("nan")
    gain_source: str = "none"
    mean_rate: float = float("nan")       # e⁻/px/s from the level line
    mean_sigma: float = float("nan")
    mean_chi2: float = float("nan")
    mean_how: str = ""                    # "slope of N exposures" / "above the bias"
    noise_rate: float = float("nan")      # e⁻/px/s from the noise line
    noise_sigma: float = float("nan")
    read_noise_e: float = float("nan")    # the noise line's intercept, √
    rate: float = float("nan")            # the chosen one
    sigma: float = float("nan")
    method: str = ""                      # "mean" / "noise"
    level_at_zero: float = float("nan")   # the level line at t = 0 (ADU)
    bias_adu: float = float("nan")


@dataclass
class TempResult:
    temp_c: float                          # the group's temperature (set temperature, or binned sensor temperature)
    exposures: list = field(default_factory=list)       # ExposureStat, by exposure
    channels: dict = field(default_factory=dict)        # ch → DarkChannel
    rate: float = float("nan")             # camera-wide e⁻/px/s (value, or the upper limit when not significant)
    measured: float = float("nan")         # the camera-wide value as measured (may be below zero)
    sigma: float = float("nan")
    method: str = ""
    upper_limit: bool = False
    spec: float | None = None              # the spec curve at this temperature
    n_darks: int = 0
    hot_count: int = 0
    hot_ppm: float = float("nan")
    hot_rate: float = float("nan")         # typical hot pixel's dark current, e⁻/s
    hot_exptime: float = float("nan")
    glow_e_s: float = float("nan")         # the largest edge excess, e⁻/px/s
    glow_where: str = ""
    glow_map: np.ndarray | None = None     # e⁻/px/s per block, whole frame
    glow_ref: str = ""                     # "bias" / "the 20 s darks"
    glow_noise: float = float("nan")       # the blocks' scatter, e⁻/px/s
    thumb: np.ndarray | None = None        # longest darks minus reference, shrunk (ADU), for the picture
    thumb_scale: int = BIN

    @property
    def significant(self) -> bool:
        return not self.upper_limit

    def glow_seen(self, longest: float) -> bool:
        """An edge glow worth a line: at an edge or corner, GLOW_MIN_E electrons or more over the longest exposure,
        and well above the blocks' own scatter."""
        if not math.isfinite(self.glow_e_s) or self.glow_where == "middle":
            return False
        floor = 6.0 * self.glow_noise if math.isfinite(self.glow_noise) else 0.0
        return self.glow_e_s * longest >= GLOW_MIN_E and self.glow_e_s > floor


@dataclass
class DarkResult:
    folder: str
    region: tuple
    region_name: str
    shape: tuple
    bayer: str | None
    setup: dict
    temps: dict = field(default_factory=dict)          # °C → TempResult
    main_temp: float = float("nan")
    bias_adu: dict = field(default_factory=dict)       # ch → level (ADU)
    bias_se: dict = field(default_factory=dict)
    bias_var: dict = field(default_factory=dict)       # ch → temporal variance (ADU²) from bias pairs
    bias_var_se: dict = field(default_factory=dict)
    n_bias: int = 0
    bias_source: str = "none"                          # frames / profile / none
    bias_dates: tuple = ()
    gain: float = float("nan")
    gain_source: str = "none"
    read_noise_e: float = float("nan")                 # from the noise line at the main temperature
    doubling_c: float = float("nan")                   # from two or more temperatures
    doubling_sigma: float = float("nan")
    left_out: list = field(default_factory=list)
    lines: list = field(default_factory=list)
    shorts: dict = field(default_factory=dict)
    date: str = ""
    summary: dict = field(default_factory=dict)
    adu_step: int = 1

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
        import re
        for want in ("bad", "warn"):
            for i, (k, t) in enumerate(self.lines):
                if k == want:
                    if i in self.shorts:
                        return f"{'✖' if k == 'bad' else '⚠'} {self.shorts[i]}"
                    return re.split(r"[:(;.]", t)[0].strip()[:70]
        return ""

    @property
    def main(self) -> TempResult | None:
        return self.temps.get(self.main_temp)


# ---- the run -------------------------------------------------------------------------------------------------------

def _gain_for(setup: dict, chans) -> tuple[dict, str, float]:
    """The gain to turn ADU into electrons: the camera profile's measured gain (per color when it has one), else the
    header's EGAIN, the spec, or 1 (labelled)."""
    import instrument
    try:
        cam = instrument.camera_in_use(setup)
    except Exception:                                # a gain is needed, a broken profile must not stop the run
        cam = None
    g = cam.value("gain_e_per_adu") if cam else None
    src = cam.source("gain_e_per_adu") if cam else "estimate"
    if not g or not math.isfinite(g) or g <= 0:
        g, src = 1.0, "estimate"
    per = {c: g for c in chans}
    prof = sp.find_camera_profile(setup.get("camera_id") or "")
    if prof:
        for c in chans:
            v, rec = sp.camera_value(prof, "gain_e_per_adu", c if c != "L" else None)
            if v and rec and rec.get("source") in ("measured", "user") and 0 < v < 100:
                per[c] = float(v)
    return per, src, float(g)


def run_darks(folder: str, region_name: str = bench.DEFAULT_REGION, step=None, scan: DarkScan | None = None,
              bias_folder: str | None = None) -> DarkResult:
    """The whole dark run. DarkError (plain words) when it cannot be done."""
    sc = scan or scan_darks(folder, bias_folder, step)
    if sc.problems:
        raise DarkError(sc.problems[0])
    frac = bench.REGIONS.get(region_name, bench.REGIONS[bench.DEFAULT_REGION])
    reg = bench.region_of(sc.shape, frac)
    res = DarkResult(folder=folder, region=reg, region_name=region_name, shape=sc.shape, bayer=sc.bayer,
                     setup=sc.setup, left_out=list(sc.left_out))
    chans = ("R", "G", "B") if sc.bayer in fr.BAYER_PATTERNS else ("L",)
    for k, t in sc.notes:
        res.add(k, t)
    gains, gsrc, g_cam = _gain_for(sc.setup, chans)
    res.gain, res.gain_source = g_cam, gsrc
    first = next(iter(next(iter(sc.darks.values())).values()))[0]
    res.date = str(first.header.get("DATE-OBS") or "")[:10] or sp.now_iso()[:10]
    total = 1 + len(sc.biases) + sum(len(x) for v in sc.darks.values() for x in v.values())
    done = [0]
    bad_reads = []

    def load(f: DarkFrame):
        done[0] += 1
        if step:
            step(f"Reading frames… {done[0]} of {total}")
        try:
            return bench.read_region(f.path, reg)
        except fr.FrameError as exc:
            bad_reads.append((f.name, str(exc)))
            return None

    sample = load(first)
    if sample is None:
        raise DarkError(f"the first dark, {first.name}, could not be read ({bad_reads[-1][1]})")
    res.adu_step = fr.adu_step(sample)
    q = res.adu_step ** 2 / 12.0

    # bias frames: level, temporal noise (pairs), and a stack for the hot-pixel check
    bias_stack = None
    if sc.biases:
        lv = {c: [] for c in chans}
        vs = {c: [] for c in chans}
        arrs = []
        prev = None
        for f in sc.biases:
            a = load(f)
            if a is None:
                continue
            if len(arrs) < MAX_PER_EXPOSURE:
                arrs.append(a)
            pa = bench.channel_planes(a, sc.bayer)
            for c in chans:
                lv[c].append(bench.clipped_stats(pa[c])[0])
            if prev is not None:
                pb = bench.channel_planes(prev, sc.bayer)
                for c in chans:
                    _, sd, _ = bench.clipped_stats(pa[c] - pb[c])
                    vs[c].append(sd * sd / 2.0 - q)
                prev = None
            else:
                prev = a
        n = len(lv[chans[0]])
        if n:
            res.n_bias = n
            res.bias_source = "frames"
            for c in chans:
                res.bias_adu[c] = float(np.mean(lv[c]))
                res.bias_se[c] = float(np.std(lv[c], ddof=1) / math.sqrt(n)) if n > 1 else float("nan")
                if vs[c]:
                    res.bias_var[c] = float(np.median(vs[c]))
                    npix = max(1, arrs[0].size // len(chans))
                    res.bias_var_se[c] = (res.bias_var[c] + q) * math.sqrt(2.0 / npix) / math.sqrt(len(vs[c]))
            bias_stack = np.median(np.stack(arrs), axis=0) if len(arrs) >= 3 else (arrs[0] if arrs else None)
            dates = sorted(str(f.header.get("DATE-OBS") or "")[:10] for f in sc.biases if f.header.get("DATE-OBS"))
            res.bias_dates = (dates[0], dates[-1]) if dates else ()
    if res.bias_source == "none":
        prof = sp.find_camera_profile(sc.setup.get("camera_id") or "")
        v, rec = sp.camera_value(prof, "bias_level_adu") if prof else (None, None)
        if v is not None and rec and rec.get("source") == "measured":
            res.bias_source = "profile"
            for c in chans:
                res.bias_adu[c] = float(v)
                res.bias_se[c] = float("nan")
            res.bias_dates = (str(rec.get("date") or ""),) * 2

    for g in sorted(sc.darks):
        res.temps[g] = _one_temperature(res, sc, g, chans, gains, load, q, bias_stack)
    if not any(math.isfinite(tr.rate) for tr in res.temps.values()):
        one = [g for g, v in sc.darks.items() if len(v) == 1]
        if one and res.bias_source == "none":
            t = next(iter(sc.darks[one[0]]))
            raise DarkError(f"the darks are all one exposure ({_exp(t)}) and there are no bias frames (and no "
                            "measured bias level in the camera profile): add bias frames, or darks at a second "
                            "exposure")
        raise DarkError("no dark current could be measured from these frames (too few darks that could be read)")
    # the main temperature: the most darks (ties: the profile's set temperature)
    if res.temps:
        set_t = sc.setup.get("set_temp_c")
        res.main_temp = max(res.temps, key=lambda g: (res.temps[g].n_darks,
                                                      -abs(g - set_t) if set_t is not None and not math.isnan(g)
                                                      else 0))
    if res.main is not None:
        rn = [ch.read_noise_e for ch in res.main.channels.values() if math.isfinite(ch.read_noise_e)]
        res.read_noise_e = float(np.median(rn)) if rn else float("nan")
        try:
            _glow(res, sc, res.main, step)
        except (fr.FrameError, ValueError, MemoryError, OSError) as exc:
            res.add("info", f"The edge-glow map could not be made ({exc}).")
    _doubling(res)
    for name, why in bad_reads:
        res.left_out.append((name, why))
    _findings(res, sc)
    res.summary = summary_of(res)
    return res


def _one_temperature(res: DarkResult, sc: DarkScan, g: float, chans, gains, load, q, bias_stack) -> TempResult:
    tr = TempResult(temp_c=g)
    groups = sc.darks[g]
    longest = max(groups)
    stack_arrs = []
    for t in sorted(groups):
        fs = groups[t]
        st = ExposureStat(exptime=t, n=0, n_found=sc.found[g][t])
        means = {c: [] for c in chans}
        pair = []
        temps = []
        for f in fs:
            a = load(f)
            if a is None:
                continue
            st.n += 1
            if f.temp is not None:
                temps.append(f.temp)
            pa = bench.channel_planes(a, sc.bayer)
            for c in chans:
                means[c].append(bench.clipped_stats(pa[c])[0])
            if len(pair) < 2:
                pair.append(a)
            if t == longest and len(stack_arrs) < MAX_PER_EXPOSURE:
                stack_arrs.append(a)
        if st.n == 0:
            continue
        for c in chans:
            st.level[c] = float(np.mean(means[c]))
            st.level_se[c] = float(np.std(means[c], ddof=1) / math.sqrt(st.n)) if st.n > 1 else float("nan")
        if len(pair) == 2:
            pa, pb = bench.channel_planes(pair[0], sc.bayer), bench.channel_planes(pair[1], sc.bayer)
            for c in chans:
                _, sd, n = bench.clipped_stats(pa[c] - pb[c])
                st.var[c] = sd * sd / 2.0 - q
                st.var_se[c] = abs(st.var[c] + q) * math.sqrt(2.0 / max(n, 2))
        st.temp = float(np.median(temps)) if temps else None
        dates = sorted(str(f.header.get("DATE-OBS") or "")[:10] for f in fs if f.header.get("DATE-OBS"))
        st.dates = (dates[0], dates[-1]) if dates else ()
        tr.exposures.append(st)
        tr.n_darks += st.n
    if not tr.exposures:
        return tr
    for c in chans:
        tr.channels[c] = _channel(res, tr, c, gains[c])
    good = [ch for ch in tr.channels.values() if math.isfinite(ch.rate) and math.isfinite(ch.sigma) and ch.sigma > 0]
    if good:
        w = np.array([1.0 / ch.sigma ** 2 for ch in good])
        r = np.array([ch.rate for ch in good])
        tr.measured = float(np.sum(w * r) / np.sum(w))
        # one sensor, one dark current: the colors' spread is real doubt when it exceeds their own errors
        tr.sigma = float(max(1.0 / math.sqrt(np.sum(w)), np.std(r, ddof=1) / math.sqrt(len(r)) if len(r) > 1 else 0))
        methods = {ch.method for ch in good}
        tr.method = methods.pop() if len(methods) == 1 else "mixed"
        if tr.measured < SIGNIFICANT * tr.sigma:
            tr.upper_limit = True
            tr.rate = max(tr.measured, 0.0) + SIGNIFICANT * tr.sigma
        else:
            tr.rate = tr.measured
    # the spec at this temperature
    try:
        import instrument
        spec = sp.find_spec(sc.setup.get("instrume"), (instrument.BUILTIN_SPECS,))
        curve = ((spec or {}).get("dark_current_curve") or {}).get("points")
        if curve and not math.isnan(g):
            tr.spec = instrument.dark_from_curve(curve, g)
    except Exception:
        tr.spec = None
    _hot(tr, stack_arrs, bias_stack, longest, res, sc)
    return tr


def _channel(res: DarkResult, tr: TempResult, c: str, gain: float) -> DarkChannel:
    ch = DarkChannel(c, gain=gain, gain_source=res.gain_source)
    ex = [s for s in tr.exposures if c in s.level]
    ch.bias_adu = res.bias_adu.get(c, float("nan"))
    # from the mean level
    if len({s.exptime for s in ex}) >= 2:
        t = np.array([s.exptime for s in ex])
        m = np.array([s.level[c] for s in ex])
        se = np.array([s.level_se[c] if math.isfinite(s.level_se.get(c, float("nan"))) else 0.05 for s in ex])
        w = 1.0 / np.maximum(se, 0.01) ** 2
        a, b, cov, chi2 = bench._wls(t, m, w)
        if math.isfinite(b):
            ch.mean_rate = b * gain
            ch.mean_sigma = math.sqrt(cov[1, 1]) * gain
            ch.mean_chi2 = chi2
            ch.level_at_zero = a
            dates = {d for s in ex for d in s.dates}
            ch.mean_how = f"slope of {len(ex)} exposures" + (f" (shot on {len(dates)} different days)"
                                                              if len(dates) > 1 else "")
    elif ex and math.isfinite(ch.bias_adu):
        s = ex[0]
        se_m = s.level_se.get(c, float("nan"))
        se_m = se_m if math.isfinite(se_m) else 0.05
        se_b = res.bias_se.get(c, float("nan"))
        se_b = se_b if math.isfinite(se_b) else 0.05
        same = res.bias_source == "frames" and res.bias_dates and s.dates and _same_session(res.bias_dates, s.dates)
        sys_ = 0.0 if same else SESSION_DRIFT_ADU
        ch.mean_rate = (s.level[c] - ch.bias_adu) / s.exptime * gain
        ch.mean_sigma = math.sqrt(se_m ** 2 + se_b ** 2 + sys_ ** 2) / s.exptime * gain
        ch.mean_how = ("above the bias frames (same session)" if same else
                       "above the bias level (" + ("the camera profile's" if res.bias_source == "profile" else
                                                    "bias frames from another session") + f"; ±{SESSION_DRIFT_ADU:g} "
                       "ADU allowed for the bias level's drift between sessions)")
    # from the noise: V(t) = RN² + D·t (e⁻²); bias pairs are the t = 0 point
    pts = [(s.exptime, s.var[c] * gain * gain, s.var_se[c] * gain * gain) for s in ex if c in s.var]
    if c in res.bias_var:
        pts.append((0.0, res.bias_var[c] * gain * gain, res.bias_var_se[c] * gain * gain))
    if len({p[0] for p in pts}) >= 2:
        t = np.array([p[0] for p in pts])
        v = np.array([p[1] for p in pts])
        w = 1.0 / np.maximum(np.array([p[2] for p in pts]), 1e-6) ** 2
        a, b, cov, chi2 = bench._wls(t, v, w)
        if math.isfinite(b):
            ch.noise_rate = b
            ch.noise_sigma = math.sqrt(cov[1, 1])
            ch.read_noise_e = math.sqrt(a) if a > 0 else float("nan")
    cands = [(ch.mean_sigma, "mean", ch.mean_rate), (ch.noise_sigma, "noise", ch.noise_rate)]
    cands = [x for x in cands if math.isfinite(x[0]) and x[0] > 0 and math.isfinite(x[2])]
    if cands:
        s, how, r = min(cands)
        ch.rate, ch.sigma, ch.method = r, s, how
    return ch


def _same_session(d1: tuple, d2: tuple) -> bool:
    from datetime import date
    try:
        a = [date.fromisoformat(x) for x in d1 if x]
        b = [date.fromisoformat(x) for x in d2 if x]
    except ValueError:
        return False
    if not a or not b:
        return False
    return min(abs((x - y).days) for x in a for y in b) <= 1


def _hot(tr: TempResult, arrs: list, bias_stack, t: float, res: DarkResult, sc: DarkScan) -> None:
    """Hot pixels in the region: the longest darks' median stack (minus the bias stack), pixels more than HOT_SIGMA
    of the stack's noise above its level."""
    if len(arrs) < 3:
        return
    stack = np.median(np.stack(arrs), axis=0).astype(np.float64)
    if bias_stack is not None and bias_stack.shape == stack.shape:
        stack = stack - bias_stack
    lvl = float(np.median(stack))
    mad = float(np.median(np.abs(stack - lvl))) * 1.4826
    if not mad > 0:
        mad = float(np.std(stack)) or 1.0
    ex = stack - lvl
    hot = ex > HOT_SIGMA * mad
    tr.hot_count = int(np.count_nonzero(hot))
    tr.hot_ppm = 1e6 * tr.hot_count / stack.size
    tr.hot_exptime = t
    if tr.hot_count:
        tr.hot_rate = float(np.median(ex[hot])) * res.gain / t


def _shrink(a: np.ndarray, n: int = BIN) -> np.ndarray:
    """n×n medians (a hot pixel or a cosmic ray does not move a median), a strip at a time to keep memory low."""
    h, w = a.shape[0] // n * n, a.shape[1] // n * n
    out = np.empty((h // n, w // n), dtype=np.float32)
    rows = max(1, 256 // n)
    for i in range(0, h // n, rows):
        part = np.asarray(a[i * n:min(h, (i + rows) * n), :w], dtype=np.float32)
        k = part.shape[0] // n
        out[i:i + k] = np.median(part.reshape(k, n, w // n, n), axis=(1, 3))
    return out


def _glow(res: DarkResult, sc: DarkScan, tr: TempResult, step) -> None:
    """Edge glow: whole frames shrunk (BIN×BIN), the longest darks minus a reference (bias frames, or the shortest
    darks when they are much shorter), in blocks; the largest excess over the centre, in e⁻/px/s."""
    groups = sc.darks[tr.temp_c]
    longest = max(groups)
    shortest = min(groups)
    if sc.biases:
        ref, t_ref, ref_word = sc.biases, 0.0, "the bias frames"
    elif shortest <= 0.25 * longest and shortest < longest:
        ref, t_ref, ref_word = groups[shortest], shortest, f"the {_exp(shortest)} darks"
    else:
        return

    def stack(fs):
        out = []
        for f in fs[:GLOW_FRAMES]:
            if step:
                step(f"Reading whole frames for the glow map… {f.name}")
            try:
                out.append(_shrink(fr.read_frame(f.path).data))
            except fr.FrameError:
                continue
        if not out:
            raise ValueError("no whole frame could be read")
        return np.median(np.stack(out), axis=0)

    diff = stack(groups[longest]) - stack(ref)
    tr.thumb = diff
    dt = longest - t_ref
    h, w = diff.shape
    nb = int(max(4, min(16, min(h, w) // 4)))         # at least 4×4 shrunk pixels a block
    by, bx = max(1, h // nb), max(1, w // nb)
    blocks = np.array([[np.median(diff[i * by:(i + 1) * by, j * bx:(j + 1) * bx]) for j in range(nb)]
                       for i in range(nb)])
    centre = float(np.median(blocks[nb // 2 - 2:nb // 2 + 2, nb // 2 - 2:nb // 2 + 2]))
    rate = (blocks - centre) * res.gain / dt
    tr.glow_map = rate
    tr.glow_ref = ref_word
    # the blocks' own scatter (noise, a little pattern) is the floor: a glow must stand well above it
    tr.glow_noise = float(np.median(np.abs(rate - np.median(rate)))) * 1.4826
    i, j = np.unravel_index(int(np.argmax(rate)), rate.shape)
    tr.glow_e_s = float(rate[i, j])
    vert = "top" if i < nb / 3 else ("bottom" if i >= 2 * nb / 3 else "")
    hor = "left" if j < nb / 3 else ("right" if j >= 2 * nb / 3 else "")
    # image rows count up from the bottom on screen (origin lower), as the Check image page draws frames
    vert = {"top": "bottom", "bottom": "top"}.get(vert, vert)
    tr.glow_where = (f"{vert}-{hor} corner" if vert and hor else (f"{vert or hor} edge" if vert or hor else "middle"))


def _doubling(res: DarkResult) -> None:
    """Two or more temperatures with a significant dark current: how many °C double it (log-linear fit)."""
    pts = [(g, tr.measured, tr.sigma) for g, tr in res.temps.items()
           if not math.isnan(g) and tr.significant and math.isfinite(tr.measured) and tr.measured > 0]
    if len(pts) < 2:
        return
    t = np.array([p[0] for p in pts])
    y = np.log2(np.array([p[1] for p in pts]))
    sy = np.array([p[2] / (p[1] * math.log(2)) for p in pts])
    a, b, cov, _ = bench._wls(t, y, 1.0 / np.maximum(sy, 1e-6) ** 2)
    if b > 0 and math.isfinite(b):
        res.doubling_c = 1.0 / b
        res.doubling_sigma = math.sqrt(cov[1, 1]) / (b * b)


# ---- findings ------------------------------------------------------------------------------------------------------

def rate_text(v: float) -> str:
    """A dark current in words-friendly digits: 0.00054, 0.0123, 1.25."""
    if v is None or not math.isfinite(v):
        return "—"
    if v == 0:
        return "0"
    d = max(2, 1 - int(math.floor(math.log10(abs(v)))))
    return f"{v:.{min(d, 7)}f}"


def _findings(res: DarkResult, sc: DarkScan) -> None:
    chans = ("R", "G", "B") if res.bayer in fr.BAYER_PATTERNS else ("L",)
    gw = {"measured": "the measured gain", "user": "your gain", "header": "the header's EGAIN",
          "spec": "the spec's gain", "estimate": "an assumed gain"}.get(res.gain_source, "the gain")
    if res.gain_source == "estimate":
        res.add("warn", f"⚠ No gain is known for this camera: electrons are worked out with 1 e⁻/ADU, so every "
                        "dark current here is only as good as that. A bench run (flat pairs) measures the gain.",
                short="gain not known")
    if res.left_out:
        cut = [n for n, why in res.left_out if why.startswith("cut short")]
        res.add("info", f"{len(res.left_out)} frame{'s' if len(res.left_out) != 1 else ''} left out: " +
                "; ".join(f"{n}: {why}" for n, why in res.left_out[:6]) +
                (f"; and {len(res.left_out) - 6} more" if len(res.left_out) > 6 else "") + "." +
                (f" A frame cut short is a copy that stopped half way: copy it again from the camera's drive."
                 if cut else ""))
    for g in sorted(res.temps, key=lambda x: (x != res.main_temp, x)):
        tr = res.temps[g]
        if not tr.channels:
            continue
        where = f" at {_t_word(g)}" if len(res.temps) > 1 or not math.isnan(g) else ""
        exps = ", ".join(_exp(s.exptime) for s in tr.exposures)
        per = ", ".join(f"{c} {rate_text(tr.channels[c].rate)}" for c in chans if c in tr.channels and
                        math.isfinite(tr.channels[c].rate))
        how = {"mean": "from the mean level", "noise": "from the noise", "mixed": "from the level and the noise"
               }.get(tr.method, "")
        spec = (f"; the spec says {rate_text(tr.spec)} at {g:g} °C" if tr.spec else "")
        if tr.upper_limit:
            res.add("ok", f"✔ Dark current{where}: too small to measure, at most {rate_text(tr.rate)} e⁻/px/s "
                          f"(measured {rate_text(tr.measured)} ± {rate_text(tr.sigma)}, {how}; {tr.n_darks} darks at "
                          f"{exps}){spec}. Over a {_exp(max(s.exptime for s in tr.exposures))} exposure that is at "
                          f"most {tr.rate * max(s.exptime for s in tr.exposures):.2f} e⁻ per pixel.")
        else:
            ratio = (f" ({tr.measured / tr.spec:.1f}× the spec)" if tr.spec and tr.spec > 0 else "")
            res.add("ok", f"✔ Dark current{where}: {rate_text(tr.measured)} ± {rate_text(tr.sigma)} e⁻/px/s, {how} "
                          f"({per}; {tr.n_darks} darks at {exps}){spec}{ratio}.")
        # the two ways side by side
        lead = tr.channels.get("G") or next(iter(tr.channels.values()))
        bits = []
        if math.isfinite(lead.mean_rate):
            bits.append(f"level {rate_text(lead.mean_rate)} ± {rate_text(lead.mean_sigma)} ({lead.mean_how})")
        if math.isfinite(lead.noise_rate):
            bits.append(f"noise {rate_text(lead.noise_rate)} ± {rate_text(lead.noise_sigma)}")
        if bits:
            res.add("info", f"The two ways in {lead.ch}{where}: " + "; ".join(bits) + " e⁻/px/s. The smaller "
                            "uncertainty is used.")
        if math.isfinite(lead.mean_chi2) and lead.mean_chi2 > 4 and math.isfinite(lead.mean_rate):
            res.add("info", f"The mean level of the darks jumps between exposures by more than its noise "
                            f"(χ² {lead.mean_chi2:.0f}): the bias level moved between the sessions they were shot "
                            "in. The noise way is not affected by that.")
        if math.isfinite(lead.mean_rate) and math.isfinite(lead.noise_rate) and lead.mean_sigma > 0 and \
                lead.noise_sigma > 0:
            z = (lead.mean_rate - lead.noise_rate) / math.hypot(lead.mean_sigma, lead.noise_sigma)
            if z > 4 and lead.mean_rate > 2 * max(lead.noise_rate, 0) + 1e-4:
                res.add("warn", f"⚠ The darks' level rises faster than their noise{where}: "
                                f"{rate_text(lead.mean_rate)} against {rate_text(lead.noise_rate)} e⁻/px/s. Light "
                                "adds both, so this is more likely the bias level creeping up during the session "
                                "(or the sensor warming) than dark current.", short="level and noise disagree")
        # light: one sensor, so every color has the same dark current; light through a loose cap does not
        if len(chans) == 3:
            mr = {c: (tr.channels[c].mean_rate, tr.channels[c].mean_sigma) for c in chans
                  if math.isfinite(tr.channels[c].mean_rate)}
            if len(mr) == 3:
                hi = max(mr, key=lambda c: mr[c][0])
                lo = min(mr, key=lambda c: mr[c][0])
                gap = mr[hi][0] - mr[lo][0]
                if gap > 4 * math.hypot(mr[hi][1], mr[lo][1]) and gap > max(0.003, 0.3 * abs(mr[hi][0])):
                    res.add("warn", f"⚠ The colors' darks disagree{where} ({', '.join(f'{c} {rate_text(mr[c][0])}' for c in chans)} "
                                    "e⁻/px/s from the level): one sensor has one dark current, so light is likely "
                                    "reaching it. Cap the scope (or the camera) and cover it, then shoot the darks "
                                    "again.", short="light in the darks")
        if tr.hot_count:
            res.add("info", f"Hot pixels{where}: {tr.hot_count:,} in the region ({tr.hot_ppm:,.0f} per million), "
                            f"typically {rate_text(tr.hot_rate)} e⁻/s each in the {_exp(tr.hot_exptime)} darks. "
                            "Dark frames (or a bad-pixel map) take them out of your lights.")
        elif math.isfinite(tr.hot_exptime):
            res.add("ok", f"✔ No hot pixels in the region{where} (in the {_exp(tr.hot_exptime)} darks).")
        if math.isfinite(tr.glow_e_s):
            longest = max(s.exptime for s in tr.exposures)
            if tr.glow_seen(longest):
                res.add("info", f"Edge glow: up to {rate_text(tr.glow_e_s)} e⁻/px/s more than the middle of the "
                                f"frame, at the {tr.glow_where} ({tr.glow_e_s * longest:.0f} e⁻ in "
                                f"{_exp(longest)}; against {tr.glow_ref}). The central region keeps it out of the "
                                "dark current above; darks of the same exposure take it out of your lights.")
            else:
                res.add("ok", f"✔ No edge glow: the whole frame is within {GLOW_MIN_E:g} e⁻ of the middle over "
                              f"{_exp(longest)} (against {tr.glow_ref}).")
    if res.main is not None and math.isfinite(res.read_noise_e):
        res.add("info", f"Read noise from the noise line: {res.read_noise_e:.2f} e⁻ (a check on the bench run's).")
    if math.isfinite(res.doubling_c):
        res.add("ok", f"✔ The dark current doubles every {res.doubling_c:.1f} ± {res.doubling_sigma:.1f} °C.")
    if res.bias_source == "none":
        res.add("info", "No bias frames: the dark current comes from the darks alone (the slope between exposures and "
                        "the noise).")
    elif res.bias_source == "profile":
        res.add("info", "No bias frames here: the camera profile's measured bias level is used for the zero point.")
    else:
        res.add("info", f"Bias: {res.n_bias} frames, level " + ", ".join(
            f"{c} {res.bias_adu[c]:,.1f}" for c in chans if c in res.bias_adu) + " ADU.")
    res.add("info", f"Electrons from {gw} ({res.gain:.4f} e⁻/ADU" +
            (", each color its own" if res.gain_source in ("measured", "user") and len(chans) == 3 else "") + ").")


# ---- summary, history and the profile ----------------------------------------------------------------------------

def _f(v, nd=7):
    return round(float(v), nd) if v is not None and isinstance(v, (int, float)) and math.isfinite(v) else None


def summary_of(res: DarkResult) -> dict:
    out = {"kind": "dark", "region": res.region_name, "bayer": res.bayer, "gain_e_per_adu": _f(res.gain, 4),
           "gain_source": res.gain_source, "main_temp_c": _f(res.main_temp, 2), "n_bias": res.n_bias,
           "bias_source": res.bias_source, "read_noise_e": _f(res.read_noise_e, 3),
           "doubling_c": _f(res.doubling_c, 2), "left_out": len(res.left_out), "worst": res.worst, "temps": {}}
    for g, tr in res.temps.items():
        out["temps"][str(g)] = {
            "dark_e_per_px_s": _f(tr.rate), "measured": _f(tr.measured), "sigma": _f(tr.sigma),
            "upper_limit": tr.upper_limit, "method": tr.method, "spec": _f(tr.spec), "n_darks": tr.n_darks,
            "exposures_s": [s.exptime for s in tr.exposures], "hot_ppm": _f(tr.hot_ppm, 1),
            "hot_rate_e_s": _f(tr.hot_rate, 4), "glow_e_per_px_s": _f(tr.glow_e_s), "glow_where": tr.glow_where,
            "channels": {c: {"rate": _f(ch.rate), "sigma": _f(ch.sigma), "method": ch.method,
                             "mean_rate": _f(ch.mean_rate), "mean_sigma": _f(ch.mean_sigma),
                             "noise_rate": _f(ch.noise_rate), "noise_sigma": _f(ch.noise_sigma)}
                         for c, ch in tr.channels.items()}}
    return out


def run_values(res: DarkResult, profile_id: str) -> list:
    rows = []

    def add(key, v, unit, sigma=None, ch=None, cond=None):
        if v is not None and math.isfinite(v):
            rows.append({"key": key, "value": float(v), "sigma": sigma, "unit": unit, "source": "measured",
                         "channel": ch, "profile_id": profile_id, "date": res.date})
    for g, tr in res.temps.items():
        tag = None if math.isnan(g) else f"{g:g}C"
        add("dark_current_e_per_px_s", tr.rate, "e-/px/s", None if tr.upper_limit else tr.sigma, tag)
        add("hot_pixel_ppm", tr.hot_ppm, "ppm", None, tag)
        add("glow_e_per_px_s", tr.glow_e_s, "e-/px/s", None, tag)
    return rows


def _conditions(res: DarkResult, g: float) -> str:
    s = res.setup
    return (f"{_t_word(g)}, gain {s.get('gain_setting')}, offset {s.get('offset')}, "
            f"{res.region_name.lower()}")


def measured_records(res: DarkResult, run_key: str | None = None) -> tuple[dict, dict, dict]:
    """(camera-wide values, per-color values, the 'dark' block) as format-v1 records, for the main temperature;
    the block keeps every temperature measured."""
    tr = res.main
    vals, per = {}, {}
    block = {"by_temp": [], "source": "measured", "date": res.date, "run": run_key, "region": res.region_name}
    if tr is None or not math.isfinite(tr.rate):
        return vals, per, block
    exps = [s.exptime for s in tr.exposures]
    how = {"mean": "from the darks' mean level", "noise": "from the darks' noise (difference of pairs)",
           "mixed": "from the darks' level and noise"}.get(tr.method, "")
    note = (f"at most: too small to measure (2σ upper limit; measured {rate_text(tr.measured)} ± "
            f"{rate_text(tr.sigma)}); {how}" if tr.upper_limit else how) + \
        f"; {tr.n_darks} darks at {', '.join(_exp(t) for t in exps)}"
    vals["dark_current_e_per_px_s"] = sp.record(
        round(tr.rate, 7), "e-/px/s", "measured", sigma=None if tr.upper_limit else round(tr.sigma, 8),
        n=tr.n_darks, date=res.date, conditions=_conditions(res, res.main_temp), run=run_key, note=note)
    for c, ch in tr.channels.items():
        if math.isfinite(ch.rate):
            ul = ch.rate < SIGNIFICANT * ch.sigma
            v = max(ch.rate, 0.0) + SIGNIFICANT * ch.sigma if ul else ch.rate
            per[c] = {"dark_current_e_per_px_s": sp.record(
                round(v, 7), "e-/px/s", "measured", sigma=None if ul else round(ch.sigma, 8), n=tr.n_darks,
                date=res.date, conditions=_conditions(res, res.main_temp), run=run_key,
                note="at most (2σ upper limit)" if ul else None)}
    for g, t in sorted(res.temps.items()):
        if not math.isfinite(t.rate):
            continue
        block["by_temp"].append({"temp_c": None if math.isnan(g) else g, "value": round(t.rate, 7),
                                 "sigma": None if t.upper_limit else round(t.sigma, 8),
                                 "upper_limit": t.upper_limit, "method": t.method, "n_darks": t.n_darks,
                                 "exposures_s": [s.exptime for s in t.exposures]})
    if math.isfinite(tr.hot_ppm):
        block["hot_pixel_ppm"] = sp.record(round(tr.hot_ppm, 1), "ppm", "measured", n=tr.hot_count, date=res.date,
                                           conditions=f"{_exp(tr.hot_exptime)} darks, {_conditions(res, res.main_temp)}",
                                           run=run_key, note=f"more than {HOT_SIGMA:g}σ above the stacked dark's level")
        if math.isfinite(tr.hot_rate):
            block["hot_pixel_rate_e_per_s"] = sp.record(round(tr.hot_rate, 4), "e-/s", "measured", date=res.date,
                                                        run=run_key, note="the typical hot pixel's dark current")
    if math.isfinite(tr.glow_e_s) and tr.glow_seen(max(s.exptime for s in tr.exposures)):
        block["glow_e_per_px_s"] = sp.record(round(max(tr.glow_e_s, 0.0), 6), "e-/px/s", "measured", date=res.date,
                                             run=run_key, conditions=_conditions(res, res.main_temp),
                                             note=f"largest excess over the middle of the frame, at the "
                                                  f"{tr.glow_where}; against {tr.glow_ref}")
    if math.isfinite(res.doubling_c):
        block["doubling_c"] = sp.record(round(res.doubling_c, 2), "C", "measured",
                                        sigma=round(res.doubling_sigma, 2), n=len(block["by_temp"]), date=res.date,
                                        run=run_key)
    return vals, per, block


def changes_for(res: DarkResult, doc: dict | None) -> list[tuple[str, object, str, object]]:
    vals, _, block = measured_records(res)
    out = []
    for k, r in vals.items():
        old = ((doc or {}).get("values") or {}).get(k) or {}
        out.append((k, old.get("value"), old.get("source", "none"), r["value"]))
    for k in ("hot_pixel_ppm", "glow_e_per_px_s"):
        if k in block:
            old = ((doc or {}).get("dark") or {}).get(k) or {}
            out.append((k, old.get("value"), old.get("source", "none"), block[k]["value"]))
    return out


def apply_to_profile(res: DarkResult, doc: dict | None, written_by: str, run_key: str | None = None,
                     keep_user: bool = False, instrume_fallback: str | None = None) -> dict:
    """The camera profile with this dark run in it (a new one is made from the frames' setup when doc is None).
    keep_user=True keeps a dark current you typed. The 'dark' block (every temperature, hot pixels, glow) is
    replaced as a whole: it is this run's."""
    import copy
    import instrument
    vals, per, block = measured_records(res, run_key)
    if doc is None:
        head = None
        s = res.setup
        cam = instrument.CameraInUse(cam_id=s.get("camera_id") or sp.camera_id(instrume_fallback, s.get("gain_setting"),
                                                                                s.get("offset"), s.get("binning")),
                                     setup=dict(s, instrume=s.get("instrume") or instrume_fallback))
        doc = instrument.starting_camera_profile(cam, head or {"NAXIS1": res.shape[1], "NAXIS2": res.shape[0]},
                                                 written_by)
        doc["camera"]["bayer_pattern"] = res.bayer if res.bayer in fr.BAYER_PATTERNS else None
        doc["camera"]["color"] = "OSC" if res.bayer in fr.BAYER_PATTERNS else "mono"
        if res.bayer not in fr.BAYER_PATTERNS:
            doc["per_channel"] = {"L": {"gain_e_per_adu": None, "read_noise_e": None, "linear_limit_adu": None}}
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
            old = slot.get(k)
            if keep_user and isinstance(old, dict) and old.get("source") == "user":
                continue
            slot[k] = r
        pc[c] = slot
    doc["dark"] = block
    return doc
