# 12. Troubleshooting

## First: the self-test

If something seems wrong with SHOBS-FLUX itself, run **Run self-test.bat** (chapter 2). It checks your Python, the
packages, and every measurement on synthetic frames whose answers are known, and lists what is in your data folder.
`ALL PASSED` means the program is sound, and the question is about your frames. The log goes to
`selftest_log.txt` in the SHOBS-FLUX folder and `SHOBS-FLUX_selftest_log.txt` in your Downloads folder.

## The program's log

SHOBS-FLUX writes what it does to **`flux.log`** in the data folder (`C:\Users\<you>\.shobs\flux.log`): each start,
each run with its result, each save, and any error with its full details. A message that says *Details are in
…\flux.log* means the details are at the end of that file.

When you report a problem, attach the self-test log and `flux.log`, and say what you pressed. Report on GitHub
(**SHOBS-S/SHOBS-FLUX → Issues → New issue**).

## It will not start

- Start it with **run_windows.bat** instead of the icon. The console shows why, and stays open with *The app did not
  start. Leave this window open and copy the message above.*
- *SHOBS-FLUX could not find a Python it can use*: install Python 3.11 or newer from python.org (tick **Add
  python.exe to PATH**) and run **Create shortcuts.bat** again.
- After moving the SHOBS-FLUX folder, run **Create shortcuts.bat** again.
- On Linux, a Python without Tkinter: the start file names the package to install.

## A frame will not open, or looks wrong

| Problem | What to do |
|---|---|
| *✖ Check image failed (…)* | The file is not a FITS or XISF frame SHOBS-FLUX can read, or is cut short. Try another frame from the same night; the log has the reason. |
| *The header has no INSTRUME or GAIN* | Your capture program did not write them. SHOBS-FLUX asks the camera's name once (Save as starting camera profile); without GAIN a saved profile cannot be matched to the frame. |
| *The header has no FOCALLEN or XPIXSZ* | Sizes are given in pixels only. Set the focal length and pixel size in your capture program. |
| *This is a processed image* | It was stacked, calibrated or stretched. Open a raw light for electrons and saturation. |
| *⚠ Sky noise does not match the gain and read noise in use* | The gain in use is probably wrong: run the bench (chapter 5). |

## A bench or dark run finds nothing

| Problem | What to do |
|---|---|
| *no flat or bias frames found (FLUX reads IMAGETYP, or 'Flat' / 'Bias' in the file name)* | Your frames' headers say neither. Shoot them with the capture program's flat and bias frame types, or put *Flat* and *Bias* in their names. |
| *the frames are from more than one camera setting* | A run needs one gain, offset and binning. Move the others out of the folder. |
| *the frames are not all the same size* | A subframe or a different binning slipped in. |
| *there are no FITS or XISF frames in this folder* | The bench run reads the folder itself, not its subfolders (the dark run reads subfolders). |
| Darks *left out: cut short* | The copy did not finish. Copy them again. |
| Darks *shot before the cooler settled* | They were more than 1.5 °C from their set temperature. Shoot after the cooler settles. |

Chapters 5 and 6 list the findings that ask you to shoot again, and what to change.

## The Sky page

| Problem | What to do |
|---|---|
| *No on-sky log files yet in …* | SHOBS-P 2.2.19.1 or later writes them after each reduction. Older SHOBS-P versions do not. |
| A log listed with ✖ *cannot be read* | The file is damaged; reduce that night again in SHOBS-P. |
| *update SHOBS-FLUX; the file is fine* | The log is from a newer SHOBS-P than this SHOBS-FLUX reads. |
| No zero point | No photometric night in that band yet, or too few catalogued stars (a night needs at least two). |
| A catalog failure (folder of lights) | VizieR did not answer. SHOBS-FLUX asks CDS first, then the Harvard (CfA) and Cape Town (IDIA) mirrors. Answers are kept for 30 days in the data folder's `cache` folder, so a field asked for once works offline. Try again later. |

## The other SHOBS programs do not see a profile

- They read the same data folder. If you set `SHOBS_DATA_HOME` for a test, unset it.
- SHOBS-SCHED matches a camera profile by camera, gain, offset and binning, and a system profile by telescope and
  camera: check both on the Profiles page.
- SHOBS-P reads the profiles from 2.4.0. Until then, type the linear limit in SHOBS-P's settings too.

## Starting afresh

To start SHOBS-FLUX's settings afresh, close it and delete `flux_settings.json` in the data folder. To start the
whole data folder afresh, close every SHOBS program and **rename** the `.shobs` folder (to `.shobs-old`, say)
rather than deleting it: it holds every profile and every measurement, and SHOBS-P's on-sky logs. SHOBS-FLUX makes a
new one the first time it saves something.

A single bad run is better taken off the History page (chapter 9), and a bad save better undone with **Roll back…**
(chapter 8). Neither deletes anything.

---

Previous: [11. Credits and citing](11-credits-and-citing.md) · Next: [13. Glossary](13-glossary.md)
