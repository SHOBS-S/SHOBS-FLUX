"""Measured against the spec: the manufacturer's curves with your camera's measured points on them.

gather(instrume, bench_res, dark_res) collects the points: every saved camera profile of this camera model (one per
gain setting), the earlier runs in the run history (lighter marks), and the runs on screen that are not saved yet
(stars). draw(fig, spec, points) fills a matplotlib Figure with four panels, each the spec curve as a line and the
measured points on it:
  gain (e⁻/ADU) against the gain setting           read noise (e⁻) against the gain setting (an HCG jump shows)
  full well (e⁻) against the gain setting          dark current (e⁻/px/s) against the sensor temperature
An upper limit is drawn as a downward triangle at the limit. With no spec for the camera the measured points are
drawn alone, and the Spec curves… editor (Profiles page) can add one."""
from __future__ import annotations

import math
import re

import bench_plots as bp
import shobs_profile as sp

PANELS = (("gain_e_per_adu", "Gain (e⁻/ADU)", "Gain setting", True),
          ("read_noise_e", "Read noise (e⁻)", "Gain setting", False),
          ("full_well_e", "Full well (e⁻)", "Gain setting", True),
          ("dark", "Dark current (e⁻/px/s)", "Sensor temperature (°C)", True))
SPEC_LINE = "#546e7a"
SAVED = "#c62828"
RUN = "#ef6c00"
EARLIER = "#ef9a9a"


def _gain_of_id(pid: str):
    m = re.search(r"__g([0-9pm]+)__", pid or "")
    if not m:
        return None
    try:
        return float(m.group(1).replace("p", ".").replace("m", "-"))
    except ValueError:
        return None


def _matches(spec: dict | None, instrume) -> set:
    names = {sp.slug(instrume)} if instrume else set()
    for m in (spec or {}).get("matches_instrume") or []:
        names.add(sp.slug(m))
    names.discard("unknown")
    return names


def _temp_of_tag(tag):
    if not isinstance(tag, str) or not tag.endswith("C"):
        return None
    try:
        return float(tag[:-1])
    except ValueError:
        return None


def gather(spec: dict | None, instrume=None, bench_res=None, dark_res=None) -> dict:
    """{panel key: [point]}; a point = {x, y, sigma, kind ('saved' / 'run' / 'earlier'), label, upper}."""
    out = {k: [] for k, *_ in PANELS}
    names = _matches(spec, instrume)
    ids = []
    for path in sp.list_profiles("cameras"):
        try:
            doc = sp.load(path)
        except sp.ProfileError:
            continue
        if doc.get("format") != sp.CAMERA_FORMAT:
            continue
        cam = doc.get("camera") or {}
        if names and sp.slug(cam.get("instrume")) not in names:
            continue
        pid = str(doc.get("profile_id") or "")
        ids.append(pid)
        g = sp._finite((doc.get("mode") or {}).get("gain_setting"))
        if g is None:
            g = _gain_of_id(pid)
        for key in ("gain_e_per_adu", "read_noise_e", "full_well_e"):
            rec = (doc.get("values") or {}).get(key)
            if sp.is_record(rec) and rec.get("source") == "measured" and sp.value_of(rec) is not None and g is not None:
                out[key].append({"x": g, "y": float(rec["value"]), "sigma": sp._finite(rec.get("sigma")),
                                 "kind": "saved", "label": f"{pid} ({rec.get('date') or ''})", "upper": False})
        block = doc.get("dark") if isinstance(doc.get("dark"), dict) else {}
        for p in block.get("by_temp") or []:
            t, v = sp._finite(p.get("temp_c")), sp._finite(p.get("value"))
            if t is not None and v is not None and v > 0:
                out["dark"].append({"x": t, "y": v, "sigma": sp._finite(p.get("sigma")), "kind": "saved",
                                    "label": f"{pid} ({block.get('date') or ''})", "upper": bool(p.get("upper_limit"))})
    # earlier runs (the history), camera-wide values only
    import history
    rows = history.values_for(("gain_e_per_adu", "read_noise_e", "full_well_e", "dark_current_e_per_px_s"),
                              ids if names else None)
    seen = {(k, round(p["x"], 3), round(p["y"], 9)) for k, pts in out.items() for p in pts}
    for r in rows:
        v = r.get("value")
        if v is None:
            continue
        if r["key"] == "dark_current_e_per_px_s":
            k, x = "dark", _temp_of_tag(r.get("channel"))
        else:
            if r.get("channel"):
                continue
            k, x = r["key"], _gain_of_id(r.get("profile_id"))
        if x is None or (k == "dark" and v <= 0):
            continue
        if (k, round(x, 3), round(float(v), 9)) in seen:
            continue
        seen.add((k, round(x, 3), round(float(v), 9)))
        out[k].append({"x": x, "y": float(v), "sigma": r.get("sigma"), "kind": "earlier",
                       "label": f"{r.get('run_key')} ({r.get('date') or ''})",
                       "upper": k == "dark" and r.get("sigma") is None})
    # runs on screen, saved or not
    if bench_res is not None:
        g = sp._finite(bench_res.setup.get("gain_setting"))
        if g is not None:
            for key, v, s in (("gain_e_per_adu", bench_res.gain, bench_res.gain_sigma),
                              ("read_noise_e", bench_res.read_noise_e, None),
                              ("full_well_e", bench_res.full_well_e, None)):
                if v is not None and math.isfinite(v):
                    out[key].append({"x": g, "y": float(v), "sigma": s if s is not None and math.isfinite(s) else None,
                                     "kind": "run", "label": "this bench run", "upper": False})
    if dark_res is not None:
        for t, tr in dark_res.temps.items():
            if not math.isnan(t) and math.isfinite(tr.rate) and tr.rate > 0:
                out["dark"].append({"x": t, "y": tr.rate, "sigma": None if tr.upper_limit else tr.sigma,
                                    "kind": "run", "label": "this dark run", "upper": tr.upper_limit})
    return out


def spec_points(spec: dict | None, key: str) -> list:
    if not spec:
        return []
    if key == "dark":
        pts = ((spec.get("dark_current_curve") or {}).get("points")) or []
    else:
        pts = (spec.get("curves") or {}).get(key) or []
    out = []
    for p in pts:
        if isinstance(p, (list, tuple)) and len(p) == 2 and sp._finite(p[0]) is not None and \
                sp._finite(p[1]) is not None:
            out.append((float(p[0]), float(p[1])))
    return sorted(out, key=lambda p: p[0])            # stable: two points at one x (an HCG switch) keep their order


def draw(fig, spec: dict | None, points: dict, title: str | None = None) -> None:
    fig.clear()
    gs = fig.add_gridspec(2, 2, wspace=0.26, hspace=0.48, left=0.08, right=0.985, top=0.9, bottom=0.1)
    model = (spec or {}).get("model")
    fig.suptitle(title or (f"Measured against the spec: {model}" if model else
                           "Measured points (no spec for this camera: add one with Spec curves… on the Profiles page)"),
                 fontsize=10.5, color=bp.INK, x=0.01, ha="left")
    for i, (key, ylab, xlab, logy) in enumerate(PANELS):
        ax = fig.add_subplot(gs[i // 2, i % 2])
        draw_panel(ax, spec, points.get(key) or [], key, ylab, xlab, logy)


def draw_panel(ax, spec, pts, key, ylab, xlab, logy, legend=True) -> None:
    curve = spec_points(spec, key)
    if not curve and not pts:
        ax.set_axis_off()
        ax.text(0.5, 0.5, f"{ylab}\nno spec curve, nothing measured yet", transform=ax.transAxes, ha="center",
                va="center", color=bp.HINT, fontsize=8.5)
        return
    bp._style(ax)
    if curve:
        ax.plot([p[0] for p in curve], [p[1] for p in curve], color=SPEC_LINE, linewidth=1.4, marker=".", ms=5,
                label="spec", zorder=2)
    styles = {"earlier": dict(marker="o", s=22, facecolors="none", edgecolors=EARLIER, linewidths=1.1, zorder=3),
              "saved": dict(marker="o", s=34, color=SAVED, zorder=4),
              "run": dict(marker="*", s=110, color=RUN, edgecolors="#5d4037", linewidths=0.6, zorder=5)}
    words = {"earlier": "earlier runs", "saved": "saved profile", "run": "this run (not saved)"}
    for kind in ("earlier", "saved", "run"):
        sel = [p for p in pts if p["kind"] == kind]
        firm = [p for p in sel if not p["upper"]]
        ul = [p for p in sel if p["upper"]]
        if firm:
            ax.scatter([p["x"] for p in firm], [p["y"] for p in firm], label=words[kind], **styles[kind])
            for p in firm:
                if p.get("sigma"):
                    ax.errorbar([p["x"]], [p["y"]], yerr=[[min(p["sigma"], 0.95 * p["y"]) if logy else p["sigma"]],
                                                          [p["sigma"]]],
                                fmt="none", ecolor=styles[kind].get("color", EARLIER), capsize=2, zorder=3)
        if ul:
            st = dict(styles[kind])
            st["marker"] = "v"
            ax.scatter([p["x"] for p in ul], [p["y"] for p in ul],
                       label=f"{words[kind]}: at most" if not firm else None, **st)
    if logy and (curve or pts):
        ys = [p[1] for p in curve] + [p["y"] for p in pts]
        if all(y > 0 for y in ys):
            ax.set_yscale("log")
    ax.set_xlabel(xlab, fontsize=8.5)
    ax.set_ylabel(ylab, fontsize=8.5)
    if not curve:
        ax.text(0.5, 0.92, "no spec curve", transform=ax.transAxes, ha="center", va="top", color=bp.HINT, fontsize=8)
    if not pts and curve:
        ax.text(0.5, 0.08, "nothing measured yet", transform=ax.transAxes, ha="center", va="bottom", color=bp.HINT,
                fontsize=8)
    if legend and (curve or pts):
        ax.legend(fontsize=7.5, frameon=False, loc="best")
    if key == "dark" and pts and curve:
        lo = min([p["x"] for p in pts] + [curve[0][0]])
        hi = max([p["x"] for p in pts] + [curve[-1][0]])
        ax.set_xlim(lo - 2, hi + 2)
    if key != "dark":
        ax.xaxis.set_major_locator(_int_locator())


def _int_locator():
    from matplotlib.ticker import MaxNLocator
    return MaxNLocator(nbins=6, integer=True)


def overlay_ratio(spec: dict | None, key: str, x: float, y: float):
    """measured ÷ spec at x (None when the spec has no such curve)."""
    if key == "dark":
        import instrument
        pts = spec_points(spec, "dark")
        v = instrument.dark_from_curve([list(p) for p in pts], x) if len(pts) >= 2 else None
    else:
        v = sp.spec_at(spec, key, x)
    return (y / v) if v else None
