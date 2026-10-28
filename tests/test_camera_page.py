"""The Camera page (needs a display: run under xvfb-run on Linux): a bench run on a synthetic ladder in the
background with the busy bar, the plots, findings, values and pairs fill in, the run is in the history, Save writes
a valid camera profile (and keeps a typed value only when asked), the shooting plan opens."""
from __future__ import annotations

import os
import unittest

from tests.base import TempData

import synth_bench

try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestCameraPage(TempData):
    def setUp(self):
        super().setUp()
        import flux_app
        self.app = flux_app.App(testing=True)
        self.app.update()

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        super().tearDown()

    def test_run_save_plan(self):
        import camera_page
        import history
        import shobs_profile as sp
        app = self.app
        app.show_page(1)
        self.assertEqual(app.title_var.get(), "Camera")
        page = app.camera_page
        folder = self.path("ladder")
        synth_bench.make_ladder(folder, shape=(300, 400))
        page.folder_var.set(folder)
        page.region_var.set("Central 50 %")
        page.run()
        self.assertTrue(app.jobs.wait(120))
        self.assertIsNotNone(page.res, page.msg_var.get())
        self.assertIn("gain", app.status_var.get())
        self.assertGreater(len(page.val_tree.get_children()), 6)
        self.assertGreater(len(page.pair_tree.get_children()), 10)
        self.assertIn("Gain", page.text.get("1.0", "end"))
        self.assertIsNotNone(page.canvas)
        self.assertTrue(any(r["kind"] == "bench" for r in history.recent_runs(5)))
        # save a new profile
        pid = page.camera_id()
        page.save()
        dlg = [w for w in app.winfo_children() if isinstance(w, camera_page.SaveDialog)][-1]
        dlg.ok()
        path = sp.profile_path("cameras", pid, make=False)
        self.assertTrue(os.path.exists(path), page.msg_var.get())
        doc = sp.load(path)
        self.assertEqual(sp.validate(doc), [])
        self.assertEqual(doc["values"]["gain_e_per_adu"]["source"], "measured")
        # a typed value, then save again keeping it
        doc["values"]["linear_limit_adu"] = sp.record(32000, "ADU", "user")
        sp.save_profile(doc, "test")
        page.save()
        dlg = [w for w in app.winfo_children() if isinstance(w, camera_page.SaveDialog)][-1]
        self.assertTrue(dlg.has_user)
        dlg.keep.set(True)
        dlg.ok()
        doc = sp.load(path)
        self.assertEqual(doc["values"]["linear_limit_adu"]["value"], 32000)
        self.assertEqual(doc["revision"], 2)          # nothing else changed: no new revision
        # the shooting plan from this run
        page.plan()
        pd = [w for w in app.winfo_children() if isinstance(w, camera_page.PlanDialog)][-1]
        self.assertIn("panel OFF", pd.text.get("1.0", "end"))
        pd.destroy()

    def test_dark_run_spec_picture_and_save(self):
        import camera_page
        import history
        import shobs_profile as sp
        import synth_dark
        app = self.app
        app.show_page(1)
        page = app.camera_page
        folder = self.path("darks")
        synth_dark.make_darks(folder, dark_e_s=0.03, shape=(240, 320))
        page.darks_var.set(folder)
        page.measure_var.set(camera_page.MEASURES[1][0])
        page.measure_changed()
        self.assertEqual(str(page.plan_btn.cget("state")), "disabled")
        page.region_var.set("Central 50 %")
        page.run()
        self.assertTrue(app.jobs.wait(120))
        self.assertIsNotNone(page.dres, page.msg_var.get())
        self.assertIn("Dark run", app.status_var.get())
        self.assertIn("Dark current", page.text.get("1.0", "end"))
        self.assertEqual(page.nb.tab(page.pairs_tab, "text"), "Exposures")
        self.assertGreaterEqual(len(page.pair_tree.get_children()), 5)          # bias + 4 exposures
        self.assertGreater(len(page.val_tree.get_children()), 8)
        self.assertTrue(any(r["kind"] == "darks" for r in history.recent_runs(5)))
        page.picture_var.set("spec")
        page.redraw()
        self.assertTrue(page.fig.axes)
        # save, then the bench view comes back empty but working
        pid = page.camera_id()
        page.save()
        dlg = [w for w in app.winfo_children() if isinstance(w, camera_page.SaveDialog)][-1]
        dlg.ok()
        doc = sp.load(sp.profile_path("cameras", pid, make=False))
        self.assertEqual(sp.validate(doc), [])
        self.assertEqual(doc["values"]["dark_current_e_per_px_s"]["source"], "measured")
        self.assertTrue(doc["dark"]["by_temp"])
        page.measure_var.set(camera_page.MEASURES[0][0])
        page.measure_changed()
        self.assertEqual(page.nb.tab(page.pairs_tab, "text"), "Pairs")
        self.assertEqual(str(page.save_btn.cget("state")), "disabled")

    def test_spec_dialog(self):
        import json
        import shobs_profile as sp
        import spec_editor
        app = self.app
        app.show_page(3)
        app.profiles_page.spec_curves()
        dlg = app.profiles_page.spec_dialog
        self.assertTrue(any("built in" in lab for lab, _p, _d in dlg.items))
        # change a built-in curve: saved as the user's own copy
        dlg.curve_var.set(spec_editor.CURVES[4][1])
        dlg.show_curve()
        n = len(dlg.tree.get_children())
        self.assertGreater(n, 5)
        dlg.add_point(-25.0, 0.00006)
        self.assertEqual(len(dlg.tree.get_children()), n + 1)
        dlg.save()
        self.assertIn("own copy", dlg.msg_var.get())
        mine = sp.list_profiles("specs")
        self.assertEqual(len(mine), 1)
        self.assertTrue(any("your copy is used" in lab for lab, _p, _d in dlg.items))
        # a bad value is refused
        dlg.x_var.set("10")
        dlg.y_var.set("-1")
        dlg.add_point()
        self.assertTrue(dlg.msg_var.get().startswith("✖"))
        # export, a new spec, import
        out = self.path("export.json")
        dlg.export_file(out)
        with open(out, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["format"], sp.SPEC_FORMAT)
        dlg.new("Test Cam X (IMX000)")
        dlg.curve_var.set(spec_editor.CURVES[0][1])
        dlg.show_curve()
        dlg.add_point(0, 1.0)
        dlg.add_point(100, 0.25)
        dlg.save()
        self.assertTrue(dlg.msg_var.get().startswith("✔"), dlg.msg_var.get())
        bad = self.path("bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write('{"format": "shobs-camera-spec", "format_version": 1, "model": "X", "curves": {"gain_e_per_adu": [[1]]}}')
        dlg.import_file(bad)
        self.assertTrue(dlg.msg_var.get().startswith("✖"))
        dlg.import_file(out)
        self.assertTrue(dlg.msg_var.get().startswith("✔"), dlg.msg_var.get())
        dlg.close()

    def test_bad_folder_message(self):
        page = self.app.camera_page
        empty = self.path("empty")
        os.makedirs(empty)
        page.folder_var.set(empty)
        page.run()
        self.assertTrue(self.app.jobs.wait(30))
        self.assertTrue(page.msg_var.get().startswith("✖"), page.msg_var.get())


if __name__ == "__main__":
    unittest.main()
