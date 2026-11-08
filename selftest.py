"""SHOBS-FLUX self-test: checks this copy with the Python the shortcuts use. It changes no data: everything it writes
goes to a temporary folder (SHOBS_DATA_HOME), never to your ~/.shobs.

The log goes to selftest_log.txt next to this file and a copy to Downloads\\SHOBS-FLUX_selftest_log.txt.
Sections:
  1 environment (Python, NumPy, SciPy, Astropy, Matplotlib, Tkinter)
  2 the shared profile code (shobs_profile.py) and the three approved format-v1 examples, byte for byte
  3 reading frames and Check image on synthetic frames with known camera values
  3b the bench run (photon transfer curve) on synthetic flat-pair ladders with known camera values
  3c the dark run (dark current, hot pixels, edge glow) on synthetic dark libraries, the spec curves and their plots
  3d on-sky: zero point, extinction, scintillation and the judging of nights on synthetic on-sky logs; 0.6.0: star
     names, the folder run's sky and frames, the sky at each star, the header focal length, the AIJ/EXOTIC/HOPS copy
  4 the run history database
  4b History (0.5): trend series, taking a run off, the trend plot and its DTG, profile roll-back, the system report
  5 the window (Check image on a frame, every page) — needs a display
  6 your SHOBS data folder: what is there (read only; reported, never a failure)
"""
from __future__ import annotations

import glob
import io
import math
import os
import shutil
import sys
import tempfile
import time
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
REAL_DATA = os.environ.get("SHOBS_DATA_HOME") or os.path.join(os.path.expanduser("~"), ".shobs")
TMP = tempfile.mkdtemp(prefix="shobs_flux_selftest_")
os.environ["SHOBS_DATA_HOME"] = os.path.join(TMP, "data")
sys.path.insert(0, HERE)

OUT = io.StringIO()
FAILS: list[str] = []


def say(line: str = "") -> None:
    print(line)
    OUT.write(line + "\n")


def check(name: str, ok: bool, detail: str = "") -> None:
    say(f"  {'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    if not ok:
        FAILS.append(name)


def section(title: str, fn) -> None:
    say("")
    say(title)
    t0 = time.time()
    try:
        fn()
    except Exception:
        FAILS.append(title)
        say("  FAIL  (unexpected error)")
        for line in traceback.format_exc().splitlines():
            say("        " + line)
    say(f"  ({time.time() - t0:.1f} s)")


def s1_environment():
    import numpy
    import scipy
    import astropy
    say(f"  Python {sys.version.split()[0]} ({sys.executable})")
    say(f"  NumPy {numpy.__version__}, SciPy {scipy.__version__}, Astropy {astropy.__version__}")
    try:
        import matplotlib
        from matplotlib.backends import backend_tkagg  # noqa: F401
        ok, ver = True, matplotlib.__version__
    except Exception as exc:
        ok, ver = False, f"does not load ({exc}); run Create shortcuts.bat again"
    check(f"Matplotlib {ver} (the frame picture)", ok)
    import tkinter
    say(f"  Tk {tkinter.TkVersion}")
    import shobs_profile as sp
    check("data folder points at the test folder", sp.data_dir().startswith(TMP))


def s2_profiles():
    import json
    import shobs_profile as sp
    say(f"  shobs_profile.py version {sp.SHOBS_PROFILE_VERSION}, format v{sp.FORMAT_VERSION}")
    check("shared profile code checks itself", sp.self_check() == [], ", ".join(sp.self_check()))
    ex = sorted(glob.glob(os.path.join(HERE, "tests", "fixtures", "*.json")))
    check("three approved examples present", len(ex) == 3, f"{len(ex)} found")
    for path in ex:
        with open(path, "rb") as fh:
            raw = fh.read()
        doc = sp.load(path)
        same = sp.dumps(doc).encode("utf-8") == raw
        check(f"{os.path.basename(path)} round-trips byte for byte", same)
        check(f"{os.path.basename(path)} validates", sp.validate(doc) == [], "; ".join(sp.validate(doc)[:2]))
    cam = sp.load(next(p for p in ex if "cameras_" in os.path.basename(p)))
    sp.save_profile(cam)
    cam2 = json.loads(json.dumps(cam))
    cam2["values"]["linear_limit_adu"]["value"] = 33000
    sp.save_profile(cam2)
    check("a changed profile keeps the old revision in history/",
          len(sp.history_of("cameras", cam["profile_id"])) == 1 and
          sp.find_camera_profile(cam["profile_id"])["revision"] == 2)
    spec = sp.find_spec("ZWO ASI2600MC Duo", (os.path.join(HERE, "builtin_specs"),))
    check("built-in IMX571 spec found for an ASI2600MC Duo", spec is not None)


def s3_check():
    import frames as fr
    import image_check as ic
    import synth
    shape = (600, 800)
    data, hdr = synth.make_star_frame(shape=shape, sky_e=60.0, gain=0.25, read_noise=3.0, bias=500.0,
                                      stars=[(200.0, 200.0, 5.0e6)] + synth.star_list(shape, 15, 1.0e5),
                                      fwhm_px=6.0)
    p = synth.write_fits(os.path.join(TMP, "synthetic_light.fit"), data, hdr)
    f = fr.read_frame(p)
    check("synthetic FITS frame read", f.data.shape == shape and f.bayer == "RGGB" and f.exptime == 60.0)
    res = ic.check_image(p)
    b = res.bias[0]
    check(f"bias worked out from the sky noise: {b:.0f} ADU", abs(b - 500) < 15, "true 500")
    g = res.channels["G"]["sky_e"]
    check(f"sky {g:.1f} e⁻/px", abs(g - 60) / 60 < 0.15, "true 60")
    check("saturated star caught", any(s.status == "bad" for s in res.stars))
    fw = [s.fwhm_px for s in res.stars if math.isfinite(s.fwhm_px) and s.status == "ok"]
    med = sorted(fw)[len(fw) // 2] if fw else float("nan")
    check(f"FWHM {med:.2f} superpixels", abs(med - 3.0) < 0.6, "true 3.0 (6 real pixels)")
    data, hdr = synth.make_star_frame(shape=shape, sky_e=40, donut=True, stars=synth.star_list(shape, 8, 4e5,
                                                                                               margin=80))
    res = ic.check_image(synth.write_fits(os.path.join(TMP, "donut.fit"), data, hdr))
    check("defocused donut stars recognised", any("donut" in t.lower() for _k, t in res.lines))
    data, hdr = synth.make_star_frame(shape=shape, sky_e=4000, adu_step=16, gain=1.0, stars=[])
    f = fr.read_frame(synth.write_fits(os.path.join(TMP, "12bit.fit"), data, hdr))
    check("12-bit camera scaled to 16 bits recognised", f.adu_step == 16)
    p = synth.write_fits(os.path.join(TMP, "cut.fit"), data, hdr, cut_short=True)
    try:
        fr.read_frame(p)
        cut = False
    except fr.FrameError as exc:
        cut = "cut short" in str(exc)
    check("a cut-short file is reported, not a crash", cut)


def s3b_bench():
    import bench
    import synth_bench
    import shobs_profile as sp
    folder = os.path.join(TMP, "ladder")
    t = synth_bench.make_ladder(folder, shape=(400, 500), nonlin=0.03, t_offset=0.004)
    r = bench.run_bench(folder, "Central 50 %")
    check(f"gain {r.gain:.4f} e⁻/ADU", abs(r.gain / t["gain"] - 1) < 0.03, f"true {t['gain']}")
    check(f"read noise {r.read_noise_e:.2f} e⁻", abs(r.read_noise_e / t["read_noise"] - 1) < 0.08,
          f"true {t['read_noise']}")
    check(f"full well {r.full_well_e:,.0f} e⁻", abs(r.full_well_e / t["full_well"] - 1) < 0.04,
          f"true {t['full_well']:,.0f}")
    want = t["full_well"] * math.sqrt(bench.LIN_TOL_PCT / 100 / t["nonlin"]) / t["gain"] + t["bias"]
    check(f"linear limit {r.linear_limit_adu:,.0f} ADU (last pair within ±{bench.LIN_TOL_PCT:g} %)",
          0.6 * want < r.linear_limit_adu <= 1.01 * want, f"bend at about {want:,.0f}; the limit is at or just under it")
    check(f"timing offset {1000 * r.t_offset:+.1f} ms", abs(r.t_offset - 0.004) < 0.001, "true +4.0")
    mono = os.path.join(TMP, "ladder_mono12")
    t = synth_bench.make_ladder(mono, shape=(400, 500), bayer=None, adc_bits=12)
    r = bench.run_bench(mono, "Central 50 %")
    check("mono 12-bit camera: gain per file ADU, step 16", r.adu_step == 16 and list(r.channels) == ["L"]
          and abs(r.gain / t["gain"] - 1) < 0.04)
    lit = os.path.join(TMP, "ladder_lit")
    synth_bench.make_ladder(lit, shape=(400, 500), lit_bias=60000.0)
    check("bias frames that saw light are caught", bench.run_bench(lit, "Central 50 %").lit_bias)
    doc = bench.apply_to_profile(r, None, "selftest")
    check("bench result makes a valid camera profile", sp.validate(doc) == [], "; ".join(sp.validate(doc)[:2]))
    from matplotlib.figure import Figure
    import bench_plots
    fig = Figure(figsize=(9, 6))
    bench_plots.draw(fig, r)
    fig.savefig(os.path.join(TMP, "bench.png"))
    check("PTC and linearity plots draw", os.path.getsize(os.path.join(TMP, "bench.png")) > 10000)


def s3c_darks():
    import darks
    import dark_plots
    import spec_editor
    import spec_plots
    import synth_dark
    import shobs_profile as sp
    from matplotlib.figure import Figure
    lib = os.path.join(TMP, "darks")
    t = synth_dark.make_darks(lib, shape=(320, 400), dark_e_s=0.02, glow_e_s=0.5, cut_short=1, master=True)
    r = darks.run_darks(lib, "Central 50 %")
    m = r.main
    check(f"dark current {darks.rate_text(m.measured)} e⁻/px/s", abs(m.measured / t["dark_e_s"] - 1) < 0.05,
          f"true {t['dark_e_s']}")
    check(f"read noise from the darks' noise {r.read_noise_e:.2f} e⁻", abs(r.read_noise_e - t["read_noise"]) < 0.08,
          f"true {t['read_noise']}")
    check(f"hot pixels found ({m.hot_count}) and edge glow seen ({m.glow_where})",
          m.hot_count > 0 and m.glow_seen(max(t["exposures"])))
    why = [w for _n, w in r.left_out]
    check("a frame cut short and a master dark are left out",
          any(w.startswith("cut short") for w in why) and any("master" in w for w in why))
    cold = os.path.join(TMP, "darks_cold")
    synth_dark.make_darks(cold, shape=(320, 400), dark_e_s=0.0, exposures=(30.0, 120.0), hot_fraction=0.0)
    rc = darks.run_darks(cold, "Central 50 %")
    check(f"no dark current gives an upper limit (at most {darks.rate_text(rc.main.rate)})",
          rc.main.upper_limit and 0 < rc.main.rate < 0.002)
    doc = darks.apply_to_profile(r, None, "selftest")
    check("dark result makes a valid camera profile", sp.validate(doc) == [], "; ".join(sp.validate(doc)[:2]))
    fig = Figure(figsize=(9, 6))
    dark_plots.draw(fig, r)
    fig.savefig(os.path.join(TMP, "darks.png"))
    check("dark plots draw", os.path.getsize(os.path.join(TMP, "darks.png")) > 10000)
    specs = spec_editor.list_specs()
    builtin = [d for _lab, _p, d in specs]
    check(f"built-in spec files read and pass their check ({len(builtin)})",
          bool(builtin) and all(spec_editor.check_spec(d) == [] for d in builtin))
    fig = Figure(figsize=(9, 6))
    spec_plots.draw(fig, builtin[0], spec_plots.gather(builtin[0], None, None, r))
    fig.savefig(os.path.join(TMP, "spec.png"))
    check("measured-against-spec plots draw", os.path.getsize(os.path.join(TMP, "spec.png")) > 10000)


def s3d_onsky():
    import onsky
    import sky_plots
    import synth_sky
    import shobs_profile as sp
    from matplotlib.figure import Figure
    folder = os.path.join(TMP, "onsky_logs")
    cam = synth_sky.camera_profile()
    truth = None
    for kw in (dict(night="2026-10-18", seed=1, cat_error={4: 1.4}, variable=7),
               dict(night="2026-10-19", seed=2, airmass=(1.1, 1.9)),
               dict(night="2026-10-20", seed=3, haze_mag=0.1)):
        doc, t = synth_sky.make_log(n_frames=100, **kw)
        truth = truth or t
        if kw["night"] == "2026-10-20":                 # thin cloud passing in the middle of the night
            n = len(doc["frames"])
            for i, f in enumerate(doc["frames"]):
                bump = 0.12 * math.exp(-0.5 * ((i - n / 2) / (n / 8)) ** 2)
                for st in f["stars"].values():
                    st["flux"] = round(st["flux"] * 10 ** (-0.4 * bump), 3)
        synth_sky.write_log(doc, folder)
    ds, bad = onsky.read_logs(onsky.log_files(folder), cam_lookup=lambda _cid: cam)
    check("three synthetic on-sky logs read", len(ds) == 3 and not bad, "; ".join(w for _p, w in bad))
    s = onsky.run_all(ds, None, truth["aperture_mm"], truth["elevation_m"])
    b = s.bands.get("TG")
    check(f"zero point {b.zp1:.3f}", abs(b.zp1 - truth["zp1"]) < 0.02, f"true {truth['zp1']}")
    check(f"extinction k {b.k:.3f}", abs(b.k - truth["k"]) < 0.02, f"true {truth['k']}")
    check(f"scintillation C_Y {b.cy:.2f}", abs(b.cy - truth["cy"]) < 0.35, f"true {truth['cy']}")
    first = next(r for r in s.nights if r.ds.night == "2026-10-18")
    check("the star with the wrong catalog value is left out", "Field 5" in first.stars_out
          and "Field 5" not in first.stars_used)
    hazy = next(r for r in s.nights if r.ds.night == "2026-10-20")
    check("the hazy night is not photometric (and not in the zero point)",
          not hazy.photometric and "2026-10-20" not in b.zp_nights)
    # SHOBS-P 2.3.0 (format 2) carries its own electrons per unit, flat factor in; an older log (format 1) does not
    d2, t2 = synth_sky.make_log(n_frames=80, night="2026-10-21", seed=4, flat_factor=1.3)
    d1, _ = synth_sky.make_log(n_frames=80, night="2026-10-21", seed=4, flat_factor=1.3, format_version=1)
    r2 = onsky.analyse(onsky.load_dataset(d2, lambda _c: cam), None, 356, 300)
    r1 = onsky.analyse(onsky.load_dataset(d1, lambda _c: cam), None, 356, 300)
    check(f"SHOBS-P 2.3.0 log: zero point {r2.zp1:.3f} with the flat in its scale", abs(r2.zp1 - t2["zp1"]) < 0.02,
          f"true {t2['zp1']}")
    check(f"older SHOBS-P log: {r1.zp1:.3f}, too faint by the flat and flagged",
          abs(r1.zp1 - (t2["zp1"] - 2.5 * math.log10(1.3))) < 0.02 and not r1.ds.scale_exact)
    d3 = dict(d2, format_version=sp.ONSKY_FORMAT_VERSION + 1)
    p3 = synth_sky.write_log(d3, os.path.join(TMP, "onsky_newer"))
    _g, b3 = onsky.read_logs([p3])
    check("a log from a newer SHOBS-P is named so (not 'damaged')", b3 and b3[0][1].startswith(onsky.NEWER_WORDS))
    pid = sp.system_id("14in RC", 2900, ds[0].camera_id)
    doc = onsky.apply_to_profile(s, None, "selftest", "sky-selftest", system_id=pid, cam_id=ds[0].camera_id,
                                 telescope={"name": "14in RC", "aperture_mm": 356, "focal_mm": 2900},
                                 setup=ds[0].setup)
    check("on-sky result makes a valid system profile", sp.validate(doc) == [], "; ".join(sp.validate(doc)[:2]))
    sp.save_profile(doc, "selftest")
    back = sp.find_system_profile(ds[0].camera_id, 2905)
    check("system profile saved and found again", back is not None and back.get("profile_id") == pid)
    fig = Figure(figsize=(9, 6))
    sky_plots.draw(fig, s, None)
    fig.savefig(os.path.join(TMP, "sky.png"))
    check("on-sky plots draw", os.path.getsize(os.path.join(TMP, "sky.png")) > 10000)
    s3e_060(onsky, synth_sky, sp, cam, s, ds, doc)


def s3e_060(onsky, synth_sky, sp, cam, s, ds, doc):
    """0.6.0: star names, the folder run's sky and frames, the star-local sky, the header focal length, the export."""
    import numpy as np
    import sky_folder
    import tool_export
    check("“HD219134” and “HD 219134” are one star", onsky.same_name("HD219134", "HD 219134")
          and not onsky.same_name("HD 219134", "HD 219135"))
    sky, _w = sky_folder.frame_sky([600, 601, 599, 600.5, 900], 500.0, 1)
    check("folder sky: clipped mean of the stars' rings", sky is not None and abs(sky - 100.125) < 1e-6)
    check(f"folder sky under {sky_folder.SKY_MIN_ADU:g} ADU per pixel above the bias is “not measurable”",
          sky_folder.frame_sky([503, 504, 503.5], 500.0, 1) == (None, "not measurable"))
    runs = sky_folder.consecutive_runs(2000, sky_folder.MAX_FRAMES)
    check(f"folders up to {sky_folder.MAX_FRAMES} lights: every frame; more: runs of consecutive frames",
          sky_folder.consecutive_runs(600, 600) == [(0, 600)] and sum(b - a for a, b in runs) == 600
          and runs[-1][1] == 2000)
    d = ds[0]
    want = d.sky[:, None] * d.star_e()
    check("noise terms use the sky at each star", np.allclose(d.noise_sky_e(), want, equal_nan=True))
    tel = {"name": "14in RC", "focal_header": 2906.0, "telescop_header": "iOptron CEM120"}
    again = onsky.apply_to_profile(s, dict(doc, telescope=dict(doc["telescope"], focal_header=2898.0)), "selftest",
                                   telescope=tel)
    check("the header's focal length follows the newest night", again["telescope"]["focal_header"] == 2906.0
          and again["telescope"]["focal_mm"] == doc["telescope"]["focal_mm"])
    sp.save_profile(cam, "selftest")
    text = tool_export.text(doc)
    check("Copy for AstroImageJ / EXOTIC / HOPS: each program's fields, with sources",
          all(w in text for w in ("CCD gain [e-/count]", "CCD readout noise [e-]",
                                  "\"Image Scale (Ex: 5.21 arcsecs/pixel)\"", "Camera gain in e/ADU"))
          and "measured" in text)


def s4_history():
    import history
    key = history.record_run("check", "selftest", {"ok": True}, app="selftest")
    check("run history database written and read", bool(key) and history.recent_runs(1)[0]["run_key"] == key)


def s4b_m5():
    import history
    import report
    import rollback
    import trend_plots
    import shobs_profile as sp
    from matplotlib.figure import Figure
    sys.path.insert(0, os.path.join(HERE, "tests"))
    import m5_data
    old_home = os.environ["SHOBS_DATA_HOME"]
    os.environ["SHOBS_DATA_HOME"] = os.path.join(TMP, "data_m5")        # its own folder: no profile from 3b–3d
    try:
        _s4b(m5_data, history, report, rollback, trend_plots, sp, Figure)
    finally:
        os.environ["SHOBS_DATA_HOME"] = old_home


def _s4b(m5_data, history, report, rollback, trend_plots, sp, Figure):
    d = m5_data.make(os.path.join(TMP, "m5"))
    pts = history.series_points("system", d["system_id"], "bands.zero_point", "TG")
    check("trend series: one zero point per photometric night", [p["date"] for p in pts] ==
          ["2026-10-18", "2026-10-19"], str([p["date"] for p in pts]))
    key = d["run_keys"][-1]
    n0 = len(history.series_points("system", d["system_id"]))
    history.set_removed(key, True)
    n1 = len(history.series_points("system", d["system_id"]))
    history.set_removed(key, False)
    check("a run taken off leaves the trends and comes back", n1 < n0 == len(history.series_points(
        "system", d["system_id"])))
    fig = Figure(figsize=(8, 4))
    trend_plots.draw_trend(fig, pts, "Zero point", "mag", now={"value": 20.2, "source": "measured"},
                           event="2026-10-19")
    mark = trend_plots.stamp(fig, "selftest")
    fig.savefig(os.path.join(TMP, "trend.png"))
    check("trend plot draws, stamped with the DTG", os.path.getsize(os.path.join(TMP, "trend.png")) > 10000
          and "Z " in mark.get_text())
    revs = rollback.revisions(d["camera_path"])
    doc = rollback.roll_back(d["camera_path"], revs[0]["path"], "selftest")
    check("profile roll-back saves the old revision as a new one", doc["revision"] == 3
          and doc["values"]["gain_e_per_adu"]["source"] == "header" and sp.validate(doc) == [])
    info = report.gather(d["system_path"], prefs={"sky_telescopes": [
        {"camera_id": d["camera_id"], "focal": 2900, "lat": 35.0, "lon": -82.0, "elevation_m": 300.0}]})
    bg = info["budgets"][0] if info["budgets"] else None
    check("photon budget worked out", bg is not None and 0.05 < bg["measured_eff"] < 1.0
          and 0.05 < bg["predicted_eff"] < 1.0)
    out = os.path.join(TMP, "report.pdf")
    n = report.write_pdf(info, out, share=True, version="selftest")
    with open(out, "rb") as fh:
        head = fh.read(5)
    check(f"system report PDF written ({n} pages)", head == b"%PDF-" and n >= 6)


def s5_window():
    try:
        import tkinter as tk
        r = tk.Tk()
        r.destroy()
    except Exception as exc:
        say(f"  SKIP  no display ({exc})")
        return
    import flux_app
    import synth
    app = flux_app.App(testing=True)
    try:
        app.update()
        data, hdr = synth.make_star_frame(shape=(500, 700), stars=synth.star_list((500, 700), 12, 3.0e5))
        p = synth.write_fits(os.path.join(TMP, "window.fit"), data, hdr)
        app.check_page.check(p)
        done = app.jobs.wait(120)
        check("Check image runs in the background and shows its findings",
              done and app.check_page.res is not None and len(app.check_page.res.stars) > 5)
        for i in range(len(app.pages)):
            app.show_page(i)
            app.update()
        check("the Sky page is built", getattr(app, "sky_page", None) is not None
              and app.pages[2] is app.sky_page and app.title_var.get() != "")
        app.show_credits()
        app.update()
        check("every page and Credits open", app.title_var.get() == "Credits and sources")
        import style
        from tkinter import ttk
        tree = app.sky_page.tree
        short = [tree.heading(c, "text") for c in tree["columns"]
                 if int(tree.column(c, "width")) < style.heading_width(tree, tree.heading(c, "text"))]
        check("the Sky table's headings fit", not short, ", ".join(short))
        check("checkboxes tick with a check mark", style.CHECK_ELEMENT in str(ttk.Style(app).layout("TCheckbutton")))
    finally:
        app.destroy()


def s6_data_folder():
    say(f"  {REAL_DATA}")
    if not os.path.isdir(REAL_DATA):
        say("  (not made yet: FLUX makes it the first time it saves something)")
        return
    for sub in ("cameras", "systems", "specs", "history", "onsky"):
        d = os.path.join(REAL_DATA, sub)
        n = len([x for x in os.listdir(d) if x.endswith(".json")]) if os.path.isdir(d) else 0
        say(f"  {sub}: {n} file{'s' if n != 1 else ''}")
    d = os.path.join(REAL_DATA, "onsky")
    if os.path.isdir(d):                                 # read only: the format version of each on-sky log
        import json
        import shobs_profile as _sp
        vers: dict = {}
        for x in sorted(os.listdir(d)):
            if not x.endswith(".json"):
                continue
            try:
                with open(os.path.join(d, x), encoding="utf-8") as fh:
                    v = json.load(fh).get("format_version")
            except (OSError, ValueError):
                v = "unreadable"
            vers[v] = vers.get(v, 0) + 1
        words = {1: "format 1 (older SHOBS-P, FLUX folder runs)", 2: "format 2 (SHOBS-P 2.3.0)"}
        for v, n in sorted(vers.items(), key=lambda kv: str(kv[0])):
            w = words.get(v, "unreadable" if v == "unreadable" else f"format {v}"
                          + (" (newer than this FLUX reads: update FLUX)"
                             if isinstance(v, int) and v > _sp.ONSKY_FORMAT_VERSION else ""))
            say(f"    on-sky logs, {w}: {n}")


def main() -> int:
    import settings
    say(f"SHOBS-FLUX self-test, version {settings.APP_VERSION}, {time.strftime('%Y-%m-%d %H:%M')}")
    section("1  Environment", s1_environment)
    section("2  Shared profile code and the format-v1 examples", s2_profiles)
    section("3  Reading frames and Check image (synthetic frames)", s3_check)
    section("3b The bench run (synthetic flat-pair ladders)", s3b_bench)
    section("3c The dark run and the spec curves (synthetic darks)", s3c_darks)
    section("3d On-sky (synthetic nights)", s3d_onsky)
    section("4  Run history", s4_history)
    section("4b History, roll-back and the system report", s4b_m5)
    section("5  The window", s5_window)
    section("6  Your SHOBS data folder (read only)", s6_data_folder)
    say("")
    say("ALL PASSED" if not FAILS else f"FAILED: {len(FAILS)}: " + "; ".join(FAILS))
    text = OUT.getvalue()
    for path in (os.path.join(HERE, "selftest_log.txt"),
                 os.path.join(os.path.expanduser("~"), "Downloads", "SHOBS-FLUX_selftest_log.txt")):
        try:
            if os.path.isdir(os.path.dirname(path)):
                with open(path, "w", encoding="utf-8") as fh:
                    fh.write(text)
        except OSError:
            pass
    shutil.rmtree(TMP, ignore_errors=True)
    return 0 if not FAILS else 1


if __name__ == "__main__":
    sys.exit(main())
