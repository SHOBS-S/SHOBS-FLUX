"""SHOBS-FLUX self-test: checks this copy with the Python the shortcuts use. It changes no data: everything it writes
goes to a temporary folder (SHOBS_DATA_HOME), never to your ~/.shobs.

The log goes to selftest_log.txt next to this file and a copy to Downloads\\SHOBS-FLUX_selftest_log.txt.
Sections:
  1 environment (Python, NumPy, SciPy, Astropy, Matplotlib, Tkinter)
  2 the shared profile code (shobs_profile.py) and the three approved format-v1 examples, byte for byte
  3 reading frames and Check image on synthetic frames with known camera values
  4 the run history database
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


def s4_history():
    import history
    key = history.record_run("check", "selftest", {"ok": True}, app="selftest")
    check("run history database written and read", bool(key) and history.recent_runs(1)[0]["run_key"] == key)


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
        app.show_credits()
        app.update()
        check("every page and Credits open", app.title_var.get() == "Credits and sources")
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


def main() -> int:
    import settings
    say(f"SHOBS-FLUX self-test, version {settings.APP_VERSION}, {time.strftime('%Y-%m-%d %H:%M')}")
    section("1  Environment", s1_environment)
    section("2  Shared profile code and the format-v1 examples", s2_profiles)
    section("3  Reading frames and Check image (synthetic frames)", s3_check)
    section("4  Run history", s4_history)
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
