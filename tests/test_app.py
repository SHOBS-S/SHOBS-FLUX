"""The window (needs a display: run under xvfb-run on Linux): opens on Check image, checks a frame in the background
with the busy bar, saves a starting camera profile, the Profiles page shows it and takes a typed value (kept in
history), every page and Credits open."""
from __future__ import annotations

import os
import unittest
from unittest import mock

from tests.base import TempData

import synth

try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


def flux_app_rows() -> int:
    import flux_app
    return flux_app.STAR_ROWS_MAX


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestApp(TempData):
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
        self.app = None
        import gc
        gc.collect()        # free the old window here, never later in a worker thread (Tcl_AsyncDelete)
        super().tearDown()

    def test_check_save_and_profiles(self):
        import shobs_profile as sp
        app = self.app
        self.assertEqual(app.title_var.get(), "Check image")
        data, hdr = synth.make_star_frame(shape=(500, 700), stars=synth.star_list((500, 700), 12, 3.0e5))
        p = synth.write_fits(self.path("light.fit"), data, hdr)
        page = app.check_page
        page.check(p)
        self.assertTrue(app.jobs.wait(120))
        self.assertIsNotNone(page.res)
        self.assertIn("stars measured", app.status_var.get())
        self.assertGreater(len(page.cam_tree.get_children()), 5)
        self.assertTrue(page.save_btn.winfo_ismapped() or page.save_btn.winfo_manager())
        # click a star
        st = page.res.stars[0]
        class E:                                                          # noqa: N801
            inaxes = True
            xdata = st.x / page.disp_f
            ydata = st.y / page.disp_f
        # the star box is one row until a star is picked; the picture fills its holder
        self.assertEqual(int(page.star_tree.cget("height")), 1)
        app.update()
        holder_h = page.pic_holder.winfo_height()
        canvas_h = page.canvas.get_tk_widget().winfo_height()
        self.assertGreater(canvas_h, 0.9 * holder_h)
        page.on_click(E())
        self.assertIs(page.selected, st)
        n_rows = len(page.star_tree.get_children())
        self.assertGreater(n_rows, 3)
        self.assertEqual(int(page.star_tree.cget("height")), min(n_rows, flux_app_rows()))
        # the History list says why, not just the status word
        app.show_page(4)
        hist = app.pages[4].tree
        status = hist.set(hist.get_children()[0], "status")
        self.assertEqual(status, page.res.summary["worst"] or "✔ ok")
        app.show_page(0)
        # starting profile
        page.save_profile()
        self.assertTrue(app.jobs.wait(120))
        self.assertIn("Saved camera profile", page.msg_var.get())
        cam_id = page.res.cam.cam_id
        self.assertTrue(os.path.exists(sp.profile_path("cameras", cam_id)))
        self.assertEqual(sp.validate(sp.load(sp.profile_path("cameras", cam_id))), [])
        self.assertTrue(page.res.cam.profile_path)
        # profiles page: type a linear limit
        app.show_page(3)
        pp = app.profiles_page
        self.assertTrue(pp.pick_var.get().startswith("Camera:"))
        iid = next(i for i, path in pp.rec_paths.items() if path[-1] == "linear_limit_adu")
        pp.tree.selection_set(iid)
        with mock.patch("flux_app.simpledialog.askstring", return_value="32000"):
            pp.set_value()
        self.assertIn("Saved 32000", pp.msg_var.get())
        doc = sp.find_camera_profile(cam_id)
        self.assertEqual(doc["values"]["linear_limit_adu"]["value"], 32000.0)
        self.assertEqual(doc["values"]["linear_limit_adu"]["source"], "user")
        self.assertEqual(doc["revision"], 2)
        self.assertEqual(len(sp.history_of("cameras", cam_id)), 1)
        # and the next check uses it
        page.check(p)
        app.jobs.wait(120)
        self.assertEqual(page.res.cam.value("linear_limit_adu"), 32000.0)

    def test_bad_file_says_why(self):
        p = self.path("junk.fit")
        with open(p, "wb") as fh:
            fh.write(b"not a fits file" * 50)
        self.app.check_page.check(p)
        self.app.jobs.wait(60)
        self.assertTrue(self.app.check_page.msg_var.get().startswith("✖"))

    def test_every_page_opens(self):
        app = self.app
        for i in range(len(app.pages)):
            app.show_page(i)
            app.update()
        self.assertIs(app.pages[2], app.sky_page)
        self.assertIn("on-sky log", app.sky_page.logs_var.get())
        app.show_credits()
        app.update()
        self.assertEqual(app.title_var.get(), "Credits and sources")
        app.credits_page.back()
        self.assertEqual(app.title_var.get(), "History")


if __name__ == "__main__":
    unittest.main()
