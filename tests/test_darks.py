"""The dark run recovers what went into synthetic dark libraries: the dark current (from the level and from the
noise), an upper limit when there is none to see, hot pixels and edge glow; it finds darks in subfolders, leaves out
frames cut short, master darks and darks shot before the cooler settled; it works with one exposure plus bias frames,
with no bias frames at all, on a mono camera, at two temperatures (doubling); it catches light in the darks, and
writes a valid profile that the Check image page reads back at other temperatures."""
from __future__ import annotations

import math
import os
import unittest

from tests.base import TempData

import darks
import instrument
import shobs_profile as sp
import synth_dark

REGION = "Central 50 %"


class TestDarks(TempData):
    def lib(self, name="darks", **kw):
        folder = self.path(name)
        truth = synth_dark.make_darks(folder, **kw)
        return folder, truth

    def test_dark_current_recovered(self):
        folder, t = self.lib(dark_e_s=0.05)
        r = darks.run_darks(folder, REGION)
        m = r.main
        self.assertEqual(m.temp_c, -10.0)
        self.assertFalse(m.upper_limit)
        self.assertLess(abs(m.measured - t["dark_e_s"]) / t["dark_e_s"], 0.03, m.measured)
        for c, ch in m.channels.items():
            self.assertLess(abs(ch.mean_rate / t["dark_e_s"] - 1), 0.05, f"level {c}")
            self.assertLess(abs(ch.noise_rate / t["dark_e_s"] - 1), 0.10, f"noise {c}")
        self.assertLess(abs(r.read_noise_e - t["read_noise"]), 0.05)
        self.assertEqual(r.gain_source, "header")
        self.assertGreater(m.hot_count, 0)
        self.assertEqual(r.bias_source, "frames")
        self.assertNotIn("bad", {k for k, _ in r.lines})

    def test_cold_sensor_uses_the_noise(self):
        # 0.0005 e⁻/s with the bias level moving ±0.4 ADU between sessions: the level way cannot see it, the noise can
        folder, t = self.lib(dark_e_s=0.0005, exposures=(20.0, 60.0, 180.0), session_drift_adu=0.4,
                             shape=(320, 400), per_exposure=6)
        r = darks.run_darks(folder, REGION)
        m = r.main
        self.assertEqual(m.method, "noise")
        self.assertLess(abs(m.measured - 0.0005), 4 * m.sigma + 1e-5, (m.measured, m.sigma))
        self.assertTrue(any("bias level moved between the sessions" in x for _, x in r.lines))

    def test_no_dark_current_is_an_upper_limit(self):
        folder, _ = self.lib(dark_e_s=0.0, exposures=(30.0, 120.0), hot_fraction=0.0)
        r = darks.run_darks(folder, REGION)
        m = r.main
        self.assertTrue(m.upper_limit)
        self.assertGreater(m.rate, 0)
        self.assertLess(m.rate, 0.002)
        self.assertTrue(any("too small to measure" in x for _, x in r.lines))
        self.assertTrue(any("No hot pixels" in x for _, x in r.lines))
        vals, _per, block = darks.measured_records(r)
        rec = vals["dark_current_e_per_px_s"]
        self.assertIsNone(rec["sigma"])
        self.assertIn("at most", rec["note"])
        self.assertTrue(block["by_temp"][0]["upper_limit"])

    def test_one_exposure_with_bias_mono_and_bad_files(self):
        folder, t = self.lib(bayer=None, exposures=(180.0,), cut_short=1, master=True, dark_e_s=0.02)
        r = darks.run_darks(folder, REGION)
        m = r.main
        self.assertEqual(set(m.channels), {"L"})
        self.assertLess(abs(m.measured / 0.02 - 1), 0.05)
        why = dict(r.left_out)
        self.assertTrue(any(w.startswith("cut short") for w in why.values()), why)
        self.assertTrue(any("master" in w for w in why.values()), why)
        self.assertIn("same session", m.channels["L"].mean_how)

    def test_one_exposure_no_bias_stops(self):
        folder, _ = self.lib(exposures=(60.0,), bias_in=None)
        with self.assertRaises(darks.DarkError) as cm:
            darks.run_darks(folder, REGION)
        self.assertIn("one exposure", str(cm.exception))

    def test_no_bias_two_exposures(self):
        folder, t = self.lib(exposures=(30.0, 120.0), bias_in=None, dark_e_s=0.03)
        r = darks.run_darks(folder, REGION)
        self.assertEqual(r.bias_source, "none")
        self.assertLess(abs(r.main.measured / 0.03 - 1), 0.08)

    def test_flat_folder_and_unsettled(self):
        folder, t = self.lib(subfolders=False, unsettled=2, dark_e_s=0.04)
        sc = darks.scan_darks(folder)
        self.assertEqual(len(sc.darks), 1)
        self.assertTrue(any("cooler had not settled" in w for _, w in sc.left_out))
        r = darks.run_darks(folder, REGION, scan=sc)
        self.assertLess(abs(r.main.measured / 0.04 - 1), 0.05)

    def test_hidden_looking_folder_is_read(self):
        folder, _ = self.lib(exposures=(0.75, 20.0))
        os.rename(os.path.join(folder, "0.75 Sec"), os.path.join(folder, ".75 Sec"))
        sc = darks.scan_darks(folder)
        self.assertIn(0.75, next(iter(sc.darks.values())))

    def test_glow(self):
        folder, _ = self.lib(glow_e_s=0.5, exposures=(20.0, 120.0), shape=(320, 400))
        r = darks.run_darks(folder, REGION)
        m = r.main
        self.assertTrue(m.glow_seen(120.0))
        self.assertIn("corner", m.glow_where)
        self.assertTrue(any(x.startswith("Edge glow") for _, x in r.lines))
        folder2, _ = self.lib("plain", exposures=(20.0, 120.0), shape=(320, 400))
        self.assertFalse(darks.run_darks(folder2, REGION).main.glow_seen(120.0))

    def test_light_in_the_darks(self):
        folder, _ = self.lib(leak={"R": 0.05, "G": 0.2, "B": 0.02})
        r = darks.run_darks(folder, REGION)
        self.assertTrue(any("light is likely reaching it" in x for _, x in r.lines))
        self.assertEqual(r.status, "warn")
        self.assertIn("light in the darks", r.worst)

    def test_two_temperatures_doubling(self):
        base = self.path("lib2")
        synth_dark.make_darks(os.path.join(base, "minus10"), temp=-10.0, dark_e_s=0.02, seed=1)
        synth_dark.make_darks(os.path.join(base, "zero"), temp=0.0, dark_e_s=0.08, seed=2, bias_in=None)
        r = darks.run_darks(base, REGION)
        self.assertEqual(sorted(r.temps), [-10.0, 0.0])
        self.assertLess(abs(r.doubling_c - 5.0), 0.6, r.doubling_c)
        self.assertTrue(any("doubles every" in x for _, x in r.lines))

    def test_no_temperature_in_headers_is_one_group(self):
        folder, _ = self.lib(temp_keys="none", dark_e_s=0.03)
        r = darks.run_darks(folder, REGION)
        self.assertEqual(len(r.temps), 1)
        self.assertEqual(r.main.n_darks, 16)
        self.assertLess(abs(r.main.measured / 0.03 - 1), 0.05)
        doc = darks.apply_to_profile(r, None, "test")
        self.assertEqual(len(doc["dark"]["by_temp"]), 1)
        self.assertIsNone(doc["dark"]["by_temp"][0]["temp_c"])

    def test_sensor_temperature_only_is_not_split(self):
        # a steady −11 °C with ±0.4 °C of scatter straddles the old fixed bins at −12 / −10
        folder, _ = self.lib(temp_keys="sensor", temp=-11.0, temp_jitter=0.4)
        r = darks.run_darks(folder, REGION)
        self.assertEqual(list(r.temps), [-11.0])
        self.assertFalse(math.isfinite(r.doubling_c))

    def test_main_temperature_is_one_that_was_measured(self):
        base = self.path("lib3")
        synth_dark.make_darks(os.path.join(base, "a"), temp=-10.0, exposures=(60.0,), per_exposure=10,
                              bias_in=None, seed=3)
        synth_dark.make_darks(os.path.join(base, "b"), temp=0.0, exposures=(30.0, 120.0), per_exposure=3,
                              bias_in=None, seed=4)
        r = darks.run_darks(base, REGION)
        self.assertEqual(r.main_temp, 0.0)
        self.assertTrue(any(x.startswith("No dark current at -10") for _, x in r.lines))
        self.assertNotIn("nan", " ".join(x for _, x in r.lines))

    def test_dark_at_temp_uses_the_nearest_measurement(self):
        prof = {"dark": {"by_temp": [{"temp_c": -10.0, "value": 0.02, "sigma": 0.001},
                                     {"temp_c": 0.0, "value": 0.08, "sigma": 0.002}]}}
        rec = instrument.dark_at_temp(prof, None, 0.4)
        self.assertEqual(rec["value"], 0.08)
        self.assertEqual(rec["source"], "measured")
        rec = instrument.dark_at_temp(prof, None, -5.0)
        self.assertEqual(rec["source"], "derived")
        self.assertAlmostEqual(rec["value"], 0.04, delta=0.002)
        close = {"dark": {"by_temp": [{"temp_c": -12.0, "value": 0.02}, {"temp_c": -10.0, "value": 0.02}]}}
        rec = instrument.dark_at_temp(close, None, 10.0)          # 2 °C apart: no flat line carried on to +10 °C
        self.assertGreater(rec["value"], 0.2)

    def test_damaged_profile_does_not_stop_the_run(self):
        folder, _ = self.lib(dark_e_s=0.05)
        r = darks.run_darks(folder, REGION)
        doc = darks.apply_to_profile(r, None, "test")
        doc["per_channel"] = "broken"
        doc["values"]["gain_e_per_adu"] = sp.record(0.25, "e-/ADU", "measured")
        sp.save_profile(doc, "test")
        r2 = darks.run_darks(folder, REGION)
        self.assertLess(abs(r2.main.measured / 0.05 - 1), 0.05)

    def test_profile_and_reading_it_back(self):
        folder, t = self.lib(dark_e_s=0.02)
        r = darks.run_darks(folder, REGION)
        doc = darks.apply_to_profile(r, None, "test")
        self.assertEqual(sp.validate(doc), [])
        self.assertEqual(doc["values"]["dark_current_e_per_px_s"]["source"], "measured")
        self.assertIn("-10 °C", doc["values"]["dark_current_e_per_px_s"]["conditions"])
        self.assertIn("R", doc["per_channel"])
        sp.save_profile(doc, "test")
        cam = instrument.camera_in_use(r.setup, temp_c=-10.0)
        self.assertEqual(cam.source("dark_current_e_per_px_s"), "measured")
        warm = instrument.camera_in_use(r.setup, temp_c=-4.0)
        self.assertEqual(warm.source("dark_current_e_per_px_s"), "derived")
        self.assertAlmostEqual(warm.value("dark_current_e_per_px_s"),
                               doc["values"]["dark_current_e_per_px_s"]["value"] * 2.0, delta=1e-4)
        # a typed value is kept when asked
        doc["values"]["dark_current_e_per_px_s"] = sp.record(0.001, "e-/px/s", "user")
        kept = darks.apply_to_profile(r, doc, "test", keep_user=True)
        self.assertEqual(kept["values"]["dark_current_e_per_px_s"]["value"], 0.001)
        changes = darks.changes_for(r, doc)
        self.assertTrue(any(k == "dark_current_e_per_px_s" and src == "user" for k, _o, src, _n in changes))

    def test_measured_gain_from_profile(self):
        folder, t = self.lib(dark_e_s=0.05, egain=False)
        sc = darks.scan_darks(folder)
        r0 = darks.run_darks(folder, REGION, scan=sc)
        self.assertEqual(r0.gain_source, "estimate")
        self.assertIn("gain not known", r0.worst)
        self.assertEqual(r0.main.method, "mean")              # a guessed gain counts twice in the noise way
        hint = [x for _, x in r0.lines if "hint at a gain" in x]
        self.assertTrue(hint)
        self.assertIn("0.2", hint[0])
        doc = darks.apply_to_profile(r0, None, "test")
        doc["values"]["gain_e_per_adu"] = sp.record(0.25, "e-/ADU", "measured")
        sp.save_profile(doc, "test")
        r = darks.run_darks(folder, REGION)
        self.assertEqual(r.gain_source, "measured")
        self.assertLess(abs(r.main.measured / 0.05 - 1), 0.03)


if __name__ == "__main__":
    unittest.main()
