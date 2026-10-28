# SHOBS-FLUX

**Shiloh Hill Observatory Optical System Characterization.** SHOBS-FLUX measures your camera and telescope (gain,
read noise, full well, linearity, dark current, zero point, extinction, seeing) and keeps a dated history of them.
It writes the shared instrument profiles that [SHOBS-P](https://github.com/SHOBS-S/SHOBS-P) and
[SHOBS-SCHED](https://github.com/SHOBS-S/SHOBS-SCHED) read, so their noise models and exposure choices use your
own system's numbers instead of guesses.

> **Work in progress.** SHOBS-FLUX is being built in six milestones (0.1 to 0.6). There is no release yet: 1.0.0
> comes when version 1 is complete and working well.

## What works now (0.4)

- **Check image.** Open any FITS or XISF frame. FLUX reports the sky in electrons per pixel (and per second) for each
  color, whether the exposure is sky- or read-noise-limited, saturated pixels, stars over the linear limit or
  saturated, each star's light in electrons, SNR and FWHM (or the size of a defocused "donut"), and the exposure at
  which the brightest star would reach the linear limit. Click a star for its numbers. Every number says where it
  came from: **measured**, **header**, **spec**, **estimate**, **user** (typed by you) or **worked out**. Stacked or
  stretched (processed) images are recognised and judged on their stars only.
- **Camera: gain and linearity (bench run).** Point FLUX at a folder of flat pairs (two flats at each of 10–20
  exposures, very short to past saturation) and bias pairs (here or in a folder of their own). In a central region,
  per color for one-shot-color cameras, it measures the **gain** (photon transfer curve), **read noise** and **bias
  level**, **full well** and **saturation**, the **linearity curve** and the **safe linear limit**, and the camera's
  **exposure-timing offset**; it checks that the panel stayed steady and that the bias frames saw no light.
  **Shooting plan** prints a ladder for your panel from one test flat or from a finished run.
- **Camera: dark current (dark run).** Point FLUX at your dark library: darks at one or more exposures, all in one
  folder or each in a subfolder of its own (processed copies such as "Debayered" or "masters" folders are skipped),
  with or without bias frames. It measures the **dark current** two independent ways, from the darks' level and from
  their noise (the noise way is not fooled by the bias level drifting between sessions, so it sees a fraction of an
  electron), the **hot pixels** and any **edge glow**, per color and per sensor temperature (with the **doubling
  temperature** when there are several). A dark current too small to measure is given as an upper limit, never as a
  number it is not. Frames cut short in copying, master darks and darks shot before the cooler settled are left out,
  and the run says so.
- **Against the spec.** The manufacturer's curves (gain, read noise and full well against the gain setting; dark
  current against the temperature) with every measured point on them: saved profiles, earlier runs and the run on
  screen. **Spec curves…** (Profiles page) shows, types, imports and exports a camera's spec file; a built-in spec you
  change is saved as your own copy.
- **Save to camera profile** writes a run's measured values (showing what changes first; values you typed can be
  kept). The Check image page reads a measured dark current at the frame's own temperature.
- **Sky: on-sky runs.** FLUX learns what your telescope, camera and sky do from ordinary nights. Two sources:
  **SHOBS-P's on-sky logs** (SHOBS-P 2.2.19.1 and later writes one after every reduction, so every night you reduce
  counts) or **a folder of lights** (one night's lights of a field with catalogued stars: FLUX measures them itself and
  writes the same kind of log). Pick the **setup** (camera setting and focal length) when the logs hold more than one,
  type the telescope's name, aperture and site elevation (aperture and elevation are for scintillation; the site is
  never stored in a profile), and Run. For each band it measures the **zero point** at airmass 1 (against APASS DR9 and
  Gaia DR3 magnitudes of every catalogued field star; saturated stars, stars whose catalog value disagrees with the
  rest and stars that vary are left out, each with the reason), the **extinction**, the **seeing** in focus and per
  defocus, the **sky brightness** (dark-Moon nights first), **scintillation** (Young's C_Y) and the **time between
  frames**.
- **How nights are judged.** A night is **photometric** when its zero point holds steady in 10-minute bins (thin
  cloud or haze moves it); only photometric nights set the zero point and the extinction, each night once, and a night
  that disagrees with the others is set aside, not averaged in. Seeing, sky, scintillation and cadence count from
  every usable night. A re-run of a night in SHOBS-P replaces the earlier one (it is listed in grey, not counted); a log
  that cannot be read is listed with ✖ and why. Pick a night in the list for its findings and pictures: its zero point
  against airmass and through the night, and every night's zero point and seeing.
- **Save to system profile** writes the nights' values into the system profile for that telescope and camera
  (showing what changes first; values you typed can be kept). Every night FLUX uses goes into the run history once.
- **Profiles.** The camera and system profiles in your SHOBS data folder, value by value with its source. Save a
  starting camera profile from a frame, type your own values (a linear limit, say); every save keeps the previous
  revision.
- **History.** Every check, bench run, dark run and on-sky night is recorded, with its worst finding in a few words.

## Coming

| Milestone | What |
| --- | --- |
| 0.5 | History and trend plots, CSV and PNG export, profile roll-back |
| 0.6 | SHOBS-P and SHOBS-SCHED read the profiles; release 1.0.0 |

## Install (Windows)

Python 3.11 or newer with Tkinter (from [python.org](https://www.python.org/downloads/)). Unzip the folder (for
example to `C:\Astro\shobs_flux`), then double-click **Create shortcuts.bat** once: it gives SHOBS-FLUX its own
Python environment, installs NumPy, SciPy, Astropy and Matplotlib there, and puts a SHOBS-FLUX icon on the Desktop
and in the Start menu. **Run self-test.bat** checks the copy (it changes no data). Linux: `./run_linux.sh`
(`--desktop` adds a menu icon). Mac: double-click **Start SHOBS-FLUX.command**.

## Where the data lives

`~/.shobs/` (on Windows `C:\Users\<you>\.shobs\`), shared by the SHOBS apps:

| Folder / file | What |
| --- | --- |
| `cameras/` | Camera profiles: one per camera, gain, offset and binning |
| `systems/` | System profiles: one per telescope and camera, with values per band |
| `specs/` | Manufacturer's camera curves (gain, read noise, full well against the gain setting; dark current against temperature) |
| `history/` | Every earlier revision of a profile |
| `onsky/` | SHOBS-P's on-sky log: one file per reduction |
| `flux_runs.sqlite` | FLUX's run history |

The profile format (version 1) is defined in `shobs_profile.py`, one file copied unchanged into all three apps.

## License

GNU General Public License, version 3 or later. © 2026 Shiloh Hill Observatory.
