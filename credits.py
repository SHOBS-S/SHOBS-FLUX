"""SHOBS-FLUX credits: the copyright line, how to cite FLUX, and every catalog, method and library it uses (the
Credits and sources page; same layout as SHOBS-P's and SHOBS-SCHED's)."""
from __future__ import annotations

COPYRIGHT = "© 2026 Shiloh Hill Observatory"
LICENSE_LINE = ("SHOBS-FLUX is free software under the GNU General Public License, version 3 or later; it comes "
                "with NO WARRANTY. See the LICENSE file.")
STATUS_LINE = ("© Shiloh Hill Observatory    Methods: photon transfer (Janesick), CCD equation (Howell)    "
               "Full list: Credits and sources")

# Set at the 1.0.0 release (Zenodo). The Credits page shows "How to cite" only when one is set.
CITATION_DOI = ""
ASCL_ID = ""


def citation_text(version: str) -> str:
    if not (CITATION_DOI or ASCL_ID):
        return ""
    where = ", ".join(x for x in (f"doi:{CITATION_DOI}" if CITATION_DOI else "",
                                  f"ascl:{ASCL_ID}" if ASCL_ID else "") if x)
    return (f"Armbrust, J. W., and Shiloh Hill Observatory, SHOBS-FLUX: Shiloh Hill Observatory Optical System "
            f"Characterization, version {version}, {where}")


# (section, [(name, used for, citation / acknowledgement, web address)])
CREDITS = [
    ("Catalogs", [
        ("APASS DR9", "Catalog magnitudes (B, V, g′, r′, i′) of the field stars the Sky page's zero point is measured "
         "against.",
         "Henden, A. A. et al. 2016, VizieR On-line Data Catalog II/336. This research was made use of the AAVSO "
         "Photometric All-Sky Survey (APASS), funded by the Robert Martin Ayers Sciences Fund and NSF AST-1412587.",
         "https://www.aavso.org/apass"),
        ("Gaia DR3", "Field-star magnitudes where APASS has none (transformed from Gaia G, BP and RP), and star "
         "positions.",
         "Gaia Collaboration, Vallenari, A. et al. 2023, A&A 674, A1. This work has made use of data from the "
         "European Space Agency (ESA) mission Gaia, processed by the Gaia Data Processing and Analysis Consortium "
         "(DPAC).", "https://www.cosmos.esa.int/gaia"),
        ("VizieR", "Reading APASS, Gaia and VSX for a field.",
         "Ochsenbein, F. et al. 2000, A&AS 143, 23, doi:10.26093/cds/vizier.", "https://vizier.cds.unistra.fr"),
        ("AAVSO VSX", "Leaving known variable stars out of the zero point.",
         "Watson, C. L. et al. 2006, SASS 25, 47.", "https://www.aavso.org/vsx"),
    ]),
    ("Methods", [
        ("Photon transfer", "Gain, read noise, full well, linearity and the exposure-timing offset from pairs of "
         "flats and bias frames (the Camera page's bench run: the variance of each pair's difference against its "
         "signal); dark current from darks, by their level and by their noise (the dark run); the electron counts "
         "on the Check image page.",
         "Janesick, J. R. 2007, Photon Transfer, SPIE Press, doi:10.1117/3.725073.", ""),
        ("The CCD equation", "Signal-to-noise of each star from its light, the sky, dark current and read noise.",
         "Howell, S. B. 2006, Handbook of CCD Astronomy, 2nd ed., Cambridge University Press.", ""),
        ("Star finding and FWHM", "The star detector and the Gaussian FWHM fit follow SHOBS-P's.",
         "Armbrust, J. W., and Shiloh Hill Observatory, SHOBS-P: Shiloh Hill Observatory Photometry, Zenodo, "
         "doi:10.5281/zenodo.23417064.", "https://github.com/SHOBS-S/SHOBS-P"),
        ("Gaia G → V and R", "Johnson–Cousins V and R of field stars from Gaia G and BP − RP.",
         "Riello, M. et al. 2021, A&A 649, A3.", ""),
        ("Gaia → Sloan", "Sloan g, r, i, z of field stars from Gaia G and BP − RP, then SDSS ugriz to the primed "
         "u′g′r′i′z′ system.",
         "Gaia DR3 documentation, section 5.5.1 (Carrasco, J. M. & Bellazzini, M.); Tucker, D. L. et al. 2006, "
         "AN 327, 821.", ""),
        ("Scintillation", "Young's C_Y from each star's frame-to-frame scatter (the Sky page), with the modified "
         "Young approximation SHOBS-P uses.",
         "Young, A. T. 1967, AJ 72, 747; Osborn, J. et al. 2015, MNRAS 452, 1707.", ""),
        ("Airmass", "The airmass of frames whose header gives none.",
         "Kasten, F. & Young, A. T. 1989, Applied Optics 28, 4735.", ""),
        ("The Moon's phase", "The Moon's illuminated fraction for each night (the dark nights set the sky "
         "brightness).", "Astropy (get_body).", "https://www.astropy.org"),
    ]),
    ("Design and code", [
        ("SHOBS-SCHED", "The window frame, colors, busy bar and launchers (House Style shared by the SHOBS apps).",
         "Armbrust, J. W., and Shiloh Hill Observatory, SHOBS-SCHED: Shiloh Hill Observatory Photometry Scheduler, "
         "Zenodo, doi:10.5281/zenodo.23674791.", "https://github.com/SHOBS-S/SHOBS-SCHED"),
        ("Camera specs", "Manufacturer's values (gain, read noise, full well against the gain setting; dark "
         "current against the sensor temperature) read by hand from published charts; shown as spec, never as "
         "measured, and drawn under the measured points.", "ZWO ASI2600MC (IMX571) gain, read noise, full well and "
         "dark current charts.", "https://www.zwoastro.com"),
    ]),
    ("Libraries", [
        ("Python and Tk", "The program and its windows; SQLite (part of Python) for the run history.",
         "Python Software Foundation.", "https://www.python.org"),
        ("NumPy", "Array math throughout.", "Harris, C. R. et al. 2020, Nature 585, 357.", "https://numpy.org"),
        ("SciPy", "Star finding filters and the FWHM fit.", "Virtanen, P. et al. 2020, Nature Methods 17, 261.",
         "https://scipy.org"),
        ("Astropy", "Reading FITS files; WCS plate solutions, times and sky coordinates (the Sky page).", "Astropy Collaboration 2022, ApJ 935, 167.", "https://www.astropy.org"),
        ("Matplotlib", "The frame picture on the Check image page; the photon transfer, linearity, dark and "
         "measured-against-spec plots; the Sky page's night plots.",
         "Hunter, J. D. 2007, Computing in Science & Engineering 9, 90.", "https://matplotlib.org"),
    ]),
]


def credits_text() -> str:
    lines = []
    for section, items in CREDITS:
        lines.append(section.upper())
        lines.append("")
        for name, use, cite, url in items:
            lines.append(name)
            lines.append(f"  Used for: {use}")
            lines.append(f"  {cite}")
            if url:
                lines.append(f"  {url}")
            lines.append("")
    return "\n".join(lines).rstrip() + "\n"
