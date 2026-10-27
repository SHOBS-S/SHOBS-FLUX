"""Synthetic frames with KNOWN camera values, for the tests and the self-test (and, from M2, synthetic flat ladders).

make_star_frame() draws a sky, Gaussian (or donut) stars and Poisson + read noise in electrons, converts to ADU with
a known gain and bias, clips at saturation, optionally steps the values like a 12- or 14-bit camera scaled to 16
bits, and returns (data, header). write_fits() saves it with an ASIAIR-like header. What went in is what the checks
must get back."""
from __future__ import annotations

import numpy as np


def make_star_frame(shape=(600, 800), gain=0.25, read_noise=1.5, bias=500.0, sky_e=40.0, exptime=60.0,
                    stars=None, fwhm_px=4.0, donut=False, bayer="RGGB", sat_adu=65535.0, adu_step=1, seed=1,
                    channel_sky=None):
    """stars: list of (x, y, electrons) in real (unbinned) pixels. channel_sky: {'R': f, 'G': f, 'B': f} sky
    multipliers per Bayer color (default 1). Returns (float32 ADU image, header dict)."""
    rng = np.random.default_rng(seed)
    h, w = shape
    e = np.full(shape, float(sky_e))
    if bayer and channel_sky:
        for i, ch in enumerate(bayer):
            dy, dx = divmod(i, 2)
            e[dy::2, dx::2] *= channel_sky.get(ch, 1.0)
    yy, xx = np.mgrid[0:h, 0:w]
    sig = fwhm_px / 2.3548
    for (x, y, flux) in stars or []:
        r = 6 * sig + (3 * fwhm_px if donut else 0)
        y0, y1, x0, x1 = int(max(0, y - r)), int(min(h, y + r + 1)), int(max(0, x - r)), int(min(w, x + r + 1))
        sub_y, sub_x = yy[y0:y1, x0:x1], xx[y0:y1, x0:x1]
        d = np.hypot(sub_x - x, sub_y - y)
        if donut:
            ring = 1.5 * fwhm_px
            prof = np.exp(-((d - ring) ** 2) / (2 * sig * sig))
        else:
            prof = np.exp(-(d * d) / (2 * sig * sig))
        prof /= prof.sum()
        e[y0:y1, x0:x1] += flux * prof
    e = rng.poisson(np.clip(e, 0, None)).astype(float) + rng.normal(0.0, read_noise, shape)
    adu = e / gain + bias
    adu = np.clip(np.round(adu), 0, sat_adu)
    if adu_step > 1:
        adu = np.floor(adu / adu_step) * adu_step
    hdr = {"SIMPLE": True, "INSTRUME": "Synthetic Cam", "TELESCOP": "Synthetic Mount", "GAIN": 100, "OFFSET": 50,
           "EGAIN": gain, "XBINNING": 1, "YBINNING": 1, "XPIXSZ": 3.76, "FOCALLEN": 2900.0, "EXPTIME": exptime,
           "CCD-TEMP": -10.0, "SET-TEMP": -10.0, "IMAGETYP": "Light", "FILTER": "CV",
           "DATE-OBS": "2026-10-27T03:00:00.000"}
    if bayer:
        hdr["BAYERPAT"] = bayer
    return adu.astype(np.float32), hdr


def write_fits(path: str, data, header: dict, cut_short: bool = False) -> str:
    """Save as a 16-bit FITS (BZERO 32768, as cameras write). cut_short=True writes only part of the pixel data,
    like a copy that stopped half way."""
    from astropy.io import fits

    hdu = fits.PrimaryHDU(np.asarray(data).astype(np.uint16))
    for k, v in header.items():
        if k in ("SIMPLE", "BITPIX", "NAXIS", "NAXIS1", "NAXIS2", "BZERO", "BSCALE", "EXTEND"):
            continue
        hdu.header[k] = v
    hdu.writeto(path, overwrite=True)
    if cut_short:
        import os
        size = os.path.getsize(path)
        with open(path, "r+b") as fh:
            fh.truncate(size // 2)
    return path


def write_xisf(path: str, data, header: dict) -> str:
    """A minimal monolithic XISF 1.0 file (UInt16, uncompressed), for the reader's test."""
    arr = np.asarray(data).astype("<u2")
    h, w = arr.shape
    kws = "".join(f'<FITSKeyword name="{k}" value="{v if not isinstance(v, str) else repr(v)}" comment=""/>'
                  for k, v in header.items() if k != "SIMPLE")
    blob = arr.tobytes()

    def xml(offset):
        return (f'<?xml version="1.0" encoding="UTF-8"?><xisf version="1.0" xmlns="http://www.pixinsight.com/xisf">'
                f'<Image geometry="{w}:{h}:1" sampleFormat="UInt16" colorSpace="Gray" '
                f'location="attachment:{offset}:{len(blob)}">{kws}</Image></xisf>').encode("utf-8")

    head = xml(0)
    offset = 16 + len(head) + 64
    head = xml(offset)
    head = head + b" " * (offset - 16 - len(head))
    with open(path, "wb") as fh:
        fh.write(b"XISF0100" + len(head).to_bytes(4, "little") + b"\0\0\0\0")
        fh.write(head)
        fh.write(blob)
    return path


def star_list(shape=(600, 800), n=25, brightest=2.0e6, seed=2, margin=60):
    """n stars spread over the frame, the brightest first, each 1.6× fainter than the last."""
    rng = np.random.default_rng(seed)
    h, w = shape
    out = []
    f = brightest
    for _ in range(n):
        out.append((float(rng.uniform(margin, w - margin)), float(rng.uniform(margin, h - margin)), f))
        f /= 1.6
    return out
