# Screenshots (SHOBS-FLUX manual, 1.0.0 edition)

All new for 1.0.0, taken on Windows from SHOBS-FLUX 1.0.0 running on a COPY of the Shiloh Hill Observatory's data
folder (`SHOBS_DATA_HOME`), so saves, roll-backs and "take off the page" in the shots never touch the real profiles.
Real camera (ZWO ASI2600MC Air, gain 100, offset 50), telescope (iOptron 14 RC) and nights. Rules for every shot:
the Sky page's **Site elevation / latitude / longitude** boxes empty; no other observatory's profile (the Moeller
CDK1000 / Andor profile) in a picker or list that shows; the main window maximized.

| File | Chapter | Set up before taking it |
|---|---|---|
| `img/01-window-parts.png` | 1 | Whole main window on the Camera page after the bench run (sidebar, page title, page, status strip). |
| `img/02-first-start.png` | 2 | Check image page, no frame open, status strip "Data folder: …". |
| `img/04-check-image.png` | 3, 4 | An HD 219134 or GSC 03308 light opened, a star clicked (Star box filled). |
| `img/05-shooting-plan.png` | 3, 5 | Shooting plan… → From one test flat… (a flat from Ladder 9Nov26). |
| `img/05-bench-page.png` | 3, 5 | Bench run on W:\FLUX\Ladder 9Nov26, bias W:\Bias, Central 25 %, Findings tab. |
| `img/05-save-camera.png` | 5 | Save to camera profile… window after that run. |
| `img/06-dark-page.png` | 6 | Dark run on the dark library (W:\Exoplanet\Darks or similar), Findings tab. |
| `img/07-sky-page.png` | 3, 7 | SHOBS-P's on-sky logs, the 14" RC setup, Run; the nights table and Findings. |
| `img/07-sky-night.png` | 7 | One photometric night picked: its four pictures. |
| `img/08-profiles-page.png` | 8 | The ASI2600MC camera profile. |
| `img/08-roll-back.png` | 8 | Roll back… on that profile, one earlier revision picked. |
| `img/08-spec-curves.png` | 8 | Spec curves…, ZWO ASI2600MC, Gain curve. |
| `img/08-copy-for-tools.png` | 8 | Copy for AstroImageJ / EXOTIC / HOPS… on the 14" RC system profile. |
| `img/09-history-page.png` | 9 | 14" RC system, Zero point at airmass 1 (green), All dates, an event date. |
| `img/09-report-page.png` | 9 | One page of a shared-copy system report (rendered from the PDF). |
| `img/10-busy-bar.png` | 10 | Crop of the lower left during a bench run. |
| `img/11-credits-page.png` | 11 | Credits and sources, How to cite and the catalogs. |

## Numbers in the text to check against the shots

- Ch 3, 10: bench headline *Bench run: gain 0.2571 e⁻/ADU, read noise 1.12 e⁻; 31 flat pairs, 50 bias pairs.*
- Ch 3, 7: Sky headline (nights, photometric count, ZP TG 20.60 ± 0.01, k 0.208, seeing 4.9″): fill in the "…".
- Ch 5: EGAIN line (+5.9 % or +5.8 %?), the per-color gains R 0.2583 G 0.2563 B 0.2578, "largest 0.36 %".
- Ch 6: dark headline (temperature, rate ± sigma, darks and bias counts).
- Ch 10: the busy-bar words for each job.
