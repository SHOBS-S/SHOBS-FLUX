# SHOBS-FLUX User's Manual (version 1.0.1)

**Shiloh Hill Observatory Optical System Characterization (SHOBS-FLUX)**
*Measures your camera and telescope, and keeps their numbers for SHOBS-P and SHOBS-SCHED.*

> 1.0.1 edition, November 2026 (first edition: 1.0.0). Written for SHOBS-FLUX 1.0.1 and checked against the program; the screenshots are
> real captures from SHOBS-FLUX on Windows, with the Shiloh Hill Observatory's own camera, telescope and nights.
> Each chapter is its own file in this folder; this page is the table of contents.

## Part 1: Getting started

1. **[What SHOBS-FLUX is](01-what-it-is.md)**: what it measures and why, the five pages, camera and system profiles,
   the source tags (*measured*, *header*, *spec*, *estimate*, *you*, *worked out*), how it works with SHOBS-P and
   SHOBS-SCHED.
2. **[Installing and starting](02-installing.md)**: Python, **Create shortcuts.bat**, the self-test, the Mac and
   Linux start files, the SHOBS data folder, the new-version check, updating.
3. **[Your first session, step by step](03-first-session.md)**: one frame on Check image, a bench run and **Save to
   camera profile…**, a Sky run from SHOBS-P's on-sky logs and **Save to system profile…**.

## Part 2: The pages

4. **[Check image](04-check-image.md)**: the sky in electrons, sky- or read-noise-limited, saturated stars and stars
   over the linear limit, each star's electrons, SNR and size, clicking a star, stacked and stretched frames,
   **Save as starting camera profile**.
5. **[Camera: the bench run](05-bench-run.md)**: the **Shooting plan**, the flats and bias folders, **Region**, the
   **Findings**, **Values** and **Pairs** tabs, gain and read noise, full well, the linear limit from the colors'
   ratio and from the time ladder, unsteady light, the exposure-timing offset, the gain in your FITS header (EGAIN),
   **Against the spec**, **Save to camera profile…**.
6. **[Camera: the dark run](06-dark-run.md)**: laying out a dark library, the dark current two ways, upper limits,
   hot pixels and edge glow, several temperatures and the doubling temperature, frames left out and why.
7. **[Sky](07-sky.md)**: SHOBS-P's on-sky logs or a folder of lights, the setup, the telescope, how nights are judged
   (photometric, set aside, re-runs), the zero point, extinction, seeing, sky brightness, scintillation and cadence,
   the night's pictures, **Save to system profile…**.
8. **[Profiles](08-profiles.md)**: a profile value by value, **Set a value…**, revisions and **Roll back…**, **Spec
   curves…**, **Copy for AstroImageJ / EXOTIC / HOPS…**, **Open the data folder**.
9. **[History and the system report](09-history.md)**: a value of a setup over time, dates and an event date (before
   / after), **Export PNG…** and **Export CSV…**, the runs of a setup, **Take off the page…** and **Put back**, the
   **System report** (full or shared).

## Part 3: Reference

10. **[The busy bar and messages](10-busy-bar-and-messages.md)**: the busy bar in the status strip, the ✔ / ⚠ / ✖
    lines, common messages.
11. **[Credits and citing](11-credits-and-citing.md)**: the Credits and sources page, how to cite SHOBS-FLUX, the
    new-version check, the license.
12. **[Troubleshooting](12-troubleshooting.md)**: the self-test log, `flux.log`, frames that will not open, folders
    that give no pairs, catalog failures, starting afresh.
13. **[Glossary](13-glossary.md)**: ADU, bias, photon transfer curve, gain, read noise, full well, linear limit,
    zero point, extinction, scintillation, and the other words this manual uses.

---
*Screenshots taken from SHOBS-FLUX 1.0.0 on Windows.*
