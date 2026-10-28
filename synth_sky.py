"""Synthetic on-sky logs with known truth, for the Sky page's tests and self-test.

make_log() builds one night as SHOBS-P 2.2.19.1 writes it (format "shobs-onsky-log"): a target and catalogued field
stars, frames along an airmass track, fluxes in SHOBS-P's image units (TG at software bin 2: the average of the two
green pixels in each cell, binned 2 × 2, so one unit = 8 × gain electrons), with photon, sky and read noise,
scintillation (Osborn et al. 2015, C_Y), extinction, an optional haze ramp, a star with a wrong catalog magnitude, a
saturated star, a variable star, a meridian flip and the per-frame overhead. The truth comes back with it."""
from __future__ import annotations

import math
import os

import numpy as np

import shobs_profile as sp

ZWO = {"instrume": "ZWO ASI2600MC Air", "gain_setting": 100.0, "offset": 50.0, "camera_binning": 1,
       "camera_id": "zwo-asi2600mc-air__g100__o50__bin1", "focal_header_mm": 2900.0, "telescop_header": "EQMod Mount",
       "pixel_um": 3.76, "e_per_adu_header": 0.2429, "bayer_pattern": "RGGB"}
ANDOR = {"instrume": "Andor iKon-L", "gain_setting": 1.0, "offset": None, "camera_binning": 1,
         "camera_id": "andor-ikon-l__g1__ona__bin1", "focal_header_mm": 4250.0, "telescop_header": "PlaneWave",
         "pixel_um": 13.5, "e_per_adu_header": 1.1, "bayer_pattern": None}


def jd_of(night: str, hours_after_midnight_utc: float) -> float:
    """JD of a time on the UTC day after the night's evening date."""
    from datetime import datetime, timezone
    d = datetime.strptime(night, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    return 2440587.5 + d.timestamp() / 86400.0 + 1.0 + hours_after_midnight_utc / 24.0


def make_log(night: str = "2026-10-20", target: str = "Test Star", band: str = "TG", catalog_band: str = "V",
             n_frames: int = 120, exptime: float = 60.0, overhead: float = 3.0, start_h: float = 1.0,
             airmass=(1.05, 1.6), zp1: float = 20.2, k: float = 0.20, gain: float = 0.2429,
             mono: bool = False, setup: dict | None = None, soft_bin: int = 2, n_stars: int = 12,
             mags=None, seeing: float = 3.5, sky_mag: float = 19.5, read_noise: float = 1.2, cy: float = 1.5,
             aperture_mm: float = 356.0, elevation_m: float = 300.0, haze_mag: float = 0.0, cat_error: dict | None = None,
             saturate: int | None = None, variable: int | None = None, flip_after: int | None = None,
             flip_step: float = 0.0, cloud_mag: float = 0.0, focuspos: float = 6400.0, precalibrated: bool = False, seed: int = 1,
             run_id: str | None = None, written: str | None = None, written_by: str = "SHOBS-P 2.2.19.1",
             aperture_px: float = 13.0, noise: bool = True, linear_limit: float = 52775.0) -> tuple[dict, dict]:
    """(log document, truth). airmass: (start, end) — a straight track in time. cat_error {star index: mag added to
    its catalog value}. haze_mag: extra extinction rising linearly over the night (0 = clear). saturate: index of a
    star made bright enough to pass the linear limit. variable: index of a star that varies ±0.1 mag."""
    rng = np.random.default_rng(seed)
    st = dict(setup or (ANDOR if mono else ZWO))
    if mono:
        st["bayer_pattern"] = None
    st["e_per_adu_header"] = gain
    n_color = 1 if mono else (2 if band == "TG" else 4 if band == "CV" else 1)
    per_unit_px = n_color * soft_bin * soft_bin
    e_per_unit = gain * per_unit_px
    rpp = soft_bin if mono else 2 * soft_bin
    raw_arcsec = 206.264806 * st["pixel_um"] / st["focal_header_mm"]
    px_arcsec = raw_arcsec * rpp
    mags = np.array(mags if mags is not None else np.linspace(9.0, 13.0, n_stars), dtype=float)
    n_stars = len(mags)
    true_mags = mags.copy()
    cat = mags.copy()
    for i, d in (cat_error or {}).items():
        cat[i] = mags[i] + d
    if saturate is not None:
        true_mags[saturate] = cat[saturate] = 6.0
    names = [f"Field {i + 1}" for i in range(n_stars)]
    step_s = exptime + overhead
    t0 = jd_of(night, start_h)
    jds = t0 + (np.arange(n_frames) * step_s + exptime / 2.0) / 86400.0
    x = np.linspace(airmass[0], airmass[1], n_frames)
    haze = np.linspace(0.0, haze_mag, n_frames)
    # thin cloud passing in the middle of the night (a bump, not along the airmass track)
    haze = haze + cloud_mag * np.exp(-((np.arange(n_frames) - n_frames / 2.0) / (n_frames / 8.0)) ** 2)
    flip = np.zeros(n_frames)
    if flip_after is not None:
        flip[flip_after:] = flip_step
    D = aperture_mm / 1000.0
    scint = math.sqrt(1e-5) * cy * D ** (-2.0 / 3.0) * exptime ** -0.5 * x ** 1.5 * \
        math.exp(-elevation_m / 8000.0)
    sky_rate_px = 10 ** (-0.4 * (sky_mag - zp1)) * px_arcsec ** 2          # e⁻/s per measured pixel
    npix = math.pi * aperture_px ** 2
    rn2 = read_noise ** 2 * per_unit_px
    sigma_raw = seeing / 2.3548 / raw_arcsec
    frames = []
    var_amp = np.zeros(n_frames)
    if variable is not None:
        var_amp = 0.1 * np.sin(2 * math.pi * np.arange(n_frames) / max(n_frames / 2.0, 1))
    for f in range(n_frames):
        stars = {}
        sky_e = sky_rate_px * 10 ** (-0.4 * k * (x[f] - 1.0)) * exptime   # sky_mag as calibrated at that airmass
        for i in range(n_stars):
            m = true_mags[i] + (var_amp[f] if i == variable else 0.0)
            rate = 10 ** (-0.4 * (m - zp1 + k * (x[f] - 1.0) + haze[f] + flip[f]))
            e = rate * exptime
            if noise:
                e_obs = e * (1.0 + rng.normal(0.0, scint[f])) + rng.normal(0.0, math.sqrt(e + npix * (sky_e + rn2)))
            else:
                e_obs = e
            peak = 500.0 + e / (2 * math.pi * sigma_raw ** 2) / gain / (4.0 / n_color if not mono else 1.0)
            stars[names[i]] = {"flux": round(e_obs / e_per_unit, 3),
                               "inst_mag": round(-2.5 * math.log10(max(e_obs / e_per_unit, 1e-9) / exptime), 5),
                               "peak_adu": round(min(peak, 65535.0), 0), "x": 100.0 + 30 * i, "y": 200.0}
        tgt_e = 10 ** (-0.4 * (10.0 - zp1 + k * (x[f] - 1.0) + haze[f])) * exptime
        stars[target] = {"flux": round(tgt_e / e_per_unit, 3), "inst_mag": 0.0, "peak_adu": 20000.0, "x": 1, "y": 1}
        sky_units = sky_e / e_per_unit + (rng.normal(0, 0.01 * sky_e / e_per_unit) if noise else 0.0)
        frames.append({"file": f"Light_{target}_{exptime:g}s_{f + 1:04d}.fit", "jd_mid": round(float(jds[f]), 6),
                       "airmass": round(float(x[f]), 4), "exptime_s": exptime, "focuspos": focuspos,
                       "pierside": None, "ccd_temp_c": -10.0,
                       "segment": "flip" if flip_after is not None and f >= flip_after else "",
                       "fwhm_px": round(seeing / px_arcsec * (1 + (rng.normal(0, 0.03) if noise else 0)), 3),
                       "sky": round(float(sky_units), 3), "flag": "", "precalibrated": precalibrated, "stars": stars})
    stars_list = [{"name": target, "role": "target", "catalog_mag": None}] + \
        [{"name": n, "role": "comp" if i < 3 else "spare", "catalog_mag": round(float(cat[i]), 3)}
         for i, n in enumerate(names)]
    doc = {"format": sp.ONSKY_FORMAT, "format_version": 1, "run_id": run_id or f"{night.replace('-', '')}T230000-{seed:04x}",
           "written_by": written_by, "written": written or f"{night}T23:59:00-04:00", "night": night,
           "target": target, "mode": "variables", "setup": st, "band": band, "catalog_band": catalog_band,
           "measurement": {"debayer_mode": None if mono else {"TG": "green", "TR": "red", "TB": "blue"}.get(band,
                                                                                                    "luminance"),
                           "software_binning": soft_bin, "raw_px_per_px": rpp, "aperture_radius_px": aperture_px,
                           "sky_inner_px": aperture_px + 7, "sky_outer_px": aperture_px + 17,
                           "flux_units": "SHOBS-P image units summed in the aperture, sky subtracted (calibrated frames)"},
           "precalibrated": precalibrated, "stars": stars_list, "frames": frames}
    truth = {"zp1": zp1, "k": k, "seeing": seeing, "sky_mag": sky_mag, "cy": cy, "overhead": overhead,
             "px_arcsec": px_arcsec, "e_per_unit": e_per_unit, "aperture_mm": aperture_mm, "elevation_m": elevation_m,
             "names": names, "linear_limit": linear_limit}
    return doc, truth


def write_log(doc: dict, folder: str | None = None) -> str:
    d = folder or sp.onsky_dir()
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{doc['night']}__{sp.slug(doc['target'])}__{doc['run_id']}.json")
    sp.write_json(path, doc)
    return path


def camera_profile(gain: float = 0.2429, linear_limit: float = 52775.0, read_noise: float = 1.2,
                   cam_id: str = "zwo-asi2600mc-air__g100__o50__bin1", mono: bool = False) -> dict:
    """A minimal camera profile with a measured gain, read noise and linear limit (camera-wide and for G)."""
    now = sp.now_iso()

    def rec(v, unit):
        return sp.record(v, unit, "measured", n=10, date="2026-10-28", run="bench-test")
    vals = {"gain_e_per_adu": rec(gain, "e-/ADU"), "read_noise_e": rec(read_noise, "e-"),
            "linear_limit_adu": rec(linear_limit, "ADU")}
    doc = {"format": sp.CAMERA_FORMAT, "format_version": 1, "profile_id": cam_id, "revision": 1,
           "written_by": "synth_sky", "created": now, "updated": now,
           "camera": {"instrume": "ZWO ASI2600MC Air" if not mono else "Andor iKon-L",
                      "bayer_pattern": None if mono else "RGGB", "color": "mono" if mono else "OSC"},
           "mode": {"gain_setting": 100.0, "offset": 50.0, "binning": 1}, "values": vals,
           "per_channel": {} if mono else {"G": {"gain_e_per_adu": rec(gain, "e-/ADU"),
                                                 "read_noise_e": rec(read_noise, "e-"),
                                                 "linear_limit_adu": rec(linear_limit, "ADU")}}}
    return doc
