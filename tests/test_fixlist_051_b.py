"""0.5.1 fix list, builder B (History): older runs judged again by today's rules (on-sky nights from their logs, the
29 Oct defocus and marginal night; a gone log keeps its values, marked 'judged by FLUX <version>'), dark runs record
their read noise, 0.1 check runs say why, '<Value name> (<unit>)' axis labels, nights without a focal length join
their camera's one setup, one unit per trend, and the 'Seeing, imaging focus' value."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import unittest
from types import SimpleNamespace

from tests.base import TempData

import history
import shobs_profile as sp

REAL = "/home/claude/realdata/.shobs"
IOPTRON = "ioptron-14-rc-2903mm__zwo-asi2600mc-air__g100__o50__bin1"


def _set_app(app: str, where: str = "kind = 'onsky'"):
    con = sqlite3.connect(sp.runs_db_path())
    con.execute(f"UPDATE runs SET app = ? WHERE {where}", (app,))
    # a run recorded today carries today's rules: take them off, as an older FLUX wrote it
    for rid, s in con.execute(f"SELECT id, summary FROM runs WHERE {where}").fetchall():
        d = json.loads(s or "{}")
        d.pop("rules", None)
        con.execute("UPDATE runs SET summary = ? WHERE id = ?", (json.dumps(d), rid))
    con.commit()
    con.close()


@unittest.skipUnless(os.path.isdir(REAL), "John's real data folder is not here")
class TestRealDataRejudged(TempData):
    def setUp(self):
        super().setUp()
        shutil.copytree(REAL, os.environ["SHOBS_DATA_HOME"])

    def pts(self, key):
        return history.series_points("system", IOPTRON, key, "TG")

    def test_29_oct_becomes_a_defocus_point_and_marginal(self):
        before = {p["date"]: p for p in self.pts("bands.fwhm_in_focus")}
        self.assertAlmostEqual(before["2026-10-29"]["value"], 6.87, places=2)        # the 0.4.0 verdict
        self.assertEqual(before["2026-10-29"]["judged_by"], "0.4.0")
        self.assertTrue(history.stale_runs())
        got = history.rejudge()
        self.assertGreaterEqual(got["onsky"], 9)
        self.assertEqual(got["kept"], 0)
        self.assertEqual(got["darks"], 2)
        infocus = {p["date"]: p for p in self.pts("bands.fwhm_in_focus")}
        self.assertNotIn("2026-10-29", infocus)                                      # no longer 6.87 in focus
        defoc = {p["date"]: p for p in self.pts("bands.fwhm_defocus")}
        self.assertIn("2026-10-29", defoc)
        self.assertEqual(defoc["2026-10-29"]["defocus"], "+125 steps")
        self.assertAlmostEqual(defoc["2026-10-29"]["value"], 6.87, places=2)
        zp = {p["date"]: p for p in self.pts("bands.zero_point")}
        self.assertTrue(zp["2026-10-29"]["marginal"])                                 # 0.4.1's rule, not photometric
        self.assertFalse(any(p["judged_by"] for p in self.pts("bands.fwhm_defocus")))
        # traceable: the old values are kept in the run
        con = sqlite3.connect(sp.runs_db_path())
        s = json.loads(con.execute("SELECT summary FROM runs WHERE id = 29").fetchone()[0])
        st = con.execute("SELECT status FROM runs WHERE id = 29").fetchone()[0]
        con.close()
        self.assertEqual(s["rules"], history.RULES_VERSION)
        self.assertEqual(s["before_rejudge"]["app"], "SHOBS-FLUX 0.4.0")
        self.assertIn("bands.fwhm_in_focus", [v["key"] for v in s["before_rejudge"]["values"]])
        self.assertEqual(st, "warn")
        # done once: nothing left, and a second pass changes nothing
        self.assertEqual(history.stale_runs(), [])
        self.assertEqual(history.rejudge(), {"onsky": 0, "kept": 0, "darks": 0, "check": 0})

    def test_dark_runs_get_read_noise_and_checks_say_why(self):
        history.rejudge()
        rn = history.series_points("camera", "zwo-asi2600mc-air__g100__o50__bin1", "read_noise_e", None)
        by_kind = sorted((p["kind"], p["value_source"]) for p in rn)
        self.assertEqual(by_kind, [("bench", "measured"), ("darks", "darks"), ("darks", "darks")])
        self.assertTrue(all(abs(p["value"] - 1.12) < 0.01 for p in rn))
        rows = {r["id"]: r for r in history.recent_runs(100)}
        self.assertEqual(history.status_text(rows[3]), "⚠ 4 field stars saturated")
        self.assertEqual(rows[3]["status"], "warn")
        self.assertEqual(history.status_text(rows[4]), "✔ ok")


class TestRejudgeSynthetic(TempData):
    def setUp(self):
        super().setUp()
        from tests import m5_data
        self.d = m5_data.make(self.tmp)

    def test_old_values_replaced_from_the_log(self):
        sid = self.d["system_id"]
        good = {p["date"]: p["value"] for p in history.series_points("system", sid, "bands.zero_point", "TG")}
        con = sqlite3.connect(sp.runs_db_path())
        con.execute("UPDATE run_values SET value = 99.0 WHERE key = 'bands.zero_point'")
        con.commit()
        con.close()
        _set_app("SHOBS-FLUX 0.4.0")
        self.assertEqual({p["value"] for p in history.series_points("system", sid, "bands.zero_point", "TG")},
                         {99.0})
        got = history.rejudge()
        self.assertEqual(got["onsky"], 3)
        now = {p["date"]: p["value"] for p in history.series_points("system", sid, "bands.zero_point", "TG")}
        self.assertEqual(set(now), set(good))
        for d in good:
            self.assertAlmostEqual(now[d], good[d], places=2)

    def test_log_gone_keeps_values_marked(self):
        sid = self.d["system_id"]
        for p in set(r["source"] for r in history.recent_runs(10, kind="onsky")):
            os.remove(p)
        _set_app("SHOBS-FLUX 0.4.0")
        n0 = len(history.series_points("system", sid))
        got = history.rejudge()
        self.assertEqual(got["onsky"], 0)
        self.assertEqual(got["kept"], 3)
        pts = history.series_points("system", sid)
        self.assertEqual(len(pts), n0)
        self.assertTrue(all(p["judged_by"] == "0.4.0" for p in pts))
        self.assertEqual(history.stale_runs(), [])                 # not looked for again at every opening
        from matplotlib.figure import Figure
        import trend_plots
        fig = Figure()
        zp = history.series_points("system", sid, "bands.zero_point", "TG")
        trend_plots.draw_trend(fig, zp, "Zero point", "mag for 1 e-/s")
        labels = fig.axes[0].get_legend_handles_labels()[1]
        self.assertIn("judged by FLUX 0.4.0", labels)
        path = self.path("z.csv")
        history.write_csv(path, zp, "t", "0.5.1")
        with open(path, encoding="utf-8") as fh:
            self.assertIn("FLUX 0.4.0", fh.read())

    def test_new_runs_carry_todays_rules(self):
        key = history.record_run("onsky", "x.json", {"night": "2026-11-01"}, app="SHOBS-FLUX 0.5.0")
        row = next(r for r in history.recent_runs(10) if r["run_key"] == key)
        self.assertEqual(history.judged_by(row), "")
        self.assertEqual(history.judged_by({"app": "SHOBS-FLUX 0.4.0", "summary": "{}"}), "0.4.0")
        self.assertEqual(history.judged_by({"app": "test", "summary": "{}"}), "")


class TestDarksReadNoise(TempData):
    def test_dark_run_values_include_read_noise(self):
        import darks
        res = SimpleNamespace(temps={}, read_noise_e=1.12, date="2026-10-28")
        rows = darks.run_values(res, "cam__g1__o1__bin1")
        rn = [r for r in rows if r["key"] == "read_noise_e"]
        self.assertEqual(len(rn), 1)
        self.assertEqual((rn[0]["source"], rn[0]["unit"], rn[0]["channel"]), ("darks", "e-", None))
        res.read_noise_e = float("nan")
        self.assertFalse([r for r in darks.run_values(res, "x") if r["key"] == "read_noise_e"])

    def test_stored_dark_run_filled_in_or_left(self):
        history.record_run("darks", "D", {"kind": "dark", "read_noise_e": 1.119}, camera_id="cam", app="SHOBS-FLUX 0.3.0",
                           values=[{"key": "dark_current_e_per_px_s", "value": 4e-4, "unit": "e-/px/s",
                                    "channel": "-10C", "profile_id": "cam", "date": "2026-09-15"}])
        history.record_run("darks", "E", {"kind": "dark", "read_noise_e": None}, camera_id="cam", app="SHOBS-FLUX 0.3.0")
        _set_app("SHOBS-FLUX 0.3.0", "kind = 'darks'")
        self.assertEqual(history.rejudge()["darks"], 2)
        pts = history.series_points("camera", "cam", "read_noise_e", None)
        self.assertEqual([(p["value"], p["date"], p["value_source"]) for p in pts], [(1.119, "2026-09-15", "darks")])


class TestCheckReason(TempData):
    def test_old_check_runs_say_why(self):
        base = {"n_stars": 40, "n_over_linear": 0, "saturated_pixels": 0, "bias_adu": 500.0, "linear_limit": 52428,
                "gain_source": "header", "sky": {"G": {"adu": 560.0, "e": 30.0}}}
        history.record_run("check", "a.fit", dict(base, n_saturated_stars=4), status="bad", app="SHOBS-FLUX 0.1.0")
        history.record_run("check", "b.fit", dict(base, n_saturated_stars=0, n_over_linear=2), status="warn",
                           app="SHOBS-FLUX 0.1.0")
        history.record_run("check", "c.fit", dict(base, n_saturated_stars=0, sky={"G": {"adu": 40000.0, "e": 9e3}}),
                           status="bad", app="SHOBS-FLUX 0.1.0")
        _set_app("SHOBS-FLUX 0.1.0", "kind = 'check'")
        for r in history.recent_runs():                 # 0.1 wrote no "worst"
            con = sqlite3.connect(sp.runs_db_path())
            d = json.loads(r["summary"])
            d.pop("worst", None)
            con.execute("UPDATE runs SET summary = ? WHERE id = ?", (json.dumps(d), r["id"]))
            con.commit()
            con.close()
        rows = {r["source"]: history.status_text(r) for r in history.recent_runs()}
        self.assertEqual(rows["a.fit"], "⚠ 4 field stars saturated")              # not ✖ bad
        self.assertEqual(rows["b.fit"], "2 field stars over the linear limit")
        self.assertEqual(rows["c.fit"], "✖ sky over half the linear range")
        self.assertEqual(history.rejudge()["check"], 3)
        st = {r["source"]: r["status"] for r in history.recent_runs()}
        self.assertEqual(st, {"a.fit": "warn", "b.fit": "ok", "c.fit": "bad"})


class TestLabelsAndUnits(TempData):
    def test_axis_label(self):
        import trend_plots
        from matplotlib.figure import Figure
        self.assertEqual(history.axis_label("read_noise_e", "e-"), "Read noise (e⁻)")
        self.assertEqual(history.axis_label("bands.fwhm_in_focus", "arcsec"), "Seeing in focus, FWHM (″)")
        self.assertEqual(history.axis_label("bands.scintillation_cy", "Young C_Y"), "Scintillation C_Y")
        self.assertEqual(history.axis_label("bands.fwhm_imaging", "arcsec"), "Seeing, imaging focus (″)")
        fig = Figure()
        pts = [{"date": "2026-10-28", "value": 1.12, "sigma": None, "key": "read_noise_e", "unit": "e-"}]
        trend_plots.draw_trend(fig, pts, "Read noise", "e-")
        yl = fig.axes[0].yaxis.label
        self.assertEqual(yl.get_text(), "Read noise (e⁻)")
        self.assertGreaterEqual(yl.get_fontsize(), 10)

    def test_unify_units(self):
        p = lambda v, u: {"date": "2026-10-01", "value": v, "sigma": 0.1, "unit": u, "key": "k"}
        pts, unit, note = history.unify_units([p(2.0, "arcsec"), p(3.0, "″"), p(2.5, "arcsec")])
        self.assertEqual((len(pts), unit, note), (3, "arcsec", ""))
        pts, unit, note = history.unify_units([p(2.0, "s"), p(500.0, "ms"), p(3.0, "s")])
        self.assertEqual(unit, "s")
        self.assertAlmostEqual(pts[1]["value"], 0.5)
        self.assertAlmostEqual(pts[1]["sigma"], 1e-4)
        self.assertEqual(note, "")
        pts, unit, note = history.unify_units([p(2.0, "arcsec"), p(3.0, "arcsec"), p(9.0, "px")])
        self.assertEqual((len(pts), unit), (2, "arcsec"))
        self.assertIn("1 point left out", note)
        self.assertIn("px", note)
        self.assertEqual(history.unify_units([]), ([], "", ""))

    def test_imaging_focus_value_listed(self):
        self.assertEqual(history.VALUE_WORDS["bands.fwhm_imaging"], "Seeing, imaging focus")
        sid = "scope-2900mm__cam"
        history.record_run("onsky", "n.json", {"night": "2026-11-01"}, camera_id="cam", system_id=sid,
                           values=[{"key": "bands.fwhm_imaging", "value": 3.1, "unit": "arcsec", "channel": "TG",
                                    "profile_id": sid, "date": "2026-11-01"}])
        av = history.available("system", sid)
        self.assertIn(("bands.fwhm_imaging", "TG"), [(k, c) for k, c, _w, _n in av])
        self.assertTrue(any(w.startswith("Seeing, imaging focus") for _k, _c, w, _n in av))


class TestZeroFocalJoins(TempData):
    def night(self, sid, date, v):
        return history.record_run("onsky", f"{sid}-{date}.json", {"night": date}, camera_id=sid.split("__", 1)[1],
                                  system_id=sid, values=[{"key": "bands.fwhm_in_focus", "value": v, "unit": "arcsec",
                                                          "channel": "TG", "profile_id": sid, "date": date}])

    def test_joins_the_one_setup_of_its_camera(self):
        self.night("rc-2900mm__camA", "2026-10-01", 3.0)
        self.night("unknown-0mm__camA", "2026-10-02", 3.2)
        self.night("unknown-0mm__camB", "2026-10-03", 4.0)             # camera B has no setup with a focal length
        ids = [s["id"] for s in history.setups() if s["kind"] == "system"]
        self.assertEqual(ids, ["rc-2900mm__camA", "unknown-0mm__camB"])
        pts = history.series_points("system", "rc-2900mm__camA", "bands.fwhm_in_focus", "TG")
        self.assertEqual([p["date"] for p in pts], ["2026-10-01", "2026-10-02"])
        self.assertIn("joined", pts[1]["joined"])
        self.assertEqual(pts[0]["joined"], "")
        import history_page
        runs = history_page.runs_for({"kind": "system", "id": "rc-2900mm__camA"})
        self.assertEqual(len(runs), 2)
        self.assertTrue(any(r.get("joined") for r in runs))

    def test_two_setups_keep_it_separate(self):
        self.night("rc-2900mm__camA", "2026-10-01", 3.0)
        self.night("cdk-4250mm__camA", "2026-10-02", 2.0)
        self.night("unknown-0mm__camA", "2026-10-03", 3.2)
        ids = [s["id"] for s in history.setups() if s["kind"] == "system"]
        self.assertIn("unknown-0mm__camA", ids)
        self.assertEqual(len(history.series_points("system", "rc-2900mm__camA")), 1)
        self.assertEqual(len(history.series_points("system", "unknown-0mm__camA")), 1)


try:
    import tkinter as tk
    _r = tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class TestHistoryPageRejudges(TempData):
    def setUp(self):
        super().setUp()
        from tests import m5_data
        self.d = m5_data.make(self.tmp)
        _set_app("SHOBS-FLUX 0.4.0")
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

    def test_opening_the_page_judges_older_runs(self):
        app = self.app
        app.show_page(4)
        hp = app.history_page
        self.assertTrue(app.jobs.wait(120))
        self.assertIn("judged again by today's rules", hp.msg_var.get())
        self.assertEqual(history.stale_runs(), [])
        self.assertFalse(hp.rejudge_older())                       # nothing left: no job next time
        self.assertTrue(hp.points)
        self.assertNotIn("judged by FLUX", " ".join(hp.tree.set(i, "status") for i in hp.tree.get_children()))


if __name__ == "__main__":
    unittest.main()
