"""The dark run's pictures: the glow map, the dark signal against exposure, and the noise against exposure.

draw(fig, res) fills a matplotlib Figure (no Tk needed; the tests and the self-test render it with Agg), for the
run's main temperature:
  left    the longest darks minus the bias frames (or the shortest darks), whole frame, shrunk: edge glow and hot
          pixels show; the central region the dark current is measured in is outlined;
  top     the dark signal: each exposure's level above the bias, in electrons, per color, with the fitted line
          (slope = dark current) and the manufacturer's spec as a dashed grey line;
  bottom  the noise: each exposure's temporal variance in e⁻² (the difference of two darks), per color, with the
          line read noise² + dark current × t and the spec's line dashed."""
from __future__ import annotations

import math

import numpy as np

import bench_plots as bp
import darks

SPEC = "#78909c"


def draw(fig, res: "darks.DarkResult") -> None:
    fig.clear()
    gs = fig.add_gridspec(2, 2, width_ratios=[0.95, 2.5], height_ratios=[1.0, 1.0], wspace=0.28, hspace=0.45,
                          left=0.04, right=0.985, top=0.93, bottom=0.11)
    tr = res.main
    _frame(fig.add_subplot(gs[:, 0]), res, tr)
    _signal(fig.add_subplot(gs[0, 1]), res, tr)
    _noise(fig.add_subplot(gs[1, 1]), res, tr)


def _frame(ax, res, tr):
    ax.set_axis_off()
    if tr is None or tr.thumb is None:
        ax.text(0.5, 0.5, "(no glow map: needs bias frames,\nor darks much shorter than the longest)", ha="center",
                va="center", color=bp.HINT, fontsize=8.5, transform=ax.transAxes)
        return
    img = tr.thumb
    lo, hi = np.nanpercentile(img, [2.0, 99.0])
    if hi - lo < 1e-6:
        hi = lo + 1.0
    ax.imshow(np.clip((img - lo) / (hi - lo), 0, 1), cmap="magma", origin="lower", interpolation="nearest")
    ys, xs = res.region
    f = tr.thumb_scale
    from matplotlib.patches import Rectangle
    ax.add_patch(Rectangle((xs.start / f, ys.start / f), (xs.stop - xs.start) / f, (ys.stop - ys.start) / f,
                           fill=False, edgecolor="#4fc3f7", linewidth=1.6))
    longest = max(s.exptime for s in tr.exposures)
    word = (f"glow at the {tr.glow_where}" if tr.glow_seen(longest) else "no edge glow")
    ax.set_title(f"{darks._exp(longest)} darks − {tr.glow_ref.replace('the ', '')}\n{word}", fontsize=9,
                 color=bp.INK, loc="left")


def _ref(res, tr, ch):
    """The zero point for the signal panel: the bias level, else the level line's value at t = 0."""
    b = res.bias_adu.get(ch.ch)
    if b is not None and math.isfinite(b):
        return b
    return ch.level_at_zero if math.isfinite(ch.level_at_zero) else None


def _signal(ax, res, tr):
    bp._style(ax)
    if tr is None or not tr.exposures:
        return
    tmax = max(s.exptime for s in tr.exposures)
    for c, ch in tr.channels.items():
        ref = _ref(res, tr, ch)
        if ref is None:
            continue
        col = bp.COLORS.get(c, bp.INK)
        pts = [s for s in tr.exposures if c in s.level]
        x = [s.exptime for s in pts]
        y = [(s.level[c] - ref) * ch.gain for s in pts]
        e = [(s.level_se[c] if math.isfinite(s.level_se.get(c, float("nan"))) else 0.0) * ch.gain for s in pts]
        ax.errorbar(x, y, yerr=e, fmt="o", ms=4.5, color=col, capsize=2, zorder=3,
                    label=f"{c}  {darks.rate_text(ch.mean_rate)} e⁻/s" if math.isfinite(ch.mean_rate) else c)
        if math.isfinite(ch.mean_rate):
            t = np.linspace(0, tmax * 1.05, 20)
            z = (ch.level_at_zero - ref) * ch.gain if math.isfinite(ch.level_at_zero) else 0.0
            ax.plot(t, z + ch.mean_rate * t, color=col, linewidth=1.0, alpha=0.7)
    if tr.spec:
        t = np.linspace(0, tmax * 1.05, 20)
        ax.plot(t, tr.spec * t, color=SPEC, linestyle="--", linewidth=1.1, label=f"spec {darks.rate_text(tr.spec)}")
    ax.axhline(0, color="#9aa7b0", linewidth=0.8)
    ax.set_xlim(left=0)
    ax.set_xlabel("Exposure (s)", fontsize=9)
    ax.set_ylabel("Level above bias (e⁻)", fontsize=9)
    where = "" if math.isnan(tr.temp_c) else f" at {tr.temp_c:g} °C"
    ax.set_title(f"Dark signal{where}: slope = dark current", fontsize=10, color=bp.INK, loc="left")
    ax.legend(fontsize=8, frameon=False, loc="upper left")


def _noise(ax, res, tr):
    bp._style(ax)
    if tr is None or not tr.exposures:
        return
    tmax = max(s.exptime for s in tr.exposures)
    rn2 = []
    for c, ch in tr.channels.items():
        col = bp.COLORS.get(c, bp.INK)
        g2 = ch.gain * ch.gain
        pts = [(s.exptime, s.var[c] * g2, s.var_se[c] * g2) for s in tr.exposures if c in s.var]
        if c in res.bias_var:
            pts.append((0.0, res.bias_var[c] * g2, res.bias_var_se[c] * g2))
        if not pts:
            continue
        ax.errorbar([p[0] for p in pts], [p[1] for p in pts], yerr=[p[2] for p in pts], fmt="o", ms=4.5, color=col,
                    capsize=2, zorder=3,
                    label=f"{c}  {darks.rate_text(ch.noise_rate)} e⁻/s" if math.isfinite(ch.noise_rate) else c)
        if math.isfinite(ch.noise_rate) and math.isfinite(ch.read_noise_e):
            t = np.linspace(0, tmax * 1.05, 20)
            ax.plot(t, ch.read_noise_e ** 2 + ch.noise_rate * t, color=col, linewidth=1.0, alpha=0.7)
            rn2.append(ch.read_noise_e ** 2)
    if tr.spec and rn2:
        t = np.linspace(0, tmax * 1.05, 20)
        ax.plot(t, float(np.median(rn2)) + tr.spec * t, color=SPEC, linestyle="--", linewidth=1.1, label="spec")
    ax.set_xlim(left=0)
    ax.set_xlabel("Exposure (s)", fontsize=9)
    ax.set_ylabel("Variance (e⁻²)", fontsize=9)
    ax.set_title("Noise: read noise² + dark current × exposure (from pairs of darks)", fontsize=10, color=bp.INK,
                 loc="left")
    ax.legend(fontsize=8, frameon=False, loc="upper left")
