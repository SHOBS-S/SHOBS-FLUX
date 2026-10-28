"""The bench run recovers what went into synthetic ladders: gain, read noise, bias, full well, linear limit, timing
offset, for a color camera, a mono camera and a 12-bit camera; it catches bias frames that saw light, works without
bias frames, takes bias frames from a folder of their own, notices a drifting panel, and writes a valid profile."""
from __future__ import annotations

import math
import os
import unittest

from tests.base import TempData

import bench
import shobs_profile as sp
import synth_bench

SHAPE = (400, 500)
REGION = "Central 50 %"


class TestBench(TempData):
    def ladder(self, name="ladder", **kw):
        kw.setdefault("shape", SHAPE)
        folder = self.path(name)
        truth = synth_bench.make_ladder(folder, **kw)
        return folder, truth

    def assertNear(self, got, want, rel, msg=""):
        self.assertTrue(math.isfinite(got), f"{msg}: not measured")
        self.assertLess(abs(got / want - 1.0), rel, f"{msg}: got {got:.5g}, want {want:.5g}")

    def test_color_camera_recovers_truth(self):
        folder, t = self.ladder(nonlin=0.0)
        r = bench.run_bench(folder, REGION)
        self.assertEqual(set(r.channels), {"R", "G", "B"})
        self.assertNear(r.gain, t["gain"], 0.02, "gain")
        for c, ch in r.channels.items():
            self.assertNear(ch.gain, t["gain"], 0.04, f"gain {c}")
            self.assertNear(ch.read_noise_e, t["read_noise"], 0.06, f"read noise {c}")
            self.assertLess(abs(ch.bias_adu - t["bias"]), 1.0, f"bias {c}")
        self.assertNear(r.full_well_e, t["full_well"], 0.03, "full well")
        self.assertLess(abs(r.t_offset), 0.002)
        self.assertEqual(r.status, "ok", r.lines)
        self.assertFalse(r.lit_bias)

    def test_nonlinear_camera_linear_limit(self):
        folder, t = self.ladder(nonlin=0.03)
        r = bench.run_bench(folder, REGION)
        self.assertNear(r.gain, t["gain"], 0.03, "gain with a bending response")
        # the mean bends by 1 % at e ≈ FW·√(0.01/0.03): ≈ 8,660 e⁻ → 35,140 ADU
        want = t["full_well"] * math.sqrt(0.01 / t["nonlin"]) / t["gain"] + t["bias"]
        self.assertNear(r.linear_limit_adu, want, 0.10, "linear limit")
        self.assertFalse(r.linear_is_bound)

    def test_timing_offset(self):
        folder, t = self.ladder(t_offset=0.012)
        r = bench.run_bench(folder, REGION)
        self.assertLess(abs(r.t_offset - 0.012), 0.001, f"offset {r.t_offset}")
        self.assertTrue(any("longer than asked" in x for _, x in r.lines))

    def test_mono(self):
        folder, t = self.ladder(bayer=None)
        r = bench.run_bench(folder, REGION)
        self.assertEqual(list(r.channels), ["L"])
        self.assertNear(r.gain, t["gain"], 0.03, "mono gain")
        self.assertNear(r.read_noise_e, t["read_noise"], 0.06, "mono read noise")

    def test_12_bit(self):
        folder, t = self.ladder(adc_bits=12)
        r = bench.run_bench(folder, REGION)
        self.assertEqual(r.adu_step, 16)
        self.assertNear(r.gain, t["gain"], 0.03, "12-bit gain (file units)")
        self.assertNear(r.read_noise_e, t["read_noise"], 0.10, "12-bit read noise")
        self.assertTrue(any("× 16" in x for _, x in r.lines))

    def test_lit_bias_caught(self):
        folder, t = self.ladder(lit_bias=60000.0)
        r = bench.run_bench(folder, REGION)
        self.assertTrue(r.lit_bias)
        self.assertEqual(r.status, "bad")
        self.assertTrue(math.isnan(r.read_noise_e))
        self.assertNear(r.gain, t["gain"], 0.03, "gain with lit biases")

    def test_no_bias(self):
        folder, t = self.ladder(bias_pairs=0)
        r = bench.run_bench(folder, REGION)
        self.assertNear(r.gain, t["gain"], 0.03, "gain without biases")
        self.assertTrue(math.isnan(r.read_noise_e))
        self.assertTrue(all(ch.bias_source == "derived" for ch in r.channels.values()))
        self.assertTrue(math.isfinite(r.linear_limit_adu))

    def test_bias_folder(self):
        folder, t = self.ladder(lit_bias=60000.0)
        bias_dir = self.path("bias")
        synth_bench.make_ladder(bias_dir, shape=SHAPE, exposures=(), bias_pairs=6, seed=11)
        r = bench.run_bench(folder, REGION, bias_folder=bias_dir)
        self.assertFalse(r.lit_bias)
        self.assertEqual(r.n_bias_pairs, 6)
        self.assertNear(r.read_noise_e, t["read_noise"], 0.06, "read noise from the bias folder")
        self.assertTrue(any("bias folder are used" in x for _, x in r.lines))

    def test_drift_noticed(self):
        ex = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 0.5, 1.0, 1.5, 2.0, 3.0, 4.0, 5.0)
        folder, t = self.ladder(drift_per_frame=0.003, exposures=ex)
        r = bench.run_bench(folder, REGION)
        self.assertGreater(abs(r.drift_pct), 1.0)
        self.assertEqual(r.status, "warn")

    def test_small_panel_leak_caught(self):
        folder, t = self.ladder(lit_bias=3000.0)            # about 12 ADU of panel light in green
        r = bench.run_bench(folder, REGION)
        self.assertTrue(r.lit_bias, r.lines)

    def test_white_leak_on_zwo_warned(self):
        folder, t = self.ladder(lit_bias=20000.0, lit_white=True, instrume="ZWO ASI2600MC Air")
        r = bench.run_bench(folder, REGION)
        self.assertTrue(r.lit_bias or any("OFFSET" in x for _, x in r.lines), r.lines)
        self.assertNotEqual(r.status, "ok")

    def test_clean_bias_never_flagged(self):
        for seed in (1, 2, 3, 4):
            folder, t = self.ladder(f"clean{seed}", seed=seed, instrume="ZWO ASI2600MC Air")
            r = bench.run_bench(folder, REGION)
            self.assertFalse(r.lit_bias, (seed, r.lines))
            self.assertFalse(any("OFFSET" in x for _, x in r.lines), (seed, r.lines))

    def test_egain_compared_per_camera_adu(self):
        from astropy.io import fits
        import frames
        folder, t = self.ladder(adc_bits=12)
        for p in frames.list_frames(folder):              # a 12-bit camera's EGAIN is per its own ADU: 16 × file
            with fits.open(p, mode="update") as h:
                h[0].header["EGAIN"] = t["gain"] * 16
        r = bench.run_bench(folder, REGION)
        line = next(x for _, x in r.lines if x.startswith("✔ Gain"))
        pct = float(line.split("(")[-1].split("%")[0])
        self.assertLess(abs(pct), 3.0, line)

    def test_image_in_extension(self):
        from astropy.io import fits
        folder, t = self.ladder(exposures=(0.05, 0.1, 0.2, 0.3, 0.5, 1.0, 2.0, 4.0))
        import frames
        for p in frames.list_frames(folder):
            with fits.open(p) as h:
                data, hdr = h[0].data, h[0].header.copy()
            prim = fits.PrimaryHDU()
            ext = fits.ImageHDU(data=data, header=hdr)
            fits.HDUList([prim, ext]).writeto(p, overwrite=True)
        r = bench.run_bench(folder, REGION)
        self.assertNear(r.gain, t["gain"], 0.03, "gain from extension HDUs")

    def test_derived_bias_uses_spec_read_noise(self):
        folder, t = self.ladder(bias_pairs=0)
        r = bench.run_bench(folder, REGION)
        self.assertTrue(any("read noise is not known" in x for _, x in r.lines))

    def test_offset_sigma_not_shrunk(self):
        folder, t = self.ladder(t_offset=0.005)
        r = bench.run_bench(folder, REGION)
        per = [ch.t_offset_sigma for ch in r.channels.values()]
        self.assertAlmostEqual(r.t_offset_sigma, min(per))

    def test_problems(self):
        empty = self.path("empty")
        os.makedirs(empty)
        with self.assertRaises(bench.BenchError):
            bench.run_bench(empty)
        folder, _ = self.ladder(exposures=(0.1, 0.2, 0.3))
        with self.assertRaises(bench.BenchError) as cm:
            bench.run_bench(folder)
        self.assertIn("at least 4", str(cm.exception))

    def test_profile_written_and_valid(self):
        folder, t = self.ladder()
        r = bench.run_bench(folder, REGION)
        doc = bench.apply_to_profile(r, None, "test", run_key="bench-x")
        self.assertEqual(sp.validate(doc), [])
        self.assertEqual(doc["values"]["gain_e_per_adu"]["source"], "measured")
        self.assertEqual(doc["per_channel"]["G"]["gain_e_per_adu"]["run"], "bench-x")
        self.assertTrue(doc["linearity"]["curve_adu"])
        path = sp.save_profile(doc, "test")
        self.assertTrue(os.path.exists(path))
        # a user value is kept only when asked
        doc2 = sp.load(path)
        doc2["values"]["linear_limit_adu"] = sp.record(32000, "ADU", "user")
        kept = bench.apply_to_profile(r, doc2, "test", keep_user=True)
        self.assertEqual(kept["values"]["linear_limit_adu"]["value"], 32000)
        replaced = bench.apply_to_profile(r, doc2, "test", keep_user=False)
        self.assertEqual(replaced["values"]["linear_limit_adu"]["source"], "measured")
        rows = bench.run_values(r, doc["profile_id"])
        self.assertTrue(any(x["key"] == "gain_e_per_adu" and x["channel"] == "G" for x in rows))

    def test_shooting_plan(self):
        plan = bench.plan_ladder({"R": 23600.0, "G": 52400.0, "B": 29000.0}, 500.0)
        ex = plan["exposures"]
        self.assertGreaterEqual(len(ex), 12)
        self.assertLess(min(ex), 0.05)
        self.assertGreater(max(ex), (65535 - 500) / 23600.0)       # the reddest color saturates too
        text = bench.plan_text(plan, 100, 50, -10)
        self.assertIn("panel OFF", text)


if __name__ == "__main__":
    unittest.main()
