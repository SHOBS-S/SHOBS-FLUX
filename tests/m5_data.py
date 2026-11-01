"""A small, realistic SHOBS data folder for the 0.5 tests (History page, roll-back, system report): a camera profile
with two revisions, a system profile made from three synthetic on-sky nights, and those nights in the run history.
Call make() inside a TempData test (SHOBS_DATA_HOME points at an empty folder)."""
from __future__ import annotations

import math
import os

import history
import onsky
import shobs_profile as sp
import synth_sky


def make(folder: str, nights=(("2026-10-18", 1, {}), ("2026-10-19", 2, {"airmass": (1.1, 1.9)}),
                              ("2026-10-20", 3, {"haze_mag": 0.1}))) -> dict:
    """Returns {"camera_path", "system_path", "system_id", "camera_id", "truth", "summary", "run_keys"}."""
    cam = synth_sky.camera_profile()
    cam["camera"]["pixel_um"] = 3.76
    cam["values"]["qe_peak"] = sp.record(0.8, "fraction", "spec")
    first = dict(cam)
    first["values"] = dict(cam["values"])
    first["values"]["gain_e_per_adu"] = sp.record(0.25, "e-/ADU", "header", date="2026-10-27")
    sp.save_profile(first, "test")                              # revision 1: the header's gain
    cam_path = sp.save_profile(cam, "test")                     # revision 2: measured
    logs = os.path.join(folder, "logs")
    truth = None
    for night, seed, kw in nights:
        doc, t = synth_sky.make_log(n_frames=80, night=night, seed=seed, **kw)
        truth = truth or t
        if kw.get("haze_mag"):
            n = len(doc["frames"])
            for i, f in enumerate(doc["frames"]):
                bump = 0.12 * math.exp(-0.5 * ((i - n / 2) / (n / 8)) ** 2)
                for st in f["stars"].values():
                    st["flux"] = round(st["flux"] * 10 ** (-0.4 * bump), 3)
        synth_sky.write_log(doc, logs)
    ds, _bad = onsky.read_logs(onsky.log_files(logs), cam_lookup=lambda _cid: cam)
    s = onsky.run_all(ds, None, truth["aperture_mm"], truth["elevation_m"])
    cam_id = ds[0].camera_id
    pid = sp.system_id("14in RC", 2900, cam_id)
    doc = onsky.apply_to_profile(s, None, "test", "sky-test", system_id=pid, cam_id=cam_id,
                                 telescope={"name": "14in RC", "aperture_mm": 356, "focal_mm": 2900},
                                 setup=ds[0].setup)
    sys_path = sp.save_profile(doc, "test")
    keys = []
    for r in s.nights:
        keys.append(history.record_run("onsky", r.ds.path, onsky.night_summary(r), camera_id=cam_id, system_id=pid,
                                       n_frames=r.n_frames, status="ok", app="test",
                                       values=onsky.night_values(r, pid)))
    return {"camera_path": cam_path, "system_path": sys_path, "system_id": pid, "camera_id": cam_id,
            "truth": truth, "summary": s, "run_keys": keys}
