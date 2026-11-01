"""The History page's picture: one value of one setup over time (no Tk needed: the tests, the self-test and the system
report draw it with Agg).

draw_trend(fig, points, words, unit, now=None, spec=None, event=None) fills a Figure with one axis:
  dots          each night or run, with its error bar; on-sky nights that were not photometric are hollow, a night that
                was only marginal (the zero point drifted) is orange
  dashed line   the value the profile holds now (blue, with its source)
  dotted line   the maker's spec at this gain setting (grey), for camera values that have one
  event         an optional date (a mirror cleaning, a collimation): a vertical line, and the mean before and after
stamp(fig, version) writes "Created <DTG> · SHOBS-FLUX <version>" in the lower right corner for an exported PNG."""
from __future__ import annotations

import math
from datetime import datetime

import numpy as np

import sky_plots
from sky_plots import BLUE, HINT, ORANGE

SPEC_GREY = "#7a8a94"
EVENT = "#6a1b9a"


def _day(text):
    try:
        return datetime.strptime(str(text)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return None


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def num(x, sign: bool = False) -> str:
    """A number for a sentence: 4 decimals under 1, 3 under 100, 1 above ('+0.0002', '20.158', '16610.0')."""
    if not _finite(x):
        return "—"
    nd = 4 if abs(x) < 1 else (3 if abs(x) < 100 else 1)
    return f"{x:+.{nd}f}" if sign else f"{x:.{nd}f}"


def before_after(points: list[dict], event: str) -> dict | None:
    """The weighted mean before the event date and from it on, and the change with its uncertainty. None when either
    side has no point. Each side's ± is the error of its mean (the points' own errors, or their scatter when larger)."""
    ev = _day(event)
    if ev is None:
        return None

    def side(ps):
        vals = np.array([p["value"] for p in ps], float)
        sig = np.array([p["sigma"] if _finite(p.get("sigma")) and p["sigma"] > 0 else np.nan for p in ps], float)
        if not len(vals):
            return None
        if np.all(np.isfinite(sig)):
            w = 1.0 / sig ** 2
            m = float(np.sum(w * vals) / np.sum(w))
            e = float(1.0 / math.sqrt(np.sum(w)))
        else:
            m = float(np.mean(vals))
            e = float("nan")
        if len(vals) > 1:
            e_sc = float(np.std(vals, ddof=1) / math.sqrt(len(vals)))
            e = e_sc if not math.isfinite(e) else max(e, e_sc)
        return {"mean": m, "err": e, "n": len(vals)}
    pts = [p for p in points if _day(p["date"]) is not None and _finite(p.get("value"))]
    b = side([p for p in pts if _day(p["date"]) < ev])
    a = side([p for p in pts if _day(p["date"]) >= ev])
    if not b or not a:
        return None
    d = a["mean"] - b["mean"]
    de = math.hypot(b["err"], a["err"]) if math.isfinite(b["err"]) and math.isfinite(a["err"]) else float("nan")
    return {"before": b, "after": a, "change": d, "change_err": de}


def draw_trend(fig, points: list[dict], words: str, unit: str, now: dict | None = None, spec: float | None = None,
               event: str | None = None, title: str | None = None, ax=None, compact: bool = False) -> None:
    """points: from history.series_points (date, value, sigma, photometric, marginal). now: the profile's value
    record (value, sigma, source). spec: the maker's value at this gain. ax: draw into this axis (the report) instead
    of clearing the figure."""
    if ax is None:
        fig.clear()
        ax = fig.add_subplot(111)
        fig.subplots_adjust(left=0.1, right=0.97, top=0.86, bottom=0.14)
    sky_plots._style(ax)
    sky_plots._title(ax, title or words)
    ax.set_ylabel(unit or words)
    pts = [p for p in points if _day(p["date"]) is not None and _finite(p.get("value"))]
    if not pts:
        sky_plots._empty(ax, "nothing measured for this setup in these dates")
        return
    groups = (("used", BLUE, BLUE, "o"),
              ("marginal", ORANGE, ORANGE, "o"),
              ("not photometric", BLUE, "white", "o"))
    for name, edge, face, marker in groups:
        if name == "used":
            sel = [p for p in pts if p.get("photometric") is not False and not p.get("marginal")]
        elif name == "marginal":
            sel = [p for p in pts if p.get("marginal")]
        else:
            sel = [p for p in pts if p.get("photometric") is False and not p.get("marginal")]
        if not sel:
            continue
        x = [_day(p["date"]) for p in sel]
        y = [p["value"] for p in sel]
        e = [p["sigma"] if _finite(p.get("sigma")) else 0.0 for p in sel]
        label = {"used": "measured" if all(p.get("photometric") is None for p in pts) else "photometric night",
                 "marginal": "marginal night (drifting)", "not photometric": "not photometric"}[name]
        ax.errorbar(x, y, yerr=e, fmt=marker, ms=6, mfc=face, mec=edge, ecolor=edge, elinewidth=1, capsize=2,
                    zorder=3, label=label, linestyle="none")
    days = [_day(p["date"]) for p in pts]
    if now and _finite(now.get("value")):
        src = now.get("source") or ""
        ax.axhline(now["value"], color=BLUE, linewidth=1.0, linestyle="--", zorder=2,
                   label=f"profile now {now['value']:g}" + (f" ({src})" if src else ""))
    if _finite(spec):
        ax.axhline(spec, color=SPEC_GREY, linewidth=1.2, linestyle=":", zorder=2, label=f"maker's spec {spec:g}")
    if event and _day(event):
        ev = _day(event)
        ax.axvline(ev, color=EVENT, linewidth=1.2, zorder=2, label=f"event {ev:%d %b %Y}")
        ba = before_after(pts, event)
        if ba:
            lo, hi = min(days + [ev]), max(days + [ev])
            ax.hlines(ba["before"]["mean"], lo, ev, colors=EVENT, linestyles="-.", linewidth=1.0, zorder=2)
            ax.hlines(ba["after"]["mean"], ev, hi, colors=EVENT, linestyles="-.", linewidth=1.0, zorder=2)
            ch = ba["change"]
            err = ba["change_err"]
            ax.text(0.99, 0.02, f"after − before: {num(ch, True)}" + (f" ± {num(err)}" if math.isfinite(err) else "")
                    + f" {unit}", transform=ax.transAxes, ha="right", va="bottom", fontsize=8.5, color=EVENT)
        days = days + [ev]
    sky_plots._date_axis(ax, days)
    ys = [p["value"] for p in pts] + [v for v in ((now or {}).get("value"), spec) if _finite(v)]
    es = [p["sigma"] if _finite(p.get("sigma")) else 0.0 for p in pts]
    lo = min(y - e for y, e in zip([p["value"] for p in pts], es))
    hi = max(y + e for y, e in zip([p["value"] for p in pts], es))
    lo, hi = min([lo] + ys), max([hi] + ys)
    pad = (hi - lo) * 0.15 or abs(hi) * 0.05 or 1.0
    ax.set_ylim(lo - pad, hi + pad * (3.0 if compact else 2.2))     # room at the top for the legend
    ax.legend(loc="upper left", fontsize=6.5 if compact else 8, frameon=False, ncol=2 if compact else 3)


def stamp(fig, version: str, setup_words: str = ""):
    """The DTG line on an exported picture (remove it afterwards: it is not shown on screen)."""
    import history
    text = f"Created {history.dtg()} · SHOBS-FLUX {version}"
    if setup_words:
        text = f"{setup_words}   ·   {text}"
    return fig.text(0.995, 0.004, text, fontsize=6.5, color=HINT, ha="right", va="bottom")

