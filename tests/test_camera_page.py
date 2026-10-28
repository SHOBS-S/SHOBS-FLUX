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
