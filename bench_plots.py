"""The bench run's pictures: the frame with the measured region, the photon transfer curve and the linearity curve.

draw(fig, res) fills a matplotlib Figure (no Tk needed, so the tests and the self-test render it with Agg):
  left    one flat of the ladder, shrunk, with the central region outlined;
  top     the photon transfer curve: temporal variance against signal (ADU², ADU; log–log) per color, the fitted
          line (shot noise + read noise) through the points it rests on, saturated pairs as hollow marks;
  bottom  linearity: each pair's deviation from the straight line (%) against its raw level (ADU), the ±1 % band,
          and the linear limit.
Filled marks = used in the fit; hollow = shown, not used (saturated, or past the straight part)."""
from __future__ import annotations

import math

import numpy as np

import bench

COLORS = {"R": "#c62828", "G": "#2e7d32", "B": "#1565c0", "L": "#37474f"}
INK = "#1c2b33"
GRID = "#e3e9ed"
HINT = "#5d6d76"


def _style(ax):
    ax.grid(True, color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    for s in ("left", "bottom"):
        ax.spines[s].set_color("#9aa7b0")
    ax.tick_params(colors=INK, labelsize=8.5)
    ax.xaxis.label.set_color(INK)
    ax.yaxis.label.set_color(INK)


def draw(fig, res: "bench.BenchResult") -> None:
    fig.clear()
    gs = fig.add_gridspec(2, 2, width_ratios=[0.85, 2.6], height_ratios=[1.15, 1.0], wspace=0.28, hspace=0.42,
                          left=0.04, right=0.985, top=0.93, bottom=0.11)
    ax_img = fig.add_subplot(gs[:, 0])
    ax_ptc = fig.add_subplot(gs[0, 1])
    ax_lin = fig.add_subplot(gs[1, 1])
    _frame(ax_img, res)
    _ptc(ax_ptc, res)
    _linearity(ax_lin, res)


def _frame(ax, res):
    ax.set_axis_off()
    if res.thumb is None:
        ax.text(0.5, 0.5, "(no picture)", ha="center", va="center", color=HINT, transform=ax.transAxes)
        return
    img = res.thumb
    lo, hi = np.nanpercentile(img[::2, ::2], [1.0, 99.5])
    z = np.clip((img - lo) / max(hi - lo, 1e-6), 0, 1)
    ax.imshow(z, cmap="gray", origin="lower", interpolation="nearest")
    ys, xs = res.region
    f = res.thumb_scale
    from matplotlib.patches import Rectangle
    ax.add_patch(Rectangle((xs.start / f, ys.start / f), (xs.stop - xs.start) / f, (ys.stop - ys.start) / f,
                           fill=False, edgecolor="#ffb300", linewidth=1.8))
    ax.set_title(f"{res.region_name}: {xs.stop - xs.start}×{ys.stop - ys.start} px", fontsize=9, color=INK,
                 loc="left")


def _ptc(ax, res):
    _style(ax)
    xs_all, ys_all = [], []
    for c, ch in res.channels.items():
        col = COLORS.get(c, INK)
        pts = [p for p in ch.pairs if p.signal > 0 and p.var > 0]
        used = [p for p in pts if p.used_gain]
        other = [p for p in pts if not p.used_gain]
        ax.scatter([p.signal for p in used], [p.var for p in used], s=26, color=col, zorder=3,
                   label=f"{c}  {ch.gain:.4f} e⁻/ADU" if math.isfinite(ch.gain) else c)
        ax.scatter([p.signal for p in other], [p.var for p in other], s=26, facecolors="none", edgecolors=col,
                   linewidths=1.2, zorder=3)
        xs_all += [p.signal for p in pts]
        ys_all += [p.var for p in pts]
        if math.isfinite(ch.gain) and used:
            rn2 = ch.read_noise_adu ** 2 if math.isfinite(ch.read_noise_adu) else max(ch.ptc_intercept, 0.0)
            s0 = max(1.0, 0.5 * min(p.signal for p in used))
            s1 = 1.5 * max(p.signal for p in used)
            s = np.geomspace(s0, s1, 60)
            ax.plot(s, s / ch.gain + rn2, color=col, linewidth=1.1, alpha=0.75)
    if xs_all:
        ax.set_xscale("log")
        ax.set_yscale("log")
    ax.set_xlabel("Signal above bias (ADU)", fontsize=9)
    ax.set_ylabel("Variance (ADU²)", fontsize=9)
    ax.set_title("Photon transfer curve: slope = 1 / gain", fontsize=10, color=INK, loc="left")
    if res.channels:
        ax.legend(fontsize=8, frameon=False, loc="upper left")


def _linearity(ax, res):
    _style(ax)
    tol = bench.LIN_TOL_PCT
    ax.axhspan(-tol, tol, color="#eaf2f8", zorder=0)
    ax.axhline(0, color="#9aa7b0", linewidth=0.8)
    ymax = 2.5 * tol
    for c, ch in res.channels.items():
        col = COLORS.get(c, INK)
        pts = sorted([p for p in ch.pairs if p.exptime > 0 and math.isfinite(p.deviation_pct) and not p.saturated],
                     key=lambda p: p.mean_raw)
        if not pts:
            continue
        ax.plot([p.mean_raw for p in pts], [p.deviation_pct for p in pts], color=col, linewidth=0.9, alpha=0.6)
        used = [p for p in pts if p.used_lin]
        other = [p for p in pts if not p.used_lin]
        ax.scatter([p.mean_raw for p in used], [p.deviation_pct for p in used], s=24, color=col, zorder=3, label=c)
        ax.scatter([p.mean_raw for p in other], [p.deviation_pct for p in other], s=24, facecolors="none",
                   edgecolors=col, linewidths=1.2, zorder=3)
        ymax = max(ymax, min(12.0, 1.15 * max(abs(p.deviation_pct) for p in pts)))
    if math.isfinite(res.linear_limit_adu):
        ax.axvline(res.linear_limit_adu, color="#8a5a00", linewidth=1.2, linestyle="--")
        ax.text(res.linear_limit_adu, ymax * 0.92, ("at least " if res.linear_is_bound else "")
                + f"{res.linear_limit_adu:,.0f} ADU  ", color="#8a5a00", fontsize=8.5, va="top", ha="right")
    if math.isfinite(res.saturation_adu):
        ax.axvline(res.saturation_adu, color="#b00020", linewidth=1.0, linestyle=":")
    ax.set_ylim(-ymax, ymax)
    ax.set_xlim(left=0)
    ax.set_xlabel("Raw level (ADU, bias included)", fontsize=9)
    ax.set_ylabel("Off the straight line (%)", fontsize=9)
    ax.set_title(f"Linearity: ±{tol:g} % band, dashed = linear limit, dotted = saturation", fontsize=10,
                 color=INK, loc="left")
    ax.xaxis.set_major_formatter(_thousands())


def _thousands():
    from matplotlib.ticker import FuncFormatter
    return FuncFormatter(lambda v, _p: f"{v / 1000:g}k" if abs(v) >= 1000 else f"{v:g}")
