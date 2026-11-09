# SHOBS-FLUX

**Shiloh Hill Observatory Optical System Characterization.** SHOBS-FLUX measures your camera and telescope (gain,
read noise, full well, linearity, dark current, zero point, extinction, seeing) and keeps a dated history of them.
It writes the shared instrument profiles that [SHOBS-P](https://github.com/SHOBS-S/SHOBS-P) and
[SHOBS-SCHED](https://github.com/SHOBS-S/SHOBS-SCHED) read, so their noise models and exposure choices use your
own system's numbers instead of guesses.

> **Version 1.0.0**, the first release (9 November 2026). Built in milestones 0.1 to 0.6.1; every milestone's notes
> are kept below. The user's manual comes with the release.

## What it does

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
  revision, and **Roll back…** puts an earlier one back (shown against the profile now, value by value). A roll-back
  is saved as a new revision, so it can be undone too. **Copy for AstroImageJ / EXOTIC / HOPS…** copies the
  profile's gain, read noise, dark current, linear limit, saturation and plate scale in each program's own field
  names (and saves them as a .txt).
- **History.** Pick a setup (a telescope + camera, or a camera) and any value it has a history for: zero point,
  extinction, seeing in focus and defocused, sky brightness, scintillation, cadence, gain, read noise, dark current…
  Each night or run is a point with its error bar (marginal and non-photometric nights marked), with the profile's
  value now and, for the camera, the maker's spec at your gain. Pick the dates, and give an **event date** (a mirror
  cleaning, a collimation) to compare the mean before and after. **Export PNG** (stamped with the date-time group)
  and **Export CSV**. Below the plot, the setup's runs, each with its worst finding in a few words; a bad night or a
  test can be **taken off the page** (nothing is deleted; it can be put back).
- **System report.** One PDF of a telescope + camera to keep or send: the setup and the dates of the data, the key
  numbers, every camera value with its source and the other values it has had (FLUX's own measurement beside an
  operator's figure, say) and the maker's spec, the on-sky numbers per band with their nights plotted, the
  **throughput measured against a photon budget** (aperture, obstruction, mirrors, corrector, filter and QE; type the
  ones you know, the rest are assumed and the report says so), the zero point and seeing over time with a
  before / after split, and the methods and caveats. A **shared copy** leaves out the site (latitude, longitude,
  elevation) and the file locations on your computer.

## New in 1.0.0

- **Linearity from the colours' ratio (one-shot-colour cameras).** In every frame the three colours see the same
  light, so the ratio of a dimmer colour to a brighter one does not care how bright the panel was, nor how it drifted
  or stepped during the ladder. One sensor and one ADC read every colour, so FLUX chains those ratios into one
  response curve for the sensor and takes the linear limit from it. On 9 Nov 2026 a panel that drifted 6.9 % and
  stepped at several rungs put the old time ladder's limit at 2,175 ADU; the colour ratio found the camera straight
  (within ±0.5 %) to at least 60,931 ADU. It needs one colour clearly dimmer than another (a warm panel, or paper over
  it); when the colours are too alike, or on a mono camera, the time ladder is used as before.
- **The time ladder takes out the light's drift** from a repeated (reference) exposure, and a pair off the line by
  the same amount in every colour is named as the light at that exposure ("repeat it"), not the end of the linear
  range (the 1 s pair of the 27 Oct ladder).
- **Unsteady light is said plainly:** repeated exposures that disagree, a pair whose two frames disagree, pairs off
  the line alike in every colour. With steady light the lower of the two answers is used; with unsteady light the
  colour ratio alone, and the exposure-timing offset is not saved from that run. The **Shooting plan** now asks for a
  reference exposure at the start, the middle and the end, a 30-minute warm-up, and the camera off the panel.
- **Read noise in folder runs** counts every raw pixel in a measured pixel (both greens of a TG pixel; GitHub #1). It
  only moves the noise terms of faint stars on dark skies.
- **New version check** (GitHub #2). At start, in the background, FLUX asks GitHub (at most once a day) whether a newer
  release is out and, if so, says so on the status strip with a link to the release notes. Nothing about you is sent,
  nothing is downloaded, and it is silent when there is no internet. Switch it off on the Credits and sources page.
  Want an email for each release instead? On GitHub: **Watch → Custom → Releases**; or follow the feed
  `https://github.com/SHOBS-S/SHOBS-FLUX/releases.atom`.
- **History:** the Status column fits its text, and the run you pick is shown in full below the list.
- A bench re-run that cannot find a colour's linear limit clears that colour's old measured limit (a value you typed
  stays).

## New in 0.6.1

- **Bench linear limit: the last flat pair within ±0.5 % of the straight line** (GitHub #3). Before 0.6.1 FLUX used
  ±1 % and placed the limit where the curve was worked out to leave that band, past the last pair it had actually
  measured. Now the limit is a level FLUX has measured as linear, and the first pair outside the band ends the range
  (no stray pair is skipped; if brighter pairs come back in, the Bench page says so and suggests repeating that
  exposure). The profile's Linearity line ("within X % up to Y % of full scale") now runs to the linear limit, not
  to the top of the whole curve.
- **A profile saved before 0.6.1 keeps its old linear limit until the bench is run again.** Run Bench on the same
  ladder folder and Save: the new values come in as a new profile revision (Roll back can put the old one back).
  SHOBS-SCHED reads the new limit from then on.

## New in 0.6.0

- **Copy for AstroImageJ / EXOTIC / HOPS…** (Profiles page). For the camera or system profile you pick, the values
  those programs ask for, one block per program in its own field names and order: AstroImageJ's Aperture Photometry
  Settings ("CCD gain", "CCD readout noise", "CCD dark current per sec", the saturation and linearity warning
  levels), EXOTIC's initialization file ("Image Scale (Ex: 5.21 arcsecs/pixel)", camera type, binning, Bayer
  pattern and demosaic color: EXOTIC asks for no gain, read noise or dark current) and HOPS ("Camera gain in e/ADU",
  the saturation limit as a share of HOPS's 65,535 limit for 16-bit files, the star FWHM, HOPS's own binning (2 for a
  color camera), telescope and camera names). The plate scale is that of the camera's (binned) pixel. The dark current is read at the
  profile's sensor temperature. Every value says where it came from (measured, header, spec, typed by you, worked
  out, estimate); a value the profile does not have says "not measured yet". It goes to the clipboard, and **Save as
  .txt…** keeps a copy.
- Sky page: the nights table's headings are never cut off (at 100 %, 125 % or 150 % Windows scaling), and each
  heading says what its column means when you hold the pointer on it.
- A system profile's "Focal length in the header" (and TELESCOP) now follows the newest night saved into it (the
  Save window lists the change); before, it kept the first run's for ever. A folder of lights takes the median FOCALLEN of its lights, not the first one's.
- History: a saved Sky run names the on-sky log and night it saved ("…\onsky\2026-10-22__hd-219134__….json and 3
  more logs · 4 nights, 2026-10-19 to 2026-10-22"), not only the onsky folder. Runs saved by 0.5 name the nights
  recorded with them.
- Checkboxes tick with a check mark, not an ×.
- Folder of lights: the frame's sky is the clipped mean of the rings around the stars (the same 3-sigma clipping as
  the rest of the Sky page). A sky less than 5 ADU per camera pixel above the bias is too close to it to tell: the
  frame's sky is "not measurable" (the Findings say so), never a number it is not.
- The noise terms (which stars are only too faint to help, and scintillation) use the sky around each star (a
  folder run's own ring) and SHOBS-P's electrons per unit at that star, not the frame's median (the master flat
  moves it ±8 % across a field). A frame with no sky known is left out of them. The zero point is not changed.
- Folder of lights: every frame is measured (0.5 measured at most 150, every Nth). Only a folder of more than 600
  lights is cut down, to runs of consecutive frames spread through the night, so scintillation (C_Y) still compares
  each frame with its true neighbour; C_Y never compares across a gap in the frames (a flip, clouds, or between two
  runs). The Findings say when a folder was cut down.
- Star names match whatever the spacing or case: "HD219134" and "HD 219134" are one star (a hyphen or space
  between two digits still counts: TYC 123-4567-1 is not TYC 1234-567-1) (a re-run of a night
  spelled the other way replaces the first; the target is never used as a field star).
- Credits: SHOBS-P is cited by its concept DOI, 10.5281/zenodo.23417063 (every version).

## New in 0.5.3

- Save to system profile updates your existing profile even when this run's focal length came out a few mm
  different. ASIAIR writes the plate-solved focal length into FOCALLEN, so it moves from night to night (2898–2906 mm
  on a 14" RC); 0.5.2 named the profile from each run's focal length and offered to make a second one
  (ioptron-14-rc-2902mm beside ioptron-14-rc-2903mm). Now the same telescope name and camera within 3 % is the same
  profile: it keeps its name and focal length, and the Save dialog shows its current values.
- The "There is already a system profile…" warning only appears when the other profile's telescope name is
  different.

## New in 0.5.2

- Reads SHOBS-P 2.3.0's on-sky logs (log format 2). 0.5.1 refused them as "cannot be read". FLUX now uses the
  electrons per image unit that SHOBS-P writes at each star (gain, binning and the master flat's factor in it;
  rescaled to the camera profile's gain when there is one), so the zero point no longer comes out ~0.4 mag too faint
  on a color camera.
- Older SHOBS-P logs (format 1) are still read. Their scale leaves out the flat, so the Sky page says so and leaves
  their zero point out whenever a 2.3.0 night is there (their extinction, seeing, sky and C_Y still count). In the
  run history their zero point is a trend of its own, "Zero point, older logs". Older saved nights are judged again
  once by the 0.5.2 rules.
- A log written by a newer SHOBS-P than this FLUX can read is named that way ("update SHOBS-FLUX; the file is fine"),
  never "move or delete them".
- shobs_profile.py 1.2.0: on-sky logs have a format version of their own (ONSKY_FORMAT_VERSION 2). Copy it into
  SHOBS-P and SHOBS-SCHED at their next builds.

## New in 0.5.1

- Sky page: after Run the plots follow the new run; the folder line names the plate solution ("11 of 12
  plate-solved (TAN-SIP)"); a folder run can be marked **Imaging (astrophotography)**, and its star size is kept
  apart as "Seeing, imaging focus" (never mixed with photometry seeing); several runs of the same night count as one
  night; a folder run is recorded in the history only when you **Save** it (Run is a look). A one-time clean-up takes
  folder runs that 0.5.0 recorded but were never saved off the History page (nothing is deleted).
- History: older runs are judged again by today's rules and today's camera profiles (a +125-step defocused
  night is no longer "seeing in focus"; the old values are kept with the run); dark runs record read noise; old check runs say why; axis labels name the value and unit; nights with no
  focal length in their headers join their camera's setup.
- System report: camera QE in the band worked out from the maker's QE curve when the box is left blank; big numbers
  with separators (15,800); "(assumed)" shown wherever an assumed value is used; 0 mirrors explained; shared copies
  scrub whole file paths (spaces too) and date the PDF in UTC.
- Profiles: Roll back uses plain names and says which revision is in use; worked-out values (full well) are worked
  out again when their inputs change.
- The busy bar is green on the navy status strip, as in SHOBS-P and SHOBS-SCHED.

## New in 0.5

- The History page: trend plots of any value per setup and date range, with an event date for before / after; PNG
  and CSV export; runs can be taken off the page and put back. The run history database moves to version 2 (one new
  column; nothing is lost, and the upgrade is automatic).
- Profile roll-back on the Profiles page.
- The system report (History page → System report…), full or shared.

## Coming

| When | What |
| --- | --- |
| SHOBS-P 2.4.0 | SHOBS-P reads the profiles (its linear limit from FLUX, GitHub SHOBS-P #58) |
| After 1.0.0 | Multi-field Sky folders (mosaic panels); the concept DOI in Credits once Zenodo has made it |

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

## How to cite

See CITATION.cff (GitHub shows it as "Cite this repository"). The Credits and sources page gives the same line.

## License

GNU General Public License, version 3 or later. © 2026 Shiloh Hill Observatory.
