"""The 0.5.1 fix list, builder A (Sky page and on-sky engine):
  (1) after Run, the new folder run's row is selected, so the plots and findings follow it (not the first row);
  (2) the folder line names the plate solution (TAN, TAN-SIP) and counts a folder with a few unsolved lights as solved;
  (3) Imaging (astrophotography) runs: their star size goes ONLY to bands.<band>.fwhm_imaging, never to the in-focus
      seeing or the defocus reference; zero point and sky count as usual; the choice is kept per setup;
  (4) several runs of the same night (mosaic panels) count once in the medians; texts say nights, not runs;
  (7b) a folder Run is a scratch look: no run history row and no log file until Save; the one-time 0.5.1 clean-up
      marks the unsaved folder runs 0.5.0 recorded as removed (on a synthetic DB and on a copy of John's data)."""
from __future__ import annotations

import json
import math
import os
import shutil
import sqlite3
import sys
import types
import unittest
from unittest import mock

from tests.base import TempData

import history
import onsky
import settings
import shobs_profile as sp
import synth_sky

PROF = synth_sky.camera_profile(read_noise=1.2)
REAL = "/home/claude/realdata/.shobs"


def lookup(_cam):
    return PROF


def night(frames_are="photometry", written_by="SHOBS-FLUX 0.5.1", **kw):
    kw.setdefault("airmass", (1.15, 1.2))
    kw.setdefault("n_frames", 60)
    doc, _t = synth_sky.make_log(written_by=written_by, **kw)
    doc["frames_are"] = frames_are
    return onsky.analyse(onsky.load_dataset(doc, lookup), None, 356, 300)


# ---- (3) imaging focus kept apart ----------------------------------------------------------------------------------

class TestImagingSeeing(TempData):
    def test_imaging_goes_to_its_own_value(self):
        phot = night(night="2026-10-25", seeing=5.2, seed=1, target="HD 1")
        img = night("imaging", night="2026-10-06", seeing=2.9, seed=2, target="M 45_1-1")
        self.assertTrue(img.imaging)
        self.assertFalse(phot.imaging)
        s = onsky.combine([phot, img])
        b = s.bands["TG"]
        self.assertAlmostEqual(b.fwhm_in_focus, phot.fwhm_arcsec)            # photometry seeing only
        self.assertEqual(b.fwhm_in_focus_n, 1)
        self.assertAlmostEqual(b.fwhm_imaging, img.fwhm_arcsec)
        self.assertEqual(b.fwhm_imaging_n, 1)
        recs, _cad = onsky.measured_records(s, "sky-test")
        self.assertIn("fwhm_imaging", recs["TG"])
        self.assertEqual(recs["TG"]["fwhm_imaging"]["date"], "2026-10-06")
        self.assertAlmostEqual(recs["TG"]["fwhm_in_focus"]["value"], round(phot.fwhm_arcsec, 2))
        # zero point / sky from the imaging night count the normal way
        self.assertIn("2026-10-06", b.zp_nights)
        alone = onsky.combine([night("imaging", night="2026-10-06", seeing=2.9, seed=2)]).bands["TG"]
        self.assertTrue(math_finite(alone.sky) and math_finite(alone.zp1))
        self.assertTrue(math.isnan(alone.fwhm_in_focus))
        # the save dialog and the profile
        labels = [c[0] for c in onsky.changes_for(s, None)]
        self.assertIn("green (TG): Seeing, imaging focus", labels)
        doc = onsky.apply_to_profile(s, None, "test", "sky-test", system_id="t-2900mm__cam", cam_id="cam",
                                     telescope={"name": "t", "focal_mm": 2900})
        self.assertEqual(sp.validate(doc), [])
        self.assertIn("fwhm_imaging", doc["bands"]["TG"])
        # the run history rows: the imaging night's star size under its own key only
        keys = {v["key"] for v in onsky.night_values(img, "pid")}
        self.assertIn("bands.fwhm_imaging", keys)
        self.assertNotIn("bands.fwhm_in_focus", keys)
        self.assertNotIn("bands.fwhm_defocus", keys)
        self.assertEqual(onsky.night_summary(img)["frames_are"], "imaging")
        import sky_page
        self.assertIn("bands.fwhm_imaging", {v["key"] for v in sky_page.sky_values(s, "pid")})
        self.assertEqual(sky_page.sky_summary(s)["imaging_nights"], 1)

    def test_imaging_never_the_defocus_reference(self):
        # an imaging night (2.9″) with a defocused-looking photometry night (5.5″) alone: the imaging run must not
        # be taken as in focus for judging it
        img = night("imaging", night="2026-10-06", seeing=2.9, seed=2, focuspos=6233)
        phot = night(night="2026-10-29", seeing=5.5, seed=3, focuspos=6358)
        onsky.combine([img, phot])
        self.assertFalse(phot.defocused, phot.lines)          # nothing to compare with: the flat rule (≤ 7″) stands
        self.assertFalse(img.defocused)
        # same night: an imaging run never pairs with a photometry run as its in-focus partner
        a = night("imaging", night="2026-10-06", seeing=2.9, seed=2, focuspos=6233, target="A")
        b = night(night="2026-10-06", seeing=6.8, seed=4, focuspos=6358, target="B")
        onsky.combine([a, b])
        self.assertFalse(b.defocused)
        self.assertEqual(b.defocus_key, "")
        # a profile's imaging value is no in-focus reference either
        prof = {"bands": {"TG": {"fwhm_imaging": sp.record(2.9, "arcsec", "measured")}}}
        c = night(night="2026-10-29", seeing=6.5, seed=5)
        onsky.combine([c], old_profile=prof)
        self.assertFalse(c.defocused)

    def test_shobs_p_logs_are_always_photometry(self):
        r = night("imaging", written_by="SHOBS-P 2.2.19.1", night="2026-10-25", seed=1)
        self.assertFalse(r.imaging)

    def test_history_words_do_not_crash(self):
        self.assertTrue(history.value_words("bands.fwhm_imaging", "TG"))
        img = night("imaging", night="2026-10-06", seeing=2.9, seed=2)
        rk = history.record_run("onsky", "x.json", onsky.night_summary(img), system_id="t-2900mm__cam",
                                values=onsky.night_values(img, "t-2900mm__cam"))
        self.assertTrue(rk)
        pts = history.series_points("system", "t-2900mm__cam", key="bands.fwhm_imaging")
        self.assertEqual(len(pts), 1)
        self.assertTrue(any(k == "bands.fwhm_imaging" for k, _c, _w, _n in history.available("system",
                                                                                            "t-2900mm__cam")))


# ---- (4) one night once ---------------------------------------------------------------------------------------------

class TestSameNightOnce(TempData):
    def test_two_panels_one_night(self):
        sp_ = "SHOBS-P 2.2.19.1"                                 # its frames' spacing gives the overhead
        p1 = night(written_by=sp_, night="2026-10-06", seeing=3.0, seed=1, target="M 45_1-1", sky_mag=19.0)
        p2 = night(written_by=sp_, night="2026-10-06", seeing=3.4, seed=2, target="M 45_1-2", sky_mag=19.2)
        other = night(written_by=sp_, night="2026-10-25", seeing=5.0, seed=3, target="HD 1", sky_mag=18.0)
        s = onsky.combine([p1, p2, other])
        b = s.bands["TG"]
        self.assertEqual(b.fwhm_in_focus_n, 2)                  # two nights, not three runs
        mid = (p1.fwhm_arcsec + p2.fwhm_arcsec) / 2
        self.assertAlmostEqual(b.fwhm_in_focus, (mid + other.fwhm_arcsec) / 2, places=6)
        self.assertEqual(b.cy_n, len({r.ds.night for r in (p1, p2, other) if math.isfinite(r.cy)}))
        panels = onsky.combine([p1, p2]).bands["TG"]
        self.assertEqual(panels.sky_n, 1)
        self.assertAlmostEqual(panels.sky, (p1.sky_mag + p2.sky_mag) / 2, places=6)
        self.assertEqual(len(b.zp_nights), 2)                   # (already per night)
        self.assertEqual(s.overhead_runs, 2)
        recs, cad = onsky.measured_records(s)
        self.assertIn("lowest of 2 nights", cad["overhead_s"]["conditions"])
        self.assertEqual(recs["TG"]["fwhm_in_focus"]["n"], 2)
        import sky_page
        res = sky_page.SkyRun()
        res.summary = s
        text = " ".join(t for _k, t in sky_page.run_lines(res))
        self.assertIn("2 nights read", text)
        self.assertIn("from 3 runs", text)
        self.assertIn("lowest over 2 nights", text)
        self.assertIn("in focus from 2 nights", text)
        self.assertTrue(sky_page.status_text(s).startswith("On-sky run: 2 nights"))
        self.assertEqual(sky_page.sky_summary(s)["nights"], 2)

    def test_defocus_once_per_night(self):
        a = night(night="2026-10-25", seeing=3.5, seed=1, focuspos=6400, target="A")
        b1 = night(night="2026-10-25", seeing=7.6, seed=2, focuspos=6525, target="B")
        b2 = night(night="2026-10-25", seeing=8.0, seed=3, focuspos=6525, target="C")
        s = onsky.combine([a, b1, b2])
        v, _sg, n = s.bands["TG"].fwhm_defocus["+125 steps"]
        self.assertEqual(n, 1)
        self.assertAlmostEqual(v, (b1.fwhm_arcsec + b2.fwhm_arcsec) / 2, places=6)


# ---- (2) the plate solution on the folder line ----------------------------------------------------------------------

class TestPlateSolutionWords(TempData):
    def _folder(self, sip=True, unsolved_last=False):
        import synth_lights
        from astropy.io import fits
        d = self.path("lights")
        synth_lights.make_folder(d, n_frames=4, size=(300, 200), extra_files=False)
        names = sorted(os.listdir(d))
        for i, n in enumerate(names):
            with fits.open(os.path.join(d, n), mode="update") as h:
                hd = h[0].header
                if unsolved_last and i == len(names) - 1:
                    for k in ("CTYPE1", "CTYPE2", "CRVAL1", "CRVAL2", "CRPIX1", "CRPIX2", "CD1_1", "CD1_2", "CD2_1",
                              "CD2_2"):
                        del hd[k]
                    continue
                if sip:                                         # as ASIAIR writes them
                    hd["CTYPE1"], hd["CTYPE2"] = "RA---TAN-SIP", "DEC--TAN-SIP"
                    for k, v in (("A_ORDER", 2), ("B_ORDER", 2), ("AP_ORDER", 2), ("BP_ORDER", 2),
                                 ("A_2_0", 1.96e-8), ("B_0_2", 9.6e-8), ("AP_2_0", -1.96e-8), ("BP_0_2", -9.6e-8)):
                        hd[k] = v
        return d

    def test_sip_folder_says_tan_sip(self):
        import sky_folder
        import sky_page
        s = sky_folder.scan(self._folder())
        self.assertEqual(s["projection"], "TAN-SIP")
        line = sky_page.scan_line(s)
        self.assertIn("plate-solved (TAN-SIP)", line)
        self.assertNotIn("no plate solution", line)
        self.assertTrue(any("TAN-SIP" in t for _k, t in s["lines"]))

    def test_last_light_unsolved_is_still_solved(self):
        # John's M45 panels: ASIAIR solved 11 of 12 lights; 0.5.0 then said "no plate solution"
        import sky_folder
        import sky_page
        s = sky_folder.scan(self._folder(unsolved_last=True))
        self.assertEqual((s["n_wcs"], s["n_lights"], s["solved"]), (3, 4, False))
        line = sky_page.scan_line(s)
        self.assertIn("3 of 4 plate-solved (TAN-SIP)", line)
        self.assertNotIn("no plate solution", line)
        self.assertTrue(any("3 of 4 frames have a plate solution" in t for _k, t in s["lines"]))

    def test_plain_tan_and_none(self):
        import sky_folder
        import sky_page
        s = sky_folder.scan(self._folder(sip=False))
        self.assertEqual(s["projection"], "TAN")
        self.assertIn("plate-solved (TAN)", sky_page.scan_line(s))
        self.assertEqual(sky_folder.projection_of({"CTYPE1": "RA---TAN", "CTYPE2": "DEC--TAN", "A_ORDER": 2}),
                         "TAN-SIP")
        self.assertEqual(sky_folder.projection_of({}), "")
        self.assertEqual(sky_page.scan_line({"n_lights": 3, "n_wcs": 0, "solved": False}),
                         "3 lights, no plate solution")
        self.assertIn("plate-solved", sky_page.scan_line({"n_lights": 60, "solved": True}))   # older scan dicts


# ---- the page: (1) selection, (3) the choice, (7b) recorded on Save only -------------------------------------------

class TestSkyPageFolder(TempData):
    def setUp(self):
        super().setUp()
        self.p_settings = mock.patch("sky_page.shobs_p_settings_path", return_value=self.path("none.json"))
        self.p_settings.start()

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

    def _app(self):
        import flux_app
        self.app = flux_app.App(testing=True)
        self.app.update()
        return self.app.sky_page

    def _fake(self, calls, night_="2026-10-06", seeing=2.9):
        def run_folder(folder, channel="TG", step=None, catalog=None, cam_lookup=None, max_frames=150, site=None,
                       elevation_m=None, variables=None):
            doc, _t = synth_sky.make_log(night=night_, seed=9, n_frames=60, written_by="SHOBS-FLUX 0.5.1",
                                         band=channel, target="M 45_1-1", seeing=seeing,
                                         run_id=f"flux-20261102T10000{len(calls.setdefault('runs', []))}-abcd")
            calls["runs"].append(doc["run_id"])
            return types.SimpleNamespace(doc=doc, lines=[("ok", "✔ 60 of 60 frames measured.")])

        def save_log(doc):
            calls.setdefault("saved", []).append(synth_sky.write_log(doc))
            return calls["saved"][-1]

        def scan(folder):
            return {"n_lights": 60, "osc": True, "band": None, "solved": True, "n_wcs": 60,
                    "projection": "TAN-SIP", "night": night_, "target": "M 45_1-1",
                    "camera_id": synth_sky.ZWO["camera_id"], "focal": 2900.0}
        return types.SimpleNamespace(run_folder=run_folder, save_log=save_log, scan=scan)

    def test_folder_run_selected_saved_only_on_save(self):
        import sky_page
        sp.save_profile(PROF, "test")
        # older SHOBS-P nights of the same setup: the newest is on top of the table
        for n, sd in (("2026-10-29", 1), ("2026-10-25", 2)):
            synth_sky.write_log(synth_sky.make_log(night=n, seed=sd, n_frames=60)[0])
        page = self._app()
        folder = self.path("lights")
        os.makedirs(folder)
        calls = {}
        with mock.patch.dict(sys.modules, {"sky_folder": self._fake(calls)}):
            page.source_var.set(sky_page.SOURCES[1][0])
            page.source_changed()
            page.set_folder(folder)
            self.assertTrue(self.app.jobs.wait(30))
            self.assertIn("plate-solved (TAN-SIP)", page.scan_var.get())
            page.frames_are_var.set(sky_page.FRAMES_WORDS["imaging"])
            page.name_var.set("14in RC")
            page.aperture_var.set("356")
            page.elevation_var.set("300")
            n_files = len(os.listdir(sp.onsky_dir()))
            page.run()
            self.assertTrue(self.app.jobs.wait(120))
            self.assertIsNotNone(page.summary, page.msg_var.get())
            # (1) the new run's row is selected and the plots follow it, though it is not the first row
            first = page.rows[page.tree.get_children()[0]]
            self.assertEqual(first.ds.night, "2026-10-29")
            new = page.selected
            self.assertIsNotNone(new)
            self.assertEqual(new.ds.night, "2026-10-06")
            self.assertEqual(page.rows[page.tree.selection()[0]], new)
            self.assertIn("2026-10-06", page.fig.axes[0].get_title(loc="left"))
            text = page.text.get("1.0", "end")
            self.assertIn("The folder of lights", text)
            self.assertIn("imaging focus", text)
            # (3) the table labels the night imaging; the run is imaging
            row = page.tree.item(page.tree.selection()[0])["values"]
            self.assertTrue(str(row[0]).endswith("imaging"))
            self.assertTrue(new.imaging)
            self.assertTrue(math_finite(page.summary.bands["TG"].fwhm_imaging))
            vals = [page.val_tree.item(k)["text"] for p in page.val_tree.get_children()
                    for k in page.val_tree.get_children(p)]
            self.assertIn("Seeing, imaging focus", vals)
            # (7b) a Run alone: no run history rows, no log file
            self.assertEqual(history.recent_runs(50, removed=True), [])
            self.assertEqual(len(os.listdir(sp.onsky_dir())), n_files)
            self.assertNotIn("saved", calls)
            # Save: the log is written, the nights recorded, the profile gets fwhm_imaging only
            page.save()
            dlg = [w for w in self.app.winfo_children() if isinstance(w, sky_page.SaveDialog)][-1]
            dlg.ok()
            self.assertTrue(page.msg_var.get().startswith("✔"), page.msg_var.get())
            self.assertEqual(len(calls["saved"]), 1)
            self.assertTrue(os.path.exists(calls["saved"][0]))
            with open(calls["saved"][0], encoding="utf-8") as fh:
                doc = json.load(fh)
            self.assertEqual(doc["frames_are"], "imaging")
            runs = history.recent_runs(50)
            self.assertEqual(sorted(r["kind"] for r in runs), ["onsky", "onsky", "onsky", "sky"])
            pid = sp.system_id("14in RC", 2900, synth_sky.ZWO["camera_id"])
            prof = sp.load(sp.profile_path("systems", pid, make=False))
            self.assertAlmostEqual(prof["bands"]["TG"]["fwhm_imaging"]["value"], round(new.fwhm_arcsec, 2))
            self.assertGreater(prof["bands"]["TG"]["fwhm_in_focus"]["value"], 3.0)   # the photometry nights
            img_rows = history.series_points("system", pid, key="bands.fwhm_imaging")
            self.assertEqual([p["date"] for p in img_rows], ["2026-10-06"])
            self.assertEqual(history.series_points("system", pid, key="bands.fwhm_in_focus",
                                                   date_from="2026-10-06", date_to="2026-10-06"), [])
            # saving again does not record twice
            page.save()
            [w for w in self.app.winfo_children() if isinstance(w, sky_page.SaveDialog)][-1].ok()
            self.assertEqual(len(history.recent_runs(50)), 4)
            self.assertEqual(len(calls["saved"]), 1)
        # the choice is kept per setup
        e = sky_page.tel_entry(self.app.prefs["sky_telescopes"], synth_sky.ZWO["camera_id"], 2900.0)
        self.assertEqual(e["frames_are"], "imaging")

    def test_failed_profile_save_leaves_no_log(self):
        """Fable review 0.5.1: the profile is saved first, so a failed save leaves no folder log and no runs."""
        import sky_page
        sp.save_profile(PROF, "test")
        page = self._app()
        folder = self.path("lights")
        os.makedirs(folder)
        calls = {}
        with mock.patch.dict(sys.modules, {"sky_folder": self._fake(calls)}):
            page.source_var.set(sky_page.SOURCES[1][0])
            page.source_changed()
            page.set_folder(folder)
            self.assertTrue(self.app.jobs.wait(30))
            page.name_var.set("14in RC")
            page.aperture_var.set("356")
            page.run()
            self.assertTrue(self.app.jobs.wait(120))
            n_files = len(os.listdir(sp.onsky_dir()))
            page.save()
            dlg = [w for w in self.app.winfo_children() if isinstance(w, sky_page.SaveDialog)][-1]
            with mock.patch("sky_page.sp.save_profile", side_effect=OSError("disk full")):
                dlg.ok()
            self.assertTrue(page.msg_var.get().startswith("✖"), page.msg_var.get())
            self.assertNotIn("saved", calls)
            self.assertEqual(len(os.listdir(sp.onsky_dir())), n_files)
            self.assertEqual(history.recent_runs(50, removed=True), [])

    def test_unsaved_run_never_read_again(self):
        import sky_page
        sp.save_profile(PROF, "test")
        synth_sky.write_log(synth_sky.make_log(night="2026-10-29", seed=1, n_frames=60)[0])
        page = self._app()
        self.assertEqual(page.frames_are, "photometry")                  # the default
        folder = self.path("lights")
        os.makedirs(folder)
        calls = {}
        with mock.patch.dict(sys.modules, {"sky_folder": self._fake(calls)}):
            page.source_var.set(sky_page.SOURCES[1][0])
            page.source_changed()
            page.set_folder(folder)
            self.assertTrue(self.app.jobs.wait(30))
            page.name_var.set("14in RC")
            page.run()
            self.assertTrue(self.app.jobs.wait(120))
            self.assertEqual(len(page.summary.nights), 2)
            page.run()                                                    # looked at twice, never saved
            self.assertTrue(self.app.jobs.wait(120))
            self.assertEqual(len(page.summary.nights), 2)                 # the first look is not a night now
        # back to the logs: only SHOBS-P's night
        page.source_var.set(sky_page.SOURCES[0][0])
        page.source_changed()
        page.run()
        self.assertTrue(self.app.jobs.wait(120))
        self.assertEqual([r.ds.night for r in page.summary.nights], ["2026-10-29"])
        self.assertEqual(history.recent_runs(50, removed=True), [])

    def test_logs_run_recorded_on_save_only(self):
        import sky_page
        sp.save_profile(PROF, "test")
        synth_sky.write_log(synth_sky.make_log(night="2026-10-29", seed=1, n_frames=60)[0])
        page = self._app()
        page.name_var.set("14in RC")
        page.run()
        self.assertTrue(self.app.jobs.wait(120))
        self.assertEqual(history.recent_runs(50, removed=True), [])
        page.save()
        [w for w in self.app.winfo_children() if isinstance(w, sky_page.SaveDialog)][-1].ok()
        self.assertEqual(sorted(r["kind"] for r in history.recent_runs(50)), ["onsky", "sky"])


def math_finite(v) -> bool:
    import math
    return isinstance(v, float) and math.isfinite(v)


# ---- (7b) the one-time clean-up -------------------------------------------------------------------------------------

def _onsky_row(night, log, sid):
    return history.record_run("onsky", os.path.join("C:\\Users\\x\\.shobs\\onsky", log),
                              {"night": night, "log": log}, system_id=sid,
                              values=[{"key": "bands.fwhm_in_focus", "value": 2.9, "channel": "TG",
                                       "profile_id": sid, "date": night}])


class TestCleanup(TempData):
    SID = "14in-rc-2900mm__zwo-asi2600mc-air__g100__o50__bin1"

    def test_synthetic_db(self):
        import sky_page
        prof = onsky.new_system_profile(self.SID, "zwo-asi2600mc-air__g100__o50__bin1",
                                        {"name": "14in RC", "focal_mm": 2900}, "test")
        prof["bands"]["TG"] = {"zero_point": sp.record(20.1, "mag", "measured", date="2026-10-29",
                                                        conditions="2 nights (2026-10-20 to 2026-10-29)")}
        sp.save_profile(prof, "test")
        saved = _onsky_row("2026-10-22", "2026-10-22__a__flux-20261023T100000-0001.json", self.SID)   # in range
        unsaved = _onsky_row("2026-10-06", "2026-10-06__m-45__flux-20261101T172925-972d.json", self.SID)
        shobs_p = _onsky_row("2026-10-01", "2026-10-01__hd-1__20261002T100000-0002.json", self.SID)   # not FLUX
        other = _onsky_row("2026-10-06", "2026-10-06__b__flux-20261101T100000-0003.json",
                           "big-4250mm__andor__g1__ona__bin1")          # no profile of its own setup
        got = sky_page.cleanup_unsaved_folder_runs(settings.load())
        self.assertEqual(sorted(r["run_key"] for r in got), sorted([unsaved, other]))
        live = {r["run_key"] for r in history.recent_runs(50)}
        self.assertEqual(live, {saved, shobs_p})
        self.assertEqual(len(history.recent_runs(50, removed=True)), 4)    # nothing deleted
        prefs = settings.load()
        self.assertTrue(prefs[sky_page.CLEANUP_PREF])
        self.assertIn("2026-10-06__m-45__flux-20261101T172925-972d.json", prefs[sky_page.SCRATCH_PREF])
        with open(settings.log_path(), encoding="utf-8") as fh:
            self.assertIn("0.5.1 clean-up: 2 folder-of-lights runs never saved", fh.read())
        # once only: a run put back stays put back
        history.set_removed(unsaved, False)
        self.assertEqual(sky_page.cleanup_unsaved_folder_runs(prefs), [])
        self.assertIn(unsaved, {r["run_key"] for r in history.recent_runs(50)})

    def test_marker_waits_until_all_marked(self):
        """Fable review 0.5.1: when a run cannot be marked (database busy) the clean-up is tried again next start."""
        import sky_page
        a = _onsky_row("2026-10-06", "2026-10-06__m-45__flux-20261101T172925-972d.json", self.SID)
        b = _onsky_row("2026-10-07", "2026-10-07__m-45__flux-20261101T173745-1bc6.json", self.SID)
        real = history.set_removed
        with mock.patch("history.set_removed", side_effect=lambda k, r=True: False if k == b else real(k, r)):
            got = sky_page.cleanup_unsaved_folder_runs(settings.load())
        self.assertEqual([r["run_key"] for r in got], [a])
        prefs = settings.load()
        self.assertFalse(prefs.get(sky_page.CLEANUP_PREF))
        got = sky_page.cleanup_unsaved_folder_runs(prefs)
        self.assertEqual([r["run_key"] for r in got], [b])
        self.assertTrue(settings.load().get(sky_page.CLEANUP_PREF))
        self.assertEqual(history.recent_runs(50), [])

    @unittest.skipUnless(os.path.isdir(REAL), "John's data copy is not here")
    def test_johns_real_copy(self):
        import sky_page
        data = os.environ["SHOBS_DATA_HOME"]
        shutil.copytree(REAL, data)
        before = {r["id"]: r["removed"] for r in history.recent_runs(500, removed=True)}
        self.assertEqual(len(before), 38)
        prefs = settings.load()
        got = sky_page.cleanup_unsaved_folder_runs(prefs)
        self.assertEqual(sorted(r["id"] for r in got), [36, 37, 38])
        after = {r["id"]: r["removed"] for r in history.recent_runs(500, removed=True)}
        self.assertEqual(len(after), 38)                                   # nothing deleted
        self.assertEqual({i for i, v in after.items() if v}, {36, 37, 38} | {i for i, v in before.items() if v})
        self.assertFalse(after[31] or after[34])
        # the M45 logs are not read as nights any more; the Moeller ones still are
        import flux_app
        with mock.patch("sky_page.shobs_p_settings_path", return_value=self.path("none.json")):
            app = flux_app.App(testing=True)
            try:
                files = [os.path.basename(p) for p in app.sky_page.log_files()]
            finally:
                app.destroy()
        self.assertFalse(any("m-45" in f for f in files))
        self.assertTrue(any("toi-4028-01__flux-" in f for f in files))
        self.assertEqual(len(files), 9)
        con = sqlite3.connect(os.path.join(data, "flux_runs.sqlite"))
        self.assertEqual(con.execute("SELECT COUNT(*) FROM run_values").fetchone()[0],
                         sqlite3.connect(os.path.join(REAL, "flux_runs.sqlite")).execute(
                             "SELECT COUNT(*) FROM run_values").fetchone()[0])
        con.close()


if __name__ == "__main__":
    unittest.main()
