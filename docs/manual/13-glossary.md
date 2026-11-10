# 13. Glossary

**ADU (analog-to-digital unit).** The number a pixel reads in the file, also called a count. The gain turns ADU into
electrons.

**Airmass (X).** How much air the starlight passes through, against straight up (X = 1). About 2 at 30° altitude.

**APASS.** The AAVSO Photometric All-Sky Survey: catalog magnitudes for millions of stars. DR9 is the release
SHOBS-FLUX uses.

**Bayer pattern.** The grid of red, green and blue filters on a one-shot-color sensor (RGGB, say): each 2×2 cell has
one red, two green and one blue pixel.

**Bias frame.** A frame of the shortest exposure with no light: the camera's zero level and its read noise.

**Bias level.** The level, in ADU, a pixel reads with no light at all. Set by the camera's **offset**.

**Camera profile.** SHOBS-FLUX's file of one camera's values at one gain, offset and binning (chapter 1).

**C_Y.** See *scintillation*.

**Colour ratio.** SHOBS-FLUX's way of finding a color camera's linear limit from the ratio of two colors in the same
frame, which the light's brightness and drift cancel out of (chapter 5).

**Dark current.** Electrons a pixel collects per second with no light, from the sensor's own heat. It roughly
doubles for every few degrees warmer.

**Defocus.** Photometry is often done slightly out of focus, so a star's light spreads over more pixels and stays
linear. SHOBS-FLUX keeps defocused star sizes apart from in-focus seeing.

**Donut.** A strongly defocused star, which shows the telescope's central obstruction as a dark middle.

**EGAIN.** The FITS header keyword for the gain, in e⁻/ADU, written by the capture program from the camera driver.
It is the maker's figure, not your camera's (chapter 5).

**Electron (e⁻).** What a pixel really counts: each photon detected frees one. Noise is worked out in electrons.

**Exposure-timing offset.** How much longer (or shorter) a camera really exposes than it is told.

**Extinction (k).** How much fainter a star gets per unit of airmass, in magnitudes. Your sky's clarity; larger in
blue than in red.

**Flat (flat frame).** A frame of an evenly lit field (a flat panel). In a bench run, flats at many exposures make the
ladder.

**Flat pair.** Two flats at the same exposure. Their difference cancels the flat-field pattern and leaves only noise.

**Full well.** The most electrons a pixel can hold, or the most the camera can record when its ADC tops out first.

**FWHM (full width at half maximum).** A star's size: its width where it is half as bright as its peak. In
arcseconds, it is the **seeing**.

**Gain.** Electrons per ADU (e⁻/ADU). Also the camera's **gain setting** (100, say), which sets that number.

**Gaia DR3.** The European Space Agency's star catalog. SHOBS-FLUX converts its magnitudes to V, R and Sloan bands for
stars APASS lacks.

**Hot pixel.** A pixel with far more dark current than the rest.

**Linear limit.** The brightest raw level, in ADU with the bias included, at which the camera still counts light in
proportion, within ±0.5 % (chapter 5). The most important number for photometry.

**Magnitude.** The astronomer's brightness scale: 5 magnitudes fainter is 100 times less light; bigger is fainter.

**Mono camera.** A camera with no color filters on its pixels; colors come from filters in front of it.

**Offset.** The camera setting that lifts the zero level (the bias level) above zero, so no noise is cut off.

**On-sky log.** The file SHOBS-P writes after each reduction (and SHOBS-FLUX after a folder run): every catalogued
star's light in every frame, the sky, the airmass and the seeing (chapter 7).

**One-shot color (OSC).** A camera with a Bayer pattern, which records color in one frame.

**Photometric night.** A night whose zero point held steady (in 10-minute bins, within 0.025 mag rms and 0.05 mag from lowest to highest): no cloud or
haze.

**Photon transfer curve (PTC).** The noise (variance) of flat pairs against their signal. For shot noise it is a
straight line whose slope is 1 / gain (Janesick 2007).

**Plate scale.** Arcseconds of sky per pixel: 206.265 × pixel size (µm) ÷ focal length (mm).

**Plate solution (WCS).** The header's description of where on the sky each pixel points.

**Read noise.** The noise, in electrons, the camera adds each time it reads a pixel, light or not.

**Read-noise-limited.** When the sky's own noise is smaller than the read noise: longer exposures help faint stars.

**Revision.** One saved state of a profile. Every save keeps the one before (chapter 8).

**Saturation.** The level at which a pixel can count no higher: the full well, or the ADC's top (65,535 for 16 bits).

**Scintillation.** The twinkle of starlight in the atmosphere, which adds noise to bright stars that no longer
exposure removes. **C_Y** is Young's coefficient for it (Young 1967; Osborn et al. 2015); it gets smaller with a
bigger aperture and a higher site.

**Seeing.** How much the atmosphere blurs a star: its in-focus FWHM in arcseconds.

**Shot noise.** The noise of counting light itself: the square root of the number of electrons.

**Sky brightness.** The night sky's own light, in magnitudes per square arcsecond (bigger = darker).

**Sky-limited.** When the sky's noise swamps the read noise: longer exposures no longer lower the noise per hour.

**SNR (signal-to-noise ratio).** A star's light against its noise. SNR 100 is about 1 % precision.

**Spec.** The camera maker's figure for a value at your gain setting, from its published charts.

**System profile.** SHOBS-FLUX's file of one telescope with one camera: per band, its zero point, extinction, seeing,
sky and scintillation (chapter 1).

**TG, TR, TB.** The green, red and blue pixels of a one-shot-color camera used as photometric bands (AAVSO's codes).
TG is calibrated against V.

**Time ladder.** Finding the linear limit from each flat pair's signal against its exposure (chapter 5).

**Zero point.** The magnitude of a star that gives 1 e⁻/s through your system at airmass 1. It sums up your
telescope, camera and filter: bigger is better.

---

Previous: [12. Troubleshooting](12-troubleshooting.md) · Next: [Contents](README.md)
