"""sky_folder.py and catalogs.py: a folder of synthetic light frames becomes an on-sky log that onsky.py learns the
true zero point and extinction from (OSC with a plate solution, OSC matched blind, mono Sloan i′ mirrored), saturated
and crowded stars, folders without lights, the catalog parser, band names, the Gaia relations and the cache. No
network: the catalog comes from the synthetic field."""
from __future__ import annotations

import json
import os
import unittest

from tests.base import TempData

import catalogs
import onsky
import sky_folder
import synth_lights

NO_PROFILE = (lambda cam_id: None)


class FolderCase(TempData):
    def run_case(self, **kw):
        kw.setdefault("n_frames", 14)
        truth = synth_lights.make_folder(self.path("lights"), **kw)
        steps = []
        res = sky_folder.run_folder(self.path("lights"), catalog=truth["fetch"], cam_lookup=NO_PROFILE,
                                    step=steps.append)
        self.assertIsNotNone(res.doc)
        self.assertTrue(any("Reading frame 3 of" in s for s in steps))
        ds = onsky.load_dataset(res.doc, cam_lookup=NO_PROFILE)
        night = onsky.analyse(ds)
        return truth, res, ds, night

    def check_recovered(self, truth, res, ds, night):
        self.assertEqual(res.n_frames, len(truth["files"]))
        self.assertAlmostEqual(ds.e_per_unit, truth["gain"], places=6)
        self.assertAlmostEqual(ds.px_arcsec, truth["px_arcsec"], places=4)
        self.assertTrue(night.photometric, night.lines)
        self.assertAlmostEqual(night.zp1, truth["zp_e"], delta=0.03)
        self.assertAlmostEqual(night.k, truth["k"], delta=0.03)
        self.assertAlmostEqual(night.fwhm_arcsec, truth["seeing"], delta=0.3)
        self.assertAlmostEqual(night.sky_mag, truth["sky_mag"], delta=0.5)      # approximate without a profile
        self.assertGreaterEqual(res.n_matched, 20)


class TestFolderRuns(FolderCase):
    def test_a_osc_with_wcs(self):
        truth, res, ds, night = self.run_case(osc=True, wcs=True)
        self.check_recovered(truth, res, ds, night)
        doc = res.doc
        self.assertEqual(doc["format"], "shobs-onsky-log")
        self.assertEqual(doc["band"], "TG")
        self.assertEqual(doc["catalog_band"], "V")
        self.assertTrue(doc["written_by"].startswith("SHOBS-FLUX "))
        self.assertEqual(ds.source, "SHOBS-FLUX")
        m = doc["measurement"]
        self.assertTrue(m["flux_is_adu_sum"])
        self.assertEqual((m["raw_px_per_px"], m["color"], m["debayer_mode"]), (2, "G", None))
        self.assertEqual(doc["setup"]["bayer_pattern"], "RGGB")
        self.assertEqual(doc["setup"]["camera_id"], "zwo-asi2600mc-air__g100__o50__bin1")
        self.assertEqual(doc["stars"][0]["role"], "target")
        self.assertTrue(all(s["role"] == "field" for s in doc["stars"][1:]))
        self.assertTrue(any("plate solution" in t for _, t in res.lines))
        self.assertTrue(any("1 dark, 1 flat" in t for _, t in res.lines))
        self.assertEqual(doc["frames"][0]["airmass"], 1.1)

    def test_b_osc_blind_match_rotated(self):
        truth, res, ds, night = self.run_case(osc=True, wcs=False, rotation_deg=30.0)
        self.check_recovered(truth, res, ds, night)
        line = next(t for _, t in res.lines if "Stars placed" in t)
        self.assertIn("rotation 30°", line)
        self.assertNotIn("mirrored", line)

    def test_c_mono_sloan_i_mirrored(self):
        truth, res, ds, night = self.run_case(osc=False, wcs=False, flip=True, n_frames=20)
        self.check_recovered(truth, res, ds, night)
        self.assertEqual(res.doc["band"], "SI")
        self.assertEqual(res.doc["catalog_band"], "SI")
        self.assertIsNone(res.doc["setup"]["bayer_pattern"])
        self.assertEqual(res.doc["measurement"]["raw_px_per_px"], 1)
        self.assertEqual(ds.color, "M")
        self.assertIn("mirrored", next(t for _, t in res.lines if "Stars placed" in t))

    def test_d_saturated_and_crowded(self):
        truth, res, ds, night = self.run_case(osc=True, wcs=True, bright_mag=7.0, crowded_pair=True)
        names = [s["name"] for s in res.doc["stars"]]
        for n in truth["pair"]:
            self.assertNotIn(n, names)
        self.assertTrue(any("left out as crowded" in t for _, t in res.lines))
        sat = truth["saturated"]
        self.assertIn(sat, names)
        self.assertGreaterEqual(res.doc["frames"][0]["stars"][sat]["peak_adu"], onsky.LINEAR_GUESS_ADU)
        self.assertIn("saturated", night.stars_out.get(sat, ""))
        self.assertAlmostEqual(night.zp1, truth["zp_e"], delta=0.03)


class TestFolderProblems(TempData):
    def test_e_no_lights(self):
        import numpy as np
        import synth
        os.makedirs(self.path("cal"))
        synth.write_fits(self.path("cal/Flat_0001.fit"), np.full((64, 64), 20000), {"IMAGETYP": "Flat"})
        synth.write_fits(self.path("cal/Bias_0001.fit"), np.full((64, 64), 500), {"IMAGETYP": "Bias"})
        with self.assertRaisesRegex(sky_folder.SkyFolderError, r"^There are no light frames in that folder"):
            sky_folder.run_folder(self.path("cal"), catalog=lambda *a: {})
        with self.assertRaisesRegex(sky_folder.SkyFolderError, "no light frames"):
            sky_folder.scan(self.path("cal"))
        os.makedirs(self.path("empty"))
        with self.assertRaisesRegex(sky_folder.SkyFolderError, "no FITS or XISF frames"):
            sky_folder.scan(self.path("empty"))
        with self.assertRaisesRegex(sky_folder.SkyFolderError, "not there"):
            sky_folder.run_folder(self.path("nowhere"))

    def test_scan(self):
        synth_lights.make_folder(self.path("lights"), n_frames=3, osc=False, wcs=False, size=(300, 200))
        s = sky_folder.scan(self.path("lights"))
        self.assertEqual((s["n_lights"], s["osc"], s["has_wcs"], s["has_pointing"]), (3, False, False, True))
        self.assertEqual(s["bands"], {"SI": 3})
        self.assertEqual((s["band"], s["solved"]), ("SI", False))
        self.assertEqual(s["others"], {"flat": 1, "dark": 1})
        self.assertEqual(s["exposures"], [60.0])
        self.assertEqual(s["target"], "Synthetic Field")
        self.assertEqual(s["night"], "2026-10-20")

    def test_g_save_log_reads_back(self):
        truth = synth_lights.make_folder(self.path("lights"), n_frames=4, size=(500, 400), n_stars=40, n_faint=10)
        res = sky_folder.run_folder(self.path("lights"), catalog=truth["fetch"], cam_lookup=NO_PROFILE)
        red = sky_folder.run_folder(self.path("lights"), channel="TR", catalog=truth["fetch"], cam_lookup=NO_PROFILE)
        self.assertEqual((red.doc["band"], red.doc["catalog_band"], red.doc["measurement"]["color"]), ("TR", "R", "R"))
        path = sky_folder.save_log(res.doc)
        self.assertTrue(os.path.basename(path).startswith("2026-10-20__synthetic-field__flux-"))
        self.assertEqual(os.path.dirname(path), os.path.join(os.environ["SHOBS_DATA_HOME"], "onsky"))
        good, bad = onsky.read_logs(onsky.log_files(), cam_lookup=NO_PROFILE)
        self.assertEqual(bad, [])
        self.assertEqual(len(good), 1)
        self.assertEqual(len(good[0].jd), 4)
        self.assertEqual(good[0].band, "TG")

    def test_airmass_from_the_site(self):
        self.assertAlmostEqual(float(sky_folder.airmass_kasten_young(90.0)), 1.0, delta=0.001)
        self.assertAlmostEqual(float(sky_folder.airmass_kasten_young(30.0)), 1.995, delta=0.005)
        truth = synth_lights.make_folder(self.path("lights"), n_frames=3, size=(400, 300), n_stars=30, n_faint=5,
                                         airmass_header=False)
        res = sky_folder.run_folder(self.path("lights"), catalog=truth["fetch"], cam_lookup=NO_PROFILE)
        self.assertTrue(any("Airmass worked out" in t for _, t in res.lines))
        fm = res.doc["frames"]
        want = sky_folder.airmass_of(res.doc["field_centre"]["ra"], res.doc["field_centre"]["dec"],
                                     [f["jd_mid"] for f in fm], 35.36, -81.96)
        for f, x in zip(fm, want):
            self.assertAlmostEqual(f["airmass"], float(x), places=3)
            self.assertGreater(f["airmass"], 1.0)
        # no AIRMASS and no site anywhere: said, and no airmass
        for p in truth["files"]:
            from astropy.io import fits
            with fits.open(p, mode="update") as h:
                del h[0].header["SITELAT"]
                del h[0].header["SITELONG"]
        res = sky_folder.run_folder(self.path("lights"), catalog=truth["fetch"], cam_lookup=NO_PROFILE)
        self.assertTrue(any("type the site" in t for _, t in res.lines))
        self.assertIsNone(res.doc["frames"][0]["airmass"])
        res = sky_folder.run_folder(self.path("lights"), catalog=truth["fetch"], cam_lookup=NO_PROFILE,
                                    site={"lat": 35.36, "lon": -81.96, "elevation": 300})
        self.assertGreater(res.doc["frames"][0]["airmass"], 1.0)


TSV = """#
#   VizieR Astronomical Server vizier.cds.unistra.fr
#Column RAJ2000 (F10.6) Right ascension
RAJ2000\tDEJ2000\tVmag\tBmag\tg'mag\tr'mag\ti'mag
deg\tdeg\tmag\tmag\tmag\tmag\tmag
----------\t----------\t------\t------\t------\t------\t------
307.125000\t+41.250000\t10.500\t11.100\t10.800\t10.300\t10.200
307.130000\t+41.260000\t12.000\t\t12.300\t11.800\t
"""
GAIA_TSV = """Source\tRA_ICRS\tDE_ICRS\tGmag\tBP-RP
\tdeg\tdeg\tmag\tmag
-------\t-------\t-------\t-----\t-----
111\t307.1250002\t41.2500001\t10.30\t0.80
222\t307.2000000\t41.3000000\t13.00\t1.00
333\t307.2100000\t41.3100000\t14.00\t
"""


class TestCatalogs(TempData):
    def test_tsv_parser(self):
        rows, inc = catalogs.parse_tsv(TSV)
        self.assertEqual(inc, "")
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]["g'mag"], "10.800")
        self.assertEqual(rows[1]["Bmag"], "")
        rows, inc = catalogs.parse_tsv("#INFO Error: query timeout\n" + TSV)
        self.assertIn("timeout", inc)

    def test_band_of_filter(self):
        for text, band in (("g'", "SG"), ("gp", "SG"), ("SG", "SG"), ("Sloan g", "SG"), ("g", "SG"), ("r'", "SR"),
                           ("i′", "SI"), ("z'", "SZ"), ("SDSS i", "SI"), ("V", "V"), ("B", "B"), ("R", "R"),
                           ("I", "I"), ("L", "CV"), ("Lum", "CV"), ("Clear", "CV"), ("CV", "CV"), ("", "CV"),
                           (None, "CV"), ("Ha", "CV")):
            self.assertEqual(catalogs.band_of_filter(text), band, text)
        self.assertEqual(catalogs.catalog_band("TG"), "V")
        self.assertEqual(catalogs.catalog_band("SI"), "SI")
        self.assertEqual(catalogs.catalog_band("XX"), "V")

    def test_gaia_relations(self):
        self.assertAlmostEqual(catalogs.gaia_v(10.0, 1.0), 10.21414, places=5)
        self.assertAlmostEqual(catalogs.gaia_r(10.0, 1.0), 9.761135, places=5)
        self.assertIsNone(catalogs.gaia_v(10.0, 5.5))
        self.assertIsNone(catalogs.gaia_r(10.0, 3.0))
        sd = catalogs.gaia_sdss(10.0, 1.0)
        self.assertAlmostEqual(sd["SG"], 10.565, places=4)
        self.assertAlmostEqual(sd["SR"], 10.0 - (-0.09837 + 0.08592 + 0.1907 - 0.1701 + 0.02263), places=5)
        self.assertNotIn("SG", catalogs.gaia_sdss(10.0, 0.2))            # outside g's colour range
        pr = catalogs.sdss_to_primed({"SG": 10.5, "SR": 10.0, "SI": 9.8, "SZ": 9.7})
        self.assertAlmostEqual(pr["SG"], 10.5 - 0.060 * (0.5 - 0.53), places=6)
        self.assertAlmostEqual(pr["SR"], 10.0 - 0.035 * (0.2 - 0.21), places=6)
        self.assertAlmostEqual(pr["SI"], 9.8 - 0.041 * (0.2 - 0.21), places=6)
        self.assertAlmostEqual(pr["SZ"], 9.7 + 0.030 * (0.1 - 0.09), places=6)
        self.assertEqual(catalogs.sdss_to_primed({"SG": 10.5}), {"SG": 10.5})   # no partner colour: as is

    def test_fetch_field_merge_and_cache(self):
        calls = []

        def fetcher(url, timeout):
            calls.append(url)
            self.assertIn("/viz-bin/asu-tsv?", url)
            if "apass9" in url:
                return TSV.encode()
            if "gaiadr3" in url:
                self.assertIn("Gmag=%3C17.5", url)
                return GAIA_TSV.encode()
            raise OSError("no such catalog")

        out = catalogs.fetch_field(307.125, 41.25, 0.2, "V", fetcher=fetcher)
        self.assertEqual(len(calls), 2)
        self.assertEqual(out["incomplete"], "")
        names = [s["name"] for s in out["stars"]]
        self.assertEqual(len(out["all"]), 4)                              # Gaia 111 = APASS star 1: once
        self.assertEqual(names[0], "APASS J202830.0+411500")              # APASS wins for V
        self.assertEqual(out["stars"][0]["mag"], 10.5)
        self.assertIn("Gaia DR3 222", names)                              # Gaia fills in
        self.assertNotIn("Gaia DR3 333", names)                           # no colour: no V
        g222 = next(s for s in out["stars"] if s["name"] == "Gaia DR3 222")
        self.assertAlmostEqual(g222["mag"], catalogs.gaia_v(13.0, 1.0), places=3)
        # the second time it comes from the cache: no new calls
        again = catalogs.fetch_field(307.125, 41.25, 0.2, "V", fetcher=fetcher)
        self.assertEqual(len(calls), 2)
        self.assertEqual(again["stars"], out["stars"])
        self.assertIn("kept from an earlier run", again["service"])
        self.assertTrue(os.path.isdir(os.path.join(os.environ["SHOBS_DATA_HOME"], "cache")))
        # z′ comes from Gaia only
        z = catalogs.fetch_field(307.125, 41.25, 0.2, "SZ", fetcher=fetcher)
        self.assertEqual(sorted(s["catalog"] for s in z["stars"]), ["Gaia DR3", "Gaia DR3"])

    def test_incomplete_not_cached_and_failures(self):
        n = []

        def cut(url, timeout):
            n.append(url)
            return ("#INFO truncated: result limit reached\n" + GAIA_TSV).encode()

        rows, inc, _ = catalogs.vizier(catalogs.GAIA, 1.0, 2.0, 0.1, catalogs.GAIA_COLS, fetcher=cut)
        self.assertTrue(inc and len(rows) == 3)
        catalogs.vizier(catalogs.GAIA, 1.0, 2.0, 0.1, catalogs.GAIA_COLS, fetcher=cut)
        self.assertEqual(len(n), 2)                                       # not kept: asked again

        def down(url, timeout):
            raise OSError("network is unreachable")

        old_dir = catalogs.shobs_p_cache_dir
        catalogs.shobs_p_cache_dir = lambda: self.path("no_cache_here")
        try:
            with self.assertRaisesRegex(catalogs.CatalogError, "could not be reached"):
                catalogs.fetch_field(10.0, 20.0, 0.1, "V", fetcher=down)
            # VizieR down, but SHOBS-P reduced this field once: its saved catalog answer is used instead
            cache = self.path("p_cache")
            os.makedirs(cache)
            stars = [{"auid": f"APASS J{i:03d}", "ra": 10.0 + 0.004 * i, "dec": 20.0, "mags": {"V": 11.0 + 0.1 * i},
                      "bright": 11.0 + 0.1 * i} for i in range(15)]
            gaia = [{"auid": f"Gaia DR3 {i}", "ra": 10.0 + 0.004 * i, "dec": 20.0 + 0.5 / 3600,
                     "mags": {"V": 11.02 + 0.1 * i}, "bright": 10.8 + 0.1 * i} for i in range(15)] + \
                [{"auid": "Gaia DR3 far", "ra": 30.0, "dec": -5.0, "mags": {"V": 9.0}, "bright": 9.0}]
            for name, st in (("APASS_abc.json", stars), ("Gaia_def.json", gaia), ("Tycho_x.json", stars)):
                with open(os.path.join(cache, name), "w") as fh:
                    json.dump({"key": {}, "created": 0, "stars": st}, fh)
            catalogs.shobs_p_cache_dir = lambda: cache
            got = catalogs.fetch_field(10.03, 20.0, 0.1, "V", fetcher=down)
            self.assertIn("SHOBS-P's catalog cache", got["service"])
            self.assertEqual(len(got["stars"]), 15)                      # APASS and Gaia merged, the far star out
            self.assertTrue(all(s["catalog"] == "APASS DR9" for s in got["stars"]))
            with self.assertRaisesRegex(catalogs.CatalogError, "does not cover this field"):
                catalogs.fetch_field(200.0, -40.0, 0.1, "V", fetcher=down)
        finally:
            catalogs.shobs_p_cache_dir = old_dir
        notes = []
        self.assertEqual(catalogs.fetch_variables(10.0, 20.0, 0.1, fetcher=down, notes=notes), [])
        self.assertIn("VSX", notes[0])
        self.assertAlmostEqual(catalogs.parse_angle("20 28 30.0", hours=True), 307.125)
        self.assertAlmostEqual(catalogs.parse_angle("-41:15:00"), -41.25)

class TestReviewCases(FolderCase):
    def test_stale_header_solution_with_dither(self):
        # every header carries the first frame's solution while the field moves: stars are followed, not trusted
        truth, res, ds, night = self.run_case(osc=True, wcs=True, stale_wcs=True, n_frames=16, dither_px=8.0)
        self.assertTrue(night.photometric, night.lines)
        self.assertAlmostEqual(night.zp1, truth["zp_e"], delta=0.03)

    def test_cadence_from_all_lights(self):
        truth = synth_lights.make_folder(self.path("lights"), n_frames=24, cadence_s=70.0, exptime=60.0)
        res = sky_folder.run_folder(self.path("lights"), catalog=truth["fetch"], cam_lookup=NO_PROFILE, max_frames=12)
        self.assertEqual(res.n_frames, 12)
        self.assertAlmostEqual(res.doc["cadence"]["interval_s"], 70.0, delta=0.5)
        night = onsky.analyse(onsky.load_dataset(res.doc, cam_lookup=NO_PROFILE))
        self.assertAlmostEqual(night.overhead_s, 10.0, delta=0.5)

    def test_page_calls_run_folder_with_real_arguments(self):
        import inspect
        params = inspect.signature(sky_folder.run_folder).parameters
        for kw in ("channel", "step", "elevation_m", "site"):
            self.assertIn(kw, params)

    def test_web_page_answer_never_cached(self):
        calls = []

        def portal(url, timeout):
            calls.append(url)
            return b"<html><body>Sign in to the hotel wifi</body></html>"
        with self.assertRaises(catalogs.CatalogError):
            catalogs.vizier(catalogs.GAIA, 1.0, 2.0, 0.1, catalogs.GAIA_COLS, fetcher=portal)
        with self.assertRaises(catalogs.CatalogError):
            catalogs.vizier(catalogs.GAIA, 1.0, 2.0, 0.1, catalogs.GAIA_COLS, fetcher=portal)
        self.assertEqual(len(calls), 4)                                   # asked again each time (2 hosts)

    def test_sexagesimal_site(self):
        self.assertAlmostEqual(sky_folder._angle_key({"SITELAT": "35 18 10"}, "SITELAT"), 35.30278, places=4)
        self.assertAlmostEqual(sky_folder._angle_key({"SITELONG": "-81:56:24"}, "SITELONG"), -81.94, places=4)


if __name__ == "__main__":
    unittest.main()
