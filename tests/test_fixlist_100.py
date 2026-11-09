"""FLUX 1.0.0 fix list (owner John, 9 Nov 2026).

 1 GitHub #1: folder runs carry the raw pixels summed per measured pixel (a TG pixel = 2 greens), so the read noise in
   the noise terms is right; older folder logs get the count from their band.
 2 Bench: colour-ratio linearity (R/G, G/B in the same frame) chained into one curve for the sensor; light-free, so a
   drifting or stepping panel no longer sets the linear limit. Drift corrected from repeated (reference) exposures.
 3 Bench: a pair off the line by the same % in every colour is the light at that exposure ("repeat it"), not the end
   of the linear range.
 4 Bench: unsteady light flagged (pair mismatch, drift between repeated exposures); no timing offset saved from it.
 5 Profile: a re-run that cannot find a colour's linear limit clears that colour's old measured limit.
 6 GitHub #2: a check for a new version at start (status strip note; never blocks, never downloads).
 7 History: the Status column is not cut off."""
from __future__ import annotations

import copy
import unittest

import numpy as np

from tests.base import TempData

import onsky
import synth_sky


# ---- 1 ---------------------------------------------------------------------------------------------------------------

class TestFolderRunReadNoise(TempData):
    """GitHub #1: rn2 per measured pixel = rn² × the raw pixels summed into it."""

    def _ds(self, band="TG", adu_sum=True, summed=None, soft_bin=1):
        doc, _truth = synth_sky.make_log(seed=3, n_frames=30, band=band, mags=np.linspace(9, 14, 6),
                                         soft_bin=soft_bin)
        if adu_sum:
            doc["measurement"]["flux_is_adu_sum"] = True
        if summed is not None:
            doc["measurement"]["raw_px_summed"] = summed
        return onsky.load_dataset(doc, cam_lookup=lambda c: synth_sky.camera_profile())

    def test_old_folder_log_tg_counts_two_greens(self):
        self.assertEqual(self._ds("TG").raw_sum_px, 2.0)

    def test_old_folder_log_single_colour_counts_one(self):
        self.assertEqual(self._ds("TR").raw_sum_px, 1.0)

    def test_new_folder_log_uses_its_own_count(self):
        self.assertEqual(self._ds("TG", summed=2).raw_sum_px, 2.0)
        self.assertEqual(self._ds("TG", summed=1).raw_sum_px, 1.0)

    def test_shobs_p_log_unchanged(self):
        """SHOBS-P averages the colour's pixels; in electrons that is the same sum as before."""
        self.assertEqual(self._ds("TG", adu_sum=False).raw_sum_px, 2.0)
        self.assertEqual(self._ds("CV", adu_sum=False).raw_sum_px, 4.0)
        self.assertEqual(self._ds("TG", adu_sum=False, soft_bin=2).raw_sum_px, 8.0)

    def test_read_noise_term_grows_with_the_count(self):
        """A faint star on a dark sky: two summed pixels carry twice the read-noise variance of one."""
        one, two = self._ds("TG", summed=1), self._ds("TG", summed=2)
        for ds in (one, two):
            ds.read_noise_e = 5.0
            ds.sky = ds.sky * 0.0 + 0.01                          # nearly no sky: read noise shows
            ds.star_sky = None
        e1 = onsky._expected_mag_noise(one)
        e2 = onsky._expected_mag_noise(two)
        ok = np.isfinite(e1) & np.isfinite(e2)
        self.assertTrue(ok.any())
        self.assertTrue(np.all(e2[ok] >= e1[ok]))
        self.assertTrue(np.any(e2[ok] > e1[ok] * 1.001))


if __name__ == "__main__":
    unittest.main()
