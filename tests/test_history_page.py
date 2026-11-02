"""0.5 window tests (need a display: xvfb-run on Linux): the History page plots, exports and takes a run off; the
system report window makes a PDF and keeps the optics typed in it; the Profiles page rolls a profile back."""
from __future__ import annotations

import os
import unittest

from tests.base import TempData

try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestHistoryWindow(TempData):
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

    def test_history_page(self):
        app = self.app
        app.show_page(4)
        app.update()
        hp = app.history_page
        self.assertEqual(app.title_var.get(), "History")
        self.assertTrue(hp.setup_box.get().startswith("14in RC"))
        self.assertTrue(hp.value_box.get().startswith("Zero point at airmass 1"))
        self.assertEqual(len(hp.points), 2)
        self.assertEqual(len(hp.run_rows), 3)
        hp.event_var.set("19 Oct 2026")
        hp.typed_dates()
        self.assertIn("change", hp.msg_var.get())
        hp.event_var.set("someday")
        hp.typed_dates()
        self.assertIn("is not a date", hp.msg_var.get())
        hp.say("")
        hp.typed_dates()                                       # leaving the box again, nothing changed: no new warning
        self.assertEqual(hp.msg_var.get(), "")
        hp.event_var.set("")
        hp.range_var.set("These dates")
        hp.from_var.set("2026-10-19")
        hp.typed_dates()
        self.assertEqual([p["date"] for p in hp.points], ["2026-10-19"])
        hp.range_var.set("All dates")
        hp.pick_range()
        self.assertEqual(len(hp.points), 2)
        png, csv_path = self.path("t.png"), self.path("t.csv")
        self.assertTrue(hp.save_png(png))
        self.assertGreater(os.path.getsize(png), 10000)
        self.assertTrue(hp.save_csv(csv_path))
        # take the first night off, then put it back
        iid = next(i for i, r in hp.run_rows.items() if "2026-10-18" in (r["source"] or ""))
        hp.tree.selection_set(iid)
        app.update()
        hp.take_off(True)
        self.assertEqual(len(hp.run_rows), 2)
        self.assertIn("(1 point)", hp.value_box.get())
        hp.removed_var.set(True)
        hp.show_runs()
        iid = next(i for i, r in hp.run_rows.items() if r["removed"])
        hp.tree.selection_set(iid)
        app.update()
        hp.take_off(False)
        self.assertIn("(2 points)", hp.value_box.get())
        # a camera setup: no trend yet (no bench run in the history)
        hp.setup_box.current(1)
        hp.pick_setup()
        self.assertEqual(hp.points, [])

    def test_report_window(self):
        import shobs_profile as sp
        app = self.app
        app.show_page(4)
        hp = app.history_page
        hp.open_report()
        app.update()
        dlg = hp.report_dialog
        self.assertTrue(dlg.sys_box.get().startswith("14in RC"))
        dlg.opt_vars["mirror_reflectivity"].set("120%")
        dlg.make(self.path("x.pdf"))
        self.assertIn("outside", dlg.msg_var.get())
        dlg.opt_vars["mirror_reflectivity"].set("88%")
        dlg.opt_vars["mirrors"].set("2")
        from unittest import mock
        with mock.patch("history_page.filedialog.asksaveasfilename", return_value=""):
            dlg.make()                                        # the save box cancelled: the profile is untouched
        self.assertEqual(sp.load(self.d["system_path"])["revision"], 1)
        dlg.event_var.set("19 Oct 2026")
        dlg.share_var.set(True)
        out = self.path("report.pdf")
        dlg.make(out)
        self.assertTrue(app.jobs.wait(180))
        app.update()
        self.assertIn("✔ Saved", dlg.msg_var.get())
        self.assertTrue(os.path.getsize(out) > 20000)
        tel = sp.load(self.d["system_path"])["telescope"]
        self.assertEqual(tel["mirror_reflectivity"], 0.88)
        self.assertEqual(tel["mirrors"], 2)
        self.assertEqual(sp.load(self.d["system_path"])["revision"], 2)     # the optics saved as a new revision
        dlg.make(self.path("again.pdf"))                                   # nothing changed: no new revision
        self.assertTrue(app.jobs.wait(180))
        self.assertEqual(sp.load(self.d["system_path"])["revision"], 2)
        dlg.destroy()

    def test_profiles_roll_back(self):
        import shobs_profile as sp
        app = self.app
        app.show_page(3)
        pp = app.profiles_page
        pp.pick_var.set(next(n for n in pp.pick.cget("values") if n.startswith("Camera")))
        pp.show()
        pp.roll_back()
        app.update()
        dlg = pp.roll_dialog
        self.assertEqual([r["revision"] for r in dlg.revs], [1])
        self.assertIn("Gain:", dlg.text.get("1.0", "end"))           # 0.5.1: plain names, not values.gain_e_per_adu
        self.assertNotIn("gain_e_per_adu", dlg.text.get("1.0", "end"))
        dlg.put_back()
        app.update()
        self.assertIn("Revision 1 put back as revision 3", pp.msg_var.get())
        self.assertIn("rolled back to revision 1", pp.info_var.get())
        self.assertEqual(sp.load(self.d["camera_path"])["values"]["gain_e_per_adu"]["source"], "header")
        pp.pick_var.set(next(n for n in pp.pick.cget("values") if n.startswith("System")))
        pp.show()
        pp.roll_back()
        self.assertIn("no earlier revision", pp.msg_var.get())
