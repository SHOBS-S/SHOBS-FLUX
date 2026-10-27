"""shobs_profile.py, format v1: the three approved example files (27 Oct 2026) round-trip byte for byte, names,
value records, saving with history, finding profiles and specs, reading spec curves."""
from __future__ import annotations

import glob
import json
import os
import unittest

from tests.base import FIXTURES, TempData

import shobs_profile as sp

EXAMPLES = sorted(glob.glob(os.path.join(FIXTURES, "*.json")))


class TestExamples(TempData):
    def test_three_examples_present(self):
        self.assertEqual(len(EXAMPLES), 3)

    def test_round_trip_byte_identical(self):
        """M1 gate: the approved examples read and written back are byte-identical."""
        for path in EXAMPLES:
            with open(path, "rb") as fh:
                raw = fh.read()
            doc = sp.load(path)
            self.assertEqual(sp.dumps(doc).encode("utf-8"), raw, os.path.basename(path))
            out = self.path("out.json")
            sp.write_json(out, doc)
            with open(out, "rb") as fh:
                self.assertEqual(fh.read(), raw, os.path.basename(path))

    def test_examples_validate(self):
        for path in EXAMPLES:
            self.assertEqual(sp.validate(sp.load(path)), [], os.path.basename(path))

    def test_ids_match_the_examples(self):
        cam = sp.load(os.path.join(FIXTURES, "cameras_zwo-asi2600mc-air__g100__o50__bin1.json"))
        m = cam["mode"]
        self.assertEqual(sp.camera_id(cam["camera"]["instrume"], m["gain_setting"], m["offset"], m["binning"]),
                         cam["profile_id"])
        sysp = sp.load(os.path.join(FIXTURES, "systems_14in-rc-2900mm__zwo-asi2600mc-air__g100__o50__bin1.json"))
        t = sysp["telescope"]
        self.assertEqual(sp.system_id(t["name"], t["focal_mm"], sysp["camera_profile"]), sysp["profile_id"])

    def test_save_keeps_bytes_and_history(self):
        cam = sp.load(os.path.join(FIXTURES, "cameras_zwo-asi2600mc-air__g100__o50__bin1.json"))
        p = sp.save_profile(cam)
        with open(p, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), sp.dumps(cam))
        self.assertEqual(sp.save_profile(cam), p, "identical content: nothing changes")
        self.assertEqual(sp.history_of("cameras", cam["profile_id"]), [])
        cam2 = json.loads(json.dumps(cam))
        cam2["values"]["linear_limit_adu"]["value"] = 33000
        sp.save_profile(cam2, "test")
        hist = sp.history_of("cameras", cam["profile_id"])
        self.assertEqual(len(hist), 1)
        with open(hist[0], encoding="utf-8") as fh:
            self.assertEqual(fh.read(), sp.dumps(cam), "the old revision is kept as it was")
        now = sp.load(p)
        self.assertEqual(now["revision"], 2)
        self.assertEqual(now["values"]["linear_limit_adu"]["value"], 33000)
        self.assertEqual(now["written_by"], "test")

    def test_invalid_profile_refused(self):
        cam = sp.load(os.path.join(FIXTURES, "cameras_zwo-asi2600mc-air__g100__o50__bin1.json"))
        cam["values"]["gain_e_per_adu"]["source"] = "guess"
        with self.assertRaises(sp.ProfileError):
            sp.save_profile(cam)
        sysp = sp.load(os.path.join(FIXTURES, "systems_14in-rc-2900mm__zwo-asi2600mc-air__g100__o50__bin1.json"))
        sysp["site"] = {"lat": 35.3}
        self.assertTrue(any("site" in p for p in sp.validate(sysp)))

    def test_load_refuses_other_files(self):
        p = self.path("x.json")
        with open(p, "w") as fh:
            fh.write('{"hello": 1}')
        with self.assertRaises(sp.ProfileError):
            sp.load(p)
        with open(p, "w") as fh:
            fh.write('{"format": "shobs-camera-profile", "format_version": 2}')
        with self.assertRaisesRegex(sp.ProfileError, "newer"):
            sp.load(p)
        with open(p, "w") as fh:
            fh.write('{"format": ')
        with self.assertRaisesRegex(sp.ProfileError, "JSON"):
            sp.load(p)


class TestNamesAndRecords(TempData):
    def test_slug_and_ids(self):
        self.assertEqual(sp.slug("ZWO ASI2600MC Air"), "zwo-asi2600mc-air")
        self.assertEqual(sp.slug("Andor CCD/EMCCD (SDK2) (ASCOM)"), "andor-ccd-emccd-sdk2-ascom")
        self.assertEqual(sp.slug(""), "unknown")
        self.assertEqual(sp.camera_id("Andor iKon", 1, None, 1), "andor-ikon__g1__ona__bin1")
        self.assertEqual(sp.camera_id("Cam", 2.5, 10, 2), "cam__g2p5__o10__bin2")
        self.assertEqual(sp.system_id("14in RC", 2905.4, "c"), "14in-rc-2905mm__c")

    def test_record(self):
        r = sp.record(0.24, "e-/ADU", "measured", sigma=0.002, n=40, note="x")
        self.assertEqual(list(r), list(sp.RECORD_KEYS) + ["note"])
        self.assertEqual(sp.value_of(r), 0.24)
        nan = sp.record(float("nan"), "e-", "measured")
        self.assertIsNone(nan["value"])
        self.assertEqual(nan["source"], "none")
        with self.assertRaises(ValueError):
            sp.record(1, "x", "guess")

    def test_self_check(self):
        self.assertEqual(sp.self_check(), [])

    def test_setup_key_asiair_and_andor(self):
        asiair = {"INSTRUME": "ZWO ASI2600MC Air", "TELESCOP": "EQMod Mount", "GAIN": 100, "OFFSET": 50,
                  "EGAIN": 0.242862924933434, "XBINNING": 1, "XPIXSZ": 3.75999999046326, "BAYERPAT": "RGGB",
                  "FOCALLEN": 2905, "SET-TEMP": -10}
        k = sp.setup_key(asiair)
        self.assertEqual(k["camera_id"], "zwo-asi2600mc-air__g100__o50__bin1")
        self.assertEqual(k["bayer_pattern"], "RGGB")
        self.assertAlmostEqual(k["pixel_um"], 3.76, places=3)
        self.assertEqual(k["telescop_header"], "EQMod Mount")
        self.assertEqual(sp.setup_missing(k), [])
        andor = {"INSTRUME": "Andor CCD/EMCCD (SDK2) (ASCOM)", "GAIN": 1, "XBINNING": 1, "XPIXSZ": 13.5,
                 "FOCALLEN": 4250.0}
        k = sp.setup_key(andor)
        self.assertEqual(k["camera_id"], "andor-ccd-emccd-sdk2-ascom__g1__ona__bin1")
        self.assertIsNone(k["e_per_adu"])
        self.assertIsNone(k["bayer_pattern"])
        self.assertEqual(sp.setup_missing(k), ["OFFSET"])
        self.assertEqual(sp.setup_key({"BAYERPAT": "NONE"})["bayer_pattern"], "MONO")
        k = sp.setup_key({"XBINNING": 2, "XPIXSZ": 7.52})
        self.assertAlmostEqual(k["pixel_um"], 3.76)


class TestFinding(TempData):
    def test_spec_found_by_instrume_and_read_at_gain(self):
        spec = sp.find_spec("ZWO ASI2600MC Duo", (FIXTURES,))
        self.assertIsNotNone(spec)
        self.assertIsNone(sp.find_spec("ZWO ASI533MC Pro", (FIXTURES,)))
        self.assertAlmostEqual(sp.spec_at(spec, "read_noise_e", 100), 1.3)
        self.assertAlmostEqual(sp.spec_at(spec, "read_noise_e", 50), 2.2)
        self.assertAlmostEqual(sp.spec_at(spec, "read_noise_e", 300), 1.3, msg="flat beyond the end")
        self.assertIsNone(sp.spec_at(spec, "nothing", 100))

    def test_spec_jump(self):
        spec = {"curves": {"rn": [[0, 3.5], [99, 3.0], [100, 1.5], [100, 1.4], [200, 1.0]]}}
        self.assertAlmostEqual(sp.spec_at(spec, "rn", 100), 1.4, msg="the later point at a jump wins")
        self.assertAlmostEqual(sp.spec_at(spec, "rn", 150), 1.2)

    def test_find_camera_and_system(self):
        cam = sp.load(os.path.join(FIXTURES, "cameras_zwo-asi2600mc-air__g100__o50__bin1.json"))
        sp.save_profile(cam)
        self.assertEqual(sp.find_camera_profile(cam["profile_id"])["profile_id"], cam["profile_id"])
        self.assertIsNone(sp.find_camera_profile("nope__g1__o1__bin1"))
        sysp = sp.load(os.path.join(FIXTURES, "systems_14in-rc-2900mm__zwo-asi2600mc-air__g100__o50__bin1.json"))
        sp.save_profile(sysp)
        self.assertIsNotNone(sp.find_system_profile(cam["profile_id"], 2905), "FOCALLEN 2905 vs 2900: same scope")
        self.assertIsNone(sp.find_system_profile(cam["profile_id"], 1600))
        self.assertIsNotNone(sp.find_system_profile(cam["profile_id"]))
        v, rec = sp.camera_value(cam, "gain_e_per_adu", "G")
        self.assertEqual(v, 0.2429, "per-channel null → the camera-wide value")
        self.assertEqual(rec["source"], "header")
        v, rec = sp.camera_value(cam, "bias_level_adu")
        self.assertIsNone(v)

    def test_iter_records_counts(self):
        cam = sp.load(os.path.join(FIXTURES, "cameras_zwo-asi2600mc-air__g100__o50__bin1.json"))
        self.assertEqual(len(list(sp.iter_records(cam))), 9)


if __name__ == "__main__":
    unittest.main()


class TestBuiltinSpec(TempData):
    """The ASI2600MC spec read from ZWO's three charts (27 Oct 2026)."""

    def test_builtin_spec(self):
        import instrument
        spec = sp.find_spec("ZWO ASI2600MC Air", (instrument.BUILTIN_SPECS,))
        self.assertEqual(sp.validate(spec), [])
        self.assertAlmostEqual(sp.spec_at(spec, "read_noise_e", 100), 1.31, msg="HCG value from gain 100 up")
        self.assertAlmostEqual(sp.spec_at(spec, "read_noise_e", 99), 3.03, places=1)
        self.assertAlmostEqual(sp.spec_at(spec, "gain_e_per_adu", 100), 0.24)
        self.assertAlmostEqual(instrument.dark_from_curve(spec["dark_current_curve"]["points"], -10), 0.00054)
        self.assertAlmostEqual(instrument.dark_from_curve(spec["dark_current_curve"]["points"], -12.5),
                               (0.00054 * 0.00023) ** 0.5, places=6)
        cam = instrument.camera_in_use(sp.setup_key({"INSTRUME": "ZWO ASI2600MC Air", "GAIN": 100, "OFFSET": 50,
                                                     "XBINNING": 1}), temp_c=-10.0)
        self.assertAlmostEqual(cam.value("dark_current_e_per_px_s"), 0.00054)
        self.assertEqual(cam.source("dark_current_e_per_px_s"), "spec")
