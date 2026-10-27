"""frames.py: FITS and XISF reading, frame kinds, the ADU step of 12/14-bit cameras, Bayer planes, cut-short files."""
from __future__ import annotations

import unittest

import numpy as np

from tests.base import TempData

import frames as fr
import synth


class TestFrames(TempData):
    def test_fits_roundtrip_and_setup(self):
        data, hdr = synth.make_star_frame(shape=(200, 300), stars=synth.star_list((200, 300), 5))
        p = synth.write_fits(self.path("a.fit"), data, hdr)
        f = fr.read_frame(p)
        self.assertEqual(f.data.shape, (200, 300))
        self.assertTrue(np.array_equal(f.data, data.astype(np.uint16).astype(np.float32)))
        self.assertEqual(f.kind, "light")
        self.assertEqual(f.exptime, 60.0)
        self.assertEqual(f.bayer, "RGGB")
        self.assertEqual(f.adu_step, 1)
        self.assertEqual(f.setup["camera_id"], "synthetic-cam__g100__o50__bin1")

    def test_adu_step_12_and_14_bit(self):
        for step, bits in ((16, 12), (4, 14)):
            data, hdr = synth.make_star_frame(shape=(200, 300), sky_e=4000, adu_step=step, gain=1.0)
            f = fr.read_frame(synth.write_fits(self.path(f"s{step}.fit"), data, hdr))
            self.assertEqual(f.adu_step, step)
            self.assertEqual(f.bits, bits)
            self.assertTrue(any("scaled to 16 bits" in n for n in f.notes))

    def test_kinds(self):
        self.assertEqual(fr.kind_of({"IMAGETYP": "Flat Field"}), "flat")
        self.assertEqual(fr.kind_of({"IMAGETYP": "BIAS"}), "bias")
        self.assertEqual(fr.kind_of({"FRAME": "Dark"}), "dark")
        self.assertEqual(fr.kind_of({"IMAGETYP": "Light Frame"}), "light")
        self.assertEqual(fr.kind_of({}, "C:/x/Flat_16.7ms_0001.fit"), "flat")
        self.assertEqual(fr.kind_of({}, "frame.fit"), "unknown")
        self.assertEqual(fr.exptime_of({"EXPOSURE": "0.75"}), 0.75)
        self.assertIsNone(fr.exptime_of({}))

    def test_planes(self):
        d = np.arange(16, dtype=np.float32).reshape(4, 4)
        p = fr.planes(d, "RGGB")
        self.assertEqual(p["R"].tolist(), [[0, 2], [8, 10]])
        self.assertEqual(p["B"].tolist(), [[5, 7], [13, 15]])
        self.assertEqual(p["G"].shape, (2, 4))
        s, k = fr.superpixel_sum(d, "RGGB")
        self.assertEqual(k, 4)
        self.assertEqual(s[0, 0], 0 + 1 + 4 + 5)
        self.assertIs(fr.planes(d, None)["L"], d)

    def test_cut_short_and_missing(self):
        data, hdr = synth.make_star_frame(shape=(400, 400))
        p = synth.write_fits(self.path("cut.fit"), data, hdr, cut_short=True)
        with self.assertRaisesRegex(fr.FrameError, "cut short"):
            fr.read_frame(p)
        with self.assertRaisesRegex(fr.FrameError, "not there"):
            fr.read_frame(self.path("nothing.fit"))
        with open(self.path("junk.fit"), "wb") as fh:
            fh.write(b"hello" * 100)
        with self.assertRaises(fr.FrameError):
            fr.read_frame(self.path("junk.fit"))

    def test_xisf(self):
        data, hdr = synth.make_star_frame(shape=(120, 160), bayer=None)
        p = synth.write_xisf(self.path("a.xisf"), data, {"INSTRUME": "Mono Cam", "EXPTIME": 30.0, "GAIN": 0,
                                                          "IMAGETYP": "Light"})
        f = fr.read_frame(p)
        self.assertEqual(f.data.shape, (120, 160))
        self.assertTrue(np.array_equal(f.data, data.astype(np.uint16).astype(np.float32)))
        self.assertEqual(f.header["INSTRUME"], "Mono Cam")
        self.assertEqual(f.exptime, 30.0)
        self.assertIsNone(f.bayer)

    def test_list_frames(self):
        for n in ("b.fits", "a.fit", "c.txt", ".hidden.fit", "d.XISF"):
            open(self.path(n), "w").close()
        names = [p.rsplit("/", 1)[-1].rsplit("\\", 1)[-1] for p in fr.list_frames(self.tmp)]
        self.assertEqual(names, ["a.fit", "b.fits", "d.XISF"])


if __name__ == "__main__":
    unittest.main()
