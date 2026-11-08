"""FLUX 0.6.0 (milestone 6) fix list, items 7–15 (owner John, 8 Nov 2026).

 7 Copy for AstroImageJ / EXOTIC / HOPS (Profiles page): each program's own field names, every value with its
   source, "not measured yet" where the profile has none; clipboard and Save as .txt.
 8 Sky page: the nights table's headings are never cut off (100 % and 125 % scaling), with hover notes.
 9 focal_header (and TELESCOP) in a system profile follow the newest night's headers instead of the first run's.
10 History: a SAVED run's "What it read" names its on-sky log(s) and night(s), not only the onsky folder.
11 Checkboxes tick with a check mark (an image element), not the clam theme's ×.
12 Folder runs: the frame's sky is a clipped mean of the stars' rings; "not measurable" near the bias.
13 Noise terms use the sky around each star (its ring, and the electrons per unit at the star), not the frame
   median; the zero point is untouched.
14 Folder runs measure every frame; over MAX_FRAMES, runs of consecutive frames; C_Y differences true neighbours.
15 Star names: "HD219134" and "HD 219134" are one star wherever FLUX compares names."""
from __future__ import annotations

import copy
import json
import math
import os
import shutil
import unittest

import numpy as np

from tests.base import FIXTURES, TempData

import onsky
import shobs_profile as sp
import synth_sky

try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False

CAM = synth_sky.ZWO["camera_id"]


def fixtures_in():
    """The approved example camera and system profiles, copied into the test's data folder."""
    for kind in ("cameras", "systems"):
        for n in os.listdir(FIXTURES):
            if n.startswith(kind + "_"):
                shutil.copy(os.path.join(FIXTURES, n), os.path.join(sp.sub_dir(kind), n[len(kind) + 1:]))
    return (sp.load(sp.list_profiles("cameras")[0]), sp.load(sp.list_profiles("systems")[0]))


def field(text: str, name: str) -> str:
    """The rest of the export line that starts with a field name."""
    for line in text.splitlines():
        if line.strip().startswith(name):
            return line.strip()[len(name):].strip()
    raise AssertionError(f"no line for {name!r} in\n{text}")


# ---- 7 ---------------------------------------------------------------------------------------------------------------

class TestToolExport(TempData):
    def test_system_profile_blocks_in_each_programs_words(self):
        import tool_export
        cam, sysd = fixtures_in()
        t = tool_export.text(sysd)
        for head in ("AstroImageJ: Aperture Photometry Settings", "EXOTIC: initialization file", "HOPS"):
            self.assertIn(head, t)
        # AstroImageJ's own labels, in its order
        order = ["CCD gain [e-/count]", "CCD readout noise [e-]", "CCD dark current per sec [e-/pix/sec]",
                 "Saturation warning", "Linearity warning"]
        pos = [t.index(o) for o in order]
        self.assertEqual(pos, sorted(pos))
        self.assertTrue(field(t, "CCD gain [e-/count]").startswith("0.2429"))
        self.assertIn("from the FITS header", field(t, "CCD gain [e-/count]"))
        self.assertIn("manufacturer's spec", field(t, "CCD readout noise [e-]"))
        self.assertTrue(field(t, "Linearity warning: for levels higher than").startswith("32000"))
        self.assertIn("typed by you", field(t, "Linearity warning: for levels higher than"))
        self.assertTrue(field(t, "Saturation warning: for levels higher than").startswith("65535"))
        # the dark current is an estimate in this profile: said so
        self.assertIn("estimate, not measured yet", field(t, "CCD dark current per sec [e-/pix/sec]"))
        # EXOTIC: its real key for the scale; no invented gain keys
        self.assertTrue(field(t, '"Image Scale (Ex: 5.21 arcsecs/pixel)"').startswith("0.267"))
        self.assertIn('"Demosaic Format"', t)
        self.assertIn("EXOTIC asks for no gain", t)
        # HOPS
        self.assertTrue(field(t, "Camera gain in e/ADU").startswith("0.2429"))
        self.assertTrue(field(t, "Saturation limit, relatively to the full-well depth").startswith("0.49"))
        self.assertIn("÷ 65535, HOPS's limit for 16-bit files", t)
        self.assertTrue(field(t, "Binning (use 2 for coloured cameras)").startswith("2 "))   # OSC
        self.assertIn("HOPS's binning of the frames", t)
        self.assertTrue(field(t, '"Demosaic Output"').startswith("green"))
        self.assertTrue(field(t, "Telescope Name").startswith("14in RC"))
        self.assertTrue(field(t, "Camera Name").startswith("ZWO ASI2600MC Air"))
        self.assertIn("HOPS asks for no read noise", t)
        self.assertIn(f"SHOBS-FLUX {__import__('settings').APP_VERSION}", t)

    def test_bin2_plate_scale_is_the_binned_pixel(self):
        """Fable 0.6.0 #1: the profile's scale is per sensor pixel; a bin-2 camera's pixel is twice it."""
        import tool_export
        cam, sysd = fixtures_in()
        cam["mode"]["binning"] = 2
        cid = cam["profile_id"].replace("__bin1", "__bin2")
        cam["profile_id"] = cid
        sp.save_profile(cam, "test")
        sysd = dict(sysd, camera_profile=cid, profile_id=sysd["profile_id"].replace("__bin1", "__bin2"))
        sp.save_profile(sysd, "test")
        t = tool_export.text(sysd)
        self.assertTrue(field(t, '"Image Scale (Ex: 5.21 arcsecs/pixel)"').startswith("0.534"), t)
        self.assertIn("per camera pixel at 2×2 (0.267 × 2)", t)
        self.assertTrue(field(t, '"Pixel Binning"').startswith("2x2"))

    def test_mono_camera_blocks(self):
        """Fable 0.6.0 #3, #4: a mono camera: no Demosaic Output, Demosaic Format null, HOPS binning 1."""
        import tool_export
        cam, sysd = fixtures_in()
        cam["camera"]["bayer_pattern"] = None
        cam["camera"]["color"] = "mono"
        sp.save_profile(cam, "test")
        t = tool_export.text(sysd)
        self.assertTrue(field(t, '"Demosaic Format"').startswith("null"))
        self.assertNotIn('"Demosaic Output"', t)
        self.assertTrue(field(t, "Binning (use 2 for coloured cameras)").startswith("1 "))
        self.assertIn("1 for a mono camera", t)

    def test_names_not_set_and_columns_line_up(self):
        """Fable 0.6.0 #11: a missing name says "not set"; every value starts in the same column."""
        import tool_export
        cam, _sysd = fixtures_in()
        for p in sp.list_profiles("systems"):
            os.remove(p)
        t = tool_export.text(sp.load(sp.list_profiles("cameras")[0]))
        self.assertTrue(field(t, "Telescope Name").startswith("not set"))
        starts = set()
        for line in t.splitlines():
            for name in ("CCD gain [e-/count]", "Camera Name", "Camera gain in e/ADU", '"Pixel Binning"'):
                if line.strip().startswith(name):
                    starts.add(len(line) - len(line[2 + len(name):].lstrip()))
        self.assertEqual(len(starts), 1, starts)
        srcs = set()
        for line in t.splitlines():
            for name, value in (("Camera Name", "ZWO ASI2600MC Air"), ('"Pixel Binning"', "1x1")):
                if line.strip().startswith(name):
                    srcs.add(line.index(value) + len(value) + len(line[line.index(value) + len(value):])
                             - len(line[line.index(value) + len(value):].lstrip()))
        self.assertEqual(len(srcs), 1, srcs)

    def test_missing_values_are_not_measured_yet(self):
        import tool_export
        cam, _sysd = fixtures_in()
        for k in ("gain_e_per_adu", "read_noise_e", "linear_limit_adu"):
            cam["values"][k] = sp.record(None, cam["values"][k]["unit"], "none")
        sp.save_profile(cam, "test")
        for p in sp.list_profiles("systems"):
            os.remove(p)
        t = tool_export.text(sp.load(sp.list_profiles("cameras")[0]))
        for name in ("CCD gain [e-/count]", "CCD readout noise [e-]", "Linearity warning: for levels higher than",
                     "Camera gain in e/ADU", "Saturation limit, relatively to the full-well depth",
                     '"Image Scale (Ex: 5.21 arcsecs/pixel)"', "Star FWHM in arcseconds (approximate)"):
            self.assertTrue(field(t, name).startswith(tool_export.NOT_YET), (name, field(t, name)))
        self.assertIn("a camera alone has no telescope", t)

    def test_camera_profile_finds_its_one_system_for_the_scale(self):
        import tool_export
        cam, _sysd = fixtures_in()
        t = tool_export.text(cam)
        self.assertTrue(field(t, '"Image Scale (Ex: 5.21 arcsecs/pixel)"').startswith("0.267"))

    def test_measured_dark_read_at_the_profile_temperature(self):
        import tool_export
        cam, sysd = fixtures_in()
        cam["values"]["dark_current_e_per_px_s"] = sp.record(0.004, "e-/px/s", "measured", conditions="-20 °C")
        cam["dark"] = {"by_temp": [{"temp_c": -20.0, "value": 0.004, "sigma": 0.0002, "upper_limit": False}],
                       "source": "measured", "date": "2026-11-01"}
        cam["mode"]["set_temp_c"] = -10
        sp.save_profile(cam, "test")
        v = tool_export.values(sysd)
        self.assertGreater(v["dark"][0], 0.004 * 1.5)                 # read 10 °C warmer: several times more
        self.assertIn("-10", v["dark"][1])
        self.assertIn("worked out", v["dark"][1])
        self.assertIn("measured 0.004 at -20 °C", v["dark"][1])
        cam["mode"]["set_temp_c"] = -20                                  # at its own temperature: the measurement
        sp.save_profile(cam, "test")
        v = tool_export.values(sysd)
        self.assertEqual(v["dark"][0], 0.004)
        self.assertTrue(v["dark"][1].startswith("measured"))

    @unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
    def test_profiles_page_button_copies_and_saves(self):
        import flux_app
        fixtures_in()
        app = flux_app.App(testing=True)
        try:
            app.update()
            app.show_page(3)
            page = app.profiles_page
            self.assertEqual(page.export_btn.cget("text"), "Copy for AstroImageJ / EXOTIC / HOPS…")
            sys_name = next(n for n in page.paths if n.startswith("System:"))
            page.pick_var.set(sys_name)
            page.show()
            page.copy_for_tools()
            app.update()
            self.assertTrue(page.msg_var.get().startswith("✔ Copied"), page.msg_var.get())
            self.assertIn("CCD gain [e-/count]", app.clipboard_get())
            first = page.export_dialog
            page.copy_for_tools()                                       # Fable 0.6.0 #9: one window at a time
            app.update()
            self.assertFalse(first.winfo_exists())
            dlg = page.export_dialog
            out = self.path("export.txt")
            self.assertTrue(dlg.save_as(out))
            with open(out, encoding="utf-8") as fh:
                self.assertIn("Camera gain in e/ADU", fh.read())
            dlg.destroy()
            spec_name = next((n for n in page.paths if n.startswith("Spec:")), None)
            if spec_name:
                page.pick_var.set(spec_name)
                page.show()
                page.copy_for_tools()
                self.assertIn("Pick a camera or system profile", page.msg_var.get())
        finally:
            app.destroy()


# ---- 8 and 11 --------------------------------------------------------------------------------------------------------

@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestHeadingsAndCheckboxes(TempData):
    def _sky_tree(self, scaling: float):
        import style
        import flux_app
        orig = style.setup

        def scaled(root):
            root.tk.call("tk", "scaling", scaling)
            return orig(root)
        style.setup = scaled
        try:
            app = flux_app.App(testing=True)
        finally:
            style.setup = orig
        return app

    def test_sky_headings_fit_at_100_and_125_percent(self):
        import style
        from tkinter import font as tkfont
        for scaling in (1.3333, 1.6667):              # Windows 100 % and 125 %
            app = self._sky_tree(scaling)
            try:
                app.update()
                tree = app.sky_page.tree
                bold = tkfont.Font(tree, font=style.font(10, "bold"))
                for c in tree["columns"]:
                    head = tree.heading(c, "text")
                    self.assertGreaterEqual(int(tree.column(c, "width")), bold.measure(head) + style.px(tree, 8),
                                            (scaling, head))
                    self.assertGreaterEqual(int(tree.column(c, "minwidth")), bold.measure(head), (scaling, head))
                tips = app.sky_page.head_tips.tips
                self.assertEqual(set(tips), set(tree["columns"]))
                self.assertIn("arcsecond", tips["sky"])
            finally:
                app.destroy()

    def test_heading_tip_shows_and_goes(self):
        import flux_app
        app = flux_app.App(testing=True)
        try:
            app.update()
            ht = app.sky_page.head_tips
            ht.show("cy", 10, 10)
            self.assertIsNotNone(ht.tip)
            ht._hide()
            self.assertIsNone(ht.tip)
        finally:
            app.destroy()

    def test_checkbox_draws_a_check_mark(self):
        import style
        import flux_app
        app = flux_app.App(testing=True)
        try:
            app.update()
            from tkinter import ttk
            st = ttk.Style(app)
            self.assertIn(style.CHECK_ELEMENT, st.element_names())
            lay = json.dumps(st.layout("Card.TCheckbutton"))
            self.assertIn(style.CHECK_ELEMENT, lay)
            self.assertNotIn('"Checkbutton.indicator"', lay)
            on = app._shobs_check_images[(True, False)]
            off = app._shobs_check_images[(False, False)]
            n = on.height()
            # the check's corner (lower middle) is white on the navy box; the unticked box is white inside, no mark
            self.assertEqual(on.get(int(0.42 * n), int(0.70 * n)), (255, 255, 255))
            fill = tuple(int(style.CHECK_FILL[i:i + 2], 16) for i in (1, 3, 5))
            self.assertEqual(on.get(2, 2), fill)
            self.assertEqual(off.get(int(0.42 * n), int(0.70 * n)), (255, 255, 255))
            self.assertEqual(off.get(int(0.79 * n) - 1, int(0.3 * n)), (255, 255, 255))
            box = app.history_page.removed_box
            self.assertEqual(str(box.cget("style")), "Card.TCheckbutton")
        finally:
            app.destroy()


# ---- 9 ---------------------------------------------------------------------------------------------------------------

class TestFocalHeaderFresh(TempData):
    def _summary(self, focal, night, seed, telescop="EQMod Mount"):
        setup = copy.deepcopy(synth_sky.ZWO)
        setup["focal_header_mm"] = focal
        setup["telescop_header"] = telescop
        doc, t = synth_sky.make_log(night=night, seed=seed, n_frames=60, setup=setup)
        ds = onsky.load_dataset(doc, cam_lookup=lambda c: None)
        return ds, onsky.run_all([ds], None, 356.0, 300.0)

    def test_existing_profile_follows_the_new_header(self):
        import sky_page
        ds1, s1 = self._summary(2898.0, "2026-10-19", 1)
        pid = sp.system_id("iOptron 14 RC", 2903, CAM)
        tel = {"name": "iOptron 14 RC", "aperture_mm": 356.0, "focal_mm": 2903,
               "focal_header": sky_page.focal_header_of([ds1]), "telescop_header": "EQMod Mount"}
        doc = onsky.apply_to_profile(s1, None, "test", system_id=pid, cam_id=CAM, telescope=tel, setup=ds1.setup)
        self.assertEqual(doc["telescope"]["focal_header"], 2898.0)
        ds2, s2 = self._summary(2906.0, "2026-10-22", 2, telescop="iOptron CEM120")
        heads = sky_page.focal_header_of([ds1, ds2])
        self.assertEqual(heads, 2906.0)                                  # the newest night's header
        tel2 = dict(tel, focal_header=heads, telescop_header="iOptron CEM120")
        doc2 = onsky.apply_to_profile(s2, doc, "test", system_id=pid, cam_id=CAM, telescope=tel2, setup=ds2.setup)
        self.assertEqual(doc2["telescope"]["focal_header"], 2906.0)      # was stuck at 2898 before 0.6.0
        self.assertEqual(doc2["telescope"]["telescop_header"], "iOptron CEM120")
        self.assertEqual(doc2["telescope"]["focal_mm"], 2903)            # the id's focal length stays
        # Fable 0.6.0 #12: the Save preview lists the header's change
        ch = onsky.changes_for(s2, doc, tel2)
        self.assertIn(("Telescope: Focal length in the header", 2898.0, "header", 2906.0, "mm", 0), ch)
        self.assertTrue(any(c[0] == "Telescope: TELESCOP in the header" and c[3] == "iOptron CEM120" for c in ch))
        self.assertFalse(any(c[0].startswith("Telescope:") for c in onsky.changes_for(s2, doc2, tel2)))
        self.assertFalse(any(c[0].startswith("Telescope:") for c in onsky.changes_for(s2, None, tel2)))
        doc3 = onsky.apply_to_profile(s2, doc2, "test", system_id=pid, cam_id=CAM,
                                      telescope=dict(tel2, focal_header=None, telescop_header=None))
        self.assertEqual(doc3["telescope"]["focal_header"], 2906.0)      # no header: nothing is wiped

    def test_folder_focal_is_the_median_of_its_lights(self):
        import sky_folder
        import synth_lights
        folder = self.path("lights")
        truth = synth_lights.make_folder(folder, n_frames=12, extra_files=False)
        from astropy.io import fits
        for i, p in enumerate(truth["files"]):                          # ASIAIR's FOCALLEN wobble: first light off
            with fits.open(p, mode="update") as hl:
                hl[0].header["FOCALLEN"] = 2860.0 if i == 0 else 2900.0 + (i % 3)
        res = sky_folder.run_folder(folder, catalog=truth["fetch"], cam_lookup=lambda c: None)
        self.assertEqual(res.doc["setup"]["focal_header_mm"], 2901.0)


# ---- 10 --------------------------------------------------------------------------------------------------------------

class TestSavedRunWords(TempData):
    def test_new_saved_run_names_logs_and_nights(self):
        import history_page
        onsky_dir = sp.onsky_dir()
        row = {"kind": "sky", "source": os.path.join(onsky_dir, "2026-10-22__hd-219134__b.json"),
               "summary": json.dumps({"logs": ["2026-10-22__hd-219134__b.json", "2026-10-21__hd-219134__a.json",
                                               "2026-10-19__wasp-12__c.json"],
                                      "night_list": ["2026-10-19", "2026-10-21", "2026-10-22"]})}
        w = history_page.read_words(row)
        self.assertTrue(w.startswith(os.path.join(onsky_dir, "2026-10-22__hd-219134__b.json")), w)
        self.assertIn("and 2 more logs", w)
        self.assertIn("3 nights, 2026-10-19 to 2026-10-22", w)
        one = dict(row, summary=json.dumps({"logs": ["2026-10-22__hd-219134__b.json"], "night_list": ["2026-10-22"]}))
        self.assertTrue(history_page.read_words(one).endswith("· night 2026-10-22"))

    def test_folder_saved_run(self):
        import history_page
        row = {"kind": "sky", "source": self.path("lights"),
               "summary": json.dumps({"logs": ["2026-10-21__m-31__flux-20261108T1200-ab12.json",
                                               "2026-10-19__m-31__x.json"],
                                      "night_list": ["2026-10-19", "2026-10-21"]})}
        w = history_page.read_words(row)
        self.assertIn(self.path("lights"), w)
        self.assertIn("its log 2026-10-21__m-31__flux-", w)
        self.assertIn("and 1 more log ·", w)

    def test_old_saved_run_finds_its_nights(self):
        import history_page
        d = sp.onsky_dir()
        old = {"kind": "sky", "source": d, "started": "2026-11-01T21:14:05-04:00", "system_id": "s1",
               "summary": json.dumps({"nights": 2})}
        nights = [{"kind": "onsky", "source": os.path.join(d, f"2026-10-{n}__hd-219134__x.json"),
                   "started": f"2026-11-01T21:14:0{i}-04:00", "system_id": "s1",
                   "summary": json.dumps({"night": f"2026-10-{n}"})} for i, n in enumerate((19, 20))]
        w = history_page.read_words(old, nights + [old])
        self.assertIn("2026-10-19__hd-219134__x.json and 1 more log", w)
        self.assertIn("2 nights, 2026-10-19 to 2026-10-20", w)
        lone = history_page.read_words(old, [old])
        self.assertIn("did not keep which ones", lone)
        self.assertEqual(history_page.read_words({"kind": "onsky", "source": "x.json"}), "x.json")

    def test_sky_summary_keeps_logs_and_saved_source(self):
        import sky_page
        paths = []
        for i, n in enumerate(("2026-10-19", "2026-10-21")):
            paths.append(synth_sky.write_log(synth_sky.make_log(night=n, seed=i + 1, n_frames=50)[0]))
        ds, _bad = onsky.read_logs(paths, cam_lookup=lambda c: None)
        s = onsky.run_all(ds, None, 356.0, 300.0)
        summ = sky_page.sky_summary(s)
        self.assertEqual(summ["logs"][0], os.path.basename(paths[1]))     # newest first
        self.assertEqual(summ["night_list"], ["2026-10-19", "2026-10-21"])
        res = sky_page.SkyRun()
        res.summary, res.datasets = s, ds
        self.assertEqual(sky_page.saved_source(res), paths[1])
        res.source, res.folder = "folder", self.path("lights")
        self.assertEqual(sky_page.saved_source(res), self.path("lights"))


# ---- 12 and 14 (folder runs) -----------------------------------------------------------------------------------------

def _profile_with_bias(bias=500.0):
    p = synth_sky.camera_profile(gain=0.25, read_noise=1.5)
    p["values"]["bias_level_adu"] = sp.record(bias, "ADU", "measured")
    return p


class TestFolderSky(TempData):
    def test_frame_sky_clipped_mean_and_not_measurable(self):
        import sky_folder
        rings = [600.0, 601.0, 599.0, 600.5, 599.5, 900.0]               # one ring with a faint star in it
        sky, why = sky_folder.frame_sky(rings, 500.0, 2)
        self.assertEqual(why, "")
        self.assertAlmostEqual(sky, 100.0, places=6)                     # mean of the five, the 900 clipped
        # Fable 0.6.0 #5: whole-ADU ring medians (robust sigma 0) still clip an outlier
        tied, _w = sky_folder.frame_sky([505, 505, 505, 505, 506, 505, 540], 500.0, 1)
        self.assertAlmostEqual(tied, 5.1667, places=3)
        self.assertEqual(sky_folder.frame_sky(rings + [float("nan"), None], 500.0, 2)[0], sky)
        # less than SKY_MIN_ADU per camera pixel above the bias (2 pixels summed here): not measurable
        near = [508.0, 509.0, 508.5]
        self.assertEqual(sky_folder.frame_sky(near, 500.0, 2), (None, "not measurable"))
        self.assertEqual(sky_folder.frame_sky([495.0, 496.0, 494.0], 500.0, 1), (None, "not measurable"))
        self.assertEqual(sky_folder.frame_sky([], 500.0, 1), (None, ""))
        self.assertEqual(sky_folder.SKY_MIN_ADU, 5.0)

    def test_sky_too_close_to_the_bias_is_said_not_a_number(self):
        import sky_folder
        import synth_lights
        folder = self.path("lights")
        truth = synth_lights.make_folder(folder, n_frames=12, extra_files=False, sky_mag=25.5)
        res = sky_folder.run_folder(folder, catalog=truth["fetch"], cam_lookup=lambda c: _profile_with_bias())
        skies = [f["sky"] for f in res.doc["frames"]]
        self.assertTrue(all(s is None for s in skies), skies)
        line = next(t for k, t in res.lines if t.startswith("Sky not measurable"))
        self.assertIn("less than 5 ADU per camera pixel above the bias", line)
        self.assertIn("no sky brightness", line)
        night = onsky.analyse(onsky.load_dataset(res.doc, cam_lookup=lambda c: _profile_with_bias()),
                              aperture_mm=356.0, elevation_m=300.0)
        self.assertTrue(math.isnan(night.sky_mag))
        self.assertAlmostEqual(night.zp1, truth["zp_e"], delta=0.03)       # the zero point is not touched

    def test_normal_sky_still_measured_and_per_star_rings_written(self):
        import sky_folder
        import synth_lights
        folder = self.path("lights")
        truth = synth_lights.make_folder(folder, n_frames=12, extra_files=False)
        res = sky_folder.run_folder(folder, catalog=truth["fetch"], cam_lookup=lambda c: _profile_with_bias())
        self.assertTrue(all(f["sky"] is not None and f["sky"] > 0 for f in res.doc["frames"]))
        f0 = res.doc["frames"][0]
        rings = [m["sky"] for m in f0["stars"].values() if m.get("sky") is not None]
        self.assertGreater(len(rings), 5)
        self.assertAlmostEqual(float(np.median(rings)), f0["sky"], delta=0.05 * f0["sky"])
        night = onsky.analyse(onsky.load_dataset(res.doc, cam_lookup=lambda c: _profile_with_bias()))
        self.assertAlmostEqual(night.sky_mag, truth["sky_mag"], delta=0.1)
        self.assertFalse(any(t.startswith("Sky not measurable") for _k, t in res.lines))


class TestEveryFrame(TempData):
    def test_consecutive_runs(self):
        import sky_folder
        self.assertEqual(sky_folder.consecutive_runs(500, 600), [(0, 500)])
        runs = sky_folder.consecutive_runs(2000, 600)
        self.assertEqual(sum(b - a for a, b in runs), 600)
        self.assertEqual(len(runs), 12)
        self.assertTrue(all(b - a == 50 for a, b in runs))
        self.assertEqual(runs[0][0], 0)
        self.assertEqual(runs[-1][1], 2000)                               # spread to the night's end
        self.assertTrue(all(r[1] <= q[0] for r, q in zip(runs, runs[1:])))  # never overlapping
        small = sky_folder.consecutive_runs(30, 12)
        self.assertEqual(sum(b - a for a, b in small), 12)

    def test_default_measures_every_frame(self):
        import inspect
        import sky_folder
        self.assertEqual(inspect.signature(sky_folder.run_folder).parameters["max_frames"].default, 600)
        self.assertEqual(sky_folder.MAX_FRAMES, 600)

    def test_a_capped_folder_is_measured_in_consecutive_runs(self):
        import sky_folder
        import synth_lights
        folder = self.path("lights")
        truth = synth_lights.make_folder(folder, n_frames=40, cadence_s=70.0, exptime=60.0, extra_files=False)
        res = sky_folder.run_folder(folder, catalog=truth["fetch"], cam_lookup=lambda c: None, max_frames=20)
        self.assertEqual(res.n_frames, 20)
        jd = sorted(f["jd_mid"] for f in res.doc["frames"])
        steps = np.round(np.diff(jd) * 86400.0)
        self.assertGreaterEqual(int(np.sum(np.abs(steps - 70.0) < 1)), 18)  # true neighbours, not every 2nd
        line = next(t for _k, t in res.lines if "consecutive" in t)
        self.assertIn("40 light frames, more than 20", line)
        self.assertIn("C_Y", line)
        self.assertAlmostEqual(res.doc["cadence"]["interval_s"], 70.0, delta=0.5)   # from all the lights

    def test_cy_differences_only_true_neighbours(self):
        doc, truth = synth_sky.make_log(n_frames=300, seed=4, cy=1.5, exptime=30.0, mags=np.linspace(9, 12, 12))
        cam = synth_sky.camera_profile()
        full = onsky.analyse(onsky.load_dataset(doc, cam_lookup=lambda c: cam), aperture_mm=356, elevation_m=300)
        runs = copy.deepcopy(doc)
        runs["frames"] = [f for i, f in enumerate(doc["frames"]) if (i // 30) % 3 == 0]   # runs with gaps
        part = onsky.analyse(onsky.load_dataset(runs, cam_lookup=lambda c: cam), aperture_mm=356, elevation_m=300)
        self.assertTrue(math.isfinite(full.cy) and math.isfinite(part.cy))
        self.assertAlmostEqual(part.cy, truth["cy"], delta=0.4)


# ---- 13 --------------------------------------------------------------------------------------------------------------

class TestStarLocalSky(TempData):
    def _log(self, spread=0.08, seed=1):
        doc, truth = synth_sky.make_log(seed=seed, n_frames=80, sky_mag=17.0, mags=np.linspace(9, 15, 12))
        rng = np.random.default_rng(seed)
        fac = {n: 1.0 + rng.uniform(-spread, spread) for n in truth["names"]}
        for f in doc["frames"]:
            for n, m in f["stars"].items():
                k = fac.get(n, 1.0)
                m["flux"] = round(m["flux"] / k, 3)
                m["e_per_unit"] = round(m["e_per_unit"] * k, 4)
        return doc, truth, fac

    def test_noise_uses_the_scale_at_each_star(self):
        doc, truth, fac = self._log()
        cam = synth_sky.camera_profile()
        ds = onsky.load_dataset(doc, cam_lookup=lambda c: cam)
        sky_e = ds.noise_sky_e()
        self.assertEqual(sky_e.shape, ds.flux.shape)
        for j, n in enumerate(ds.names):
            want = ds.sky * ds.e_star[:, j]
            np.testing.assert_allclose(sky_e[:, j], want, rtol=1e-9)
        # a star where the flat is 8 % high has 8 % more sky electrons than the frame median says
        j = int(np.argmax([fac[n] for n in ds.names]))
        ratio = float(np.median(sky_e[:, j] / (ds.sky * ds.e_per_unit)))
        self.assertAlmostEqual(ratio, fac[ds.names[j]], delta=0.002)

    def test_star_rings_win_and_zero_point_is_untouched(self):
        doc, truth, _fac = self._log()
        doc["measurement"]["flux_is_adu_sum"] = True          # a FLUX folder run's log: its rings are read
        cam = synth_sky.camera_profile()
        base = onsky.analyse(onsky.load_dataset(doc, cam_lookup=lambda c: cam), aperture_mm=356, elevation_m=300)
        rings = copy.deepcopy(doc)
        for f in rings["frames"]:
            for n, m in f["stars"].items():
                m["sky"] = round(f["sky"] * 3.0, 3)                     # a much brighter sky around every star
        ds2 = onsky.load_dataset(rings, cam_lookup=lambda c: cam)
        self.assertIsNotNone(ds2.star_sky)
        np.testing.assert_allclose(ds2.noise_sky_e()[:, 0], 3.0 * ds2.sky * ds2.star_e()[:, 0], rtol=1e-3)
        r2 = onsky.analyse(ds2, aperture_mm=356, elevation_m=300)
        self.assertEqual(r2.zp1, base.zp1)                                # the zero point does not use it
        self.assertEqual(r2.sky_mag, base.sky_mag)                        # nor the sky brightness
        exp_base = onsky._expected_mag_noise(onsky.load_dataset(doc, cam_lookup=lambda c: cam))
        exp_ring = onsky._expected_mag_noise(ds2)
        self.assertTrue(np.all(exp_ring[np.isfinite(exp_ring)] > exp_base[np.isfinite(exp_base)]))

    def test_unknown_sky_is_left_out_not_zero(self):
        """Fable 0.6.0 #7: a null sky is NaN in the noise terms (left out of the medians), never 0 e⁻."""
        doc, _t, _f = self._log()
        for i, f in enumerate(doc["frames"]):
            if i % 2:
                f["sky"] = None
        ds = onsky.load_dataset(doc, cam_lookup=lambda c: synth_sky.camera_profile())
        sky_e = ds.noise_sky_e()
        self.assertTrue(np.all(np.isnan(sky_e[1::2])))
        self.assertTrue(np.all(np.isfinite(sky_e[0::2])))
        self.assertTrue(np.all(np.isfinite(onsky._expected_mag_noise(ds))))

    def test_star_rings_only_from_flux_folder_logs(self):
        """Fable 0.6.0 #6: a per-star "sky" in a SHOBS-P log (other units, some day) is not used."""
        doc, _t, _f = self._log()
        for f in doc["frames"]:
            for m in f["stars"].values():
                m["sky"] = 999.0
        ds = onsky.load_dataset(doc, cam_lookup=lambda c: synth_sky.camera_profile())
        self.assertIsNone(ds.star_sky)
        doc["measurement"]["flux_is_adu_sum"] = True
        self.assertIsNotNone(onsky.load_dataset(doc, cam_lookup=lambda c: synth_sky.camera_profile()).star_sky)


# ---- 15 --------------------------------------------------------------------------------------------------------------

class TestStarNames(TempData):
    def test_digit_hyphens_count(self):
        """Fable 0.6.0 #8: a separator between two digits is part of the name."""
        self.assertFalse(onsky.same_name("TYC 123-4567-1", "TYC 1234-567-1"))
        self.assertTrue(onsky.same_name("TYC 123-4567-1", "TYC 123 4567 1"))
        self.assertTrue(onsky.same_name("GSC 02652-01324", "GSC 02652 01324"))
        self.assertFalse(onsky.same_name("GSC 02652-01324", "GSC 0265-201324"))
        self.assertTrue(onsky.same_name("HD219134", "HD 219134") and onsky.same_name("HD 219134", "hd-219134"))

    def test_name_key(self):
        for a, b in (("HD219134", "HD 219134"), ("hd 219134", "HD219134"), ("HD-219134", "HD_219134"),
                     ("WASP 12", "WASP-12"), ("HAT-P-32", "HAT P 32")):
            self.assertTrue(onsky.same_name(a, b), (a, b))
        for a, b in (("HD 219134", "HD 219135"), ("BD+39 4529", "BD 39 4529"), ("TOI 1234.01", "TOI 123401")):
            self.assertFalse(onsky.same_name(a, b), (a, b))
        self.assertFalse(onsky.same_name("", ""))

    def test_a_rerun_spelled_differently_replaces_the_first(self):
        a, _ = synth_sky.make_log(night="2026-10-20", target="HD219134", seed=1, n_frames=40,
                                  written="2026-10-20T23:00:00-04:00")
        b, _ = synth_sky.make_log(night="2026-10-20", target="HD 219134", seed=2, n_frames=40,
                                  written="2026-10-21T08:00:00-04:00")
        ds = [onsky.load_dataset(d, cam_lookup=lambda c: None) for d in (a, b)]
        kept, replaced = onsky.newest_per_night(ds)
        self.assertEqual([d.target for d in kept], ["HD 219134"])
        self.assertEqual([d.target for d in replaced], ["HD219134"])

    def test_the_target_spelled_as_a_field_star_is_left_out(self):
        doc, truth = synth_sky.make_log(target="HD219134", seed=1, n_frames=30)
        doc["stars"].append({"name": "HD 219134", "role": "comp", "catalog_mag": 5.57})
        doc["stars"].append({"name": "field 1", "role": "spare", "catalog_mag": 9.0})   # Field 1 again
        for f in doc["frames"]:
            f["stars"]["HD 219134"] = dict(f["stars"]["HD219134"])
        ds = onsky.load_dataset(doc, cam_lookup=lambda c: None)
        self.assertNotIn("HD 219134", ds.names)
        self.assertEqual(ds.names.count("Field 1") + ds.names.count("field 1"), 1)
        self.assertEqual(len(ds.names), len(truth["names"]))

    def test_frame_star_spelled_differently_is_read(self):
        doc, truth = synth_sky.make_log(seed=1, n_frames=30)
        for f in doc["frames"]:
            f["stars"]["FIELD-1"] = f["stars"].pop("Field 1")
        ds = onsky.load_dataset(doc, cam_lookup=lambda c: None)
        j = ds.names.index("Field 1")
        self.assertTrue(np.all(np.isfinite(ds.flux[:, j])))

    def test_report_counts_one_night_per_spelling(self):
        import history
        import report
        from tests import m5_data
        d = m5_data.make(self.tmp)
        real = history.series_points

        def two_spellings(*a, **kw):
            pts = real(*a, **kw)
            for i, p in enumerate(pts):
                p["date"], p["target"] = "2026-10-20", ("HD219134" if i % 2 else "HD 219134")
            return pts
        history.series_points = two_spellings
        try:
            info = report.gather(d["system_path"])
        finally:
            history.series_points = real
        self.assertEqual(len(info["nights"]), 1)


class TestCitation(unittest.TestCase):
    def test_shobs_p_concept_doi(self):
        """SHOBS-P is cited by its concept DOI (every version), not the v2.2.16 record."""
        import credits
        text = credits.credits_text()
        self.assertIn("10.5281/zenodo.23417063", text)
        self.assertNotIn("23417064", text)
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        for name in os.listdir(here):
            if name.endswith((".py", ".md", ".cff")):
                with open(os.path.join(here, name), encoding="utf-8") as fh:
                    self.assertNotIn("23417064", fh.read(), name)


if __name__ == "__main__":
    unittest.main()
