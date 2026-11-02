"""0.5 (M5): the run history behind the History page (schema 2, setups, trend series, taking a run off), the trend
plot, profile roll-back, and the system report (photon budget, caveats, the PDF and its shared copy)."""
from __future__ import annotations

import csv
import math
import os
import sqlite3

from tests.base import TempData

import history
import shobs_profile as sp


class TestSchema(TempData):
    def test_version1_database_upgraded_without_losing_rows(self):
        path = sp.runs_db_path()
        con = sqlite3.connect(path)
        v1 = history.SCHEMA.replace("    summary     TEXT,                        -- JSON\n    removed     INTEGER NOT NULL "
                                    "DEFAULT 0   -- 1 = taken off the History page (schema 2)\n",
                                    "    summary     TEXT\n")
        self.assertNotEqual(v1, history.SCHEMA)
        con.executescript(v1)
        con.execute("PRAGMA user_version = 1")
        con.execute("INSERT INTO runs (run_key, kind, started, status) VALUES ('old-1', 'check', '2026-10-27', 'ok')")
        con.commit()
        cols = {r[1] for r in con.execute("PRAGMA table_info(runs)").fetchall()}
        con.close()
        self.assertNotIn("removed", cols)
        rows = history.recent_runs(10)
        self.assertEqual([r["run_key"] for r in rows], ["old-1"])
        self.assertEqual(rows[0]["removed"], 0)
        con = sqlite3.connect(path)
        self.assertEqual(con.execute("PRAGMA user_version").fetchone()[0], history.SCHEMA_VERSION)
        con.close()


class TestSetupsAndSeries(TempData):
    def setUp(self):
        super().setUp()
        from tests import m5_data
        self.d = m5_data.make(self.tmp)

    def test_same_system(self):
        sid = self.d["system_id"]
        self.assertTrue(history.same_system(sid, sid))
        other_name = sid.replace("14in-rc", "ioptron-14-rc")           # saved before the name was typed
        self.assertTrue(history.same_system(other_name, sid))
        self.assertTrue(history.same_system(sid.replace("2900mm", "2905mm"), sid))    # ASIAIR's focal wobble
        self.assertFalse(history.same_system(sid.replace("2900mm", "4250mm"), sid))
        self.assertFalse(history.same_system(sid.replace("__zwo", "__andor"), sid))
        self.assertFalse(history.same_system(None, sid))

    def test_setups_and_available(self):
        st = history.setups()
        kinds = [(s["kind"], s["id"]) for s in st]
        self.assertEqual(kinds[0], ("system", self.d["system_id"]))
        self.assertIn(("camera", self.d["camera_id"]), kinds)
        av = history.available("system", self.d["system_id"])
        keys = [(k, c) for k, c, _w, _n in av]
        self.assertEqual(keys[0], ("bands.zero_point", "TG"))
        self.assertIn(("bands.fwhm_in_focus", "TG"), keys)
        n_zp = dict(((k, c), n) for k, c, _w, n in av)[("bands.zero_point", "TG")]
        self.assertEqual(n_zp, 2)                    # the hazy night has no zero point at airmass 1

    def test_series_newest_run_counts_once_per_night(self):
        sid = self.d["system_id"]
        s = self.d["summary"]
        r = next(r for r in s.nights if r.ds.night == "2026-10-18")
        import onsky
        vals = onsky.night_values(r, sid)
        for v in vals:
            if v["key"] == "bands.zero_point":
                v["value"] = 21.0                      # a re-run of the same night with a different answer
        history.record_run("onsky", r.ds.path, onsky.night_summary(r), camera_id=self.d["camera_id"],
                           system_id=sid.replace("14in-rc", "old-name"), values=vals)
        pts = history.series_points("system", sid, "bands.zero_point", "TG")
        self.assertEqual([p["date"] for p in pts], ["2026-10-18", "2026-10-19"])
        self.assertEqual(pts[0]["value"], 21.0)
        self.assertEqual(history.series_points("system", sid, "bands.zero_point", "TG", date_from="2026-10-19"),
                         pts[1:])
        self.assertEqual(history.series_points("system", sid, "bands.zero_point", "TG", date_to="2026-10-18")[0]
                         ["value"], 21.0)

    def test_take_off_and_put_back(self):
        sid = self.d["system_id"]
        key = next(r["run_key"] for r in history.recent_runs(50) if "2026-10-18" in (r["source"] or ""))
        n0 = len(history.series_points("system", sid))
        self.assertTrue(history.set_removed(key, True))
        self.assertLess(len(history.series_points("system", sid)), n0)
        self.assertNotIn(key, [r["run_key"] for r in history.recent_runs(50)])
        self.assertIn(key, [r["run_key"] for r in history.recent_runs(50, removed=True)])
        self.assertTrue(history.set_removed(key, False))
        self.assertEqual(len(history.series_points("system", sid)), n0)
        self.assertFalse(history.set_removed("no-such-run", True))

    def test_csv(self):
        pts = history.series_points("system", self.d["system_id"], "bands.zero_point", "TG")
        path = self.path("zp.csv")
        history.write_csv(path, pts, "14in RC + test", "0.5.0")
        with open(path, encoding="utf-8") as fh:
            lines = fh.read().splitlines()
        self.assertTrue(lines[0].startswith("# 14in RC"))
        self.assertRegex(lines[1], r"^# Created \d{6}Z [A-Z]{3} \d\d by SHOBS-FLUX 0\.5\.0$")
        rows = list(csv.DictReader(lines[2:]))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["value_name"], "Zero point at airmass 1")
        self.assertEqual(rows[0]["photometric"], "yes")

    def test_trend_plot_draws(self):
        from matplotlib.figure import Figure
        import trend_plots
        pts = history.series_points("system", self.d["system_id"], "bands.fwhm_in_focus", "TG")
        fig = Figure(figsize=(8, 4))
        trend_plots.draw_trend(fig, pts, "Seeing", "arcsec", now={"value": 3.5, "source": "measured"}, spec=3.0,
                               event="2026-10-19")
        mark = trend_plots.stamp(fig, "0.5.0", "test")
        path = self.path("t.png")
        fig.savefig(path)
        self.assertGreater(os.path.getsize(path), 10000)
        self.assertIn("Created", mark.get_text())
        trend_plots.draw_trend(fig, [], "Seeing", "arcsec")          # nothing in these dates: no error

    def test_before_after(self):
        import trend_plots
        pts = [{"date": d, "value": v, "sigma": 0.01} for d, v in
               (("2026-10-01", 20.0), ("2026-10-02", 20.02), ("2026-10-10", 20.3), ("2026-10-11", 20.32))]
        ba = trend_plots.before_after(pts, "2026-10-05")
        self.assertAlmostEqual(ba["before"]["mean"], 20.01, places=6)
        self.assertAlmostEqual(ba["change"], 0.30, places=6)
        self.assertEqual(ba["after"]["n"], 2)
        self.assertGreater(ba["change_err"], 0.0)
        self.assertIsNone(trend_plots.before_after(pts, "2026-12-01"))
        self.assertIsNone(trend_plots.before_after(pts, "not a date"))

    def test_parse_date(self):
        import history_page as hp
        self.assertEqual(hp.parse_date("2026-10-30"), "2026-10-30")
        self.assertEqual(hp.parse_date("30 Oct 2026"), "2026-10-30")
        self.assertEqual(hp.parse_date("30Oct26"), "2026-10-30")
        self.assertEqual(hp.parse_date("10/30/2026"), "2026-10-30")
        self.assertEqual(hp.parse_date(""), "")
        self.assertIsNone(hp.parse_date("next week"))
        self.assertIsNone(hp.parse_date("31 Feb 2026"))


class TestRollback(TempData):
    def test_roll_back_and_forward(self):
        import rollback
        from tests import m5_data
        d = m5_data.make(self.tmp)
        path = d["camera_path"]
        now = sp.load(path)
        self.assertEqual(now["revision"], 2)
        revs = rollback.revisions(path)
        self.assertEqual([r["revision"] for r in revs], [1])
        self.assertEqual(revs[0]["changes"][0][0], "values.gain_e_per_adu")
        self.assertIn("header", revs[0]["changes"][0][1])
        doc = rollback.roll_back(path, revs[0]["path"], "test 0.5")
        self.assertEqual(doc["revision"], 3)
        self.assertEqual(doc["values"]["gain_e_per_adu"]["source"], "header")
        self.assertEqual(doc["rolled_back"]["to_revision"], 1)
        self.assertEqual(doc["rolled_back"]["from_revision"], 2)
        self.assertEqual(sp.validate(doc), [])
        self.assertEqual(sp.load(path)["revision"], 3)
        revs = rollback.revisions(path)                  # revision 2 (measured) is kept and can come back
        self.assertEqual([r["revision"] for r in revs], [2, 1])
        doc = rollback.roll_back(path, revs[0]["path"], "test 0.5")
        self.assertEqual(doc["revision"], 4)
        self.assertEqual(doc["values"]["gain_e_per_adu"]["source"], "measured")

    def test_wrong_profile_refused(self):
        import rollback
        from tests import m5_data
        d = m5_data.make(self.tmp)
        with self.assertRaises(rollback.RollbackError):
            rollback.roll_back(d["camera_path"], d["system_path"])


class TestReport(TempData):
    def setUp(self):
        super().setUp()
        from tests import m5_data
        self.d = m5_data.make(self.tmp)
        self.prefs = {"sky_telescopes": [{"camera_id": self.d["camera_id"], "focal": 2900, "name": "14in RC",
                                          "lat": 35.3031, "lon": -81.9407, "elevation_m": 280.0}]}

    def test_budget_arithmetic(self):
        import report
        optics = report.optics_values({"telescope": {"central_obstruction_fraction": 0.47, "mirrors": 3,
                                                     "mirror_reflectivity": 0.96, "corrector_transmission": 0.95,
                                                     "filter_transmission": 0.92, "qe_in_band": 0.65}}, None)
        bg = report.budget("SI", 22.459, 0.08, 1000, optics)
        n0 = 3631e-26 * (153.5 / 767.2) / 6.62607015e-34
        self.assertAlmostEqual(bg["photons_mag0"], n0, delta=n0 * 1e-9)
        ideal = 2.5 * math.log10(n0 * math.pi * 0.25)
        self.assertAlmostEqual(bg["ideal_zp"], ideal, places=9)
        self.assertAlmostEqual(bg["measured_eff"], 10 ** (-0.4 * (ideal - 22.539)), places=9)
        pred = (1 - 0.47 ** 2) * 0.96 ** 3 * 0.95 * 0.92 * 0.65
        self.assertAlmostEqual(bg["predicted_eff"], pred, places=9)
        self.assertAlmostEqual(bg["predicted_zp1"], ideal + 2.5 * math.log10(pred) - 0.08, places=9)
        self.assertGreater(bg["shortfall_mag"], 0)               # the Moeller system is fainter than this budget
        self.assertIsNone(report.budget("SI", None, 0.08, 1000, optics))
        self.assertIsNone(report.budget("SI", 22.4, 0.08, None, optics))
        v = report.budget("TG", 20.0, None, 356, optics)
        self.assertEqual(v["catalog_band"], "V")
        self.assertAlmostEqual(v["predicted_eff"], pred * 0.5, places=9)    # only the green pixels count
        self.assertAlmostEqual(v["k"], 0.20)                       # typical V extinction when none is measured

    def test_optics_assumed_and_osc(self):
        import report
        cam = sp.load(self.d["camera_path"])
        o = report.optics_values({"telescope": {}}, cam)
        self.assertEqual(o["central_obstruction_fraction"]["how"], "assumed")
        self.assertEqual(o["filter_transmission"]["value"], 1.0)           # one-shot color: the Bayer dye is in QE
        self.assertEqual(o["qe_in_band"]["how"], "maker's QE curve")      # 0.5.1: was the 0.8 peak QE
        self.assertLess(o["qe_in_band"]["value"], 0.8)
        mono = report.optics_values({"telescope": {"mirrors": 3}}, None, {"mirror_reflectivity": 0.96})
        self.assertEqual(mono["filter_transmission"]["value"], 0.90)
        self.assertEqual(mono["mirrors"]["how"], "profile")
        self.assertEqual(mono["mirror_reflectivity"]["how"], "user")
        self.assertEqual(mono["qe_in_band"]["how"], "assumed")

    def test_gather(self):
        import report
        info = report.gather(self.d["system_path"], prefs=self.prefs, event="2026-10-19")
        self.assertEqual(info["site"], {"lat": 35.3031, "lon": -81.9407, "elevation_m": 280.0})
        self.assertEqual([b["band"] for b in info["bands"]], ["TG"])
        self.assertEqual(len({d for d, _t in info["nights"]}), 3)
        self.assertEqual(info["date_range"], ("2026-10-18", "2026-10-20"))
        gain = next(r for r in info["cam_rows"] if r["key"] == "gain_e_per_adu")
        self.assertEqual(gain["source"], "measured")
        self.assertTrue(any("header" in e for e in gain["earlier"]))     # the other value it has held
        self.assertIsNotNone(gain["spec"])                               # the built-in ASI2600MC spec at gain 100
        self.assertEqual(len(info["budgets"]), 1)
        self.assertTrue(any("assumed" in c for c in info["caveats"]))
        with self.assertRaises(report.ReportError):
            report.gather(self.d["camera_path"])

    def _texts(self, share):
        import report
        got = []
        orig = report.Page._t

        def spy(page, x, y, text, **kw):
            got.append(str(text))
            return orig(page, x, y, text, **kw)
        report.Page._t = spy
        try:
            info = report.gather(self.d["system_path"], prefs=self.prefs, event="2026-10-19")
            path = self.path("r.pdf")
            n = report.write_pdf(info, path, share=share, version="0.5.0")
        finally:
            report.Page._t = orig
        self.assertGreaterEqual(n, 6)
        with open(path, "rb") as fh:
            self.assertTrue(fh.read(5) == b"%PDF-")
        self.assertFalse(os.path.exists(path + ".part"))
        return " ".join(got)

    def test_pdf_full_copy_has_site_and_files(self):
        text = self._texts(False)
        self.assertIn("latitude 35.3031", text)
        self.assertIn("elevation 280 m", text)
        self.assertIn(self.tmp, text)
        self.assertIn("Throughput", text)
        self.assertIn("before", text)

    def test_pdf_shared_copy_leaves_out_site_and_paths(self):
        text = self._texts(True)
        self.assertNotIn("35.30", text)
        self.assertNotIn("-81.94", text)
        self.assertNotIn("280 m", text)
        self.assertNotIn(self.tmp, text)
        self.assertIn("Shared copy", text)

    def test_default_name(self):
        import report
        info = report.gather(self.d["system_path"])
        self.assertTrue(report.default_name(info, True).endswith("(shared).pdf"))
        self.assertNotIn("/", report.default_name(info, False))


class TestReviewFixes(TempData):
    """The 0.5 review's findings, each pinned."""

    def setUp(self):
        super().setUp()
        from tests import m5_data
        self.d = m5_data.make(self.tmp)

    def test_v1_values_and_logs_survive_upgrade(self):
        con = sqlite3.connect(sp.runs_db_path())
        con.execute("ALTER TABLE runs RENAME TO runs_v2")              # rebuild runs as version 1, keep its rows
        con.execute("CREATE TABLE runs (id INTEGER PRIMARY KEY AUTOINCREMENT, run_key TEXT UNIQUE, kind TEXT NOT "
                    "NULL, started TEXT NOT NULL, finished TEXT, app TEXT, camera_id TEXT, system_id TEXT, source "
                    "TEXT, n_frames INTEGER, status TEXT, summary TEXT)")
        con.execute("INSERT INTO runs SELECT id, run_key, kind, started, finished, app, camera_id, system_id, source, "
                    "n_frames, status, summary FROM runs_v2")
        con.execute("DROP TABLE runs_v2")
        con.execute("INSERT INTO onsky_files (path, size, mtime) VALUES ('x.json', 1, 1.0)")
        con.execute("PRAGMA user_version = 1")
        con.commit()
        n_vals = con.execute("SELECT COUNT(*) FROM run_values").fetchone()[0]
        con.close()
        pts = history.series_points("system", self.d["system_id"], "bands.zero_point", "TG")
        self.assertEqual(len(pts), 2)
        con = sqlite3.connect(sp.runs_db_path())
        self.assertEqual(con.execute("SELECT COUNT(*) FROM run_values").fetchone()[0], n_vals)
        self.assertEqual(con.execute("SELECT COUNT(*) FROM onsky_files").fetchone()[0], 1)
        con.close()

    def test_night_without_telescope_name_joins_its_setup(self):
        import onsky
        r = next(r for r in self.d["summary"].nights if r.ds.night == "2026-10-19")
        anon = sp.system_id("", 2903, self.d["camera_id"])               # what the Sky page records with no name
        self.assertTrue(anon.startswith("unknown-2903mm__"))
        vals = onsky.night_values(r, anon)
        for v in vals:
            v["date"] = "2026-10-25"
        history.record_run("onsky", "other.json", onsky.night_summary(r), camera_id=self.d["camera_id"],
                           system_id=anon, values=vals)
        dates = [p["date"] for p in history.series_points("system", self.d["system_id"], "bands.zero_point", "TG")]
        self.assertIn("2026-10-25", dates)

    def test_rolled_back_note_only_while_in_use(self):
        import rollback
        doc = rollback.roll_back(self.d["camera_path"], rollback.revisions(self.d["camera_path"])[0]["path"], "t")
        self.assertEqual(doc["rolled_back"]["as_revision"], doc["revision"])
        doc["values"]["read_noise_e"] = sp.record(1.3, "e-", "user")
        sp.save_profile(doc, "t")                                       # an ordinary save after the roll-back
        later = sp.load(self.d["camera_path"])
        self.assertGreater(later["revision"], later["rolled_back"]["as_revision"])

    def test_share_copy_scrubs_paths_in_notes(self):
        import report
        doc = sp.load(self.d["camera_path"])
        doc["values"]["gain_e_per_adu"]["note"] = r"from C:\Users\kross\Downloads\specs.pdf"
        sp.save_profile(doc, "t")
        self.assertEqual(report.scrub(r"see C:\Users\kross\x.json"), "see (a file on the observer's computer)")
        got = []
        orig = report.Page._t

        def spy(page, x, y, text, **kw):
            t = orig(page, x, y, text, **kw)
            got.append(t.get_text())
            return t
        report.Page._t = spy
        try:
            report.write_pdf(report.gather(self.d["system_path"]), self.path("s.pdf"), share=True, version="t")
        finally:
            report.Page._t = orig
        text = " ".join(got)
        self.assertNotIn("Users", text)
        self.assertNotIn(self.tmp, text)

    def test_no_budget_caveat_without_aperture(self):
        import report
        doc = sp.load(self.d["system_path"])
        doc["telescope"]["aperture_mm"] = None
        sp.save_profile(doc, "t")
        info = report.gather(self.d["system_path"])
        self.assertEqual(info["budgets"], [])
        self.assertTrue(any("aperture is not known" in c for c in info["caveats"]))
        report.write_pdf(info, self.path("n.pdf"), version="t")         # and the PDF still comes out

    def test_locked_target_leaves_no_part_file(self):
        import report
        target = os.path.join(self.tmp, "a_folder.pdf")
        os.makedirs(target)                                             # os.replace onto a folder fails
        with self.assertRaises(OSError):
            report.write_pdf(report.gather(self.d["system_path"]), target, version="t")
        self.assertEqual([n for n in os.listdir(self.tmp) if n.endswith(".part")], [])

    def test_dtg_month_in_english(self):
        from datetime import datetime, timezone
        self.assertEqual(history.dtg(datetime(2026, 10, 12, 11, 53, tzinfo=timezone.utc)), "121153Z OCT 26")
