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
    ("Methods", [
        ("Photon transfer", "Gain, read noise, full well, linearity and the exposure-timing offset from pairs of "
         "flats and bias frames (the Camera page's bench run: the variance of each pair's difference against its "
         "signal); the electron counts on the Check image page.",
         "Janesick, J. R. 2007, Photon Transfer, SPIE Press, doi:10.1117/3.725073.", ""),
        ("The CCD equation", "Signal-to-noise of each star from its light, the sky, dark current and read noise.",
         "Howell, S. B. 2006, Handbook of CCD Astronomy, 2nd ed., Cambridge University Press.", ""),
        ("Star finding and FWHM", "The star detector and the Gaussian FWHM fit follow SHOBS-P's.",
         "Armbrust, J. W., and Shiloh Hill Observatory, SHOBS-P: Shiloh Hill Observatory Photometry, Zenodo, "
         "doi:10.5281/zenodo.23417064.", "https://github.com/SHOBS-S/SHOBS-P"),
    ]),
    ("Design and code", [
        ("SHOBS-SCHED", "The window frame, colors, busy bar and launchers (House Style shared by the SHOBS apps).",
         "Armbrust, J. W., and Shiloh Hill Observatory, SHOBS-SCHED: Shiloh Hill Observatory Photometry Scheduler, "
         "Zenodo, doi:10.5281/zenodo.23674791.", "https://github.com/SHOBS-S/SHOBS-SCHED"),
        ("Camera specs", "Manufacturer's values (gain, read noise, full well against the gain setting) read by "
         "hand from published charts; shown as spec, never as measured.", "ZWO ASI2600MC (IMX571) gain chart.",
         "https://www.zwoastro.com"),
    ]),
    ("Libraries", [
        ("Python and Tk", "The program and its windows; SQLite (part of Python) for the run history.",
         "Python Software Foundation.", "https://www.python.org"),
        ("NumPy", "Array math throughout.", "Harris, C. R. et al. 2020, Nature 585, 357.", "https://numpy.org"),
        ("SciPy", "Star finding filters and the FWHM fit.", "Virtanen, P. et al. 2020, Nature Methods 17, 261.",
         "https://scipy.org"),
        ("Astropy", "Reading FITS files.", "Astropy Collaboration 2022, ApJ 935, 167.", "https://www.astropy.org"),
        ("Matplotlib", "The frame picture on the Check image page; the photon transfer and linearity plots.",
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
