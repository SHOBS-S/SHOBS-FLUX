"""The on-sky engine learns what went into synthetic nights: the zero point and extinction on a clear night, the
zero point alone when the airmass hardly moves, seeing (in focus and defocused, keyed by the focuser offset), sky
brightness, scintillation (C_Y) and the per-frame overhead; it sets aside a star with a wrong catalog magnitude, a
saturated star and a variable, judges a hazy night not photometric, keeps a meridian-flip step from spoiling a clear
night, counts only the newest run of a night, refuses pre-calibrated logs and logs with no gain, and writes a valid
system profile that keeps values you typed."""
from __future__ import annotations

import math
import os
import unittest

import numpy as np

from tests.base import TempData

import onsky
import shobs_profile as sp
import synth_sky

PROF = synth_sky.camera_profile(read_noise=1.2)


def lookup(_cam):
    return PROF


def night(**kw):
    doc, truth = synth_sky.make_log(**kw)
    return onsky.load_dataset(doc, lookup), truth


class TestOneNight(TempData):
    def test_clear_night(self):
        ds, t = night()
        self.assertAlmostEqual(ds.e_per_unit, 0.2429 * 8, places=6)          # TG, software bin 2
        self.assertAlmostEqual(ds.px_arcsec, t["px_arcsec"], places=6)
        r = onsky.analyse(ds, None, 356, 300)
        self.assertTrue(r.photometric)
        self.assertLess(abs(r.zp1 - t["zp1"]), 0.02, r.zp1)
        self.assertLess(abs(r.k - t["k"]), 0.02, r.k)
        self.assertLess(abs(r.fwhm_arcsec - t["seeing"]), 0.1)
        self.assertLess(abs(r.sky_mag - t["sky_mag"]), 0.05)
        self.assertLess(abs(r.overhead_s - t["overhead"]), 0.1)
        self.assertLess(abs(r.cy - t["cy"]) / t["cy"], 0.35, r.cy)
        self.assertEqual(r.stars_out, {})
        self.assertFalse(any(k == "bad" for k, _ in r.lines))

    def test_short_airmass_span_uses_the_prior(self):
        ds, t = night(airmass=(1.10, 1.15))
        r = onsky.analyse(ds, (0.21, "profile"), 356, 300)
        self.assertTrue(math.isnan(r.k))
        self.assertEqual(r.k_used_source, "profile")
        self.assertLess(abs(r.zp1 - t["zp1"]), 0.02)
        r2 = onsky.analyse(ds, None, 356, 300)
        self.assertEqual(r2.k_used_source, "typical")

    def test_bad_stars_left_out(self):
        ds, t = night(cat_error={2: 1.4}, saturate=4, variable=6)
        r = onsky.analyse(ds, None, 356, 300)
        out = r.stars_out
        self.assertIn("catalog magnitude disagrees", out.get("Field 3", ""))
        self.assertIn("saturated", out.get("Field 5", ""))
        self.assertIn("varies", out.get("Field 7", ""))
        self.assertLess(abs(r.zp1 - t["zp1"]), 0.02)

    def test_haze_is_not_photometric(self):
        doc, t = synth_sky.make_log(airmass=(1.2, 1.2))
        # a haze bump in the middle of the night (not along the airmass track)
        n = len(doc["frames"])
        for i, f in enumerate(doc["frames"]):
            dim = 10 ** (-0.4 * 0.08 * math.exp(-((i - n / 2) / (n / 8)) ** 2))
            for s in f["stars"].values():
                s["flux"] *= dim
        r = onsky.analyse(onsky.load_dataset(doc, lookup), None, 356, 300)
        self.assertFalse(r.photometric)
        self.assertTrue(any("Not photometric" in x for _, x in r.lines))

    def test_flip_step_does_not_spoil_a_clear_night(self):
        ds, t = night(airmass=(1.1, 1.18), flip_after=60, flip_step=0.06)
        r = onsky.analyse(ds, None, 356, 300)
        self.assertTrue(r.photometric, r.lines)
        self.assertTrue(any("meridian flip" in x for _, x in r.lines))

    def test_flip_step_with_a_long_airmass_track(self):
        ds, t = night(airmass=(1.05, 1.6), flip_after=60, flip_step=0.03)
        r = onsky.analyse(ds, None, 356, 300)
        self.assertLess(abs(r.k - t["k"]), 0.02, r.k)
        self.assertLess(abs(r.zp1 - (t["zp1"] - 0.015)), 0.02)             # the average of the two sides

    def test_cloud_bump(self):
        ds, t = night(cloud_mag=0.08)
        self.assertFalse(onsky.analyse(ds, None, 356, 300).photometric)

    def test_short_exposures(self):
        ds, t = night(exptime=0.75, overhead=30.0, n_frames=200, airmass=(1.08, 1.14),
                      mags=[8.7, 9.1, 9.4, 9.6, 11.2, 9.8])
        r = onsky.analyse(ds, None, 356, 300)
        self.assertLess(abs(r.zp1 - t["zp1"]), 0.02)
        self.assertLess(abs(r.overhead_s - 30.0), 0.2)
        self.assertLess(abs(r.cy - t["cy"]) / t["cy"], 0.35, r.cy)

    def test_mono_sloan(self):
        doc, t = synth_sky.make_log(mono=True, band="SI", catalog_band="SI", soft_bin=1, aperture_px=8, gain=1.1)
        ds = onsky.load_dataset(doc, lambda c: None)                         # the Andor has no profile: EGAIN
        self.assertEqual(ds.color, "M")
        self.assertAlmostEqual(ds.e_per_unit, 1.1)
        r = onsky.analyse(ds, None, 600, 276)
        self.assertLess(abs(r.zp1 - t["zp1"]), 0.02)

    def test_header_gain_and_linear_guess(self):
        doc, t = synth_sky.make_log(saturate=4)
        ds = onsky.load_dataset(doc, lambda c: None)
        self.assertEqual(ds.gain_source, "header")
        r = onsky.analyse(ds, None, 356, 300)
        self.assertIn("saturated", r.stars_out.get("Field 5", ""))
        self.assertTrue(any("EGAIN" in x for _, x in r.lines))

    def test_refusals(self):
        doc, _ = synth_sky.make_log(precalibrated=True)
        r = onsky.analyse(onsky.load_dataset(doc, lookup))
        self.assertFalse(r.usable)
        self.assertIn("calibrated", r.why_not)
        doc, _ = synth_sky.make_log()
        doc["setup"]["e_per_adu_header"] = None
        r = onsky.analyse(onsky.load_dataset(doc, lambda c: None))
        self.assertFalse(r.usable)
        self.assertIn("gain", r.why_not)
        doc, _ = synth_sky.make_log(n_frames=5)
        r = onsky.analyse(onsky.load_dataset(doc, lookup))
        self.assertFalse(r.usable)
        with self.assertRaises(onsky.OnSkyError):
            onsky.load_dataset({"format": "something else"})


class TestNights(TempData):
    def logs(self):
        a, t = synth_sky.make_log(night="2026-10-20", seed=1, focuspos=6400)
        b, _ = synth_sky.make_log(night="2026-10-20", seed=2, target="HD 1", exptime=0.75, overhead=30.0,
                                  n_frames=200, airmass=(1.08, 1.14), seeing=10.0, focuspos=6525)
        c, _ = synth_sky.make_log(night="2026-10-21", seed=3, zp1=19.6, airmass=(1.2, 1.25))   # dimmer: haze-free
        d, _ = synth_sky.make_log(night="2026-10-22", seed=4, target="HD 1", exptime=0.75, overhead=30.0,
                                  n_frames=200, airmass=(1.08, 1.14), seeing=9.5, focuspos=6390)
        # a re-run of the 20th (newer) that must replace the first
        a2, _ = synth_sky.make_log(night="2026-10-20", seed=5, focuspos=6400, run_id="20261021T100000-beef",
                                   written="2026-10-21T10:00:00-04:00")
        for doc in (a, b, c, d, a2):
            synth_sky.write_log(doc)
        return t

    def test_combine(self):
        t = self.logs()
        ds, bad = onsky.read_logs(onsky.log_files(), lookup)
        self.assertEqual(bad, [])
        self.assertEqual(len(ds), 5)
        s = onsky.run_all(ds, None, 356, 300)
        self.assertEqual(len(s.replaced), 1)
        self.assertEqual(len(s.nights), 4)
        b = s.bands["TG"]
        self.assertIn("2026-10-21", b.zp_out)                               # 0.6 mag off: set aside
        self.assertIn("2026-10-20", b.zp_nights)
        self.assertLess(abs(b.zp1 - t["zp1"]), 0.02, (b.zp1, b.zp_nights))
        self.assertLess(abs(b.k - t["k"]), 0.02)
        self.assertLess(abs(b.fwhm_in_focus - 3.5), 0.15)
        self.assertIn("+125 steps", b.fwhm_defocus)                         # 6525 − 6400 the same night
        nights = {r.ds.night + r.ds.target: r for r in s.nights}
        self.assertEqual(nights["2026-10-22HD 1"].defocus_key, "+125 steps")  # no in-focus run: same size
        self.assertLess(abs(s.overhead_s - 3.0), 0.2)                       # the 30 s runs (a set delay) don't count
        self.assertTrue(math.isfinite(b.cy))

    def test_profile(self):
        self.logs()
        ds, _ = onsky.read_logs(onsky.log_files(), lookup)
        s = onsky.run_all(ds, None, 356, 300)
        cam = ds[0].camera_id
        pid = sp.system_id("14in RC", 2900, cam)
        doc = onsky.apply_to_profile(s, None, "test", run_key="onsky-1", system_id=pid, cam_id=cam,
                                     telescope={"name": "14in RC", "aperture_mm": 356, "focal_mm": 2900},
                                     setup=ds[0].setup)
        self.assertEqual(sp.validate(doc), [])
        self.assertEqual(doc["bands"]["TG"]["zero_point"]["source"], "measured")
        self.assertNotIn("site", [k for k in doc if doc.get(k) and k == "site" and "never" not in str(doc[k])])
        path = sp.save_profile(doc, "test")
        self.assertTrue(os.path.exists(path))
        back = sp.find_system_profile(cam, 2905)
        self.assertEqual(back["profile_id"], pid)
        # a value typed by you is kept when asked
        back["bands"]["TG"]["extinction_k"] = sp.record(0.25, "mag/airmass", "user")
        changes = onsky.changes_for(s, back)
        self.assertTrue(any(c[0].endswith("Extinction") and c[2] == "user" for c in changes))
        kept = onsky.apply_to_profile(s, back, "test", keep_user=True)
        self.assertEqual(kept["bands"]["TG"]["extinction_k"]["value"], 0.25)
        replaced = onsky.apply_to_profile(s, back, "test", keep_user=False)
        self.assertEqual(replaced["bands"]["TG"]["extinction_k"]["source"], "measured")
        # the next run's extinction prior comes from the profile
        self.assertEqual(onsky.k_prior_from(replaced, "TG")[1], "profile")
        rows = onsky.night_values(s.nights[0], pid)
        self.assertTrue(all(r["profile_id"] == pid and r["date"] for r in rows))
        summ = onsky.night_summary(s.nights[0])
        import json
        json.dumps(summ)

    def test_damaged_log_is_reported(self):
        self.logs()
        bad = os.path.join(sp.onsky_dir(), "2026-10-30__broken__x.json")
        with open(bad, "w") as fh:
            fh.write("{not json")
        ds, problems = onsky.read_logs(onsky.log_files(), lookup)
        self.assertEqual(len(problems), 1)
        self.assertEqual(len(ds), 5)


class TestStats(unittest.TestCase):
    def test_night_of(self):
        self.assertEqual(onsky.night_of(2461339.508176), "2026-10-25")       # 25–26 Oct, 00:12 UT on the 26th

    def test_clipped_and_wmean(self):
        keep = onsky.clipped([1.0, 1.01, 0.99, 1.02, 5.0])
        self.assertFalse(keep[-1])
        m, e = onsky.wmean([1.0, 1.2], [0.01, 0.01])
        self.assertAlmostEqual(m, 1.1)
        self.assertGreater(e, 0.05)                                          # never less than the scatter allows

    def test_moon(self):
        v = onsky.moon_illumination(2461340.5)                              # 26 Oct 2026, near full
        self.assertTrue(math.isnan(v) or v > 0.9)


if __name__ == "__main__":
    unittest.main()
