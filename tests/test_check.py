"""image_check.py on synthetic frames with known camera values: the sky in electrons, the bias worked out from the
sky noise, saturated and over-linear stars, FWHM, donuts, the time to reach the linear limit, and calibration frames."""
from __future__ import annotations

import math
import unittest

from tests.base import TempData

import image_check as ic
import shobs_profile as sp
import synth

SHAPE = (600, 800)


def kinds(res, k):
    return [t for kk, t in res.lines if kk == k]


class TestCheck(TempData):
    def frame(self, name="f.fit", **kw):
        kw.setdefault("stars", synth.star_list(SHAPE, 20, brightest=1.0e5))
        data, hdr = synth.make_star_frame(shape=SHAPE, **kw)
        return synth.write_fits(self.path(name), data, hdr)

    def test_sky_and_bias_recovered(self):
        # read noise 3 e-: the value FLUX assumes for a camera it has no spec for
        res = ic.check_image(self.frame(sky_e=60.0, gain=0.25, read_noise=3.0, bias=500.0,
                                        channel_sky={"R": 0.6, "G": 1.0, "B": 0.8}))
        b, src, _ = res.bias
        self.assertEqual(src, "derived")
        self.assertLess(abs(b - 500.0), 12.0, f"bias {b:.1f}")
        g = res.channels["G"]
        self.assertLess(abs(g["sky_e"] - 60.0) / 60.0, 0.15, f"green sky {g['sky_e']:.1f} e-")
        self.assertLess(abs(res.channels["R"]["sky_e"] - 36.0) / 36.0, 0.2)
        self.assertAlmostEqual(g["sky_rate"], g["sky_e"] / 60.0)
        self.assertTrue(any("Sky-limited" in t or "sky-limited" in t for t in kinds(res, "ok")))

    def test_known_bias_and_noise_check(self):
        cam = {"bias_level_adu": sp.record(500.0, "ADU", "measured")}
        import instrument
        p = self.frame(sky_e=50.0)
        frame_cam = instrument.camera_in_use(sp.setup_key({"INSTRUME": "Synthetic Cam", "GAIN": 100, "OFFSET": 50,
                                                           "XBINNING": 1, "EGAIN": 0.25}))
        doc = instrument.starting_camera_profile(frame_cam, {}, "test")
        doc["values"].update(cam)
        doc["values"]["read_noise_e"] = sp.record(1.5, "e-", "measured")
        sp.save_profile(doc)
        res = ic.check_image(p)
        self.assertEqual(res.bias, (500.0, "measured", "from the camera profile"))
        self.assertTrue(any("Sky noise matches" in t for t in kinds(res, "ok")), res.lines)
        # a wrong gain in the profile is caught by the noise check
        doc = sp.find_camera_profile(frame_cam.cam_id)
        doc["values"]["gain_e_per_adu"] = sp.record(0.5, "e-/ADU", "user")
        sp.save_profile(doc)
        res = ic.check_image(p)
        self.assertTrue(any("does not match" in t for t in kinds(res, "warn")), res.lines)

    def test_saturated_and_over_linear_stars(self):
        stars = [(200.0, 200.0, 5.0e6), (500.0, 300.0, 2.5e5), (650.0, 450.0, 2.0e4)] + \
            synth.star_list(SHAPE, 8, brightest=1.0e4, seed=5)
        res = ic.check_image(self.frame(stars=stars, sky_e=30.0))
        by_pos = {(round(s.x * 2 / 10), round(s.y * 2 / 10)): s for s in res.stars}
        sat = by_pos.get((20, 20))
        self.assertIsNotNone(sat)
        self.assertEqual(sat.status, "bad")
        self.assertTrue(kinds(res, "bad"))
        mid = by_pos.get((50, 30))
        self.assertIsNotNone(mid)
        self.assertEqual(mid.status, "warn", f"peak {mid.raw_peak:.0f}")
        self.assertTrue(kinds(res, "warn"))
        faint = by_pos.get((65, 45))
        self.assertEqual(faint.status, "ok")
        # linear-limit time: 60 s × (limit − sky) / (peak − sky)
        self.assertTrue(math.isfinite(faint.t_linear) and faint.t_linear > 60.0)

    def test_fwhm_recovered(self):
        res = ic.check_image(self.frame(fwhm_px=6.0, stars=synth.star_list(SHAPE, 15, brightest=2.0e5)))
        f = [s.fwhm_px for s in res.stars if math.isfinite(s.fwhm_px)]
        self.assertGreater(len(f), 5)
        med = sorted(f)[len(f) // 2]
        self.assertLess(abs(med - 3.0) / 3.0, 0.2, f"superpixel FWHM {med:.2f} (6 px real = 3 superpixels)")
        self.assertIsNotNone(res.scale_arcsec)
        self.assertAlmostEqual(res.scale_arcsec, 206.265 * 3.76 * 2 / 2900.0, places=4)

    def test_donuts(self):
        res = ic.check_image(self.frame(donut=True, fwhm_px=4.0, stars=synth.star_list(SHAPE, 8, brightest=4.0e5,
                                                                                      margin=80)))
        self.assertTrue(any("donut" in t.lower() for _k, t in res.lines))
        self.assertEqual(len(res.stars), len({(round(s.x), round(s.y)) for s in res.stars}))
        xs = sorted((round(s.x / 10), round(s.y / 10)) for s in res.stars)
        self.assertEqual(len(xs), len(set(xs)), "one donut is one star, not two")

    def test_mono_and_no_gain(self):
        data, hdr = synth.make_star_frame(shape=SHAPE, bayer=None, stars=synth.star_list(SHAPE, 10, 2.0e5))
        for k in ("EGAIN", "FOCALLEN", "INSTRUME", "GAIN"):
            hdr.pop(k)
        p = synth.write_fits(self.path("mono.fit"), data, hdr)
        res = ic.check_image(p)
        self.assertEqual(list(res.channels), ["L"])
        self.assertEqual(res.cam.source("gain_e_per_adu"), "estimate")
        self.assertIsNone(res.scale_arcsec)
        warns = " ".join(kinds(res, "warn"))
        self.assertIn("FOCALLEN", warns)
        self.assertIn("plain guess", warns)
        res = ic.check_image(p, fill={"INSTRUME": "My Mono"})
        self.assertTrue(res.cam.cam_id.startswith("my-mono__"))

    def test_flat_bias_frames(self):
        data, hdr = synth.make_star_frame(shape=SHAPE, stars=[], sky_e=8000.0, channel_sky={"R": 0.5, "B": 0.7})
        hdr["IMAGETYP"] = "Flat"
        res = ic.check_image(synth.write_fits(self.path("flat.fit"), data, hdr))
        self.assertTrue(any("Flat level fine" in t for t in kinds(res, "ok")), res.lines)
        hdr["IMAGETYP"] = "Bias"
        data, _ = synth.make_star_frame(shape=SHAPE, stars=[], sky_e=0.0)
        res = ic.check_image(synth.write_fits(self.path("bias.fit"), data, hdr))
        self.assertTrue(any("read noise" in t for _k, t in res.lines))
        self.assertEqual(res.stars, [])

    def test_click_measure(self):
        res = ic.check_image(self.frame())
        st = res.stars[0]
        self.assertIs(ic.star_near(res, st.x + 2, st.y - 1), st)
        n = len(res.stars)
        got = ic.measure_at(res, 50.0, 50.0)          # empty sky: maybe nothing, never an error
        self.assertTrue(got is None or len(res.stars) == n + 1)
        lines = dict(ic.star_detail(res, st))
        self.assertIn("SNR (this frame)", lines)


if __name__ == "__main__":
    unittest.main()
