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
        # field stars over the limits are plain notes (no icon), not a problem with the frame
        notes = " ".join(kinds(res, "info"))
        self.assertIn("field star saturated", notes)
        self.assertIn("over the linear limit", notes)
        self.assertFalse(any("brightest stars" in t or "field star" in t for t in kinds(res, "bad") + kinds(res, "warn")), res.lines)
        self.assertNotEqual(res.status, "bad")
        self.assertEqual(res.summary["worst"], "⚠ read-noise-limited")      # a ⚠ outranks the notes
        mid = by_pos.get((50, 30))
        self.assertIsNotNone(mid)
        self.assertEqual(mid.status, "warn", f"peak {mid.raw_peak:.0f}")
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
        self.assertIn("FOCALLEN", " ".join(kinds(res, "info")))       # a missing header key is a note
        self.assertNotIn("FOCALLEN", warns)
        self.assertIn("plain guess", warns)                            # electron counts depend on the gain
        self.assertEqual(res.status, "warn")
        self.assertEqual(res.summary["worst"], "⚠ gain is a guess")
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


class TestReviewFixes(TempData):
    """Fable review, 27 Oct 2026."""

    def test_nan_pixels_do_not_set_saturation(self):
        data, hdr = synth.make_star_frame(shape=SHAPE, stars=synth.star_list(SHAPE, 8, 1.0e5))
        from astropy.io import fits
        d = data.copy()
        d[10, 10] = float("nan")
        p = self.path("nan.fits")
        h = fits.Header()
        for k, v in hdr.items():
            if k != "SIMPLE":
                h[k] = v
        fits.PrimaryHDU(d.astype("float32"), header=h).writeto(p)
        res = ic.check_image(p)
        self.assertEqual(res.cam.value("saturation_adu"), 65535.0)
        self.assertFalse(kinds(res, "bad"), res.lines)
        fits.PrimaryHDU((d * float("nan")).astype("float32"), header=h).writeto(p, overwrite=True)
        import frames
        with self.assertRaises(frames.FrameError):
            ic.check_image(p)

    def test_time_to_linear_scales_sky_too(self):
        # everything above the bias grows with time: 60 s × (32500 − 500) / (16500 − 500) = 120 s
        self.assertAlmostEqual(ic.time_to_linear(16500.0, 500.0, 32500.0, 60.0), 120.0)
        self.assertTrue(math.isnan(ic.time_to_linear(400.0, 500.0, 32500.0, 60.0)))

    def test_bayer_offset(self):
        import frames
        self.assertEqual(frames.shift_pattern("RGGB", 1, 0), "GRBG")
        self.assertEqual(frames.shift_pattern("RGGB", 0, 1), "GBRG")
        self.assertEqual(frames.shift_pattern("RGGB", 1, 1), "BGGR")
        self.assertEqual(frames.bayer_of({"BAYERPAT": "RGGB", "XBAYROFF": 1, "YBAYROFF": 1}), "BGGR")

    def test_bad_xisf_is_plain(self):
        import frames
        p = self.path("bad.xisf")
        with open(p, "wb") as fh:
            fh.write(b"XISF0100" + (20).to_bytes(4, "little") + b"\0\0\0\0" + b"<xisf><Image geom")
        with self.assertRaises(frames.FrameError):
            frames.read_frame(p)

    def test_record_numpy_and_nan(self):
        import numpy as np
        r = sp.record(np.float32(0.25), "e-/ADU", "measured", sigma=float("nan"), n=np.int64(40))
        self.assertIsInstance(r["value"], float)
        self.assertIsNone(r["sigma"])
        import json
        json.dumps(r, allow_nan=False)
        self.assertTrue(sp.validate({"format": sp.SPEC_FORMAT, "format_version": True, "curves": {}}))


class TestPolish02(TempData):
    """The 0.2 polish list (John, 28 Oct 2026)."""

    def write(self, name, data, hdr):
        return synth.write_fits(self.path(name), data, hdr)

    def test_display_stretch_noise_only_is_smooth_grey(self):
        import numpy as np
        rng = np.random.default_rng(3)
        img = (500.0 + rng.normal(0.0, 6.0, (300, 400))).astype("float32")      # read noise only, no stars
        z = ic.display_stretch(img, read_noise_limited=True)
        self.assertEqual(z.shape, img.shape)
        self.assertGreaterEqual(float(z.min()), 0.0)
        self.assertLessEqual(float(z.max()), 1.0)
        self.assertLess(float(np.std(z)), 0.06, "noise must look like a smooth grey, not speckle")
        self.assertLess(float(np.mean(z >= 0.99)), 0.001, "no saturated speckle")
        self.assertTrue(0.03 < float(np.median(z)) < 0.5, f"sky grey level {float(np.median(z)):.2f}")
        # stars still stand out above the sky
        data, _hdr = synth.make_star_frame(shape=(300, 400), bayer=None, exptime=0.75, sky_e=2.0, read_noise=3.0,
                                           stars=synth.star_list((300, 400), 10, brightest=2.0e4, margin=40))
        z = ic.display_stretch(data, read_noise_limited=True)
        self.assertGreater(float(z.max()), 0.9)
        self.assertLess(float(np.std(z[np.abs(z - np.median(z)) < 0.2])), 0.06)

    def test_donut_never_gives_a_tiny_fwhm(self):
        for bayer in ("RGGB", None):
            data, hdr = synth.make_star_frame(shape=SHAPE, donut=True, fwhm_px=12.0, bayer=bayer, read_noise=3.0,
                                              sky_e=20.0, stars=synth.star_list(SHAPE, 25, 1.0e6, margin=90))
            res = ic.check_image(self.write(f"donut_{bayer}.fit", data, hdr))
            f = res.summary["fwhm_px"]
            self.assertTrue(f is None or f >= ic.MIN_FWHM, f"summary FWHM {f}")
            self.assertIsNotNone(res.summary["donut_d95_px"])
            self.assertTrue(all(not math.isfinite(s.fwhm_px) or s.fwhm_px >= ic.MIN_FWHM for s in res.stars))
        # normal stars: the true FWHM within 15 %
        data, hdr = synth.make_star_frame(shape=SHAPE, fwhm_px=5.0, bayer=None,
                                          stars=synth.star_list(SHAPE, 15, 2.0e5))
        res = ic.check_image(self.write("sharp.fit", data, hdr))
        self.assertLess(abs(res.summary["fwhm_px"] - 5.0) / 5.0, 0.15, res.summary["fwhm_px"])
        self.assertIsNone(res.summary["donut_d95_px"])

    def test_fwhm_far_below_r95_means_defocused(self):
        # a Gaussian fit that landed on noise: tiny FWHM against a large 95 %-light radius
        import numpy as np
        from unittest import mock
        img = np.zeros((10, 10), dtype="float32")
        m = {"x": 5.0, "y": 5.0, "sky": 0.0, "r95": 20.0, "donut": False}
        with mock.patch.object(ic, "gaussian_fwhm", return_value=1.5):
            self.assertTrue(ic.size_of(img, m)[1])
        with mock.patch.object(ic, "gaussian_fwhm", return_value=18.0):
            self.assertEqual(ic.size_of(img, m), (18.0, False))
        self.assertGreater(ic.MIN_FWHM, 1.0)

    def twelve_bit(self, datamax: bool):
        stars = [(200.0, 200.0, 3.0e6), (500.0, 300.0, 2.0e6)] + synth.star_list(SHAPE, 10, 3.0e4, seed=5)
        data, hdr = synth.make_star_frame(shape=SHAPE, bayer=None, gain=1.0, bias=100.0, sky_e=100.0,
                                          sat_adu=4095.0, stars=stars)
        self.assertEqual(float(data.max()), 4095.0)
        if datamax:
            hdr["DATAMAX"] = 4095
        return ic.check_image(self.write(f"mo_{datamax}.fit", data, hdr))

    def test_12_bit_saturation_from_datamax(self):
        res = self.twelve_bit(True)
        self.assertEqual(res.frame.adu_step, 1)
        self.assertEqual(res.cam.value("saturation_adu"), 4095.0)
        self.assertEqual(res.cam.source("saturation_adu"), "header")
        self.assertEqual(res.cam.value("linear_limit_adu"), round(0.8 * 4095))
        clipped = [s for s in res.stars if s.raw_peak >= 4095]
        self.assertEqual(len(clipped), 2)
        self.assertTrue(all(s.status == "bad" for s in clipped))
        self.assertIn("2 field stars saturated", " ".join(kinds(res, "info")))

    def test_12_bit_saturation_from_pile_up(self):
        res = self.twelve_bit(False)
        self.assertEqual(res.cam.value("saturation_adu"), 4095.0)
        self.assertEqual(res.cam.source("saturation_adu"), "estimate")
        self.assertIn("inferred", res.cam.records["saturation_adu"]["note"])
        self.assertEqual(sum(1 for s in res.stars if s.status == "bad"), 2)

    def test_header_saturation_keywords(self):
        self.assertEqual(ic.header_saturation({"SATURATE": 60000, "DATAMAX": 4095}, 4095.0), (60000.0, "SATURATE"))
        self.assertEqual(ic.header_saturation({"DATAMAX": 4095}, 4095.0), (4095.0, "DATAMAX"))
        self.assertIsNone(ic.header_saturation({"DATAMAX": 4095}, 9000.0))      # pixels above it: not saturation
        self.assertIsNone(ic.header_saturation({"MAXADU": "nan", "DATAMAX": 0}, 100.0))

    def test_processed_image(self):
        import numpy as np
        from astropy.io import fits
        import frames
        data, hdr = synth.make_star_frame(shape=SHAPE, bayer=None, stars=synth.star_list(SHAPE, 15, 2.0e5))
        stacked = (data - 500.0) / 20000.0 + 0.01                 # calibrated, normalised, float: like a stack
        p = self.path("stack.fits")
        h = fits.Header()
        h["NCOMBINE"] = 24
        h["HISTORY"] = "ImageIntegration: 24 images"
        fits.PrimaryHDU(stacked.astype(np.float32), header=h).writeto(p)
        f = frames.read_frame(p)
        yes, why = frames.is_processed(f)
        self.assertTrue(yes)
        self.assertIn("NCOMBINE", why)
        res = ic.check_image(p)
        self.assertTrue(res.lines[0][1].startswith("This is a processed image"))
        self.assertEqual(res.lines[0][0], "info")
        self.assertFalse(kinds(res, "bad") + kinds(res, "warn"), res.lines)
        self.assertEqual(res.status, "ok")
        self.assertGreater(len(res.stars), 5)
        self.assertTrue(all(s.status == "ok" for s in res.stars))
        self.assertLess(abs(res.summary["fwhm_px"] - 4.0) / 4.0, 0.15)
        self.assertFalse(any("e⁻" in t for _k, t in res.lines))
        lines = dict(ic.star_detail(res, res.stars[0]))
        self.assertNotIn("Light in aperture", lines)
        # float pixels with no exposure time and no keywords at all
        fits.PrimaryHDU(stacked.astype(np.float32)).writeto(p, overwrite=True)
        self.assertTrue(frames.is_processed(frames.read_frame(p))[0])
        # a raw camera frame (whole numbers, INSTRUME) is not processed
        raw = frames.read_frame(self.write("raw.fit", data, hdr))
        self.assertEqual(frames.is_processed(raw), (False, ""))
        # nor a raw frame saved as float by capture software (whole numbers, EXPTIME, INSTRUME)
        h2 = fits.Header()
        for k, v in hdr.items():
            if k != "SIMPLE":
                h2[k] = v
        fits.PrimaryHDU(data.astype(np.float32), header=h2).writeto(self.path("rawf.fits"))
        self.assertEqual(frames.is_processed(frames.read_frame(self.path("rawf.fits"))), (False, ""))

    def test_processed_xisf(self):
        import numpy as np
        import frames
        data, _hdr = synth.make_star_frame(shape=(200, 300), bayer=None, stars=synth.star_list((200, 300), 6, 2e5))
        p = synth.write_xisf_float(self.path("master.xisf"), (data - 400.0) / 65535.0,
                                   {"EXPTIME": 300.0}, history=True)
        f = frames.read_frame(p)
        self.assertTrue(f.float_samples)
        self.assertTrue(frames.is_processed(f)[0])
        p = synth.write_xisf(self.path("raw.xisf"), data, {"INSTRUME": "Mono Cam", "EXPTIME": 30.0, "GAIN": 0})
        self.assertEqual(frames.is_processed(frames.read_frame(p)), (False, ""))
        self.assertTrue(np.isfinite(frames.read_frame(p).data).all())

    def test_status_follows_icons_and_worst(self):
        res = ic.check_image(self.write("ok.fit", *synth.make_star_frame(
            shape=SHAPE, sky_e=200.0, read_noise=1.5, stars=synth.star_list(SHAPE, 15, 1.0e5))))
        self.assertEqual(res.status, "ok", res.lines)
        self.assertIsNone(res.summary["worst"])
        # bright sky: ✖ and the run is bad
        data, hdr = synth.make_star_frame(shape=SHAPE, sky_e=9000.0, gain=0.25, stars=synth.star_list(SHAPE, 8))
        res = ic.check_image(self.write("sky.fit", data, hdr))
        self.assertEqual(res.status, "bad")
        self.assertEqual(res.summary["worst"], "✖ sky over half the linear range")
        for kind, text in res.lines:
            if kind in ("bad", "warn"):
                self.assertTrue(text.startswith({"bad": "✖", "warn": "⚠"}[kind]), text)
            else:
                self.assertFalse(text.startswith(("✖", "⚠")), text)
