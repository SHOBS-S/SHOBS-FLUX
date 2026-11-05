"""FLUX 0.5.2: SHOBS-P 2.3.0's on-sky logs (format version 2, GitHub SHOBS-P #55).

FLUX 0.5.1 refused every format-2 log ("cannot be read ... move or delete them"). 0.5.2 reads them and uses the
electrons per image unit SHOBS-P writes at each star (gain, binning and the master flat's factor in it), rescaled to
the camera profile's gain when the bench measured one. Format-1 logs are still read the old way, with a caveat: their
scale leaves out the flat, so their zero point is set aside when a night with the real scale exists (and filed as a
trend of its own in the run history). A log from a NEWER SHOBS-P is named as such, never "move or delete"."""
from __future__ import annotations

import json
import math
import os
import unittest

import numpy as np

from tests.base import TempData

import history
import onsky
import shobs_profile as sp
import synth_sky

PROF = synth_sky.camera_profile(read_noise=1.2)
FLAT = 1.3                                   # green sits ~1.3 above an OSC flat's median


def lookup(_cam):
    return PROF


def ds_of(**kw):
    doc, truth = synth_sky.make_log(**kw)
    return onsky.load_dataset(doc, lookup), truth


class TestSharedLoader(TempData):
    def _write(self, doc, name="x.json"):
        p = self.path(name)
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        return p

    def test_version_2_log_loads_and_validates(self):
        doc, _ = synth_sky.make_log()
        self.assertEqual(doc["format_version"], 2)
        self.assertEqual(sp.load(self._write(doc))["format_version"], 2)
        self.assertFalse([e for e in sp.validate(doc) if "format_version" in e])

    def test_newer_log_is_named_newer(self):
        doc, _ = synth_sky.make_log()
        doc["format_version"] = sp.ONSKY_FORMAT_VERSION + 1
        with self.assertRaises(sp.NewerFormatError):
            sp.load(self._write(doc))
        self.assertTrue(issubclass(sp.NewerFormatError, sp.ProfileError))   # old callers still catch it

    def test_profiles_stay_at_version_1(self):
        cam = synth_sky.camera_profile()
        cam["format_version"] = 2
        with self.assertRaises(sp.NewerFormatError):
            sp.load(self._write(cam, "cam.json"))
        self.assertEqual(sp.reads_version(sp.CAMERA_FORMAT), 1)
        self.assertEqual(sp.reads_version(sp.ONSKY_FORMAT), 2)

    def test_version_bumped(self):
        self.assertEqual(sp.SHOBS_PROFILE_VERSION, "1.2.0")


class TestElectronScale(TempData):
    def test_version_2_uses_the_logs_scale_with_the_flat(self):
        ds, t = ds_of(flat_factor=FLAT)
        self.assertEqual(ds.scale, onsky.SCALE_LOG)
        self.assertTrue(ds.scale_exact)
        self.assertIsNotNone(ds.e_star)
        self.assertAlmostEqual(float(np.nanmedian(ds.e_star)), 0.2429 * 8 * FLAT, places=3)
        self.assertEqual(ds.raw_sum_px, 8.0)                  # read noise: raw pixels, never the flat
        r = onsky.analyse(ds, None, 356, 300)
        self.assertTrue(r.photometric)
        self.assertLess(abs(r.zp1 - t["zp1"]), 0.02, r.zp1)
        self.assertLess(abs(r.sky_mag - t["sky_mag"]), 0.05, r.sky_mag)

    def test_version_1_is_too_faint_by_the_flat_and_says_so(self):
        ds, t = ds_of(flat_factor=FLAT, format_version=1)
        self.assertEqual(ds.scale, onsky.SCALE_CAMERA)
        self.assertFalse(ds.scale_exact)
        self.assertIsNone(ds.e_star)
        r = onsky.analyse(ds, None, 356, 300)
        self.assertLess(abs(r.zp1 - (t["zp1"] - 2.5 * math.log10(FLAT))), 0.02, r.zp1)
        self.assertTrue(any("older SHOBS-P log" in text for _k, text in r.lines))
        # sky brightness does not depend on the scale (the night's own zero point cancels it)
        self.assertLess(abs(r.sky_mag - t["sky_mag"]), 0.05, r.sky_mag)

    def test_measured_gain_rescales_egain(self):
        true_g, hdr = 0.2554, 0.2429
        doc, t = synth_sky.make_log(gain=true_g, flat_factor=FLAT)
        doc["setup"]["e_per_adu_header"] = hdr               # SHOBS-P used the header's EGAIN
        scale = hdr / true_g
        doc["measurement"]["e_per_unit_median"] *= scale
        for fr in doc["frames"]:
            for m in fr["stars"].values():
                m["e_per_unit"] *= scale
        prof = synth_sky.camera_profile(gain=true_g)
        ds = onsky.load_dataset(doc, lambda c: prof)
        self.assertAlmostEqual(ds.gain, true_g, places=6)
        r = onsky.analyse(ds, None, 356, 300)
        self.assertLess(abs(r.zp1 - t["zp1"]), 0.02, r.zp1)
        # with no camera profile: EGAIN as SHOBS-P used it (the profile-free answer)
        ds2 = onsky.load_dataset(doc, lambda c: None)
        self.assertEqual(ds2.gain_source, "header")
        self.assertAlmostEqual(ds2.gain, hdr, places=6)

    def test_sky_noise_scale_is_a_lower_limit(self):
        ds, _ = ds_of(e_source="sky")
        self.assertEqual(ds.scale, onsky.SCALE_LOG_LOWER)
        self.assertFalse(ds.scale_exact)
        r = onsky.analyse(ds, None, 356, 300)
        self.assertTrue(any("lower limit" in text for _k, text in r.lines))

    def test_star_without_its_own_scale_uses_the_frame_median(self):
        doc, t = synth_sky.make_log(flat_factor=FLAT)
        for fr in doc["frames"]:
            fr["stars"]["Field 3"].pop("e_per_unit")
        ds = onsky.load_dataset(doc, lookup)
        self.assertTrue(np.isfinite(ds.e_star).all())
        r = onsky.analyse(ds, None, 356, 300)
        self.assertLess(abs(r.zp1 - t["zp1"]), 0.02)

    def test_folder_run_scale_is_exact(self):
        doc, _ = synth_sky.make_log(format_version=1, written_by="SHOBS-FLUX 0.5.2")
        doc["measurement"]["flux_is_adu_sum"] = True
        ds = onsky.load_dataset(doc, lookup)
        self.assertEqual(ds.scale, onsky.SCALE_ADU_SUM)
        self.assertTrue(ds.scale_exact)


class TestCombine(TempData):
    def test_old_log_left_out_of_the_zero_point_only(self):
        new, t = ds_of(night="2026-10-20", flat_factor=FLAT, seed=1)
        old, _ = ds_of(night="2026-10-21", flat_factor=FLAT, format_version=1, seed=2, target="Other")
        s = onsky.run_all([new, old], None, 356, 300)
        b = s.bands["TG"]
        self.assertEqual(b.zp_nights, ["2026-10-20"])
        self.assertLess(abs(b.zp1 - t["zp1"]), 0.02)
        self.assertFalse(b.zp_scale_inexact_only)
        self.assertIn("2026-10-21 Other", b.zp_out)
        self.assertIn("electron scale", b.zp_out["2026-10-21 Other"])
        self.assertEqual(sorted(b.k_nights), ["2026-10-20", "2026-10-21"])   # extinction still from both

    def test_only_old_logs_used_and_flagged(self):
        old, _ = ds_of(night="2026-10-21", flat_factor=FLAT, format_version=1)
        s = onsky.run_all([old], None, 356, 300)
        b = s.bands["TG"]
        self.assertTrue(math.isfinite(b.zp1))
        self.assertTrue(b.zp_scale_inexact_only)
        recs, _ = onsky.measured_records(s)
        self.assertIn("leaves out the flat", recs["TG"]["zero_point"]["note"])

    def test_rerun_on_230_replaces_the_old_log(self):
        old, _ = ds_of(night="2026-10-20", format_version=1, written="2026-10-21T08:00:00-04:00", flat_factor=FLAT)
        new, t = ds_of(night="2026-10-20", written="2026-11-04T10:00:00-05:00", run_id="b", flat_factor=FLAT)
        s = onsky.run_all([old, new], None, 356, 300)
        self.assertEqual(len(s.replaced), 1)
        self.assertEqual(s.replaced[0].log_version, 1)
        self.assertLess(abs(s.bands["TG"].zp1 - t["zp1"]), 0.02)


class TestHistoryValues(TempData):
    def test_old_scale_zero_point_is_its_own_trend(self):
        ds, _ = ds_of(format_version=1, flat_factor=FLAT)
        r = onsky.analyse(ds, None, 356, 300)
        keys = {v["key"] for v in onsky.night_values(r, "sys")}
        self.assertNotIn("bands.zero_point", keys)
        self.assertNotIn("bands.zero_point_at_x", keys)
        self.assertIn("bands.zero_point_low_scale", keys)
        self.assertIn("bands.extinction_k", keys)
        self.assertIn("bands.zero_point_low_scale", history.VALUE_WORDS)
        self.assertEqual(onsky.night_summary(r)["electron_scale"], onsky.SCALE_CAMERA)

    def test_new_scale_zero_point_is_the_trend(self):
        ds, _ = ds_of(flat_factor=FLAT)
        r = onsky.analyse(ds, None, 356, 300)
        keys = {v["key"] for v in onsky.night_values(r, "sys")}
        self.assertIn("bands.zero_point", keys)
        self.assertNotIn("bands.zero_point_low_scale", keys)

    def test_rules_version_raised(self):
        self.assertEqual(history.RULES_VERSION, "0.5.2")


class TestNewerLogWords(TempData):
    def test_newer_log_is_not_called_damaged(self):
        doc, _ = synth_sky.make_log()
        doc["format_version"] = sp.ONSKY_FORMAT_VERSION + 1
        p = synth_sky.write_log(doc)
        good, bad = onsky.read_logs([p])
        self.assertEqual(good, [])
        self.assertTrue(bad[0][1].startswith(onsky.NEWER_WORDS), bad)
        self.assertIn("update SHOBS-FLUX", bad[0][1])
        try:
            import sky_page
        except ImportError:                              # no Tk here
            self.skipTest("tkinter not available")
        info = sky_page.log_info(p)
        self.assertTrue(info.get("newer"))
        res = sky_page.SkyRun()
        ok, _ = ds_of()
        res.summary = onsky.run_all([ok], None, 356, 300)
        res.bad = bad
        text = " ".join(t for _k, t in sky_page.run_lines(res))
        self.assertIn("update SHOBS-FLUX", text)
        self.assertNotIn("move or delete", text)

    def test_damaged_log_still_called_damaged(self):
        p = self.path("broken.json")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("{not json")
        good, bad = onsky.read_logs([p])
        self.assertFalse(bad[0][1].startswith(onsky.NEWER_WORDS))


if __name__ == "__main__":
    unittest.main()


# ---- the Fable review's findings (0.5.2) ------------------------------------------------------------------------------

class TestReviewFixes(TempData):
    def test_averaged_camera_binning_rescales_like_with_like(self):
        # SHOBS-P's scale holds EGAIN × bin² when the camera averaged its 2×2 pixels; the profile for that binned
        # setup measured the binned pixel (EGAIN × 4). No rescale must happen when they agree.
        hdr, cam_bin = 0.25, 2
        doc, t = synth_sky.make_log(gain=hdr * cam_bin ** 2, flat_factor=FLAT)
        doc["setup"]["camera_binning"] = cam_bin
        doc["setup"]["e_per_adu_header"] = hdr
        doc["measurement"]["camera_bin_mode"] = "averaged"
        prof = synth_sky.camera_profile(gain=hdr * cam_bin ** 2)
        ds = onsky.load_dataset(doc, lambda c: prof)
        r = onsky.analyse(ds, None, 356, 300)
        self.assertLess(abs(r.zp1 - t["zp1"]), 0.02, r.zp1)
        # binning mode not known: no rescale, the log's own scale stands
        doc["measurement"]["camera_bin_mode"] = "unknown"
        prof2 = synth_sky.camera_profile(gain=9.0)               # a wild profile gain must not be applied
        r2 = onsky.analyse(onsky.load_dataset(doc, lambda c: prof2), None, 356, 300)
        self.assertLess(abs(r2.zp1 - t["zp1"]), 0.02, r2.zp1)

    def test_median_missing_uses_the_stars_own(self):
        doc, t = synth_sky.make_log(flat_factor=FLAT)
        doc["measurement"]["e_per_unit_median"] = None
        ds = onsky.load_dataset(doc, lookup)
        self.assertEqual(ds.scale, onsky.SCALE_LOG)
        r = onsky.analyse(ds, None, 356, 300)
        self.assertLess(abs(r.zp1 - t["zp1"]), 0.02)

    def test_no_scale_anywhere_falls_back_with_caveat(self):
        doc, _ = synth_sky.make_log(flat_factor=FLAT)
        doc["measurement"]["e_per_unit_median"] = None
        for fr in doc["frames"]:
            for m in fr["stars"].values():
                m["e_per_unit"] = None
        ds = onsky.load_dataset(doc, lookup)
        self.assertEqual(ds.scale, onsky.SCALE_CAMERA)
        self.assertIn("wrote no electrons", ds.scale_note)

    def test_exact_but_marginal_beats_clean_but_inexact(self):
        drift, t = ds_of(night="2026-10-20", flat_factor=FLAT, seed=1)
        old, _ = ds_of(night="2026-10-21", flat_factor=FLAT, format_version=1, seed=2, target="Other")
        r1 = onsky.analyse(drift, None, 356, 300)
        r2 = onsky.analyse(old, None, 356, 300)
        self.assertTrue(r1.photometric and r2.photometric)
        r1.marginal, r1.trend = True, 0.04                   # the 2.3.0 night drifted a little
        s = onsky.combine([r1, r2])
        b = s.bands["TG"]
        self.assertEqual(b.zp_nights, ["2026-10-20"])
        self.assertLess(abs(b.zp1 - t["zp1"]), 0.02)
        self.assertFalse(b.zp_scale_inexact_only)
        self.assertTrue(b.zp_marginal_only)

    def test_sky_source_with_old_logs_only(self):
        a, _ = ds_of(night="2026-10-20", e_source="sky", seed=1)
        b_, _ = ds_of(night="2026-10-21", format_version=1, seed=2, target="Other")
        s = onsky.run_all([a, b_], None, 356, 300)
        self.assertTrue(s.bands["TG"].zp_scale_inexact_only)

    def test_clear_luminance_is_not_exact_for_the_zero_point(self):
        ds, _ = ds_of(band="CV")
        self.assertEqual(ds.scale, onsky.SCALE_LOG_LUM)
        self.assertFalse(ds.scale_exact)
        self.assertIsNotNone(ds.e_star)                      # its noise still uses the log's scale

    def test_profile_gain_without_header_egain_is_labelled_log(self):
        doc, _ = synth_sky.make_log()
        doc["setup"]["e_per_adu_header"] = None
        ds = onsky.load_dataset(doc, lookup)
        self.assertEqual(ds.gain_source, "log")


class TestRejudgeMovesOldZeroPoint(TempData):
    def test_format_1_night_moves_to_its_own_trend(self):
        import sqlite3
        from tests import m5_data
        d = m5_data.make(self.tmp)
        sid = d["system_id"]
        before = history.series_points("system", sid, "bands.zero_point", "TG")
        runs = history.recent_runs(10, kind="onsky")
        old_path = sorted(r["source"] for r in runs)[0]
        with open(old_path, encoding="utf-8") as fh:
            doc = json.load(fh)
        doc["format_version"] = 1                            # as SHOBS-P 2.2.x wrote it: no scale in it
        doc["measurement"] = {k: v for k, v in doc["measurement"].items() if not k.startswith("e_per_unit")}
        for fr in doc["frames"]:
            for m in fr["stars"].values():
                m.pop("e_per_unit", None)
        with open(old_path, "w", encoding="utf-8") as fh:
            json.dump(doc, fh)
        con = sqlite3.connect(sp.runs_db_path())
        con.execute("UPDATE runs SET app = 'SHOBS-FLUX 0.5.1' WHERE kind = 'onsky'")
        for rid, s in con.execute("SELECT id, summary FROM runs WHERE kind = 'onsky'").fetchall():
            dd = json.loads(s or "{}")
            dd.pop("rules", None)
            con.execute("UPDATE runs SET summary = ? WHERE id = ?", (json.dumps(dd), rid))
        con.commit()
        con.close()
        got = history.rejudge()
        self.assertGreaterEqual(got["onsky"], 1)
        after = history.series_points("system", sid, "bands.zero_point", "TG")
        low = history.series_points("system", sid, "bands.zero_point_low_scale", "TG")
        self.assertEqual(len(after) + len(low), len(before))
        self.assertEqual(len(low), 1 if any(p["date"] == doc["night"] for p in before) else 0)
        self.assertNotIn(doc["night"], {p["date"] for p in after})
