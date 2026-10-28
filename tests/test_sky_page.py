"""The Sky page (needs a display: run under xvfb-run on Linux): an on-sky run over synthetic SHOBS-P logs in the
background, the nights table (older runs grey, unreadable logs ✖), a night's findings and plots, Save to system
profile through the dialog, each night in the run history once, a folder-of-lights run with a stand-in sky_folder,
a damaged system profile blocks the save. Also the two history helpers for on-sky log files (no display needed)."""
from __future__ import annotations

import json
import os
import sys
import types
import unittest
from unittest import mock

from tests.base import TempData

import history
import shobs_profile as sp
import synth_sky

try:
    import tkinter as tk
    HAVE_TKINTER = True
except ImportError:
    HAVE_TKINTER = False
try:
    _r = tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


def haze_bump(doc: dict, mag: float = 0.12) -> dict:
    """Thin cloud passing in the middle of the night (a bump, so it cannot pass for extinction)."""
    import math
    fr = doc["frames"]
    n = len(fr)
    for i, f in enumerate(fr):
        a = mag * math.exp(-0.5 * ((i - n / 2) / (n / 8)) ** 2)
        for st in f["stars"].values():
            st["flux"] = round(st["flux"] * 10 ** (-0.4 * a), 3)
    return doc


def write_nights(n_frames: int = 80) -> list:
    """Three nights (one with a wrong catalog value and a variable, one hazy), an older run of the second night, and
    the camera profile that gives the gain. Returns the log paths."""
    sp.save_profile(synth_sky.camera_profile(), "test")
    paths = []
    for kw in (dict(night="2026-10-18", seed=1, cat_error={4: 1.4}, variable=7),
               dict(night="2026-10-19", seed=2, airmass=(1.1, 1.9)),
               dict(night="2026-10-19", seed=5, airmass=(1.1, 1.9), written="2026-10-19T21:00:00-04:00",
                    run_id="20261019T210000-0001"),
               dict(night="2026-10-20", seed=3, haze_mag=0.15)):
        doc, _t = synth_sky.make_log(n_frames=n_frames, **kw)
        if kw["night"] == "2026-10-20":
            haze_bump(doc)
        paths.append(synth_sky.write_log(doc))
    return paths


def onsky_rows() -> list:
    return [r for r in history.recent_runs(500) if r["kind"] == "onsky"]


class TestOnskyFiles(TempData):
    def test_known_and_marked(self):
        p = self.path("a.json")
        with open(p, "w") as fh:
            fh.write("{}")
        st = os.stat(p)
        self.assertFalse(history.onsky_file_known(p, st.st_size, st.st_mtime))
        key = history.record_run("onsky", p, {"night": "2026-10-18"}, status="ok")
        self.assertTrue(history.mark_onsky_file(p, st.st_size, st.st_mtime, key))
        self.assertTrue(history.onsky_file_known(p, st.st_size, st.st_mtime))
        self.assertFalse(history.onsky_file_known(p, st.st_size + 1, st.st_mtime))      # written again: new
        self.assertFalse(history.onsky_file_known(p, st.st_size, st.st_mtime + 5))
        import sqlite3
        con = sqlite3.connect(sp.runs_db_path())
        rid = con.execute("SELECT id FROM runs WHERE run_key = ?", (key,)).fetchone()[0]
        self.assertEqual(con.execute("SELECT run_id FROM onsky_files").fetchone()[0], rid)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], 1)
        con.close()
        # marking again (a re-written log) replaces the row
        self.assertTrue(history.mark_onsky_file(p, st.st_size + 1, st.st_mtime, None))
        self.assertTrue(history.onsky_file_known(p, st.st_size + 1, st.st_mtime))

    def test_never_raises(self):
        os.makedirs(sp.runs_db_path())          # a folder where the file should be
        self.assertFalse(history.onsky_file_known("x", 1, 1.0))
        self.assertFalse(history.mark_onsky_file("x", 1, 1.0, "k"))


@unittest.skipUnless(HAVE_TKINTER, "needs Tkinter (sky_page.py is a window page)")
class TestSkyHelpers(TempData):
    def test_shobs_p_settings(self):
        import sky_page
        p = self.path("shobs_p.json")
        with mock.patch("sky_page.shobs_p_settings_path", return_value=p):
            self.assertEqual(sky_page.shobs_p_telescope(), {})                  # missing
            with open(p, "w") as fh:
                fh.write("{not json")
            self.assertEqual(sky_page.shobs_p_telescope(), {})                  # damaged
            with open(p, "w") as fh:
                json.dump({"telescope_name": "14in RC", "aperture_mm": 356, "elevation": "300"}, fh)
            self.assertEqual(sky_page.shobs_p_telescope(),
                             {"name": "14in RC", "aperture_mm": 356.0, "elevation_m": 300.0})

    def test_setups(self):
        import sky_page
        paths = write_nights(n_frames=12)
        other = dict(synth_sky.ZWO, focal_header_mm=530.0)
        doc, _t = synth_sky.make_log(night="2026-10-25", setup=other, n_frames=12)
        paths.append(synth_sky.write_log(doc))
        setups = sky_page.setups_of([sky_page.log_info(p) for p in paths])
        self.assertEqual(len(setups), 2)
        self.assertEqual(setups[0][0], ("zwo-asi2600mc-air__g100__o50__bin1", 530))       # newest night first
        self.assertIn("1 night", setups[0][1])
        self.assertEqual(setups[1][1], "zwo-asi2600mc-air__g100__o50__bin1 · 2900 mm · 3 nights")


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestSkyPage(TempData):
    def setUp(self):
        super().setUp()
        self.p_settings = mock.patch("sky_page.shobs_p_settings_path", return_value=self.path("none.json"))
        self.p_settings.start()
        import flux_app
        self.app = flux_app.App(testing=True)
        self.app.update()
        self.page = self.app.sky_page

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        self.p_settings.stop()
        super().tearDown()

    def _run(self):
        page = self.page
        page.name_var.set("14in RC")
        page.aperture_var.set("356")
        page.elevation_var.set("300")
        page.run()
        self.assertTrue(self.app.jobs.wait(120))
        self.assertIsNotNone(page.summary, page.msg_var.get())

    def test_run_select_save(self):
        import sky_page
        app, page = self.app, self.page
        write_nights()
        bad = os.path.join(sp.onsky_dir(), "2026-10-21__broken.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{ this is not json")
        app.show_page(2)
        self.assertEqual(app.title_var.get(), "Sky")
        self.assertIn("5 on-sky log files", page.logs_var.get())
        self.assertIn("(1 cannot be read)", page.logs_var.get())
        self.assertEqual(page.setup_var.get(), "zwo-asi2600mc-air__g100__o50__bin1 · 2900 mm · 3 nights")
        self._run()
        self.assertEqual(app.prefs["sky_aperture_mm"], 356.0)
        self.assertEqual(settings_prefs()["sky_telescope_name"], "14in RC")
        st = app.status_var.get()
        self.assertTrue(st.startswith("On-sky run: 3 nights (2 photometric); zero point TG 20.2"), st)
        # the table: three nights, the older run grey, the broken log ✖
        rows = [page.tree.item(i) for i in page.tree.get_children()]
        self.assertEqual(len(rows), 5)
        vals = [r["values"] for r in rows]
        self.assertEqual([v[0] for v in vals[:3]], ["2026-10-20", "2026-10-19", "2026-10-18"])
        self.assertEqual(vals[1][7], "✔")
        self.assertEqual(vals[0][7], "—")                                    # the hazy night
        self.assertIn("Not photometric", vals[0][12])
        self.assertEqual(vals[1][12], "used")
        self.assertEqual(vals[1][4], "SHOBS-P")
        self.assertAlmostEqual(float(vals[1][5]), 20.2, delta=0.03)
        self.assertEqual(vals[3][12], sky_page.OLD_RUN)
        self.assertIn("old", rows[3]["tags"])
        self.assertTrue(str(vals[4][12]).startswith("✖ "), vals[4])
        self.assertIn("broken", str(vals[4][1]))
        # no selection: the run's summary; values tab filled
        text = page.text.get("1.0", "end")
        self.assertIn("Nights: 3 nights read", text)
        self.assertIn("could not be read", text)
        self.assertGreater(len(page.val_tree.get_children()), 0)
        self.assertTrue(page.fig.axes)
        # select the first night: its lines (the bad catalog star) and plots
        r18 = next(r for r in page.summary.nights if r.ds.night == "2026-10-18")
        self.assertTrue(page.select_night(r18))
        text = page.text.get("1.0", "end")
        self.assertIn("Left out Field 5", text)
        self.assertIn("2026-10-18", page.fig.axes[0].get_title(loc="left"))
        page.canvas.draw()
        # selecting the older run changes nothing
        page.tree.selection_set(page.tree.get_children()[3])
        page.night_selected()
        self.assertIs(page.selected, r18)
        # the run history: the three nights (the older run is not used)
        self.assertEqual(len(onsky_rows()), 3)
        # save to the system profile (a new one)
        pid = sp.system_id("14in RC", 2900, "zwo-asi2600mc-air__g100__o50__bin1")
        page.save()
        dlg = [w for w in app.winfo_children() if isinstance(w, sky_page.SaveDialog)][-1]
        self.assertFalse(dlg.has_user)
        dlg.ok()
        path = sp.profile_path("systems", pid, make=False)
        self.assertTrue(os.path.exists(path), page.msg_var.get())
        self.assertTrue(page.msg_var.get().startswith("✔"))
        doc = sp.load(path)
        self.assertEqual(sp.validate(doc), [])
        self.assertEqual(doc["telescope"]["name"], "14in RC")
        self.assertEqual(doc["telescope"]["aperture_mm"], 356.0)
        self.assertEqual(doc["bands"]["TG"]["zero_point"]["source"], "measured")
        self.assertEqual(doc["learning"]["last_onsky_run"], page.run_key)
        self.assertIsNotNone(sp.find_system_profile("zwo-asi2600mc-air__g100__o50__bin1", 2905))
        sky = [r for r in history.recent_runs(50) if r["kind"] == "sky"]
        self.assertEqual(len(sky), 1)
        self.assertEqual(sky[0]["run_key"], page.run_key)
        # a typed value, then save again keeping it
        doc["bands"]["TG"]["extinction_k"] = sp.record(0.25, "mag/airmass", "user")
        sp.save_profile(doc, "test")
        page.save()
        dlg = [w for w in app.winfo_children() if isinstance(w, sky_page.SaveDialog)][-1]
        self.assertTrue(dlg.has_user)
        dlg.keep.set(True)
        dlg.ok()
        doc = sp.load(path)
        self.assertEqual(doc["bands"]["TG"]["extinction_k"]["value"], 0.25)
        self.assertEqual(len([r for r in history.recent_runs(50) if r["kind"] == "sky"]), 1)   # same run, once
        # run again: nothing new in the history; a re-written log is recorded again
        self._run()
        self.assertEqual(len(onsky_rows()), 3)
        r19 = next(r for r in page.summary.nights if r.ds.night == "2026-10-19")
        with open(r19.ds.path, "a", encoding="utf-8") as fh:
            fh.write(" ")
        self._run()
        self.assertEqual(len(onsky_rows()), 4)
        # the profile now in place is what the Values tab compares with
        first = page.val_tree.get_children()[0]
        kid = page.val_tree.get_children(first)[0]
        self.assertNotEqual(page.val_tree.set(kid, "old"), "—")

    def test_damaged_profile_blocks_save(self):
        write_nights(n_frames=60)
        self.app.show_page(2)
        self._run()
        pid = sp.system_id("14in RC", 2900, "zwo-asi2600mc-air__g100__o50__bin1")
        path = sp.profile_path("systems", pid)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("{ damaged")
        self.page.save()
        self.assertTrue(self.page.msg_var.get().startswith("✖"), self.page.msg_var.get())
        self.assertIn("cannot be read", self.page.msg_var.get())
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "{ damaged")

    def test_no_logs_and_bad_numbers(self):
        page = self.page
        self.app.show_page(2)
        self.assertIn("No on-sky log files yet", page.logs_var.get())
        page.run()
        self.assertTrue(page.msg_var.get().startswith("⚠"), page.msg_var.get())
        page.aperture_var.set("big")
        page.run()
        self.assertIn("not a number", page.msg_var.get())

    def test_folder_mode(self):
        import sky_page
        sp.save_profile(synth_sky.camera_profile(), "test")
        doc0, _t = synth_sky.make_log(night="2026-10-18", seed=1, n_frames=60)
        synth_sky.write_log(doc0)                          # an earlier SHOBS-P night of the same setup
        folder = self.path("lights")
        os.makedirs(folder)
        calls = {}

        def run_folder(folder, channel="TG", step=None, elevation_m=None):
            calls["run"] = (folder, channel, elevation_m)
            step("Measuring light 1 of 60")
            doc, _t = synth_sky.make_log(night="2026-10-22", seed=9, n_frames=60, written_by="SHOBS-FLUX 0.4.0",
                                         band=channel)
            return types.SimpleNamespace(doc=doc, lines=[("info", "60 lights read."),
                                                         ("warn", "⚠ 3 lights had no plate solution.")])

        def save_log(doc):
            calls["saved"] = synth_sky.write_log(doc)
            return calls["saved"]

        def scan(folder):
            return {"n_lights": 60, "osc": True, "band": None, "solved": True, "night": "2026-10-22",
                    "target": "Test Star"}

        fake = types.SimpleNamespace(run_folder=run_folder, save_log=save_log, scan=scan)
        with mock.patch.dict(sys.modules, {"sky_folder": fake}):
            page = self.page
            self.app.show_page(2)
            page.source_var.set(sky_page.SOURCES[1][0])
            page.source_changed()
            self.assertTrue(page.folder_row.winfo_manager())
            page.set_folder(folder)
            self.assertTrue(self.app.jobs.wait(30))
            self.assertIn("60 lights", page.scan_var.get())
            self.assertIn("plate-solved", page.scan_var.get())
            page.color_var.set("Green (TG)")
            self._run()
        self.assertEqual(calls["run"], (folder, "TG", 300.0))
        self.assertTrue(os.path.exists(calls["saved"]))
        nights = {r.ds.night: r for r in page.summary.nights}
        self.assertEqual(set(nights), {"2026-10-18", "2026-10-22"})
        self.assertEqual(nights["2026-10-22"].ds.source, "SHOBS-FLUX")
        row = page.tree.item(page.tree.get_children()[0])["values"]
        self.assertEqual(row[4], "FLUX")
        text = page.text.get("1.0", "end")
        self.assertLess(text.index("The folder of lights"), text.index("All nights"))
        self.assertLess(text.index("⚠ 3 lights"), text.index("60 lights read."))     # warnings first
        self.assertEqual(self.app.prefs["sky_source"], "folder")

    def test_folder_mode_error(self):
        import sky_page

        class SkyFolderError(Exception):
            pass

        def run_folder(folder, channel="TG", step=None, elevation_m=None):
            raise SkyFolderError("no light in this folder has a plate solution")

        fake = types.SimpleNamespace(run_folder=run_folder, save_log=None, scan=lambda f: {}, SkyFolderError=SkyFolderError)
        folder = self.path("lights")
        os.makedirs(folder)
        with mock.patch.dict(sys.modules, {"sky_folder": fake}):
            page = self.page
            page.source_var.set(sky_page.SOURCES[1][0])
            page.source_changed()
            page.folder_var.set(folder)
            page.run()
            self.assertTrue(self.app.jobs.wait(30))
        self.assertEqual(page.msg_var.get(), "✖ The on-sky run could not be done: no light in this folder has a "
                                             "plate solution.")


def settings_prefs() -> dict:
    import settings
    return settings.load()


if __name__ == "__main__":
    unittest.main()
