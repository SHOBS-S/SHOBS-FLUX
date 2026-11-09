"""FLUX 1.0.0 fix list (owner John, 9 Nov 2026).

 1 GitHub #1: folder runs carry the raw pixels summed per measured pixel (a TG pixel = 2 greens), so the read noise in
   the noise terms is right; older folder logs get the count from their band.
 2 Bench: colour-ratio linearity (R/G, G/B in the same frame) chained into one curve for the sensor; light-free, so a
   drifting or stepping panel no longer sets the linear limit. Drift corrected from repeated (reference) exposures.
 3 Bench: a pair off the line by the same % in every colour is the light at that exposure ("repeat it"), not the end
   of the linear range.
 4 Bench: unsteady light flagged (pair mismatch, drift between repeated exposures); no timing offset saved from it.
 5 Profile: a re-run that cannot find a colour's linear limit clears that colour's old measured limit.
 6 GitHub #2: a check for a new version at start (status strip note; never blocks, never downloads).
 7 History: the Status column is not cut off."""
from __future__ import annotations

import copy
import unittest

import numpy as np

from tests.base import TempData

import onsky
import synth_sky


# ---- 1 ---------------------------------------------------------------------------------------------------------------

class TestFolderRunReadNoise(TempData):
    """GitHub #1: rn2 per measured pixel = rn² × the raw pixels summed into it."""

    def _ds(self, band="TG", adu_sum=True, summed=None, soft_bin=1):
        doc, _truth = synth_sky.make_log(seed=3, n_frames=30, band=band, mags=np.linspace(9, 14, 6),
                                         soft_bin=soft_bin)
        if adu_sum:
            doc["measurement"]["flux_is_adu_sum"] = True
        if summed is not None:
            doc["measurement"]["raw_px_summed"] = summed
        return onsky.load_dataset(doc, cam_lookup=lambda c: synth_sky.camera_profile())

    def test_old_folder_log_tg_counts_two_greens(self):
        self.assertEqual(self._ds("TG").raw_sum_px, 2.0)

    def test_old_folder_log_single_colour_counts_one(self):
        self.assertEqual(self._ds("TR").raw_sum_px, 1.0)

    def test_new_folder_log_uses_its_own_count(self):
        self.assertEqual(self._ds("TG", summed=2).raw_sum_px, 2.0)
        self.assertEqual(self._ds("TG", summed=1).raw_sum_px, 1.0)

    def test_shobs_p_log_unchanged(self):
        """SHOBS-P averages the colour's pixels; in electrons that is the same sum as before."""
        self.assertEqual(self._ds("TG", adu_sum=False).raw_sum_px, 2.0)
        self.assertEqual(self._ds("CV", adu_sum=False).raw_sum_px, 4.0)
        self.assertEqual(self._ds("TG", adu_sum=False, soft_bin=2).raw_sum_px, 8.0)

    def test_read_noise_term_grows_with_the_count(self):
        """A faint star on a dark sky: two summed pixels carry twice the read-noise variance of one."""
        one, two = self._ds("TG", summed=1), self._ds("TG", summed=2)
        for ds in (one, two):
            ds.read_noise_e = 5.0
            ds.sky = ds.sky * 0.0 + 0.01                          # nearly no sky: read noise shows
            ds.star_sky = None
        e1 = onsky._expected_mag_noise(one)
        e2 = onsky._expected_mag_noise(two)
        ok = np.isfinite(e1) & np.isfinite(e2)
        self.assertTrue(ok.any())
        self.assertTrue(np.all(e2[ok] >= e1[ok]))
        self.assertTrue(np.any(e2[ok] > e1[ok] * 1.001))


# ---- 2–5: the bench and the light -------------------------------------------------------------------------------------

import math
import os

import bench
import synth_bench

REF = 2.0
LADDER = ([REF] + [0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8] + [REF] +
          [2.2, 2.4, 2.6, 2.8, 3.0, 3.2, 3.4, 3.6, 3.8, 4.2, 4.6] + [REF])
RATES = {"R": 2800.0, "G": 4000.0, "B": 2000.0}               # a warm light: green brightest, blue half of it
UNSTEADY = dict(drift_per_frame=0.002, color_drift={"R": 0.0005, "B": -0.0004}, jumps={8: 0.985, 14: 1.012},
                frame_jitter=0.002)


class BenchCase(TempData):
    _cache: dict = {}

    def run_ladder(self, key, **kw):
        if key in BenchCase._cache:
            return BenchCase._cache[key]
        kw.setdefault("exposures", LADDER)
        kw.setdefault("rates", RATES)
        kw.setdefault("nonlin", 0.03)
        folder = self.path(key)
        synth_bench.make_ladder(folder, shape=(200, 240), **kw)
        res = bench.run_bench(folder)
        BenchCase._cache[key] = res
        return res


class TestColourRatio(BenchCase):
    def test_steady_light_both_methods_agree(self):
        r = self.run_ladder("steady")
        self.assertTrue(r.light_steady)
        self.assertTrue(math.isfinite(r.ratio_limit_adu))
        self.assertAlmostEqual(r.ratio_limit_adu, r.channels["G"].ladder_limit_adu, delta=3500)   # one rung
        # the ratio curve follows the time ladder's bend where both see it
        g = {round(p.mean_raw): p.deviation_pct for p in r.channels["G"].pairs if not p.saturated}
        diff = [q["dev_pct"] - g[round(q["level"])] for q in r.ratio_curve
                if q["hi"] == "G" and q["level"] > 35000 and round(q["level"]) in g]
        self.assertGreaterEqual(len(diff), 5)
        self.assertLess(abs(float(np.median(diff))), 0.15)          # small frames: each point is noisy, not biased
        self.assertLess(max(abs(x) for x in diff), 0.5)
        self.assertTrue(r.offset_trusted)

    def test_unsteady_light_ratio_decides(self):
        steady = self.run_ladder("steady")
        r = self.run_ladder("unsteady", **UNSTEADY)
        self.assertIs(r.light_steady, False)
        self.assertEqual(r.lin_method, "colour ratio")
        truth = steady.channels["G"].ladder_limit_adu
        self.assertAlmostEqual(r.linear_limit_adu, truth, delta=3500)                 # within a rung of the truth
        self.assertLess(r.channels["G"].ladder_limit_adu, truth - 3000)               # the light fooled the ladder
        for c in "RGB":
            self.assertEqual(r.channels[c].linear_limit_adu, r.linear_limit_adu)      # one curve for the sensor
        self.assertFalse(r.offset_trusted)
        text = " ".join(t for _k, t in r.lines)
        self.assertIn("The light was not steady", text)
        self.assertIn("from the colour ratio" if r.linear_is_bound else "colour ratio:", text)
        self.assertFalse(any(k == "warn" and "light" in t.lower() for k, t in r.lines))   # plain note: result fine
        vals, _per, lin = bench.measured_records(r)
        self.assertNotIn("exposure_offset_s", vals)
        self.assertEqual(lin["method"], "colour ratio")
        self.assertIn("time_ladder_curve_adu", lin)
        self.assertIn("colour ratio", vals["linear_limit_adu"]["note"])

    def test_linear_sensor_in_unsteady_light_is_linear_to_the_top(self):
        r = self.run_ladder("unsteady_linear", nonlin=0.0, drift_per_frame=0.002, color_drift={"R": 0.0005},
                            jumps={8: 0.985}, frame_jitter=0.002)
        self.assertEqual(r.lin_method, "colour ratio")
        self.assertTrue(r.linear_is_bound)
        self.assertGreater(r.linear_limit_adu, 55000)

    def test_neutral_light_falls_back_to_the_time_ladder(self):
        r = self.run_ladder("neutral", rates={"R": 4000.0, "G": 4000.0, "B": 3900.0})
        self.assertTrue(math.isnan(r.ratio_limit_adu))
        self.assertIn("too alike", r.ratio_why_not)
        self.assertEqual(r.lin_method, "time ladder")
        self.assertTrue(any("Colour-ratio check: not possible" in t for _k, t in r.lines))

    def test_mono_unsteady_warns(self):
        r = self.run_ladder("mono", bayer=None, rates={"L": 4000.0}, drift_per_frame=0.002, jumps={8: 0.985})
        self.assertEqual(r.lin_method, "time ladder")
        self.assertIs(r.light_steady, False)
        self.assertTrue(any(k == "warn" and "light was not steady" in t for k, t in r.lines))
        self.assertIn("light not steady", r.worst)
        self.assertFalse(any("Colour-ratio check" in t for _k, t in r.lines))      # nothing to say on mono

    def test_no_reference_exposure_says_so(self):
        r = self.run_ladder("norefs", exposures=[t for t in LADDER if t != REF])
        self.assertIsNone(r.light_steady)
        self.assertTrue(any("No exposure was repeated" in t for _k, t in r.lines))
        self.assertTrue(r.offset_trusted)

    def test_plot_draws_the_ratio_curve(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import bench_plots
        r = self.run_ladder("unsteady", **UNSTEADY)
        fig = plt.figure(figsize=(10, 6))
        bench_plots.draw(fig, r)
        labels = [t.get_text() for ax in fig.axes for t in (ax.get_legend().get_texts() if ax.get_legend() else [])]
        self.assertTrue(any("colour ratio" in x for x in labels))
        plt.close(fig)


class TestCommonMode(BenchCase):
    def test_one_rung_dip_in_every_colour_is_stepped_over(self):
        """8 Nov 2026 (Ladder 27Oct26): one exposure off by the same % in every colour = the light, not the end of
        the linear range."""
        ex = [0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 2.2, 2.6, 3.0]
        r = self.run_ladder("dip", exposures=ex, nonlin=0.0, jumps={6: 0.99})
        self.assertIn(1.2, r.common_mode)
        g = r.channels["G"]
        self.assertGreater(g.ladder_limit_adu, 30000)                  # not stopped at the 1.2 s dip
        self.assertIn("stepped over 1.2 s", g.ladder_how)
        self.assertIn("repeat it", g.ladder_how)

    def test_a_real_bend_is_not_common_mode(self):
        r = self.run_ladder("steady")
        self.assertEqual(r.common_mode, [])


# ---- 5: a re-run's missing colour limit ---------------------------------------------------------------------------

class TestPerChannelNone(BenchCase):
    def test_missing_limit_clears_the_old_measured_one_but_keeps_a_typed_one(self):
        r = self.run_ladder("steady")
        doc = bench.apply_to_profile(r, None, "test")
        self.assertIsNotNone(doc["per_channel"]["R"]["linear_limit_adu"])
        r2 = copy.deepcopy(r)
        r2.channels["R"].linear_limit_adu = float("nan")
        doc2 = bench.apply_to_profile(r2, doc, "test")
        self.assertIsNone(doc2["per_channel"]["R"]["linear_limit_adu"])
        doc["per_channel"]["R"]["linear_limit_adu"] = {"value": 30000, "unit": "ADU", "source": "user"}
        doc3 = bench.apply_to_profile(r2, doc, "test")
        self.assertEqual(doc3["per_channel"]["R"]["linear_limit_adu"]["value"], 30000)


class TestShootingPlan(unittest.TestCase):
    def test_reference_at_start_middle_end(self):
        plan = bench.plan_ladder({"R": 2600.0, "G": 3400.0, "B": 3400.0}, 500.0)
        text = bench.plan_text(plan, 100, 50, -10)
        self.assertEqual(text.count("Reference"), 3)
        self.assertIn("30 minutes", text)
        self.assertIn("off the panel", text)
        self.assertIn("colours' ratio", text)
        self.assertNotIn("colours' ratio", bench.plan_text(plan, 100, 50, -10, color=False))
        self.assertIn("panel OFF", text)


# ---- 6: GitHub #2, the new-version check -----------------------------------------------------------------------------

import io
import json as _json

import shobs_update


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _opener(doc=None, exc=None):
    def op(req, timeout=None):
        op.seen = (req.full_url, dict(req.header_items()), timeout)
        if exc:
            raise exc
        return _Resp(_json.dumps(doc).encode())
    return op


class TestUpdateCheck(unittest.TestCase):
    REL = {"tag_name": "v1.0.1", "html_url": "https://github.com/SHOBS-S/SHOBS-FLUX/releases/tag/v1.0.1",
           "name": "SHOBS-FLUX v1.0.1", "draft": False, "prerelease": False}

    def test_versions(self):
        self.assertEqual(shobs_update.parse_version("v2.3.2"), (2, 3, 2, 0))
        self.assertTrue(shobs_update.is_newer("v1.0.1", "1.0.0"))
        self.assertTrue(shobs_update.is_newer("v1.10.0", "1.9.9"))
        self.assertFalse(shobs_update.is_newer("v1.0.0", "1.0.0"))
        self.assertFalse(shobs_update.is_newer("v0.6.1", "1.0.0"))
        self.assertFalse(shobs_update.is_newer("nightly", "1.0.0"))

    def test_fetch_asks_the_public_api_only(self):
        op = _opener(self.REL)
        got = shobs_update.fetch_latest("SHOBS-S/SHOBS-FLUX", "SHOBS-FLUX/1.0.0", opener=op)
        self.assertEqual(got["tag"], "v1.0.1")
        url, headers, timeout = op.seen
        self.assertEqual(url, "https://api.github.com/repos/SHOBS-S/SHOBS-FLUX/releases/latest")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(timeout, shobs_update.TIMEOUT_S)

    def test_silent_on_errors_drafts_and_prereleases(self):
        import urllib.error
        for op in (_opener(exc=urllib.error.URLError("offline")), _opener(exc=TimeoutError()),
                   _opener(exc=urllib.error.HTTPError("u", 403, "rate limit", {}, None)),
                   _opener(dict(self.REL, draft=True)), _opener(dict(self.REL, prerelease=True)),
                   _opener({"message": "Not Found"}), _opener(dict(self.REL, html_url="https://evil.example/x"))):
            self.assertIsNone(shobs_update.fetch_latest("SHOBS-S/SHOBS-FLUX", opener=op))

    def test_note_and_once_a_day(self):
        calls = []

        def fetch():
            calls.append(1)
            return {"tag": "v1.0.1", "url": self.REL["html_url"], "name": "x"}
        st = {"check_updates": True}
        note = shobs_update.check("SHOBS-FLUX", "S", "1.0.0", st, now=1000.0, fetch=fetch)
        self.assertEqual(note["text"], "SHOBS-FLUX 1.0.1 is available — see the release notes")
        self.assertNotIn("⚠", note["text"])                         # a plain note (house rule)
        self.assertEqual(st["update_tag"], "v1.0.1")
        again = shobs_update.check("SHOBS-FLUX", "S", "1.0.0", st, now=1000.0 + 3600, fetch=fetch)
        self.assertEqual(len(calls), 1)                              # within a day: the kept answer
        self.assertEqual(again["text"], note["text"])                # shown again at each start
        shobs_update.check("SHOBS-FLUX", "S", "1.0.0", st, now=1000.0 + 90000, fetch=fetch)
        self.assertEqual(len(calls), 2)
        self.assertIsNone(shobs_update.check("SHOBS-FLUX", "S", "1.0.1", st, now=1000.0 + 90001, fetch=fetch))

    def test_failed_question_keeps_old_answer_and_asks_next_time(self):
        st = {"update_checked_at": 0.0, "update_tag": "v1.0.1", "update_url": self.REL["html_url"]}
        note = shobs_update.check("SHOBS-FLUX", "S", "1.0.0", st, now=200000.0, fetch=lambda: None)
        self.assertIsNotNone(note)
        self.assertEqual(st["update_checked_at"], 0.0)

    def test_switched_off(self):
        st = {"check_updates": False}
        self.assertIsNone(shobs_update.check("SHOBS-FLUX", "S", "1.0.0", st, fetch=lambda: 1 / 0))


# ---- 6–7 in the window -------------------------------------------------------------------------------------------------

try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestWindow(TempData):
    def setUp(self):
        super().setUp()
        from tests import m5_data
        self.d = m5_data.make(self.tmp)
        import flux_app
        self.app = flux_app.App(testing=True)
        self.app.update()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        self.app = None
        import gc
        gc.collect()
        super().tearDown()

    def test_update_note_stays_beside_other_messages(self):
        app = self.app
        self.assertEqual(app.update_var.get(), "")                    # the tests never ask GitHub
        app.show_update({"text": "SHOBS-FLUX 1.0.1 is available — see the release notes", "tag": "v1.0.1",
                         "url": "https://github.com/SHOBS-S/SHOBS-FLUX/releases/tag/v1.0.1"})
        app.set_status("✔ Something else happened.")
        self.assertIn("1.0.1 is available", app.update_var.get())
        self.assertEqual(str(app.update_label.cget("style")), "StatusLink.TLabel")

    def test_checkbox_on_credits(self):
        import settings
        app = self.app
        app.show_credits()
        cp = app.credits_page
        self.assertTrue(cp.check_var.get())
        cp.check_var.set(False)
        cp.set_check()
        self.assertFalse(settings.load()["check_updates"])
        cp.check_var.set(True)
        cp.set_check()
        self.assertTrue(settings.load()["check_updates"])

    def test_history_status_in_full(self):
        app = self.app
        app.geometry("1000x700")
        app.show_page(4)
        app.update()
        hp = app.history_page
        self.assertTrue(hp.run_rows)
        long = "⚠ Not photometric: the zero point wandered 0.12 mag through the night (thin cloud?) " * 2
        iid = next(iter(hp.run_rows))
        hp.tree.set(iid, "status", long)
        hp._fit_status()
        app.update()
        self.assertGreater(int(hp.tree.column("status", "width")), 330)
        hp.tree.selection_set(iid)
        app.update()
        self.assertIn(long.strip()[:60], hp.detail_var.get())


if __name__ == "__main__":
    unittest.main()
