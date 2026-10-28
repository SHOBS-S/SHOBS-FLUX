"""The Sky page's pictures: one night's zero points and every night's zero point and seeing.

draw(fig, summary, selected) fills a matplotlib Figure (no Tk needed, so the tests and the self-test render it with
Agg), 2 × 2:
  top left      the selected night: each frame's zero point against airmass, with the fitted line ZP1 − k (X − 1)
                (blue = a photometric night, orange = not photometric);
  top right     the selected night: each frame's zero point moved to airmass 1, against the time (hours UT), the
                frames after a meridian flip shaded;
  bottom left   every night: the zero point at airmass 1 with its error bar (filled = photometric and used, hollow =
                set aside), the combined value as a line with its ± band;
  bottom right  every night: the seeing (in focus = blue dots, defocused = orange triangles) and the in-focus median.
With no night selected the top row shows the newest night that has a zero point."""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np

import onsky

INK = "#1c2b33"
GRID = "#e3e9ed"
HINT = "#5d6d76"
BLUE = "#1f5f99"
ORANGE = "#e07b00"
BAND_COLORS = {"TG": "#2e7d32", "V": "#2e7d32", "CV": "#37474f", "TR": "#c62828", "R": "#c62828", "TB": "#1565c0",
               "B": "#1565c0", "I": "#6a1b9a", "SG": "#2e7d32", "SR": "#c62828", "SI": "#6a1b9a", "SZ": "#4e342e",
               "SU": "#283593"}
FONT = 9.5


def _style(ax):
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#9aa7b0")
    ax.tick_params(colors=INK, labelsize=9)
    ax.xaxis.label.set_color(INK)
    ax.yaxis.label.set_color(INK)
    ax.xaxis.label.set_fontsize(FONT)
    ax.yaxis.label.set_fontsize(FONT)


def _title(ax, text):
    ax.set_title(text, fontsize=10, color=INK, loc="left")


def _empty(ax, text):
    ax.text(0.5, 0.5, text, ha="center", va="center", color=HINT, fontsize=FONT, transform=ax.transAxes)


def _finite(v) -> bool:
    return v is not None and isinstance(v, (int, float)) and math.isfinite(v)


def _has_zp(r) -> bool:
    return r is not None and r.zp_frame is not None and np.isfinite(np.asarray(r.zp_frame, dtype=float)).sum() >= 2 \
        and r.ds.airmass is not None and len(r.ds.airmass) == len(r.zp_frame)


def night_for(summary, selected):
    """The night the top row shows: the selected one, else the newest usable night with a zero point."""
    if selected is not None:
        return selected
    for r in (summary.nights if summary else []):
        if r.usable and _has_zp(r):
            return r
    return None


def _day(night: str):
    try:
        return datetime.strptime(night, "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def draw(fig, summary, selected=None) -> None:
    fig.clear()
    gs = fig.add_gridspec(2, 2, wspace=0.30, hspace=0.62, left=0.09, right=0.985, top=0.93, bottom=0.11)
    axes = [fig.add_subplot(gs[i, j]) for i in (0, 1) for j in (0, 1)]
    for ax in axes:
        _style(ax)
    r = night_for(summary, selected)
    _zp_airmass(axes[0], r)
    _zp_time(axes[1], r)
    _zp_nights(axes[2], summary)
    _seeing_nights(axes[3], summary)


def _night_words(r) -> str:
    return f"{r.ds.night} {r.ds.target}" if r is not None else ""


def _zp_airmass(ax, r):
    _title(ax, "Zero point against airmass" + (f"  ·  {_night_words(r)}" if r is not None else ""))
    ax.set_xlabel("airmass X")
    ax.set_ylabel("zero point (mag, 1 e⁻/s)")
    if not _has_zp(r):
        _empty(ax, "no zero point this night" if r is not None else "no night with a zero point yet")
        return
    x = np.asarray(r.ds.airmass, dtype=float)
    z = np.asarray(r.zp_frame, dtype=float)
    ok = np.isfinite(x) & np.isfinite(z)
    col = BLUE if r.photometric else ORANGE
    ax.scatter(x[ok], z[ok], s=12, color=col, alpha=0.75, zorder=3,
               label="frames (photometric)" if r.photometric else "frames (not photometric)")
    if _finite(r.zp1) and _finite(r.k_used) and ok.any():
        xs = np.linspace(float(np.min(x[ok])), float(np.max(x[ok])), 20)
        ax.plot(xs, r.zp1 - r.k_used * (xs - 1.0), color=INK, linewidth=1.4, zorder=4,
                label=f"ZP1 {r.zp1:.3f}, k {r.k_used:.3f}")
    ax.legend(loc="best", fontsize=9, frameon=True, framealpha=0.9, edgecolor=GRID)


def _hours(jd) -> np.ndarray:
    jd = np.asarray(jd, dtype=float)
    base = math.floor(float(np.nanmin(jd)) - 0.5) + 0.5        # 0 h UT of the day of the first frame
    return (jd - base) * 24.0


def _zp_time(ax, r):
    _title(ax, "Zero point at airmass 1 through the night")
    ax.set_xlabel("time (hours UT)")
    ax.set_ylabel("zero point at X = 1 (mag)")
    if not _has_zp(r) or not _finite(r.k_used):
        _empty(ax, "no zero point this night" if r is not None else "no night with a zero point yet")
        return
    x = np.asarray(r.ds.airmass, dtype=float)
    z = np.asarray(r.zp_frame, dtype=float) + r.k_used * (x - 1.0)
    jd = np.asarray(r.ds.jd, dtype=float)
    ok = np.isfinite(z) & np.isfinite(jd)
    if not ok.any():
        _empty(ax, "no zero point this night")
        return
    h = _hours(jd[ok])
    col = BLUE if r.photometric else ORANGE
    ax.scatter(h, z[ok], s=12, color=col, alpha=0.75, zorder=3)
    seg = r.ds.segment
    if seg is not None and len(seg) == len(jd):
        flip = np.asarray([s == "flip" for s in seg])[ok]
        if flip.any():
            ax.axvspan(float(h[flip].min()), float(h[flip].max()), color="#ffe0b2", alpha=0.6, zorder=1,
                       label="after the meridian flip")
            ax.legend(loc="best", fontsize=9, frameon=True, framealpha=0.9, edgecolor=GRID)
    if _finite(r.zp1):
        ax.axhline(r.zp1, color=INK, linewidth=1.0, linestyle="--", zorder=2)
    from matplotlib.ticker import FuncFormatter
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _p: f"{v % 24:g}"))


def _zp_nights(ax, summary):
    _title(ax, "Zero point per night (filled = used)")
    ax.set_ylabel("zero point at X = 1 (mag)")
    nights = [r for r in (summary.nights if summary else []) if _finite(r.zp1) and _day(r.ds.night)]
    if not nights:
        _empty(ax, "no night has a zero point yet")
        ax.set_xticks([])
        return
    for band in sorted({r.ds.band for r in nights}):
        bs = summary.bands.get(band)
        col = BAND_COLORS.get(band, INK)
        rs = [r for r in nights if r.ds.band == band]
        used = set(bs.zp_nights) if bs else set()
        for r in rs:
            filled = r.photometric and r.ds.night in used
            ax.errorbar([_day(r.ds.night)], [r.zp1], yerr=[r.zp1_sigma if _finite(r.zp1_sigma) else 0.0],
                        fmt="o", ms=6, color=col, mfc=col if filled else "none", mec=col, elinewidth=1.0,
                        capsize=2, zorder=3)
        if bs is not None and _finite(bs.zp1):
            ax.axhline(bs.zp1, color=col, linewidth=1.2, zorder=2,
                       label=f"{onsky.BAND_WORDS.get(band, band)} {bs.zp1:.3f}")
            if _finite(bs.zp1_sigma):
                ax.axhspan(bs.zp1 - bs.zp1_sigma, bs.zp1 + bs.zp1_sigma, color=col, alpha=0.12, zorder=1)
    _date_axis(ax)
    if ax.get_legend_handles_labels()[0]:
        ax.legend(loc="best", fontsize=9, frameon=True, framealpha=0.9, edgecolor=GRID)


def _seeing_nights(ax, summary):
    _title(ax, "Seeing per night")
    ax.set_ylabel("FWHM (″)")
    rs = [r for r in (summary.nights if summary else []) if _finite(r.fwhm_arcsec) and _day(r.ds.night)]
    if not rs:
        _empty(ax, "no seeing measured yet")
        ax.set_xticks([])
        return
    inf = [r for r in rs if not r.defocused]
    dfc = [r for r in rs if r.defocused]
    if inf:
        ax.scatter([_day(r.ds.night) for r in inf], [r.fwhm_arcsec for r in inf], s=28, color=BLUE, zorder=3,
                   label="in focus")
        med = float(np.median([r.fwhm_arcsec for r in inf]))
        ax.axhline(med, color=BLUE, linewidth=1.0, linestyle="--", zorder=2, label=f"in-focus median {med:.2f}″")
    if dfc:
        ax.scatter([_day(r.ds.night) for r in dfc], [r.fwhm_arcsec for r in dfc], s=34, marker="^", color=ORANGE,
                   zorder=3, label="defocused")
    _date_axis(ax)
    ax.set_ylim(bottom=0)
    ax.legend(loc="best", fontsize=9, frameon=True, framealpha=0.9, edgecolor=GRID)


def _date_axis(ax):
    import matplotlib.dates as mdates
    loc = mdates.AutoDateLocator(minticks=2, maxticks=5)
    ax.xaxis.set_major_locator(loc)
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%b %d"))
    lo, hi = ax.get_xlim()
    if hi - lo < 2:                                           # one night: give it some room either side
        mid = (lo + hi) / 2.0
        ax.set_xlim(mid - 1.5, mid + 1.5)
