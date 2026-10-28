"""Synthetic dark frames with KNOWN camera values, for the dark run's tests and the self-test.

make_darks() writes what a user shoots for dark current: bias frames and darks at one or more exposures (each
exposure in a subfolder of its own, as a dark library is usually kept, or all in one folder), from a camera whose
gain, read noise, bias level, dark current, hot pixels, edge glow, temperature and Bayer pattern are chosen here.
Options make the awkward cases: the bias level drifting between sessions, light leaking in (in the colors' own
proportions, as through a lens cap that lets light in), a frame cut short in copying, a master dark left in the
folder, and a cooler that had not settled."""
from __future__ import annotations

import os

import numpy as np

import synth
import synth_bench


def dark_frame(shape, exptime: float, gain: float, read_noise: float, bias: float, dark_e_s: float,
               hot: np.ndarray | None = None, glow_e_s: float = 0.0, leak: dict | None = None,
               bayer: str | None = "RGGB", rng=None) -> np.ndarray:
    """One dark in file ADU (float32). hot: per-pixel EXTRA dark current (e⁻/s) of the hot pixels (zeros elsewhere);
    glow_e_s: extra dark current at the top-left corner, fading over about a tenth of the frame; leak: {'R','G','B'}
    or {'L'} e⁻/s/px of light reaching the sensor."""
    rng = rng or np.random.default_rng(0)
    h, w = shape
    lam = np.full(shape, float(dark_e_s))
    if hot is not None:
        lam = lam + hot
    if glow_e_s:
        yy, xx = np.mgrid[0:h, 0:w]
        r = np.hypot(yy / h, xx / w)
        lam = lam + glow_e_s * np.exp(-r / 0.06)
    if leak:
        add = np.zeros(shape)
        if bayer:
            for i, ch in enumerate(bayer):
                dy, dx = divmod(i, 2)
                add[dy::2, dx::2] = leak.get(ch, 0.0)
        else:
            add[:] = leak.get("L", 0.0)
        lam = lam + add
    e = rng.poisson(np.clip(lam * max(exptime, 0.0), 0, None)).astype(float)
    e = e + rng.normal(0.0, read_noise, shape)
    adu = np.clip(np.round(e / gain + bias), 0, 65535)
    return adu.astype(np.float32)


def make_darks(folder: str, shape=(240, 320), gain=0.25, read_noise=1.4, bias=500.0, dark_e_s=0.05,
               exposures=(10.0, 30.0, 60.0, 120.0), per_exposure=4, bias_frames=6, bayer: str | None = "RGGB",
               temp=-10.0, hot_fraction=0.0005, hot_rate=2.0, glow_e_s=0.0, leak: dict | None = None,
               session_drift_adu=0.0, subfolders=True, bias_in="Bias", cut_short: int = 0, master=False,
               unsettled: int = 0, seed=11, instrume="Synthetic Bench Cam", egain: bool = True) -> dict:
    """Write a dark library into folder. subfolders=True: each exposure in '<t> Sec', bias frames in bias_in (None:
    no bias frames). session_drift_adu: the bias level moves by this much from one exposure's session to the next
    (darks shot on different days). cut_short: that many frames of the longest exposure are written half way, like
    a copy that stopped. egain: write the gain as EGAIN (as ZWO cameras do). master: a stacked 32-bit master dark is left in the longest exposure's folder. unsettled:
    that many darks of the shortest exposure were shot with the sensor 4 °C warm. Returns what went in."""
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(seed)
    hot = None
    if hot_fraction:
        hot = np.zeros(shape)
        n = max(1, int(round(hot_fraction * shape[0] * shape[1])))
        ys = rng.integers(0, shape[0], n)
        xs = rng.integers(0, shape[1], n)
        hot[ys, xs] = hot_rate * rng.uniform(0.5, 1.5, n)
    k = 0

    def put(sub, kind, t, data, i, temp_now=temp, cut=False):
        nonlocal k
        d = os.path.join(folder, sub) if sub else folder
        os.makedirs(d, exist_ok=True)
        word = "Bias" if kind == "bias" else "Dark"
        tt = f"{t * 1000:.1f}ms" if t < 1 else f"{t:.1f}s"
        name = f"{word}_{tt}_Bin1_CV_20261026-{90000 + k:06d}_0deg_{i:04d}.fit"
        hdr = synth_bench.header(kind, t, bayer, n=k, instrume=instrume)
        hdr["CCD-TEMP"] = temp_now
        hdr["SET-TEMP"] = temp
        if egain:
            hdr["EGAIN"] = gain
        hdr["DATE-OBS"] = f"2026-10-{10 + (k // 40):02d}T12:{(k // 60) % 60:02d}:{k % 60:02d}.000"
        synth.write_fits(os.path.join(d, name), data, hdr, cut_short=cut)
        k += 1
        return os.path.join(d, name)

    common = dict(gain=gain, read_noise=read_noise, dark_e_s=dark_e_s, hot=hot, glow_e_s=glow_e_s, leak=leak,
                  bayer=bayer)
    paths = {"cut": [], "master": None}
    if bias_in is not None:
        for i in range(bias_frames):
            put(bias_in if subfolders else "", "bias", 0.0,
                dark_frame(shape, 0.0, gain, read_noise, bias, 0.0, bayer=bayer, rng=rng), i + 1)
    longest = max(exposures)
    for j, t in enumerate(sorted(exposures)):
        sub = (f"{t:g} Sec" if subfolders else "")
        level = bias + session_drift_adu * (1 if j % 2 else -1) if session_drift_adu else bias
        for i in range(per_exposure):
            warm = unsettled and t == min(exposures) and i < unsettled
            put(sub, "dark", t, dark_frame(shape, t, bias=level, rng=rng, **common), i + 1,
                temp_now=temp + 4.0 if warm else temp)
        if t == longest:
            for i in range(cut_short):
                p = put(sub, "dark", t, dark_frame(shape, t, bias=level, rng=rng, **common), 900 + i, cut=True)
                paths["cut"].append(p)
            if master:
                from astropy.io import fits
                d = os.path.join(folder, sub) if sub else folder
                p = os.path.join(d, f"SHOBS-P_master_dark_bin1_{t:g}s.fits")
                hdu = fits.PrimaryHDU(np.full(shape, bias + dark_e_s * t / gain, dtype=np.float32))
                hdu.header["IMAGETYP"] = "Master Dark"
                hdu.header["EXPTIME"] = float(t)
                hdu.header["NCOMBINE"] = per_exposure
                for kk, v in synth_bench.header("dark", t, bayer, instrume=instrume).items():
                    if kk not in ("IMAGETYP", "EXPTIME"):
                        hdu.header[kk] = v
                hdu.writeto(p, overwrite=True)
                paths["master"] = p
    return {"gain": gain, "read_noise": read_noise, "bias": bias, "dark_e_s": dark_e_s, "exposures": list(exposures),
            "hot_fraction": hot_fraction, "hot_rate": hot_rate, "glow_e_s": glow_e_s, "temp": temp, "bayer": bayer,
            "paths": paths}
