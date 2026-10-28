"""Synthetic bench frames with KNOWN camera values: flat-pair ladders and bias pairs, for the bench run's tests and the
self-test.

make_ladder() writes, into one folder, what a user shoots for a photon transfer curve: bias pairs and flat pairs at
a list of exposures, from a camera whose gain, read noise, bias level, full well, non-linearity, exposure-timing
offset, ADC bit depth and Bayer pattern are all chosen here. The bench run must recover what went in.

The light: each color gets its own rate (e⁻/s per pixel), times a gentle vignetting across the frame, times an
optional panel drift during the ladder. Each pixel collects Poisson electrons over (exposure + timing offset), the
response bends by `nonlin` near full well and stops at full well, read noise is added, the result is converted to
ADU with the gain and the bias, rounded, clipped at the ADC's top, and stepped like a 12- or 14-bit camera scaled to
16 bits when adc_bits < 16."""
from __future__ import annotations

import os

import numpy as np

import synth


def flat_frame(shape, rates: dict, exptime: float, gain: float, read_noise: float, bias: float, full_well: float,
               nonlin: float = 0.0, t_offset: float = 0.0, bayer: str | None = "RGGB", adc_bits: int = 16,
               light_scale: float = 1.0, rng=None) -> np.ndarray:
    """One frame in file ADU (float32). rates: {'R','G','B'} e⁻/s/px for color, {'L': …} for mono. exptime 0 (or a
    zero rate) gives a bias frame. nonlin: fractional loss of signal AT full well (response e·(1 − nonlin·(e/FW)²)),
    so the curve stays straight at low signal and bends near the top."""
    rng = rng or np.random.default_rng(0)
    h, w = shape
    yy, xx = np.mgrid[0:h, 0:w]
    vignette = 1.0 - 0.08 * (((xx - w / 2) / w) ** 2 + ((yy - h / 2) / h) ** 2) * 4
    lam = np.zeros(shape)
    t_eff = max(0.0, exptime + t_offset) if exptime > 0 else 0.0
    if bayer:
        for i, ch in enumerate(bayer):
            dy, dx = divmod(i, 2)
            lam[dy::2, dx::2] = rates.get(ch, 0.0)
    else:
        lam[:] = rates.get("L", 0.0)
    lam = lam * vignette * t_eff * light_scale
    e = rng.poisson(np.clip(lam, 0, None)).astype(float)
    if nonlin:
        e = e * (1.0 - nonlin * np.clip(e / full_well, 0, 1.0) ** 2)
    e = np.minimum(e, full_well)
    e = e + rng.normal(0.0, read_noise, shape)
    adu = e / gain + bias
    step = 2 ** (16 - adc_bits) if adc_bits < 16 else 1
    top = 65535.0 if step == 1 else (2 ** adc_bits - 1) * step
    adu = np.clip(np.round(adu / step), 0, top / step) * step
    return adu.astype(np.float32)


def header(kind: str, exptime: float, bayer: str | None, gain_setting=100, offset=50, temp=-10.0,
           instrume="Synthetic Bench Cam", n: int = 0, adc_bits: int = 16) -> dict:
    hdr = {"INSTRUME": instrume, "TELESCOP": "Bench", "GAIN": gain_setting, "OFFSET": offset, "XBINNING": 1,
           "YBINNING": 1, "XPIXSZ": 3.76, "EXPTIME": float(exptime), "CCD-TEMP": temp, "SET-TEMP": temp,
           "IMAGETYP": {"bias": "Bias", "flat": "Flat", "dark": "Dark"}.get(kind, "Light"),
           "DATE-OBS": f"2026-10-27T19:{30 + n // 60:02d}:{n % 60:02d}.000"}
    if bayer:
        hdr["BAYERPAT"] = bayer
    return hdr


DEFAULT_EXPOSURES = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0)


def make_ladder(folder: str, shape=(256, 320), gain=0.25, read_noise=1.4, bias=500.0, full_well=15000.0,
                nonlin=0.03, t_offset=0.0, bayer: str | None = "RGGB", adc_bits=16, rates=None,
                exposures=DEFAULT_EXPOSURES, bias_pairs=5, drift_per_frame=0.0, lit_bias: float = 0.0, lit_white: bool = False,
                seed=7, instrume="Synthetic Bench Cam") -> dict:
    """Write a bias + flat-pair ladder into folder (ASIAIR-like names). Returns what went in. drift_per_frame: the
    panel's fractional change per frame shot (0.001 = 0.1 % per frame). lit_bias: e⁻/s/px of panel light leaking
    into the 'bias' frames (shot at 1 ms with the panel on), as on 27 Oct: in the flats' colors (the panel), or the same
    in every color (lit_white=True: room light)."""
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(seed)
    if rates is None:
        rates = {"R": 9000.0, "G": 13000.0, "B": 7000.0} if bayer else {"L": 12000.0}
    n = 0
    tag = "_".join(["Bin1", "CV"])

    def put(kind, t, data, i):
        nonlocal n
        name = (f"{'Bias' if kind == 'bias' else 'Flat'}_"
                f"{(f'{t * 1000:.1f}ms' if t < 1 else f'{t:.1f}s')}_{tag}_20261027-{193000 + n:06d}_0deg_{i:04d}.fit")
        synth.write_fits(os.path.join(folder, name), data, header(kind, t, bayer, n=n, instrume=instrume,
                                                                     adc_bits=adc_bits))
        n += 1

    common = dict(gain=gain, read_noise=read_noise, bias=bias, full_well=full_well, nonlin=nonlin, bayer=bayer,
                  adc_bits=adc_bits)
    for i in range(2 * bias_pairs):
        t_b = 0.001 if lit_bias else 0.0
        if lit_bias and lit_white:
            lr = {k: lit_bias for k in rates}
        elif lit_bias:                         # panel light: each color in the flats' proportions (lit_bias = brightest)
            top = max(rates.values())
            lr = {k: lit_bias * v / top for k, v in rates.items()}
        else:
            lr = {k: 0.0 for k in rates}
        put("bias", t_b, flat_frame(shape, lr, t_b, t_offset=0.0, rng=rng, **common), i + 1)
    for t in exposures:
        for i in range(2):
            scale = 1.0 + drift_per_frame * n
            put("flat", t, flat_frame(shape, rates, t, t_offset=t_offset, light_scale=scale, rng=rng, **common),
                i + 1)
    return {"gain": gain, "read_noise": read_noise, "bias": bias, "full_well": full_well, "nonlin": nonlin,
            "t_offset": t_offset, "bayer": bayer, "adc_bits": adc_bits, "rates": rates, "exposures": list(exposures)}
