"""Synthetic folders of light frames with known truth, for the "folder of lights" tests (sky_folder.py).

make_folder() writes raw frames as a capture program does (16-bit FITS, BZERO 32768, bias 500 ADU, Poisson and read
noise): an OSC camera like John's ASIAIR + ASI2600MC Air (RGGB mosaic, TAN WCS on the raw pixels when wcs=True), or a
mono camera like an Andor iKon-L behind a Sloan i′ filter (no WCS, AIRMASS in the header). The stars come from a
synthetic catalog that is returned with the truth in the shape catalogs.fetch_field gives, with a callable for
run_folder's catalog argument, so no test ever touches the network.

The truth is set in electrons: a star of catalog magnitude m gives 10^(−0.4 (m − zp_e + k (X − 1))) e⁻/s in the
measured color (OSC: both green pixels of each cell together; mono: the pixels)."""
from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone

import numpy as np

import synth

R_FRACTION, B_FRACTION = 0.6, 0.35           # red and blue light of a star, as a share of its green (OSC)


def _cd(scale_deg: float, rotation_deg: float, flip: bool) -> np.ndarray:
    """The CD matrix of a frame: north up and east left at rotation 0 (CD1_1 negative), turned by rotation_deg; a
    mirrored frame (flip) has its x axis reversed."""
    c, s = math.cos(math.radians(rotation_deg)), math.sin(math.radians(rotation_deg))
    cd = scale_deg * np.array([[-c, s], [s, c]])
    if flip:
        cd = cd @ np.diag([-1.0, 1.0])
    return cd


def _wcs(crval, crpix1, cd):
    from astropy.wcs import WCS
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = list(crval)
    w.wcs.crpix = list(crpix1)
    w.wcs.cd = cd
    return w


def make_folder(folder: str, n_frames: int = 12, osc: bool = True, wcs: bool = True, size=(800, 600),
                zp_e: float = 20.0, k: float = 0.2, gain: float = 0.25, seeing_arcsec: float = 3.0,
                focal_mm: float | None = None, pixel_um: float | None = None, rotation_deg: float = 30.0,
                flip: bool = False, filter: str | None = None, sky_mag: float = 19.5, exptime: float = 60.0,
                ra: float = 307.125, dec: float = 41.25, airmass_track=(1.1, 1.5), seed: int = 1,
                n_stars: int = 70, mag_range=(10.0, 15.0), n_faint: int = 40, bright_mag: float | None = None,
                crowded_pair: bool = False, read_noise: float = 1.5, bias: float = 500.0, dither_px: float = 6.0,
                cadence_s: float = 150.0, airmass_header: bool = True, target: str = "Synthetic Field",
                pointing_error_arcsec: float = 20.0, extra_files: bool = True, stale_wcs: bool = False) -> dict:
    """Write n_frames light frames (and, with extra_files, a flat and a dark that must be skipped) into folder.
    size = (width, height) in camera pixels. stale_wcs: every header carries the FIRST frame's plate solution while
    the field dithers (as capture software that does not re-solve each frame). Returns the truth (see the module
    text)."""
    os.makedirs(folder, exist_ok=True)
    rng = np.random.default_rng(seed)
    focal_mm = focal_mm or (2900.0 if osc else 4250.0)
    pixel_um = pixel_um or (3.76 if osc else 13.5)
    if not osc and filter is None:
        filter = "i'"
    w, h = int(size[0]), int(size[1])
    raw_arcsec = 206.264806 * pixel_um / focal_mm
    meas_arcsec = raw_arcsec * (2 if osc else 1)
    cd = _cd(raw_arcsec / 3600.0, rotation_deg, flip)
    centre = ((w - 1) / 2.0, (h - 1) / 2.0)                       # 0-based raw pixel of the field centre
    base = _wcs((ra, dec), (centre[0] + 1, centre[1] + 1), cd)

    # the synthetic catalog: stars over the circle a catalog query of the field would cover
    r_px = 0.5 * math.hypot(w, h) * 1.05
    stars = []

    def add(name, x, y, m):
        sra, sdec = base.wcs_pix2world([[x, y]], 0)[0]
        stars.append({"name": name, "x": float(x), "y": float(y), "ra": float(sra), "dec": float(sdec),
                      "mag": float(m)})

    truth_extra = {}
    if bright_mag is not None:
        add("Synth Bright", centre[0] - 0.22 * w, centre[1] + 0.18 * h, bright_mag)
        truth_extra["saturated"] = "Synth Bright"
    if crowded_pair:
        sep = 12.0 if osc else 6.0
        add("Synth Pair A", centre[0] + 0.2 * w, centre[1] - 0.2 * h, 11.0)
        add("Synth Pair B", centre[0] + 0.2 * w + sep, centre[1] - 0.2 * h + 0.3 * sep, 11.3)
        truth_extra["pair"] = ["Synth Pair A", "Synth Pair B"]
    # the field stars keep apart (6 FWHM), so that only the pair is crowded
    min_sep = 6.0 * seeing_arcsec / raw_arcsec
    mags = np.concatenate([rng.uniform(mag_range[0], mag_range[1], n_stars),
                           rng.uniform(mag_range[1], mag_range[1] + 2.0, n_faint)])
    placed = [(s["x"], s["y"]) for s in stars]
    i = tries = 0
    while i < len(mags) and tries < 200 * len(mags):
        tries += 1
        rr, th = r_px * math.sqrt(rng.uniform(0, 1)), rng.uniform(0, 2 * math.pi)
        x, y = centre[0] + rr * math.cos(th), centre[1] + rr * math.sin(th)
        if any((x - a) ** 2 + (y - b) ** 2 < min_sep ** 2 for a, b in placed):
            continue
        placed.append((x, y))
        add(f"Synth {i + 1:03d}", x, y, mags[i])
        i += 1
    cat_band = "V" if osc else _cat_band(filter)
    catalog = {"stars": sorted([{"name": s["name"], "ra": s["ra"], "dec": s["dec"], "mag": round(s["mag"], 4),
                                 "catalog": "synthetic", "bright": round(s["mag"], 4)} for s in stars],
                               key=lambda s: s["mag"]),
               "all": [{"ra": s["ra"], "dec": s["dec"], "bright": s["mag"]} for s in stars],
               "service": "synthetic catalog", "incomplete": "", "band": cat_band}

    def fetch(ra_deg, dec_deg, radius_deg, band):
        return catalog

    # frames
    sigma = seeing_arcsec / 2.3548 / raw_arcsec                   # PSF sigma, camera pixels
    half = int(math.ceil(5.5 * sigma))
    sky_rate_meas = 10 ** (-0.4 * (sky_mag - zp_e)) * meas_arcsec ** 2   # e⁻/s per measured pixel at airmass 1
    yy, xx = np.mgrid[0:h, 0:w]
    if osc:
        green = ((yy + xx) % 2 == 1)                              # RGGB: G at (0,1) and (1,0)
        red = (yy % 2 == 0) & (xx % 2 == 0)
        weight = np.where(green, 2.0, np.where(red, 4.0 * R_FRACTION, 4.0 * B_FRACTION)).astype(np.float64)
        sky_w = np.where(green, 0.5, np.where(red, 0.7, 0.5)).astype(np.float64)
    else:
        weight = np.ones((h, w))
        sky_w = np.ones((h, w))
    t0 = datetime(2026, 10, 21, 2, 0, 0, tzinfo=timezone.utc)
    xs = np.linspace(airmass_track[0], airmass_track[1], n_frames)
    files = []
    for f in range(n_frames):
        dx, dy = (rng.uniform(-dither_px, dither_px, 2) if f else (0.0, 0.0))
        x_air = float(xs[f])
        ext = 10 ** (-0.4 * k * (x_air - 1.0))
        e = np.full((h, w), sky_rate_meas * ext * exptime) * sky_w
        for s in stars:
            sx, sy = s["x"] + dx, s["y"] + dy
            if not (-half < sx < w + half and -half < sy < h + half):
                continue
            tot = 10 ** (-0.4 * (s["mag"] - zp_e)) * ext * exptime
            x0, x1 = max(0, int(sx) - half), min(w, int(sx) + half + 1)
            y0, y1 = max(0, int(sy) - half), min(h, int(sy) + half + 1)
            if x1 <= x0 or y1 <= y0:
                continue
            gx = np.exp(-0.5 * ((np.arange(x0, x1) - sx) / sigma) ** 2)
            gy = np.exp(-0.5 * ((np.arange(y0, y1) - sy) / sigma) ** 2)
            psf = np.outer(gy, gx) / (2 * math.pi * sigma * sigma)
            e[y0:y1, x0:x1] += tot * psf * weight[y0:y1, x0:x1]
        adu = bias + rng.poisson(e) / gain + rng.normal(0.0, read_noise / gain, (h, w))
        data = np.clip(np.round(adu), 0, 65535)
        t = t0 + timedelta(seconds=f * cadence_s)
        hdr = {"IMAGETYP": "Light", "EXPTIME": float(exptime), "DATE-OBS": t.strftime("%Y-%m-%dT%H:%M:%S.%f"),
               "XBINNING": 1, "YBINNING": 1, "XPIXSZ": pixel_um, "YPIXSZ": pixel_um, "FOCALLEN": focal_mm,
               "EGAIN": gain, "OBJECT": target, "CCD-TEMP": -10.0, "FOCUSPOS": 6400,
               "RA": ra + pointing_error_arcsec / 3600.0 / math.cos(math.radians(dec)),
               "DEC": dec - pointing_error_arcsec / 3600.0, "SITELAT": 35.36, "SITELONG": -81.96}
        if osc:
            hdr.update({"INSTRUME": "ZWO ASI2600MC Air", "BAYERPAT": "RGGB", "XBAYROFF": 0, "YBAYROFF": 0,
                        "GAIN": 100, "OFFSET": 50, "TELESCOP": "EQMod Mount", "PIERSIDE": "West"})
        else:
            hdr.update({"INSTRUME": "Andor iKon-L", "FILTER": filter, "TELESCOP": "PlaneWave"})
        if airmass_header:
            hdr["AIRMASS"] = round(x_air, 5)
        if wcs:
            hdr.update({"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "CRVAL1": ra, "CRVAL2": dec,
                        "CRPIX1": centre[0] + 1 + (0.0 if stale_wcs else dx),
                        "CRPIX2": centre[1] + 1 + (0.0 if stale_wcs else dy), "CD1_1": cd[0, 0],
                        "CD1_2": cd[0, 1], "CD2_1": cd[1, 0], "CD2_2": cd[1, 1], "EQUINOX": 2000.0,
                        "RADESYS": "ICRS"})
        name = f"Light_{target.replace(' ', '_')}_{exptime:g}.0s_Bin1_{f + 1:04d}.fit"
        files.append(synth.write_fits(os.path.join(folder, name), data, hdr))
    if extra_files:
        flat = np.clip(rng.normal(20000, 100, (h, w)), 0, 65535)
        synth.write_fits(os.path.join(folder, "Flat_1.0s_0001.fit"), flat, {"IMAGETYP": "Flat", "EXPTIME": 1.0})
        dark = np.clip(rng.normal(bias, 5, (h, w)), 0, 65535)
        synth.write_fits(os.path.join(folder, "Dark_60.0s_0001.fit"), dark, {"IMAGETYP": "Dark", "EXPTIME": 60.0})
    truth = {"zp_e": zp_e, "k": k, "gain": gain, "bias": bias, "sky_mag": sky_mag, "seeing": seeing_arcsec,
             "band": "TG" if osc else _band(filter), "catalog_band": cat_band, "catalog": catalog, "fetch": fetch,
             "files": files, "px_arcsec": meas_arcsec, "stars": stars, "target": target}
    truth.update(truth_extra)
    return truth


def _band(filter_text) -> str:
    import catalogs
    return catalogs.band_of_filter(filter_text)


def _cat_band(filter_text) -> str:
    import catalogs
    return catalogs.catalog_band(_band(filter_text))
