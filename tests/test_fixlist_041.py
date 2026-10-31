"""The 0.4.1 fix list: a lone defocused night is judged against the setup's own seeing (not a flat 7″); a night whose
zero point drifts steadily is MARGINAL and set aside while a clean night exists; one-night date axes say the date;
a remembered folder that moved is not offered; the Telescope boxes are kept per setup (a folder from another
telescope never runs with this one's aperture); a mono folder shows its FILTER band instead of a Color choice."""
from __future__ import annotations

import os
import sys
import types
import unittest
from unittest import mock

from tests.base import TempData

import onsky
import settings
import shobs_profile as sp
import synth_sky

PROF = synth_sky.camera_profile(read_noise=1.2)


def lookup(_cam):
    return PROF


def run(**kw):
    kw.setdefault("airmass", (1.15, 1.2))
    doc, _t = synth_sky.make_log(**kw)
    return onsky.analyse(onsky.load_dataset(doc, lookup), None, 356, 300)


class TestMarginal(TempData):
    def test_steady_drift_is_marginal(self):
        r = run(haze_mag=0.04, seed=3)
        self.assertTrue(r.photometric, r.lines)          # the bins alone pass…
        self.assertTrue(r.marginal)                     # …but the night drifts
        self.assertLess(r.trend, -0.03)
        self.assertTrue(any(t.startswith("⚠ Marginal") and "fell" in t for _k, t in r.lines))
        self.assertEqual(onsky.night_summary(r)["marginal"], True)

    def test_clear_night_is_not_marginal(self):
        r = run(seed=3)
        self.assertTrue(r.photometric)
        self.assertFalse(r.marginal)
        self.assertLess(abs(r.trend), onsky.TREND_MAG)

    def test_combine_prefers_clean_nights(self):
        clean = run(night="2026-10-25", seed=4)
        drift = run(night="2026-10-29", seed=5, haze_mag=0.04)
        s = onsky.combine([clean, drift])
        b = s.bands["TG"]
        self.assertEqual(b.zp_nights, ["2026-10-25"])
        self.assertIn("marginal", b.zp_out["2026-10-29 Test Star"])
        self.assertFalse(b.zp_marginal_only)
        only = onsky.combine([run(night="2026-10-29", seed=5, haze_mag=0.04)]).bands["TG"]
        self.assertEqual(only.zp_nights, ["2026-10-29"])
        self.assertTrue(only.zp_marginal_only)
        recs, _cad = onsky.measured_records(onsky.combine([run(night="2026-10-29", seed=5, haze_mag=0.04)]))
        self.assertIn("marginal", recs["TG"]["zero_point"]["note"])


def seeing_run(night, seeing, focus, target="Test Star", seed=1):
    r = run(night=night, seeing=seeing, focuspos=focus, target=target, seed=seed, n_frames=60)
    return r


class TestDefocus(TempData):
    def setUp(self):
        super().setUp()
        # one night with both: in focus (~3.5″) and +125 steps (~7.6″)
        self.inf = seeing_run("2026-10-25", 3.5, 6400, "Star A", 1)
        self.dfc = seeing_run("2026-10-25", 7.6, 6525, "Star B", 2)

    def test_lone_night_near_the_defocus_size_is_defocused(self):
        lone = seeing_run("2026-10-29", 6.8, 6300, seed=3)
        self.assertLess(lone.fwhm_arcsec, onsky.DEFOCUS_ARCSEC)   # the flat rule would call it in focus
        onsky.combine([self.inf, self.dfc, lone])
        self.assertTrue(self.dfc.defocused)
        self.assertEqual(self.dfc.defocus_key, "+125 steps")
        self.assertTrue(lone.defocused)
        self.assertEqual(lone.defocus_key, "+125 steps")
        self.assertTrue(any("Defocus taken as +125 steps" in t for _k, t in lone.lines))

    def test_lone_night_near_focus_stays_in_focus(self):
        lone = seeing_run("2026-10-29", 4.0, 6300, seed=3)
        s = onsky.combine([self.inf, self.dfc, lone])
        self.assertFalse(lone.defocused)
        self.assertEqual(s.bands["TG"].fwhm_in_focus_n, 2)

    def test_poor_site_over_seven_arcsec_is_in_focus(self):
        a = seeing_run("2026-10-25", 7.4, 6400, "Star A", 1)
        b = seeing_run("2026-10-25", 17.0, 6525, "Star B", 2)
        lone = seeing_run("2026-10-29", 7.8, 6300, seed=3)
        self.assertTrue(lone.defocused)                          # the flat rule first
        onsky.combine([a, b, lone])
        self.assertFalse(a.defocused)                            # the night's sharpest run, over 7″ but in focus
        self.assertEqual(b.defocus_key, "+125 steps")
        self.assertFalse(lone.defocused)
        self.assertTrue(any("does not apply" in t for _k, t in lone.lines))

    def test_profile_alone_decides(self):
        prof = {"bands": {"TG": {"fwhm_in_focus": sp.record(3.5, "arcsec", "measured"),
                                 "fwhm_defocus": {"+125 steps": sp.record(7.6, "arcsec", "measured")}}}}
        lone = seeing_run("2026-10-29", 6.8, 6300, seed=3)
        onsky.combine([lone], None, prof)
        self.assertEqual((lone.defocused, lone.defocus_key), (True, "+125 steps"))
        lone2 = seeing_run("2026-10-29", 6.8, 6300, seed=3)
        onsky.combine([lone2])                                   # no reference at all: the flat 7″ rule stands
        self.assertFalse(lone2.defocused)


    def test_unproven_nights_do_not_vouch_for_each_other(self):
        prof = {"bands": {"TG": {"fwhm_in_focus": sp.record(3.5, "arcsec", "measured"),
                                 "fwhm_defocus": {"+125 steps": sp.record(7.6, "arcsec", "measured")}}}}
        a = seeing_run("2026-10-29", 6.8, 6300, seed=3)
        b = seeing_run("2026-10-30", 6.9, 6310, seed=4)
        onsky.combine([a, b], None, prof)
        self.assertEqual((a.defocus_key, b.defocus_key), ("+125 steps", "+125 steps"))

    def test_two_in_focus_runs_on_a_poor_night(self):
        prof = {"bands": {"TG": {"fwhm_in_focus": sp.record(7.4, "arcsec", "measured")}}}
        a = seeing_run("2026-10-29", 7.5, 6300, "Star A", 3)
        b = seeing_run("2026-10-29", 7.6, 6300, "Star B", 4)
        self.assertTrue(a.defocused and b.defocused)            # the flat rule
        onsky.combine([a, b], None, prof)
        self.assertFalse(a.defocused or b.defocused)

    def test_bad_profile_value_does_not_crash(self):
        prof = {"bands": {"TG": {"fwhm_in_focus": sp.record(-1.0, "arcsec", "user"),
                                 "fwhm_defocus": {"+125 steps": sp.record(0.0, "arcsec", "user")}}}}
        lone = seeing_run("2026-10-29", 6.8, 6300, seed=3)
        onsky.combine([lone], None, prof)
        self.assertFalse(lone.defocused)


class TestTrendWithFittedK(TempData):
    def test_drift_along_the_airmass_track_is_caught_by_the_profile_k(self):
        doc, _t = synth_sky.make_log(airmass=(1.05, 1.6), haze_mag=0.04, seed=7)
        free = onsky.analyse(onsky.load_dataset(doc, lookup), None, 356, 300)
        self.assertTrue(free.photometric and not free.marginal)     # the free k soaks the haze up
        withk = onsky.analyse(onsky.load_dataset(doc, lookup), (0.20, "profile"), 356, 300)
        self.assertTrue(withk.photometric, withk.lines)
        self.assertTrue(withk.marginal, (withk.trend, withk.k))
        self.assertTrue(any("hazing over" in t for _k, t in withk.lines))


class TestDateAxis(unittest.TestCase):
    def test_one_night_says_its_date(self):
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from datetime import datetime
        import sky_plots
        fig, ax = plt.subplots()
        d = datetime(2026, 10, 30)
        ax.scatter([d], [20.1])
        ax.axhline(20.1)
        sky_plots._date_axis(ax, [d])
        fig.canvas.draw()
        labels = [t.get_text() for t in ax.get_xticklabels() if t.get_text()]
        self.assertIn("Oct 30", labels)
        self.assertEqual(len(labels), len(set(labels)))
        lo, hi = ax.get_xlim()
        self.assertAlmostEqual(hi - lo, 3.0)
        plt.close(fig)


class _AppCase(TempData):
    def start(self, prefs=None):
        if prefs:
            settings.save(prefs)
        self.p_settings = mock.patch("sky_page.shobs_p_settings_path", return_value=self.path("none.json"))
        self.p_settings.start()
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
        self.p_settings.stop()
        super().tearDown()


class TestMovedFolder(_AppCase):
    def test_moved_dark_folder_is_not_offered(self):
        self.start({"dark_folder": self.path("Exoplanet/Darks")})
        page = self.app.camera_page
        self.assertEqual(page.darks_var.get(), "")
        self.assertIn("Not there any more", page.msg_var.get())
        self.assertIn("Darks", page.msg_var.get())
        self.assertEqual(self.app.prefs["dark_folder"], self.path("Exoplanet/Darks"))   # the setting itself kept


class TestTelescopePerSetup(_AppCase):
    def test_folder_from_another_telescope(self):
        import sky_page
        synth_sky.write_log(synth_sky.make_log(night="2026-10-25", n_frames=40)[0])
        self.start({"sky_telescope_name": "14in RC", "sky_aperture_mm": 356, "sky_elevation_m": 280,
                    "sky_lat": 35.4, "sky_lon": -81.9, "sky_source": "logs"})
        page = self.app.sky_page
        page.scan_logs()
        key = page._setup_key()
        self.assertIsNotNone(key)
        self.assertEqual(page.aperture_var.get(), "356")
        self.assertEqual(page._tel_owner[0], key[0])
        folder = self.path("moeller")
        os.makedirs(folder)
        fake = types.SimpleNamespace(scan=lambda f: {"n_lights": 128, "osc": False, "band": "SI",
                                                     "camera_id": "andor-ikon", "focal": 4250.0})
        with mock.patch.dict(sys.modules, {"sky_folder": fake}):
            page.source_var.set(sky_page.SOURCES[1][0])
            page.source_changed()
            page.set_folder(folder)
            self.assertTrue(self.app.jobs.wait(30))
            self.app.update()
        self.assertEqual((page.name_var.get(), page.aperture_var.get(), page.elevation_var.get()), ("", "", ""))
        self.assertIn("4,250 mm", page.msg_var.get())
        self.assertTrue(page.msg_var.get().startswith("⚠"))
        self.assertEqual(page.color_var.get(), "From FILTER: i′")
        self.assertEqual(str(page.color.cget("state")), "disabled")
        page.aperture_var.set("1000")
        page.elevation_var.set("276")
        page.source_var.set(sky_page.SOURCES[0][0])
        page.source_changed()                                     # back to the logs: John's own boxes return
        self.assertEqual((page.aperture_var.get(), page.elevation_var.get()), ("356", "280"))
        page.source_var.set(sky_page.SOURCES[1][0])
        page.source_changed()                                     # and the Moeller boxes were kept
        self.assertEqual((page.aperture_var.get(), page.elevation_var.get()), ("1000", "276"))
        tels = self.app.prefs["sky_telescopes"]
        self.assertEqual(len(tels), 2)

    def test_folder_only_user_keeps_the_boxes(self):
        import sky_page
        self.start({"sky_telescope_name": "Backyard 8in", "sky_aperture_mm": 203, "sky_elevation_m": 150,
                    "sky_lat": 40.1, "sky_lon": -75.2, "sky_source": "folder"})
        page = self.app.sky_page
        page._folder_setup({"osc": True, "camera_id": "cam-x", "focal": 1000.0})
        self.assertEqual((page.name_var.get(), page.aperture_var.get(), page.lat_var.get()),
                         ("Backyard 8in", "203", "40.1"))
        self.assertEqual(self.app.prefs["sky_telescopes"][0]["aperture_mm"], 203.0)

    def test_failed_scan_refuses_the_run(self):
        import sky_page

        def scan(_f):
            raise RuntimeError("unreadable")
        folder = self.path("odd")
        os.makedirs(folder)
        self.start({"sky_source": "folder"})
        page = self.app.sky_page
        with mock.patch.dict(sys.modules, {"sky_folder": types.SimpleNamespace(scan=scan)}):
            page.set_folder(folder)
            self.assertTrue(self.app.jobs.wait(30))
            self.app.update()
            page.run()
        self.assertIn("could not look at this folder", page.msg_var.get())

    def test_mono_run_keeps_the_color_choice(self):
        self.start({"sky_color": "TR", "sky_source": "folder"})
        page = self.app.sky_page
        page._set_mono(True, "SI")
        page._save_prefs(None, None)
        self.assertEqual(self.app.prefs["sky_color"], "TR")

    def test_color_folder_restores_choice(self):
        import sky_page
        self.start({"sky_color": "TR", "sky_source": "folder"})
        page = self.app.sky_page
        page._set_mono(True, "SI")
        page._set_mono(False)
        self.assertEqual(page.color_var.get(), "Red (TR)")
        self.assertEqual(str(page.color.cget("state")), "readonly")


if __name__ == "__main__":
    unittest.main()
