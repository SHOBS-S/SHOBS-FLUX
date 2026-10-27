# SHOBS-FLUX

**Shiloh Hill Observatory Optical System Characterization.** SHOBS-FLUX measures your camera and telescope (gain,
read noise, full well, linearity, dark current, zero point, extinction, seeing) and keeps a dated history of them.
It writes the shared instrument profiles that [SHOBS-P](https://github.com/SHOBS-S/SHOBS-P) and
[SHOBS-SCHED](https://github.com/SHOBS-S/SHOBS-SCHED) read, so their noise models and exposure choices use your
own system's numbers instead of guesses.

> **Work in progress.** SHOBS-FLUX is being built in six milestones (0.1 to 0.6). There is no release yet: 1.0.0
> comes when version 1 is complete and working well.

## What works now (0.1)

- **Check image.** Open any FITS or XISF frame. FLUX reports the sky in electrons per pixel (and per second) for each
  color, whether the exposure is sky- or read-noise-limited, saturated pixels, stars over the linear limit or
  saturated, each star's light in electrons, SNR and FWHM (or the size of a defocused "donut"), and the exposure at
  which the brightest star would reach the linear limit. Click a star for its numbers. Every number says where it
  came from: **measured**, **header**, **spec**, **estimate**, **user** (typed by you) or **worked out**.
- **Profiles.** The camera and system profiles in your SHOBS data folder, value by value with its source. Save a
  starting camera profile from a frame, type your own values (a linear limit, say); every save keeps the previous
  revision.
- **Run history.** Every check is recorded in a small database for the History page to come.

## Coming

| Milestone | What |
| --- | --- |
| 0.2 | Bench photon transfer curve: gain, read noise, full well, linearity, timing offset, per color |
| 0.3 | Dark current; manufacturer's spec curves drawn under the measured points |
| 0.4 | On-sky: zero point per band, extinction, seeing, sky brightness, scintillation (from SHOBS-P's on-sky log) |
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
| `specs/` | Manufacturer's camera curves (gain, read noise, full well against the gain setting) |
| `history/` | Every earlier revision of a profile |
| `onsky/` | SHOBS-P's on-sky log: one file per reduction |
| `flux_runs.sqlite` | FLUX's run history |

The profile format (version 1) is defined in `shobs_profile.py`, one file copied unchanged into all three apps.

## License

GNU General Public License, version 3 or later. © 2026 Shiloh Hill Observatory.
