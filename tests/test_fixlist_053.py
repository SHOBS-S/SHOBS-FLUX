"""FLUX 0.5.3: one system profile per telescope, whatever FOCALLEN the plate solve wrote that night.

ASIAIR writes the plate-solved focal length into FOCALLEN, so John's 14" RC reads 2898-2906 mm from night to night.
0.5.2 named the profile from the run's median focal length: a run whose median came out 2902 offered to make
ioptron-14-rc-2902mm__... beside the existing ioptron-14-rc-2903mm__... ("There is no profile ... a new one is
made", Now column all "—"), and told John to retype a name that was already right. 0.5.3 keeps the existing id when
the telescope name and camera match and the focal length is within 3 %."""
from __future__ import annotations

import copy
import os
import unittest

from tests.base import TempData

import onsky
import shobs_profile as sp
import synth_sky

CAM = synth_sky.ZWO["camera_id"]


def old_profile(name="iOptron 14 RC", focal=2903.0, zp=20.158):
    """A saved system profile like John's 1 Nov one (format-1 zero point)."""
    pid = sp.system_id(name, focal, CAM)
    doc = onsky.new_system_profile(pid, CAM, {"name": name, "aperture_mm": 356.0, "focal_mm": focal,
                                              "focal_header": focal}, "SHOBS-FLUX 0.5.0", dict(synth_sky.ZWO))
    doc["profile_id"] = pid
    doc.setdefault("bands", {})["TG"] = {"zero_point": sp.record(zp, "mag for 1 e-/s", "measured", sigma=0.01, n=1)}
    sp.save_profile(doc, "SHOBS-FLUX 0.5.0")
    return pid


class TestResolveId(TempData):
    def test_same_name_nearby_focal_keeps_the_old_id(self):
        import sky_page
        pid = old_profile()
        for f in (2898.0, 2902.0, 2906.0):
            self.assertEqual(sky_page.resolve_system_id("iOptron 14 RC", f, CAM), pid, f)
        self.assertEqual(sky_page.resolve_system_id("ioptron 14 rc", 2902.0, CAM), pid)   # slug, not spelling
        self.assertEqual(sky_page.resolve_system_id("iOptron 14 RC", 2990.0, CAM), pid)   # inside 3 %
        self.assertNotEqual(sky_page.resolve_system_id("iOptron 14 RC", 3000.0, CAM), pid)  # outside 3 %

    def test_no_focal_profile_does_not_capture(self):
        """Fable 0.5.3: a same-name profile with no focal length (a -0mm id) must not take a 2902 or reducer run."""
        import sky_page
        pid = old_profile()
        zero = old_profile(focal=None)
        self.assertEqual(sky_page.resolve_system_id("iOptron 14 RC", 2902.0, CAM), pid)
        self.assertNotEqual(sky_page.resolve_system_id("iOptron 14 RC", 2102.0, CAM), zero)
        self.assertEqual(sky_page.resolve_system_id("iOptron 14 RC", None, CAM), zero)

    def test_nearer_other_name_does_not_hide_ours(self):
        """Fable 0.5.3: Borrowed RC at 2902 is nearer to a 2901 run, but the iOptron 14 RC at 2903 is still ours."""
        import sky_page
        pid = old_profile()
        old_profile(name="Borrowed RC", focal=2902.0)
        self.assertEqual(sky_page.resolve_system_id("iOptron 14 RC", 2901.0, CAM), pid)

    def test_other_names_and_far_focal_get_their_own(self):
        import sky_page
        pid = old_profile()
        self.assertNotEqual(sky_page.resolve_system_id("Borrowed RC", 2902.0, CAM), pid)          # another scope
        self.assertNotEqual(sky_page.resolve_system_id("iOptron 14 RC", 2102.0, CAM), pid)        # the reducer
        self.assertEqual(sky_page.resolve_system_id("iOptron 14 RC", 2102.0, CAM),
                         sp.system_id("iOptron 14 RC", 2102.0, CAM))

    def test_exact_file_wins(self):
        import sky_page
        old_profile(focal=2903.0)
        exact = old_profile(focal=2902.0)                       # both exist: the exact one is used
        self.assertEqual(sky_page.resolve_system_id("iOptron 14 RC", 2902.0, CAM), exact)


class TestSaveUpdatesInPlace(TempData):
    def test_2902_run_updates_the_2903_profile(self):
        import flux_app
        import sky_page
        pid = old_profile()
        for i, (n, f) in enumerate((("2026-10-19", 2899.0), ("2026-10-20", 2902.0), ("2026-10-21", 2902.0),
                                    ("2026-10-22", 2905.0))):
            setup = copy.deepcopy(synth_sky.ZWO)
            setup["focal_header_mm"] = f
            synth_sky.write_log(synth_sky.make_log(night=n, seed=i + 1, n_frames=60, setup=setup, zp1=20.59)[0])
        app = flux_app.App(testing=True)
        try:
            app.update()
            page = app.sky_page
            page.scan_logs()
            page.name_var.set("iOptron 14 RC")
            page.aperture_var.set("356")
            page.elevation_var.set("280")
            page.run()
            self.assertTrue(app.jobs.wait(120))
            self.assertIsNotNone(page.summary, page.msg_var.get())
            self.assertEqual(page.system_id(ask=False), pid)
            page.save()
            dlg = [w for w in app.winfo_children() if isinstance(w, sky_page.SaveDialog)][-1]
            self.assertIsNotNone(dlg.old)             # "It exists: ... replace the old ones"
            dlg.ok()
            self.assertTrue(page.msg_var.get().startswith("✔"), page.msg_var.get())
        finally:
            app.destroy()
        files = sorted(os.path.basename(p) for p in sp.list_profiles("systems"))
        self.assertEqual(files, [pid + ".json"])                  # no second profile
        prof = sp.load(sp.profile_path("systems", pid, make=False))
        self.assertEqual(prof["telescope"]["focal_mm"], 2903.0)   # the id and its focal length stay put
        self.assertGreater(prof["bands"]["TG"]["zero_point"]["value"], 20.4)


if __name__ == "__main__":
    unittest.main()
