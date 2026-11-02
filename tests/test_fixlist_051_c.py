"""0.5.1 fix list, builder C (John, 1 Nov 2026 + M5 leftovers): the busy bar is SHOBS-P's green everywhere; the
report works out the camera QE in the band from the maker's QE curve; report numbers with thousands separators,
short source words, '0.4 (assumed)' and '0 mirrors (…)'; a shared copy's PDF dates in UTC, a single on-sky date;
Roll back with plain names, separators and the revision in use; worked-out values re-derived on save; whole paths
(spaces too) scrubbed from a shared copy."""
from __future__ import annotations

import glob
import math
import os
import re
import unittest
from datetime import datetime, timedelta, timezone

from tests.base import APP, TempData

import shobs_profile as sp

try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


def _andor(**vals) -> dict:
    """A starting camera profile like the Moeller Andor's revision 1 (0.5.0 wrote it)."""
    now = sp.now_iso()
    v = {"gain_e_per_adu": sp.record(1.0, "e-/ADU", "estimate", note="1 e-/ADU assumed"),
         "read_noise_e": sp.record(3.0, "e-", "estimate"),
         "full_well_e": sp.record(65535, "e-", "derived", note="saturation × gain"),
         "saturation_adu": sp.record(65535.0, "ADU", "estimate", note="the largest value the file can hold"),
         "linear_limit_adu": sp.record(52428, "ADU", "estimate",
                                       note="80% of saturation until a bench run measures it"),
         "bias_level_adu": sp.record(None, "ADU", "none"),
         "qe_peak": sp.record(None, "fraction", "none")}
    v.update(vals)
    return {"format": sp.CAMERA_FORMAT, "format_version": 1, "profile_id": "andor-ccd__g1__ona__bin1", "revision": 1,
            "written_by": "test", "created": now, "updated": now,
            "camera": {"instrume": "Andor CCD", "color": "mono", "bayer_pattern": None},
            "mode": {"gain_setting": 1.0, "offset": None, "binning": 1}, "values": v}


# ---- (5) busy bar ---------------------------------------------------------------------------------------------------

class TestBusyBarGreen(unittest.TestCase):
    def test_constant(self):
        import style
        self.assertEqual(style.BUSY_GREEN.lower(), "#4caf50")

    def test_no_blue_busy_bar_anywhere_in_the_code(self):
        """Every Progressbar style is configured with BUSY_GREEN, and no busy-bar line uses a blue (OK, #1f5f99…)."""
        with open(os.path.join(APP, "style.py"), encoding="utf-8") as fh:
            src = fh.read()
        for m in re.finditer(r'st\.configure\("([^"]*TProgressbar)"(.*?)\)\n', src, re.S):
            body = m.group(2)
            self.assertIn("BUSY_GREEN", body, m.group(1))
            self.assertNotRegex(body, r"\bOK\b|#1f5f99|#2a5a80|#16324a", m.group(1))
        for path in glob.glob(os.path.join(APP, "*.py")):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            for line in text.splitlines():
                if "Progressbar" in line and "style=" in line and "bar_style" not in line:
                    self.assertTrue(any(s in line for s in ("Card.Horizontal.TProgressbar",
                                                            "Status.Horizontal.TProgressbar")),
                                    f"{os.path.basename(path)}: a busy bar with its own style: {line.strip()}")

    @unittest.skipUnless(HAVE_TK, "needs a display")
    def test_styles_drawn_green(self):
        import style
        root = tk.Tk()
        try:
            st = style.setup(root)
            for name in style.BUSY_BAR_STYLES:
                self.assertEqual(str(st.lookup(name, "background")).lower(), style.BUSY_GREEN, name)
            bar = style.BusyBar(root, label_style="Status.TLabel", frame_style="Status.TFrame",
                                bar_style="Status.Horizontal.TProgressbar")
            self.assertEqual(str(st.lookup(str(bar.bar.cget("style")), "background")).lower(), style.BUSY_GREEN)
            dialog_bar = style.BusyBar(root)                  # a dialog's: the card style, green too
            self.assertEqual(str(st.lookup(str(dialog_bar.bar.cget("style")), "background")).lower(),
                             style.BUSY_GREEN)
        finally:
            root.destroy()


# ---- (6) QE from the maker's curve ---------------------------------------------------------------------------------

class TestBandQE(TempData):
    def spec(self):
        import report
        return sp.find_spec("ZWO ASI2600MC Air", (report.BUILTIN_SPECS,))

    def test_green_from_curve(self):
        import report
        q = report.band_qe(self.spec(), "TG")
        # the G curve (percent of the peak) averaged over V (500–590 nm) × peak QE 0.8, worked out here by hand
        pts = self.spec()["qe_relative"]["G"]
        import numpy as np
        lo, hi = report.BAND_RANGE_NM["V"]                               # 500.5–589.5 nm
        xs = np.linspace(lo, hi, 181)
        want = float(np.mean(np.interp(xs, [p[0] for p in pts], [p[1] for p in pts]))) / 100 * 0.8
        self.assertAlmostEqual(q["value"], round(want, 3), places=3)
        self.assertTrue(0.6 < q["value"] < 0.8)
        self.assertEqual(q["peak"], 0.8)
        self.assertIn("500–590 nm", q["words"])
        r = report.band_qe(self.spec(), "TR")
        self.assertLess(r["value"], q["value"])
        self.assertIsNotNone(report.band_qe(self.spec(), "TB"))           # the curve starts at 400: most of B
        self.assertIsNotNone(report.band_qe(self.spec(), "V"))            # a filter on the OSC: the RGGB mix
        self.assertIsNone(report.band_qe(None, "TG"))
        self.assertIsNone(report.band_qe(self.spec(), "U"))               # 332–400 nm: the curve starts at 400
        mono = {"qe_relative": {"unit": "fraction", "L": [[400, 0.5], [500, 0.9], [700, 0.7]]}}
        # V 500–590 nm on a straight line from 0.9 to 0.81: the mean is 0.855
        self.assertAlmostEqual(report.band_qe(mono, "V")["value"], 0.855, places=3)
        rel = {"qe_relative": {"unit": "percent of the peak (relative)", "L": [[400, 50], [700, 100]]}}
        self.assertIsNone(report.band_qe(rel, "V"))                       # relative, and no peak QE: unusable

    def test_optics_use_curve_typed_wins_no_curve_assumed(self):
        import report
        cam = {"camera": {"instrume": "ZWO ASI2600MC Air", "color": "OSC", "bayer_pattern": "RGGB"},
               "values": {"qe_peak": sp.record(0.8, "fraction", "spec")}}
        doc = {"telescope": {}, "bands": {"TG": {}, "TR": {}}}
        o = report.optics_values(doc, cam)["qe_in_band"]
        self.assertEqual(o["how"], "maker's QE curve")
        self.assertEqual(set(o["by_band"]), {"TG", "TR"})
        self.assertEqual(o["value"], o["by_band"]["TG"])
        typed = report.optics_values({"telescope": {"qe_in_band": 0.7}, "bands": {"TG": {}}}, cam)["qe_in_band"]
        self.assertEqual((typed["value"], typed["how"]), (0.7, "profile"))
        self.assertEqual(report.COAT_WORDS[typed["how"]], "entered by hand")
        over = report.optics_values(doc, cam, {"qe_in_band": 0.65})["qe_in_band"]
        self.assertEqual((over["value"], over["how"]), (0.65, "user"))
        andor = _andor(qe_peak=sp.record(0.9, "fraction", "spec"))
        a = report.optics_values({"telescope": {}, "bands": {"SI": {}}}, andor)["qe_in_band"]
        self.assertEqual((a["value"], a["how"]), (0.9, "assumed"))        # no curve for the Andor: peak, assumed
        self.assertEqual(report.optics_value_words("qe_in_band", a), "0.9 (assumed)")
        none = report.optics_values({"telescope": {}}, None)["qe_in_band"]
        self.assertEqual((none["value"], none["how"]), (0.6, "assumed"))

    def test_user_spec_copy_is_used_first(self):
        import report
        spec = dict(self.spec())
        spec["qe_relative"] = dict(spec["qe_relative"])
        spec["qe_relative"]["G"] = [[400, 50], [1000, 50]]
        spec["points"] = dict(spec["points"], qe_peak=0.8)
        sp.write_json(os.path.join(sp.sub_dir("specs"), "zwo-asi2600mc.json"), spec)
        cam = {"camera": {"instrume": "ZWO ASI2600MC Air", "color": "OSC"}, "values": {}}
        o = report.optics_values({"telescope": {}, "bands": {"TG": {}}}, cam)["qe_in_band"]
        self.assertAlmostEqual(o["value"], 0.4, places=3)

    def test_budget_uses_the_band_value(self):
        import report
        cam = {"camera": {"instrume": "ZWO ASI2600MC Air", "color": "OSC", "bayer_pattern": "RGGB"},
               "values": {"qe_peak": sp.record(0.8, "fraction", "spec")}}
        o = report.optics_values({"telescope": {}, "bands": {"TG": {}, "TR": {}}}, cam)
        g = report.budget("TG", 20.16, 0.2, 356, o)
        r = report.budget("TR", 19.8, 0.13, 356, o)
        qe_g = next(v for n, v, _w in g["parts"] if n == "Camera QE")
        qe_r = next(v for n, v, _w in r["parts"] if n == "Camera QE")
        self.assertEqual(qe_g, o["qe_in_band"]["by_band"]["TG"])
        self.assertEqual(qe_r, o["qe_in_band"]["by_band"]["TR"])
        self.assertIn("maker's QE curve", next(w for n, _v, w in g["parts"] if n == "Camera QE"))


# ---- (7) and (10)/(11) the report's words ----------------------------------------------------------------------------

class TestReportWords(TempData):
    def setUp(self):
        super().setUp()
        from tests import m5_data
        self.d = m5_data.make(self.tmp)

    def texts(self, share=False, info=None):
        import report
        got = []
        orig = report.Page._t

        def spy(page, x, y, text, **kw):
            t = orig(page, x, y, text, **kw)
            got.append(t.get_text())
            return t
        report.Page._t = spy
        try:
            info = info or report.gather(self.d["system_path"])
            report.write_pdf(info, self.path("r.pdf"), share=share, version="t")
        finally:
            report.Page._t = orig
        return got

    def test_numbers(self):
        import report
        self.assertEqual(report.num(1.58e4), "15,800")
        self.assertEqual(report.num(65535), "65,535")
        self.assertEqual(report.num(0.2562), "0.2562")
        self.assertEqual(report.num(2.2e-05), "0.000022")
        self.assertEqual(report.fmt(0.0004242, "e-/px/s", sigma=2.2e-05), "0.0004242 ± 0.000022 e-/px/s")
        self.assertEqual(report.fmt(15800, "e-"), "15,800 e-")

    def test_pdf_has_no_e_notation_and_no_split_words(self):
        doc = sp.load(self.d["camera_path"])
        doc["values"]["full_well_e"] = sp.record(15800.0, "e-", "spec")
        doc["values"]["saturation_adu"] = sp.record(65535.0, "ADU", "header")
        sp.save_profile(doc, "t")
        lines = self.texts()
        text = " | ".join(lines)
        self.assertIn("15,800 e-", text)
        self.assertIn("65,535 ADU", text)
        self.assertNotRegex(text, r"\d\.?\d*e[+-]\d\d")                   # no 1.58e+04, no 2.2e-05
        self.assertIn("maker's spec", lines)                               # one cell, one line: never split
        self.assertNotIn("manufacturer", text)
        for ln in lines:
            self.assertFalse(ln.startswith("'s spec"), ln)

    def test_obstruction_assumed_said_the_same_everywhere(self):
        text = " | ".join(self.texts())
        self.assertNotIn("Central obstruction | not known", text)
        self.assertIn("0.4 of the diameter (assumed)", text)
        self.assertIn("0.4 (assumed)", text)
        self.assertIn("obstruction 0.4 (assumed)", text)

    def test_zero_mirrors_explained(self):
        import report
        doc = sp.load(self.d["system_path"])
        doc["telescope"]["mirrors"] = 0
        sp.save_profile(doc, "t")
        text = " ".join(self.texts())
        self.assertIn("0 mirrors (refractor or lens: no mirror", text)
        bg = report.gather(self.d["system_path"])["budgets"][0]
        self.assertIn(("Mirrors (none)", 1.0, report.NO_MIRRORS), bg["parts"])

    def test_single_on_sky_date(self):
        import report
        self.assertEqual(report.date_span("2026-09-01", "2026-09-01"), "2026-09-01")
        self.assertEqual(report.date_span("2026-09-01", "2026-10-07"), "2026-09-01 to 2026-10-07")
        info = report.gather(self.d["system_path"])
        info["date_range"] = ("2026-09-01", "2026-09-01")
        text = " ".join(self.texts(info=info))
        self.assertNotIn("2026-09-01 to 2026-09-01", text)

    def test_methods_say_how_qe_is_worked_out(self):
        text = " ".join(self.texts())
        self.assertIn("top-hat", text)
        self.assertIn("maker's QE curve", text)


class TestPdfDates(TempData):
    def test_pdf_date_strings(self):
        import report
        utc = datetime(2026, 11, 2, 14, 30, 0, tzinfo=timezone.utc)
        self.assertEqual(report.pdf_date(utc), "D:20261102143000Z")
        est = utc.astimezone(timezone(timedelta(hours=-5)))
        self.assertEqual(report.pdf_date(est), "D:20261102093000-05'00'")
        ist = utc.astimezone(timezone(timedelta(hours=5, minutes=30)))
        self.assertEqual(report.pdf_date(ist), "D:20261102200000+05'30'")

    def test_shared_copy_in_utc_full_copy_with_real_offset(self):
        import report
        from tests import m5_data
        d = m5_data.make(self.tmp)
        info = report.gather(d["system_path"])
        now = datetime(2026, 11, 2, 14, 30, 0, tzinfo=timezone(timedelta(hours=-5)))   # after DST ended
        md = report.pdf_metadata(info, True, "t", now=now)
        self.assertEqual(md["CreationDate"].pdfRepr(), b"(D:20261102193000Z)")
        self.assertEqual(md["ModDate"].pdfRepr(), b"(D:20261102193000Z)")
        full = report.pdf_metadata(info, False, "t", now=datetime(2026, 11, 2, 19, 30, tzinfo=timezone.utc))
        local = datetime(2026, 11, 2, 19, 30, tzinfo=timezone.utc).astimezone()
        off = local.utcoffset().total_seconds() / 60
        want = "Z" if off == 0 else f"{'+' if off > 0 else '-'}{int(abs(off)) // 60:02d}'{int(abs(off)) % 60:02d}'"
        self.assertTrue(full["CreationDate"].pdfRepr().decode().endswith(want + ")"))
        for share in (True, False):
            p = self.path(f"d{share}.pdf")
            report.write_pdf(info, p, share=share, version="t")
            with open(p, "rb") as fh:
                raw = fh.read()
            m = re.search(rb"/CreationDate \(D:(\d{14})([^)]*)\)", raw)
            self.assertIsNotNone(m)
            if share:
                self.assertEqual(m.group(2), b"Z")
            self.assertIn(b"/ModDate", raw)


class TestScrub(unittest.TestCase):
    def test_paths_with_spaces(self):
        import report
        gone = "(a file on the observer's computer)"
        cases = {
            r"W:\Astrophotography\Targets\Messier\M045\Raw\Panel 1-1\2026-10-07": gone,
            r"lights in W:\Astrophotography\Targets\Messier\M045\Raw\Panel 1-1\2026-10-07 (80 frames)":
                f"lights in {gone} (80 frames)",
            r"from C:\Users\kross\Downloads\Andor spec sheet.pdf; sent 30 Oct": f"from {gone}; sent 30 Oct",
            r"C:\Users\kross\My Documents\M045 Pleiades": gone,
            "/home/john/My Data/Panel 1-1 was used": f"{gone} was used",
            "/Users/kross/Astro Data/run 1.json, then": f"{gone}, then",
            r"\\nas\astro\Panel 2-2": gone,
            "nothing here: 1/2 and a/b": "nothing here: 1/2 and a/b",
        }
        for text, want in cases.items():
            self.assertEqual(report.scrub(text), want, text)
            out = report.scrub(text)
            for word in ("Panel", "kross", "Astrophotography", "Pleiades", "john", "nas"):
                self.assertNotIn(word, out, text)


# ---- (10c, d) Roll back and worked-out values ----------------------------------------------------------------------

class TestRederive(TempData):
    def test_full_well_follows_the_typed_gain(self):
        path = sp.save_profile(_andor(), "t")
        doc = sp.load(path)
        self.assertEqual(doc["values"]["full_well_e"]["value"], 65535)
        doc["values"]["gain_e_per_adu"] = sp.record(1.89, "e-/ADU", "user", note="typed on the Profiles page")
        sp.save_profile(doc, "t")
        doc = sp.load(path)
        self.assertEqual(doc["values"]["full_well_e"]["value"], round(65535 * 1.89))
        self.assertEqual(doc["values"]["full_well_e"]["source"], "derived")
        doc["values"]["bias_level_adu"] = sp.record(300.0, "ADU", "user")
        sp.save_profile(doc, "t")
        doc = sp.load(path)
        self.assertEqual(doc["values"]["full_well_e"]["value"], round((65535 - 300) * 1.89))
        self.assertIn("(saturation − bias) × gain", doc["values"]["full_well_e"]["note"])
        doc["values"]["saturation_adu"] = sp.record(52800.0, "ADU", "user")
        sp.save_profile(doc, "t")
        doc = sp.load(path)
        self.assertEqual(doc["values"]["full_well_e"]["value"], round((52800 - 300) * 1.89))
        self.assertEqual(doc["values"]["linear_limit_adu"]["value"], round(0.8 * 52800))   # the estimate follows

    def test_typed_or_measured_never_overwritten(self):
        doc = _andor(full_well_e=sp.record(100000.0, "e-", "user", note="operator"),
                     linear_limit_adu=sp.record(45000.0, "ADU", "user"),
                     gain_e_per_adu=sp.record(1.904, "e-/ADU", "user"))
        path = sp.save_profile(doc, "t")
        doc = sp.load(path)
        self.assertEqual(doc["values"]["full_well_e"]["value"], 100000.0)
        self.assertEqual(doc["values"]["linear_limit_adu"]["value"], 45000.0)
        m = _andor(full_well_e=sp.record(16610.0, "e-", "measured"), gain_e_per_adu=sp.record(0.25, "e-/ADU",
                                                                                               "measured"))
        self.assertNotIn("full_well_e", sp.rederive(m))
        self.assertEqual(m["values"]["full_well_e"]["value"], 16610.0)

    def test_save_does_not_change_the_callers_doc_and_stays_valid(self):
        doc = _andor(gain_e_per_adu=sp.record(2.0, "e-/ADU", "user"))
        sp.save_profile(doc, "t")
        self.assertEqual(doc["values"]["full_well_e"]["value"], 65535)    # the caller's copy is left alone
        saved = sp.load(sp.profile_path("cameras", doc["profile_id"]))
        self.assertEqual(sp.validate(saved), [])
        self.assertEqual(saved["values"]["full_well_e"]["value"], 131070)

    def test_camera_in_use_rederives_an_old_file(self):
        import instrument
        doc = _andor(gain_e_per_adu=sp.record(1.89, "e-/ADU", "user"))
        sp.write_json(sp.profile_path("cameras", doc["profile_id"]), doc)   # as 0.5.0 left it: stale
        cam = instrument.camera_in_use({"camera_id": doc["profile_id"], "instrume": "Andor CCD",
                                        "gain_setting": 1.0})
        self.assertEqual(cam.value("full_well_e"), round(65535 * 1.89))

    def test_moeller_andor_revisions_corrected(self):
        """John's real Andor revisions 2–4 (0.5.0): full well stayed 65535 e- (worked out) after the gain was typed
        as 1.89; the fix works it out again from each revision's own gain, saturation and bias."""
        revs = [  # (gain, bias) as in the real revisions 2, 3, 4
            (1.89, None), (1.89, None), (1.89, 300.0)]
        for gain, bias in revs:
            d = _andor(gain_e_per_adu=sp.record(gain, "e-/ADU", "user"),
                       bias_level_adu=sp.record(bias, "ADU", "user" if bias else "none"))
            sp.rederive(d)
            want = round((65535 - (bias or 0)) * gain)
            self.assertEqual(d["values"]["full_well_e"]["value"], want)
            self.assertNotEqual(want, 65535)


class TestRollbackWords(TempData):
    def test_names_and_numbers(self):
        import rollback
        self.assertEqual(rollback.value_name("values.gain_e_per_adu"), "Gain")
        self.assertEqual(rollback.value_name("values.read_noise_e"), "Read noise")
        self.assertEqual(rollback.value_name("values.full_well_e"), "Full well")
        self.assertEqual(rollback.value_name("per_channel.G.read_noise_e"), "Read noise (G)")
        self.assertEqual(rollback.value_name("bands.TG.zero_point"), "Zero point at airmass 1 (green (TG))")
        self.assertEqual(rollback.value_name("bands.TG.fwhm_imaging"), "Seeing, imaging focus (green (TG))")
        self.assertEqual(rollback.value_name("telescope.mirrors"), "Mirrors")
        self.assertEqual(rollback.number_words(65535), "65,535")
        self.assertEqual(rollback.number_words(65535.0), "65,535")
        self.assertEqual(rollback.number_words(1.89), "1.89")
        self.assertEqual(rollback.number_words(0.0004242), "0.0004242")
        self.assertEqual(rollback.number_words(1e-7), "0.0000001")

    def test_revisions_show_separators(self):
        import rollback
        path = sp.save_profile(_andor(), "t")
        d = sp.load(path)
        d["values"]["full_well_e"] = sp.record(100000.0, "e-", "user")
        sp.save_profile(d, "t")
        r = rollback.revisions(path)[0]
        ch = {name: (old, new) for name, old, new in r["changes"]}
        self.assertEqual(ch["values.full_well_e"], ("65,535 e- (worked out)", "100,000 e- (typed by you)"))

    @unittest.skipUnless(HAVE_TK, "needs a display")
    def test_dialog_plain_names_and_revision_in_use(self):
        import flux_app
        path = sp.save_profile(_andor(), "t")
        d = sp.load(path)
        d["values"]["gain_e_per_adu"] = sp.record(1.89, "e-/ADU", "user")
        sp.save_profile(d, "t")
        app = flux_app.App(testing=True)
        try:
            app.update()
            dlg = flux_app.RollbackDialog(app, path, lambda _d: None)
            app.update()
            self.assertEqual(dlg.now_var.get()[:24], "Now using revision 2, up")
            text = dlg.text.get("1.0", "end")
            self.assertIn("Gain:", text)
            self.assertIn("Full well:", text)
            self.assertIn("65,535", text)
            self.assertNotIn("values.", text)
            self.assertNotIn("gain_e_per_adu", text)
            dlg.destroy()
            # the Profiles page names its rows the same way
            app.show_page(3)
            pp = app.profiles_page
            pp.refresh()
            pp.pick_var.set(next(n for n in pp.pick.cget("values") if n.startswith("Camera")))
            pp.show()
            names = [pp.tree.item(i, "text") for i in pp.rec_paths]
            self.assertIn("Gain", names)
            self.assertIn("Full well", names)
            fw = next(i for i, p in pp.rec_paths.items() if p[-1] == "full_well_e")
            self.assertEqual(pp.tree.item(fw, "values")[0], f"{round(65535 * 1.89):,}")
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
