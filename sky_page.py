"""The Sky page: what the telescope, camera and sky do, learned from ordinary nights (milestone 4).

Source (a drop-down) picks where the nights come from:
  SHOBS-P's on-sky logs (every night)   the log SHOBS-P writes after every reduction (the data folder's onsky/)
  A folder of lights                    FLUX reduces one night's lights itself (sky_folder.py) and writes the same
                                        kind of log, then learns from it together with every other night
The logs can hold several cameras and telescopes: Setup picks one (camera setting + focal length). Telescope name,
aperture and site elevation are typed here (aperture and elevation are for scintillation; the site is never stored
in a profile). Run reads every night of that setup (onsky.py has the whole method), lists the nights with what each
says, draws them, and shows what the system profile would get; "Save to system profile…" writes it after showing what
changes. Run is only a look: nothing goes into the run history and a folder run writes no log until Save; Save puts
each night used into the run history once (a log SHOBS-P writes again counts again) and writes the folder run's log.
A folder run says whether its frames are Photometry or Imaging (astrophotography, careful focus): an imaging run's
star size is kept apart as "Seeing, imaging focus" (bands.<band>.fwhm_imaging), never the in-focus seeing."""
from __future__ import annotations

import json
import math
import os
import tkinter as tk
from collections import Counter
from tkinter import filedialog, simpledialog, ttk

import numpy as np

import frames
import history
import onsky
import settings
import shobs_profile as sp
import style
from style import px

SOURCES = (("SHOBS-P's on-sky logs (every night)", "logs"), ("A folder of lights", "folder"))
SOURCE_WORDS = {k: w for w, k in SOURCES}
COLORS = (("Green (TG)", "TG"), ("Red (TR)", "TR"), ("Blue (TB)", "TB"))
COLOR_WORDS = {k: w for w, k in COLORS}
COLOR_HINT = "for one-shot-color cameras; a mono camera uses its FILTER"
FOCAL_TOL = 0.03                     # a log belongs to a setup when its focal length is within 3 % (plate solves)
GREY = "#8a969c"
OLD_RUN = "older run of this night (not counted)"
FRAMES_ARE = (("Photometry", "photometry"), ("Imaging (astrophotography)", "imaging"))
FRAMES_WORDS = {k: w for w, k in FRAMES_ARE}
FRAMES_HINT = ("Imaging = guided long exposures focused with care: their star size is kept as “Seeing, imaging "
               "focus”, apart from the photometry seeing. Zero point, extinction and sky count as usual.")
CLEANUP_PREF = "cleanup_051_unsaved_folder_runs"     # the one-time clean-up of 0.5.1 has been done
SCRATCH_PREF = "sky_scratch_logs"                    # folder-run logs never saved: not read again


TEL_FIELDS = ("name", "aperture_mm", "elevation_m", "lat", "lon")
# the nights table: (heading, width at 100 %, what the column means: the heading's hover note)
NIGHT_HEADS = (("Night", 86, "The night's date (the evening's date); “imaging” for astrophotography frames."),
               ("Target", 110, "The field's target as the log names it."),
               ("Band", 44, "TG / TR / TB: the green, red or blue pixels of a color camera; a mono camera's filter."),
               ("Frames", 54, "Frames used from this night's log."),
               ("From", 64, "Who measured the night: SHOBS-P (its on-sky log) or FLUX (a folder of lights)."),
               ("Zero point", 76, "Magnitude that gives 1 e⁻/s at airmass 1."),
               ("k", 92, "Extinction (mag per airmass): this night's own, or where it came from."),
               ("Photometric", 82, "✔ = the zero point held steady in 10-minute bins; marginal = it drifted."),
               ("Seeing ″", 110, "Star size (FWHM) in arcseconds; defocused and imaging runs are marked."),
               ("Sky mag/″²", 80, "Sky brightness in magnitudes per square arcsecond (bigger = darker)."),
               ("C_Y", 46, "Young's scintillation coefficient (Osborn et al. 2015)."),
               ("Between frames s", 108, "Seconds lost between frames (download, save and any delay you set)."),
               ("Status", 320, "What the night was used for, or its worst finding."))


def resolve_system_id(name: str, focal, cam: str) -> str:
    """The system profile id for this telescope name, camera and focal length. ASIAIR writes the plate-solved focal
    length into FOCALLEN, so it moves a few mm from night to night (John's 14" RC: 2898-2906 mm). An existing profile
    of the SAME telescope name and camera within 3 % is that telescope: its id is kept (0.5.3; before, a run whose
    median came out 2902 tried to make a second profile beside the 2903 one). Exact file first, then the nearest
    same-name profile; a profile with no focal length only matches a run with none (same_focal)."""
    pid = sp.system_id(name, focal, cam)
    if os.path.exists(sp.profile_path("systems", pid, make=False)):
        return pid
    want = sp.slug(name)
    best = None
    try:
        paths = sp.list_profiles("systems")
    except Exception:
        paths = []
    for path in paths:
        try:
            doc = sp.load(path)
        except Exception:
            continue
        if doc.get("camera_profile") != cam or not doc.get("profile_id"):
            continue
        tel = doc.get("telescope") or {}
        if sp.slug(tel.get("name") or "") != want or not same_focal(tel.get("focal_mm"), focal):
            continue
        f, g = sp._finite(tel.get("focal_mm")), sp._finite(focal)
        d = abs(f - g) if f is not None and g is not None else 0.0
        if best is None or d < best[0]:
            best = (d, doc["profile_id"])
    return best[1] if best else pid


def same_focal(a, b) -> bool:
    """Two focal lengths are one setup within 3 % (both unknown counts as the same)."""
    a, b = sp._finite(a), sp._finite(b)
    if not a or not b:
        return not a and not b
    return abs(a - b) <= FOCAL_TOL * max(a, b)


def tel_entry(entries, cam, focal) -> dict | None:
    """The Telescope boxes saved for one setup (camera id + focal length within 3 %), or None."""
    for e in entries or []:
        if isinstance(e, dict) and e.get("camera_id") == cam and same_focal(e.get("focal"), focal):
            return e
    return None


def shobs_p_settings_path() -> str:
    """SHOBS-P's own settings file (read only: the telescope name, aperture and elevation typed there)."""
    return os.path.join(os.path.expanduser("~"), ".shobs_p_settings.json")


def shobs_p_telescope() -> dict:
    """{"name", "aperture_mm", "elevation_m", "lat", "lon"} from SHOBS-P's settings ({} when the file is missing or
    damaged)."""
    try:
        with open(shobs_p_settings_path(), encoding="utf-8") as fh:
            got = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError):
        return {}
    if not isinstance(got, dict):
        return {}
    out = {}
    name = got.get("telescope_name")
    if isinstance(name, str) and name.strip():
        out["name"] = name.strip()
    # SHOBS-P keeps the site as site_lat / site_lon / site_elevation (text); older names as a fallback
    for keys, mine in ((("aperture_mm",), "aperture_mm"), (("site_elevation", "elevation"), "elevation_m"),
                       (("site_lat", "lat"), "lat"), (("site_lon", "lon"), "lon")):
        for key in keys:
            v = sp._finite(str(got.get(key)).strip().replace(",", "") if got.get(key) is not None else None)
            if v is not None:
                out[mine] = v
                break
    return out


def _num(v) -> str:
    """A number for an entry: 356.0 → '356', '' for nothing."""
    f = sp._finite(v)
    if f is None:
        return "" if v is None else str(v)
    return f"{f:g}"


def _fmt(v, nd=3, unit=""):
    if isinstance(v, str) and v:
        return v
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return "—"
    return f"{v:.{nd}f}{(' ' + unit) if unit else ''}"


def _plural(n: int, word: str) -> str:
    return f"{n} {word}{'' if n == 1 else 's'}"


# ---- the logs, grouped by setup --------------------------------------------------------------------------------------

def log_info(path: str) -> dict:
    """What one log says about its setup, without reading its frames: camera_id, focal, night, or why it cannot be
    read ("why")."""
    try:
        doc = sp.load(path)
    except sp.NewerFormatError as exc:
        return {"path": path, "why": f"{onsky.NEWER_WORDS} ({exc}): update SHOBS-FLUX; the file is fine, leave it "
                                     "where it is", "newer": True}
    except sp.ProfileError as exc:
        return {"path": path, "why": f"it cannot be read: {exc}"}
    return doc_info(doc, path)


def folder_log_path(doc: dict) -> str:
    """Where a folder run's on-sky log goes when it is saved (the name sky_folder.save_log gives it)."""
    return os.path.join(sp.onsky_dir(make=False), f"{doc['night']}__{sp.slug(doc['target'])}__{doc['run_id']}.json")


def doc_info(doc, path: str = "") -> dict:
    """log_info for a log already read (or still in memory)."""
    if not isinstance(doc, dict) or doc.get("format") != sp.ONSKY_FORMAT:
        return {"path": path, "why": "it is not an on-sky log"}
    s = doc.get("setup") if isinstance(doc.get("setup"), dict) else {}
    cam = s.get("camera_id") or sp.camera_id(s.get("instrume") or "unknown camera", s.get("gain_setting"),
                                             s.get("offset"), s.get("camera_binning") or 1)
    focal = sp._finite(s.get("focal_header_mm")) or sp._finite(s.get("focal_mm"))
    return {"path": path, "camera_id": cam, "focal": focal, "night": str(doc.get("night") or "")}


def setup_key(info: dict) -> tuple:
    f = info.get("focal")
    return (info.get("camera_id"), int(round(f / 10.0) * 10) if f else 0)


def setups_of(infos: list) -> list:
    """[(key, label, newest night, paths)] for each distinct camera + focal length in the logs, the setup with the
    newest night first. Focal lengths within 3 % are one setup (ASIAIR's FOCALLEN moves a few mm with each plate
    solve), labelled with their median."""
    by_cam: dict = {}
    for i in infos:
        if i.get("why"):
            continue
        by_cam.setdefault(i.get("camera_id"), []).append(i)
    out = []
    for cam, items in by_cam.items():
        known = sorted((i for i in items if i.get("focal")), key=lambda i: i["focal"])
        clusters, cur = [], []
        for i in known:
            if cur and i["focal"] > cur[0]["focal"] * 1.03:
                clusters.append(cur)
                cur = []
            cur.append(i)
        if cur:
            clusters.append(cur)
        unknown = [i for i in items if not i.get("focal")]
        if unknown:
            clusters.append(unknown)
        for cl in clusters:
            fs = [i["focal"] for i in cl if i.get("focal")]
            f = int(round(float(np.median(fs)) / 10.0) * 10) if fs else 0
            key = (cam, f)
            nights = {i["night"] for i in cl}
            label = f"{cam} · {f} mm · {_plural(len(nights), 'night')}" if f else \
                f"{cam} · focal length not known · {_plural(len(nights), 'night')}"
            out.append((key, label, max(nights), [i["path"] for i in cl]))
    out.sort(key=lambda t: (t[2], t[1]), reverse=True)
    return out


def in_setup(ds: onsky.Dataset, key: tuple) -> bool:
    """A dataset belongs to a setup: the same camera id, the focal length within 3 %."""
    cam, focal = key
    if ds.camera_id != cam:
        return False
    f = sp._finite(ds.setup.get("focal_header_mm")) or sp._finite(ds.setup.get("focal_mm"))
    if not focal:
        return not f
    return f is not None and abs(f - focal) <= FOCAL_TOL * focal


class SkyRun:
    """One Run of the page: the nights' summary and everything shown with it."""

    def __init__(self):
        self.summary = None              # onsky.Summary (None: nothing to learn from)
        self.bad: list = []              # [(path, why)] logs that could not be read
        self.datasets: list = []         # the setup's datasets (kept and replaced)
        self.key = None                  # the setup
        self.source = "logs"
        self.folder = ""
        self.folder_lines: list = []     # the folder run's own findings
        self.new_log = ""                # where the folder run's log goes (written only on Save)
        self.new_doc = None              # that log, kept in memory until Save (a Run alone is a scratch look)
        self.log_written = False
        self.frames_are = "photometry"   # a folder run: "photometry" or "imaging" (astrophotography)
        self.recorded = False            # its nights are in the run history (done on Save)


def cleanup_unsaved_folder_runs(prefs: dict) -> list:
    """Once, on the first start of 0.5.1: FLUX 0.5.0 put every folder-of-lights Run into the run history at once,
    saved or not. Those never saved are marked removed (nothing is deleted; the History page can put one back), and
    their on-sky logs are no longer read as nights. The marker in the settings makes it run once; flux.log says
    what it did. Returns the runs marked."""
    if prefs.get(CLEANUP_PREF):
        return []
    try:
        got, wanted = history.remove_unsaved_folder_runs()
    except Exception as exc:                              # never stops FLUX from opening; tried again next start
        settings.log(f"0.5.1 clean-up of unsaved folder runs failed: {exc!r}")
        return []
    skip = [str(n) for n in (prefs.get(SCRATCH_PREF) or [])]
    for r in got:
        if r["log"] and r["log"] not in skip:
            skip.append(r["log"])
    prefs[SCRATCH_PREF] = skip
    if len(got) == wanted:                                # all marked: done for good; else tried again next start
        prefs[CLEANUP_PREF] = sp.now_iso()
    else:
        settings.log(f"0.5.1 clean-up: only {len(got)} of {wanted} unsaved folder runs could be marked; "
                     "trying again next start")
    settings.save(prefs)
    if got:
        settings.log("0.5.1 clean-up: " + _plural(len(got), "folder-of-lights run") + " never saved taken off the "
                     "History page (nothing deleted): " + ", ".join(f"run {r['id']} ({r['night']}, {r['log']})"
                                                                     for r in got))
    else:
        settings.log("0.5.1 clean-up: no unsaved folder-of-lights runs in the history")
    return got


# ---- the page --------------------------------------------------------------------------------------------------------

class SkyPage:
    title = "Sky"

    def __init__(self, app, parent):
        self.app = app
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        prefs = app.prefs
        cleanup_unsaved_folder_runs(prefs)
        tel = shobs_p_telescope()
        self.source_var = tk.StringVar(value=SOURCE_WORDS.get(prefs.get("sky_source"), SOURCES[0][0]))
        self.folder_var = tk.StringVar(value=prefs.get("sky_folder") or "")
        self.color_var = tk.StringVar(value=COLOR_WORDS.get(prefs.get("sky_color"), COLORS[0][0]))
        self.frames_are_var = tk.StringVar(value=FRAMES_ARE[0][0])      # per setup (kept with its Telescope boxes)
        self.name_var = tk.StringVar(value=prefs.get("sky_telescope_name") or tel.get("name") or "")
        self.aperture_var = tk.StringVar(value=_num(prefs.get("sky_aperture_mm")) or _num(tel.get("aperture_mm")))
        self.elevation_var = tk.StringVar(value=_num(prefs.get("sky_elevation_m")) or _num(tel.get("elevation_m")))
        self.lat_var = tk.StringVar(value=_num(prefs.get("sky_lat")) or _num(tel.get("lat")))
        self.lon_var = tk.StringVar(value=_num(prefs.get("sky_lon")) or _num(tel.get("lon")))
        # the Telescope boxes belong to ONE setup (camera + focal length) and are kept per setup, so a folder from
        # another telescope never runs with this one's aperture and site. Older settings had one set: it belongs to
        # the logs setup last run.
        self._tel_owner = None           # (camera id, focal mm) the boxes hold now
        self._folder_info = None         # the folder scan (camera id, focal, mono or color)
        self._mono = False
        self._scan_failed = None         # a folder whose look failed: its telescope is not known
        self._scanning = None            # the folder being looked at (its setup decides the Telescope boxes)
        self._color_keep = self.color_var.get()
        if not isinstance(prefs.get("sky_telescopes"), list):
            prefs["sky_telescopes"] = []
            last = prefs.get("sky_setup")
            if isinstance(last, (list, tuple)) and len(last) == 2 and any(
                    prefs.get(k) not in (None, "") for k in ("sky_telescope_name", "sky_aperture_mm",
                                                             "sky_elevation_m", "sky_lat", "sky_lon")):
                prefs["sky_telescopes"].append({"camera_id": last[0], "focal": last[1] or None,
                                                "name": prefs.get("sky_telescope_name") or "",
                                                "aperture_mm": prefs.get("sky_aperture_mm"),
                                                "elevation_m": prefs.get("sky_elevation_m"),
                                                "lat": prefs.get("sky_lat"), "lon": prefs.get("sky_lon")})
        self.setup_var = tk.StringVar()
        self.logs_var = tk.StringVar()
        self.scan_var = tk.StringVar()
        self.run_res: SkyRun | None = None
        self.selected = None             # the NightResult picked in the nights table
        self.rows: dict = {}             # table row → NightResult
        self.run_key = None              # the sky run's key (recorded in the history on Save)
        self.sky_recorded = False
        self._infos: dict = {}           # path → (size, mtime, log_info)
        self._setups: list = []

        # ---- the inputs: where the nights come from, and the telescope
        top = ttk.Frame(self.frame, style="Card.TFrame")
        top.pack(fill=tk.X)
        src = ttk.LabelFrame(top, text="Source", style="Group.TLabelframe", padding=px(app, 8))
        src.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        src.columnconfigure(1, weight=1)
        self.source = ttk.Combobox(src, textvariable=self.source_var, state="readonly", width=34,
                                   values=[w for w, _k in SOURCES])
        self.source.grid(row=0, column=0, sticky="w")
        self.source.bind("<<ComboboxSelected>>", lambda _e: self.source_changed())
        self.logs_row = ttk.Frame(src, style="Card.TFrame")
        self.logs_label = ttk.Label(self.logs_row, textvariable=self.logs_var, style="Hint.TLabel", justify=tk.LEFT)
        self.logs_label.pack(anchor="w", fill=tk.X)
        srow = ttk.Frame(self.logs_row, style="Card.TFrame")
        srow.pack(anchor="w", fill=tk.X, pady=(px(app, 4), 0))
        ttk.Label(srow, text="Setup", style="Card.TLabel").pack(side=tk.LEFT)
        self.setup = ttk.Combobox(srow, textvariable=self.setup_var, state="readonly", width=62)
        self.setup.bind("<<ComboboxSelected>>", lambda _e: self._follow_setup())
        self.setup.pack(side=tk.LEFT, padx=(px(app, 6), 0))
        self.folder_row = ttk.Frame(src, style="Card.TFrame")
        frow = ttk.Frame(self.folder_row, style="Card.TFrame")
        frow.pack(anchor="w", fill=tk.X)
        self.folder_btn = ttk.Button(frow, text="Folder of lights…", command=self.pick_folder)
        self.folder_btn.pack(side=tk.LEFT)
        ttk.Label(frow, textvariable=self.folder_var, style="Card.TLabel").pack(side=tk.LEFT, padx=(px(app, 8), 0))
        crow = ttk.Frame(self.folder_row, style="Card.TFrame")
        crow.pack(anchor="w", fill=tk.X, pady=(px(app, 4), 0))
        ttk.Label(crow, text="Color", style="Card.TLabel").pack(side=tk.LEFT)
        self.color = ttk.Combobox(crow, textvariable=self.color_var, state="readonly", width=12,
                                  values=[w for w, _k in COLORS])
        self.color.pack(side=tk.LEFT, padx=(px(app, 6), px(app, 8)))
        self.color_hint = ttk.Label(crow, text=COLOR_HINT, style="Hint.TLabel")
        self.color_hint.pack(side=tk.LEFT)
        arow = ttk.Frame(self.folder_row, style="Card.TFrame")
        arow.pack(anchor="w", fill=tk.X, pady=(px(app, 4), 0))
        ttk.Label(arow, text="These frames are", style="Card.TLabel").pack(side=tk.LEFT)
        self.frames_are_box = ttk.Combobox(arow, textvariable=self.frames_are_var, state="readonly", width=26,
                                           values=[w for w, _k in FRAMES_ARE])
        self.frames_are_box.pack(side=tk.LEFT, padx=(px(app, 6), px(app, 8)))
        self.frames_are_box.bind("<<ComboboxSelected>>", lambda _e: self._tel_store())
        self.frames_hint = ttk.Label(self.folder_row, text=FRAMES_HINT, style="Hint.TLabel", justify=tk.LEFT)
        self.frames_hint.pack(anchor="w", fill=tk.X)
        self.scan_label = ttk.Label(self.folder_row, textvariable=self.scan_var, style="Hint.TLabel", justify=tk.LEFT)
        self.scan_label.pack(anchor="w", fill=tk.X, pady=(px(app, 4), 0))
        src.bind("<Configure>", lambda e: (self.logs_label.configure(wraplength=max(200, e.width - px(app, 20))),
                                           self.scan_label.configure(wraplength=max(200, e.width - px(app, 20))),
                                           self.frames_hint.configure(wraplength=max(200, e.width - px(app, 20)))),
                 add="+")

        self._entries = []
        tg = ttk.LabelFrame(top, text="Telescope", style="Group.TLabelframe", padding=px(app, 8))
        tg.pack(side=tk.LEFT, fill=tk.Y, padx=(px(app, 10), 0))
        for i, (label, var, w) in enumerate((("Telescope name", self.name_var, 18),
                                             ("Aperture (mm)", self.aperture_var, 8),
                                             ("Site elevation (m)", self.elevation_var, 8),
                                             ("Site latitude (°)", self.lat_var, 8),
                                             ("Site longitude (°, east +)", self.lon_var, 8))):
            ttk.Label(tg, text=label, style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=(0, px(app, 2)))
            e = ttk.Entry(tg, textvariable=var, width=w)
            e.grid(row=i, column=1, sticky="w", padx=(px(app, 6), 0), pady=(0, px(app, 2)))
            self._entries.append(e)
        ttk.Label(tg, text="aperture and elevation are for scintillation; latitude and longitude give the\n"
                           "airmass when frames don't carry it. The site is never stored in a profile.",
                  style="Hint.TLabel", justify=tk.LEFT).grid(row=5, column=0, columnspan=2, sticky="w",
                                                            pady=(px(app, 2), 0))

        run = ttk.Frame(self.frame, style="Card.TFrame")
        run.pack(fill=tk.X, pady=(px(app, 8), 0))
        self.run_btn = style.green_button(run, "Run", self.run)
        self.run_btn.pack(side=tk.LEFT)
        self.save_btn = style.green_button(run, "Save to system profile…", self.save)
        self.save_btn.configure(state="disabled")
        self.save_btn.pack(side=tk.RIGHT)
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(self.frame, textvariable=self.msg_var, style="Card.TLabel", justify=tk.LEFT)
        self.msg.pack(anchor="w", fill=tk.X, pady=(px(app, 4), 0))
        self.frame.bind("<Configure>", lambda e: self.msg.configure(wraplength=max(200, e.width - px(app, 10))),
                        add="+")

        # ---- the results: nights and pictures on the left, findings and values on the right
        pane = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, pady=(px(app, 6), 0))
        self.pane = pane
        left = ttk.PanedWindow(pane, orient=tk.VERTICAL)
        right = ttk.Frame(pane, style="Card.TFrame")
        pane.add(left, weight=3)
        pane.add(right, weight=2)
        self.left = left
        tw = ttk.Frame(left, style="Card.TFrame")
        left.add(tw, weight=1)
        cols = ("night", "target", "band", "frames", "from", "zp", "k", "phot", "seeing", "sky", "cy", "oh", "status")
        self.tree = ttk.Treeview(tw, columns=cols, show="headings", selectmode="browse", height=6)
        for c, (head, w, _tip) in zip(cols, NIGHT_HEADS):
            self.tree.heading(c, text=head)
            self.tree.column(c, width=px(app, w), minwidth=px(app, 36), stretch=(c == "status"),
                             anchor="w" if c in ("night", "target", "band", "from", "seeing", "status", "phot")
                             else "e")
        # 0.6.0: every heading shows whole at any Windows scaling (100 %, 125 %, 150 %), with a hover note
        style.fit_headings(self.tree, {"night": "2026-10-20", "from": "SHOBS-P", "zp": "20.200"})
        self.head_tips = style.HeadingTips(self.tree, {c: tip for c, (_h, _w, tip) in zip(cols, NIGHT_HEADS)})
        self.tree.tag_configure("old", foreground=GREY)
        self.tree.tag_configure("bad", foreground=style.BAD)
        vb = ttk.Scrollbar(tw, orient=tk.VERTICAL, command=self.tree.yview)
        hb = ttk.Scrollbar(tw, orient=tk.HORIZONTAL, command=self.tree.xview)
        self.tree.configure(yscrollcommand=vb.set, xscrollcommand=hb.set)
        hb.pack(side=tk.BOTTOM, fill=tk.X)
        vb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self.night_selected())

        self.pic = ttk.Frame(left, style="Card.TFrame")
        left.add(self.pic, weight=3)
        self.hint = ttk.Label(
            self.pic, style="Hint.TLabel", justify=tk.LEFT,
            text="SHOBS-P 2.2.19.1 and later write an on-sky log after every reduction; Run learns from all of them: "
                 "the zero point at airmass 1 and the extinction (from photometric nights only, each night once), "
                 "the seeing in focus and defocused, the sky brightness, scintillation (C_Y) and the time lost "
                 "between frames. Nights with clouds or haze are judged, listed and set aside, not averaged in.\n\n"
                 "No logs yet? Choose Source → A folder of lights and point FLUX at one night's lights of a field "
                 "with catalogued stars.\n\nPick a night in the list to see what it says and its pictures.")
        self.hint.pack(anchor="nw", pady=px(app, 8), fill=tk.X)
        self.pic.bind("<Configure>", lambda e: self.hint.configure(wraplength=max(200, e.width - px(app, 10))),
                      add="+")
        self.canvas = None
        self.fig = None

        nb = ttk.Notebook(right)
        nb.pack(fill=tk.BOTH, expand=True)
        self.nb = nb
        tf = ttk.Frame(nb, style="Card.TFrame")
        nb.add(tf, text="Findings")
        self.text = tk.Text(tf, wrap="word", font=style.font(10), background="#ffffff", foreground=style.TEXT,
                            relief="flat", padx=px(app, 6), pady=px(app, 4), height=12, cursor="arrow")
        tvb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=tvb.set)
        tvb.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.text.tag_configure("bad", foreground=style.BAD)
        self.text.tag_configure("warn", foreground=style.WARN_FG, background=style.WARN_BG)
        self.text.tag_configure("ok", foreground=style.OK)
        self.text.tag_configure("info", foreground=style.TEXT)
        self.text.tag_configure("head", foreground=style.SIDE, font=style.font(10, "bold"))
        self.text.tag_configure("gap", spacing1=px(app, 4))
        self.text.configure(state="disabled")

        vf = ttk.Frame(nb, style="Card.TFrame")
        nb.add(vf, text="Values")
        self.val_tree = ttk.Treeview(vf, columns=("old", "new"), show="tree headings", selectmode="none")
        self.val_tree.heading("#0", text="")
        self.val_tree.heading("old", text="Now")
        self.val_tree.heading("new", text="Measured")
        self.val_tree.column("#0", width=px(app, 190), minwidth=px(app, 120), stretch=False)
        self.val_tree.column("old", width=px(app, 140), minwidth=px(app, 80), anchor="e", stretch=True)
        self.val_tree.column("new", width=px(app, 110), minwidth=px(app, 70), anchor="e", stretch=True)
        self.val_tree.pack(fill=tk.BOTH, expand=True)
        self.foot = foot = ttk.Label(vf, style="Hint.TLabel", justify=tk.LEFT, text="")
        foot.pack(anchor="w", fill=tk.X, pady=(px(app, 4), 0))
        vf.bind("<Configure>", lambda e: foot.configure(wraplength=max(150, e.width - px(app, 8))), add="+")
        self.source_changed(save=False)
        if self.mode == "folder" and self.folder_var.get() and os.path.isdir(self.folder_var.get()):
            self.frame.after(300, lambda: self._scan_folder(self.folder_var.get()))

    # ---- the Telescope boxes, one set per setup
    def _vars(self):
        return dict(zip(TEL_FIELDS, (self.name_var, self.aperture_var, self.elevation_var, self.lat_var,
                                     self.lon_var)))

    def _tel_store(self):
        """Keep what the Telescope boxes say under the setup they belong to."""
        own = self._tel_owner
        if not own:
            return
        tels = self.app.prefs.setdefault("sky_telescopes", [])
        e = tel_entry(tels, own[0], own[1])
        if e is None:
            e = {"camera_id": own[0], "focal": own[1]}
            tels.append(e)
        for k, var in self._vars().items():
            t = var.get().strip()
            e[k] = t if k == "name" else sp._finite(t.replace(",", "")) if t else None
        e["frames_are"] = self.frames_are
        settings.save(self.app.prefs)

    def _tel_switch(self, cam, focal, from_logs: bool) -> tuple | None:
        """Show the Telescope boxes of this setup (camera id + focal length). Returns the setup they held before
        when that was ANOTHER setup (None when nothing changed or they belonged to no setup)."""
        if not cam:
            return None
        focal = sp._finite(focal) or None
        own = self._tel_owner
        if own and own[0] == cam and same_focal(own[1], focal):
            return None
        if own is None:
            # boxes nobody owns yet (first look): they are this setup's when its saved set matches or none is saved
            # (or, on the very first look when no setup has boxes saved yet, whatever setup comes first: older
            # settings had one set and nothing else to say whose it was)
            e = tel_entry(self.app.prefs.get("sky_telescopes"), cam, focal)
            if e is None and (from_logs or not self.app.prefs.get("sky_telescopes")):
                self._tel_owner = (cam, focal)
                self._tel_store()
                return None
        self._tel_store()
        e = tel_entry(self.app.prefs.get("sky_telescopes"), cam, focal)
        vals = dict(e) if e else {}
        if e is None:
            try:
                prof = sp.find_system_profile(cam, focal)
            except Exception:
                prof = None
            t = (prof or {}).get("telescope") or {}
            vals = {"name": t.get("name") or "", "aperture_mm": t.get("aperture_mm")}
            if from_logs:                              # SHOBS-P's own settings describe the telescope its logs used
                p = shobs_p_telescope()
                for k in TEL_FIELDS:
                    if vals.get(k) in (None, "") and p.get(k) not in (None, ""):
                        vals[k] = p[k]
        for k, var in self._vars().items():
            v = vals.get(k)
            var.set((v or "") if k == "name" else _num(v))
        self.frames_are_var.set(FRAMES_WORDS.get(vals.get("frames_are"), FRAMES_ARE[0][0]))
        self._tel_owner = (cam, focal)
        return own

    def _follow_setup(self):
        key = self._setup_key()
        if key is not None and self.mode == "logs":
            self._tel_switch(key[0], key[1] or None, from_logs=True)

    def _set_mono(self, mono: bool, band=None):
        """A mono folder measures the band its FILTER says: the Color choice is shown greyed with that band."""
        if mono:
            if self.color_var.get() in dict(COLORS):
                self._color_keep = self.color_var.get()
            self.color_var.set(f"From FILTER: {onsky.BAND_WORDS.get(band, band) if band else 'none'}")
            self.color.configure(state="disabled")
            self.color_hint.configure(text="mono camera: each frame's FILTER gives the band")
        else:
            if self.color_var.get() not in dict(COLORS):
                self.color_var.set(self._color_keep if self._color_keep in dict(COLORS) else COLORS[0][0])
            self.color.configure(state="readonly")
            self.color_hint.configure(text=COLOR_HINT)
        self._mono = mono

    # ---- inputs
    @property
    def mode(self) -> str:
        return dict(SOURCES).get(self.source_var.get(), "logs")

    @property
    def channel(self) -> str:
        return dict(COLORS).get(self.color_var.get(), "TG")

    @property
    def frames_are(self) -> str:
        """What a folder of lights holds: "photometry" (the default) or "imaging" (astrophotography)."""
        return dict(FRAMES_ARE).get(self.frames_are_var.get(), "photometry")

    @property
    def summary(self):
        return self.run_res.summary if self.run_res is not None else None

    def refresh(self):
        self.scan_logs()

    def source_changed(self, save: bool = True):
        if self.mode == "folder":
            self.logs_row.grid_forget()
            self.folder_row.grid(row=1, column=0, columnspan=2, sticky="we", pady=(px(self.app, 6), 0))
        else:
            self.folder_row.grid_forget()
            self.logs_row.grid(row=1, column=0, columnspan=2, sticky="we", pady=(px(self.app, 6), 0))
        if save:
            self.app.prefs["sky_source"] = self.mode
            settings.save(self.app.prefs)
            self._say("")
            if self.mode == "logs":
                self._follow_setup()
            elif self._folder_info:
                self._folder_setup(self._folder_info)

    def scan_logs(self) -> list:
        """Read what each log says about its setup (cached by size and time), fill the Setup drop-down."""
        files = self.log_files()
        infos = []
        for p in files:
            try:
                st = os.stat(p)
                stamp = (st.st_size, st.st_mtime)
            except OSError:
                stamp = None
            got = self._infos.get(p)
            if got is None or got[0] != stamp:
                got = (stamp, log_info(p))
                self._infos[p] = got
            infos.append(got[1])
        self._infos = {p: v for p, v in self._infos.items() if p in set(files)}
        d = sp.onsky_dir(make=False)
        newer = sum(1 for i in infos if i.get("newer"))
        bad = sum(1 for i in infos if i.get("why")) - newer
        why = ", ".join(([f"{newer} written by a newer SHOBS-P: update SHOBS-FLUX to read them"] if newer else [])
                        + ([f"{bad} cannot be read"] if bad else []))
        if files:
            self.logs_var.set(f"{_plural(len(files), 'on-sky log file')} in {d}"
                              + (f" ({why})" if why else "")
                              + ". SHOBS-P writes one after every reduction; Run learns from every night of the setup.")
        else:
            self.logs_var.set(f"No on-sky log files yet in {d}. SHOBS-P 2.2.19.1 and later write one each time a "
                              "reduction finishes; or choose A folder of lights.")
        self._setups = setups_of(infos)
        labels = [lab for _k, lab, _n, _p in self._setups]
        self.setup.configure(values=labels)
        if self.setup_var.get() not in labels:
            want = self.app.prefs.get("sky_setup")
            pick = next((lab for k, lab, _n, _p in self._setups if list(k) == want), None)
            self.setup_var.set(pick or (labels[0] if labels else ""))
        if self.mode == "logs":
            self._follow_setup()
        return infos

    def _setup_key(self):
        for k, lab, _n, _p in self._setups:
            if lab == self.setup_var.get():
                return k
        return self._setups[0][0] if self._setups else None

    def pick_folder(self):
        start = self.folder_var.get() or self.app.prefs.get("last_folder")
        d = filedialog.askdirectory(parent=self.app, title="One night's lights (a field with catalogued stars)",
                                    initialdir=start if start and os.path.isdir(start) else None)
        if d:
            self.set_folder(os.path.normpath(d))

    def set_folder(self, folder: str):
        self.folder_var.set(folder)
        self.app.prefs["sky_folder"] = folder
        settings.save(self.app.prefs)
        self._scan_folder(folder)

    def _folder_setup(self, info) -> None:
        """The folder's setup takes the Telescope boxes (and the Color choice); a different telescope is said."""
        get = info.get if isinstance(info, dict) else (lambda k, d=None: getattr(info, k, d))
        if get("osc") is not None:
            self._set_mono(get("osc") is False, get("band"))
        was = self._tel_switch(get("camera_id"), get("focal"), from_logs=False)
        if was is not None:
            f_new, f_old = sp._finite(get("focal")), sp._finite(was[1])
            what = (f"shot at {f_new:,.0f} mm" if f_new else "from a setup with no focal length in its headers")
            old = f"{f_old:,.0f} mm" if f_old else "the other"
            name = self.name_var.get().strip()
            self._say(f"⚠ This folder was {what}, not with the {old} setup the Telescope boxes held: they now show "
                      + (f"this setup's own values ({name})." if name else
                         "this setup's own values, empty until you type them (its aperture and site elevation, "
                         "for scintillation)."))

    def _scan_folder(self, folder: str):
        self.scan_var.set("")
        self._folder_info = None
        self._scanning = folder
        self._scan_failed = None

        def work(step):
            step(f"Looking at {os.path.basename(folder)}")
            import sky_folder
            return sky_folder.scan(folder)

        def done(info, exc):
            if self._scanning == folder:
                self._scanning = None
            if exc is not None:
                self.scan_var.set(f"⚠ The folder could not be looked at: {self._why(exc)}.")
                if folder == self.folder_var.get():
                    self._scan_failed = folder
                return
            self.scan_var.set(scan_line(info))
            if folder != self.folder_var.get():
                return                                   # another folder was picked meanwhile
            self._folder_info = info
            if self.mode == "folder":
                self._folder_setup(info)

        self.app.jobs.run(work, done, "Looking at the folder of lights")

    def _say(self, text: str):
        self.msg_var.set(text or "")
        try:
            self.msg.configure(style=style.status_style(style.kind_of(text)) if text else "Card.TLabel")
        except tk.TclError:
            pass

    def _numbers(self):
        """(aperture mm, elevation m) from the entries (None when blank); ValueError names a bad one."""
        out = []
        for var, what in ((self.aperture_var, "aperture"), (self.elevation_var, "site elevation")):
            t = var.get().strip().replace(",", "")
            if not t:
                out.append(None)
                continue
            try:
                v = float(t)
                if not math.isfinite(v):
                    raise ValueError
            except ValueError:
                raise ValueError(f"the {what} “{var.get().strip()}” is not a number") from None
            out.append(v)
        if out[0] is not None and out[0] <= 0:
            raise ValueError("the aperture must be more than 0 mm")
        return out[0], out[1]

    def _site(self) -> dict | None:
        """{"lat", "lon", "elevation"} from the entries, or None when latitude or longitude is blank or not a
        number (a folder run then uses the frames' own SITELAT / SITELONG)."""
        out = {}
        for key, var, lo, hi in (("lat", self.lat_var, -90, 90), ("lon", self.lon_var, -180, 360)):
            try:
                v = float(var.get().strip().replace(",", ""))
            except ValueError:
                return None
            if not (math.isfinite(v) and lo <= v <= hi):
                return None
            out[key] = v
        out["elevation"] = sp._finite(self.elevation_var.get().strip().replace(",", ""))
        return out

    def _save_prefs(self, aperture, elevation):
        p = self.app.prefs
        site = self._site()
        p["sky_lat"] = site["lat"] if site else None
        p["sky_lon"] = site["lon"] if site else None
        p["sky_telescope_name"] = self.name_var.get().strip()
        p["sky_aperture_mm"] = aperture
        p["sky_elevation_m"] = elevation
        p["sky_source"] = self.mode
        if not self._mono:
            p["sky_color"] = self.channel
        settings.save(p)
        self._tel_store()

    # ---- the run
    def run(self):
        # the logs are looked at FIRST: that can change the setup, and with it the Telescope boxes read below
        infos = self.scan_logs() if self.mode == "logs" else []
        try:
            aperture, elevation = self._numbers()
        except ValueError as exc:
            self._say(f"⚠ Not run: {exc}.")
            return
        self._save_prefs(aperture, elevation)
        name = self.name_var.get().strip()
        if self.mode == "folder":
            folder = self.folder_var.get()
            if not folder or not os.path.isdir(folder):
                self._say("⚠ Pick the folder of lights first (Folder of lights…).")
                return
            if self._scanning == folder:
                self._say("⚠ Still looking at the folder (which telescope and camera it is from): Run again in a "
                          "moment.")
                return
            if self._scan_failed == folder:
                self._say("⚠ Not run: FLUX could not look at this folder, so it cannot tell which telescope the "
                          "Telescope boxes should be for. Pick the folder again (Folder of lights…).")
                return
            channel = self.channel
            site = self._site()
            frames_are = self.frames_are
            self._busy()

            def work(step):
                return self._folder_work(folder, channel, aperture, elevation, name, step, site, frames_are)

            self.app.jobs.run(work, self.done, f"On-sky run on {os.path.basename(folder)}")
            return
        key = self._setup_key()
        if key is None:
            newer = sum(1 for i in infos if i.get("newer"))
            if newer:
                self._say(f"⚠ {_plural(newer, 'on-sky log')} in {sp.onsky_dir(make=False)} "
                          f"{'was' if newer == 1 else 'were'} written by a newer SHOBS-P than this SHOBS-FLUX can "
                          "read. The files are fine: leave them where they are and update SHOBS-FLUX.")
                return
            self._say(f"⚠ No on-sky logs to learn from yet in {sp.onsky_dir(make=False)}. SHOBS-P 2.2.19.1 and "
                      "later write one after every reduction; or choose Source → A folder of lights.")
            return
        self.app.prefs["sky_setup"] = list(key)
        settings.save(self.app.prefs)
        paths = next(p for k, _l, _n, p in self._setups if k == key)
        bad = [(i["path"], i["why"]) for i in infos if i.get("why")]
        self._busy()

        def work(step):
            res = SkyRun()
            res.source, res.key, res.bad = "logs", key, list(bad)
            self._learn(res, paths, aperture, elevation, name, step)
            return res

        self.app.jobs.run(work, self.done, "On-sky run")

    def _folder_work(self, folder, channel, aperture, elevation, name, step, site=None,
                     frames_are: str = "photometry") -> SkyRun:
        """A folder run is a scratch look: its on-sky log is kept in memory and written (and its night recorded in
        the run history) only when the run is saved to the system profile. "History = everything used, not
        everything I looked at" (John, 1 Nov 2026)."""
        import sky_folder
        res = SkyRun()
        res.source, res.folder, res.frames_are = "folder", folder, frames_are
        fr = sky_folder.run_folder(folder, channel=channel, step=step, elevation_m=elevation, site=site)
        res.folder_lines = list(getattr(fr, "lines", None) or [])
        doc = getattr(fr, "doc", None)
        if not doc:
            return res
        doc["frames_are"] = frames_are
        res.new_doc = doc
        res.new_log = folder_log_path(doc)
        info = doc_info(doc, res.new_log)
        if info.get("why"):
            raise onsky.OnSkyError(f"the log FLUX made cannot be read back: {info['why']}")
        key = setup_key(info)
        res.key = key
        paths, bad = [], []
        for p in self.log_files():
            if os.path.normcase(os.path.abspath(p)) == os.path.normcase(os.path.abspath(res.new_log)):
                continue
            i = log_info(p)
            if i.get("why"):
                bad.append((p, i["why"]))
            elif setup_key(i)[0] == key[0]:
                paths.append(p)                    # the focal length is checked on the datasets (3 %)
        res.bad = bad
        self._learn(res, paths, aperture, elevation, name, step, extra=[(doc, res.new_log)])
        return res

    def log_files(self) -> list:
        """The on-sky logs to learn from: every log in the onsky folder but the folder runs 0.5.0 wrote and never
        saved (the 0.5.1 clean-up lists them; nothing is deleted)."""
        skip = {str(n).lower() for n in (self.app.prefs.get(SCRATCH_PREF) or [])}
        return [p for p in onsky.log_files() if os.path.basename(p).lower() not in skip]

    def _learn(self, res: SkyRun, paths, aperture, elevation, name, step, extra=()):
        """Read the setup's logs (and a folder run's log still in memory) and learn from them (worker thread)."""
        datasets = []
        for i, p in enumerate(paths):
            step(f"Reading on-sky log {i + 1} of {len(paths)}")
            got, bad = onsky.read_logs([p])
            datasets += got
            res.bad += bad
        for doc, path in extra:
            datasets.append(onsky.load_dataset(doc, path=path))
        datasets = [d for d in datasets if in_setup(d, res.key)] if res.key else datasets
        res.datasets = datasets
        if not datasets:
            raise onsky.OnSkyError("none of the on-sky logs of this setup can be learned from")
        step("Finding the system profile")
        prof = self.current_profile(datasets, name)
        res.summary = onsky.run_all(datasets, prof, aperture, elevation, step)

    def _busy(self):
        self._say("")
        self.run_btn.configure(state="disabled")
        self.save_btn.configure(state="disabled")
        self.source.configure(state="disabled")
        self.setup.configure(state="disabled")
        # what the run on its way reads (and Save later uses) stays as it is until the run is back
        for w in [self.folder_btn, self.color, self.frames_are_box] + self._entries:
            try:
                w.configure(state="disabled")
            except tk.TclError:
                pass

    def _idle(self):
        self.run_btn.configure(state="normal")
        self.source.configure(state="readonly")
        self.setup.configure(state="readonly")
        for w in [self.folder_btn] + self._entries:
            try:
                w.configure(state="normal")
            except tk.TclError:
                pass
        try:
            self.color.configure(state="disabled" if self._mono else "readonly")
            self.frames_are_box.configure(state="readonly")
        except tk.TclError:
            pass

    @staticmethod
    def _why(exc) -> str:
        known = [onsky.OnSkyError, frames.FrameError, sp.ProfileError]
        import sys
        sf = sys.modules.get("sky_folder")
        if sf is not None and isinstance(getattr(sf, "SkyFolderError", None), type):
            known.append(sf.SkyFolderError)
        if isinstance(exc, ModuleNotFoundError) and getattr(exc, "name", "") == "sky_folder":
            return "the folder-of-lights run is not in this copy of SHOBS-FLUX"
        return str(exc).rstrip(".") if isinstance(exc, tuple(known)) else f"{type(exc).__name__}: {exc}"

    def _failed(self, exc):
        self._idle()
        self.save_btn.configure(state="normal" if self.summary is not None else "disabled")
        why = self._why(exc)
        self._say(f"✖ The on-sky run could not be done: {why}.")
        self.app.set_status(f"✖ On-sky run failed ({why}). Details are in {settings.log_path()}.")

    def done(self, res: SkyRun, exc):
        if exc is not None:
            return self._failed(exc)
        self._idle()
        if res.source == "folder" and res.key and self._tel_owner is None:
            self._tel_owner = (res.key[0], res.key[1] or None)      # the boxes ran with this setup: keep them there
            self._tel_store()
        self.scan_logs()
        if res.summary is None:                         # a folder run that gave no night to learn from
            self.run_res = res
            self.selected = None
            self.save_btn.configure(state="disabled")
            self.show_all()
            worst = next((t for k, t in res.folder_lines if k in ("bad", "warn")), "")
            self._say("⚠ The folder of lights gave no night to learn from" + (f": {worst.lstrip('✖⚠ ')}" if worst
                                                                              else "."))
            self.app.set_status("⚠ On-sky run: the folder of lights gave no night to learn from.")
            return
        self.run_res = res
        self.selected = None
        self.run_key = history.new_run_key("sky")
        self.sky_recorded = False
        s = res.summary
        # nothing goes into the run history here: a Run is a look; Save records what was used
        settings.log(f"on-sky run ({res.source}; {_plural(len(onsky_nights(s.nights)), 'night')} from "
                     f"{_plural(len(s.nights), 'run')}; recorded in the history only on Save; setup {res.key})")
        self.save_btn.configure(state="normal" if onsky.changes_for(s, None) else "disabled")
        try:
            self.show_all()
            new = self.new_night(res)
            if new is not None:                           # the plots and findings follow the run just made
                self.select_night(new)
        except Exception as exc:                          # the run is learned and saveable even if a view fails
            import traceback
            settings.log(f"showing the on-sky run failed: {exc!r}\n{traceback.format_exc()}")
            self._say(f"⚠ The run finished, but part of the page could not be drawn ({exc}). Details are in "
                      f"{settings.log_path()}.")
        self.app.set_status(status_text(s))

    @staticmethod
    def new_night(res: SkyRun):
        """The NightResult of the folder run just made (None for a logs run, or when it gave no night)."""
        if res is None or res.summary is None or res.source != "folder" or not res.new_log:
            return None
        want = os.path.normcase(os.path.abspath(res.new_log))
        for r in res.summary.nights:
            if r.ds.path and os.path.normcase(os.path.abspath(r.ds.path)) == want:
                return r
        return None

    def _write_folder_log(self, res: SkyRun, profile_saved: bool = False) -> bool:
        """Write a saved folder run's on-sky log (it was kept in memory since Run). False when it could not be."""
        if res.source != "folder" or res.new_doc is None or res.log_written:
            return True
        try:
            import sky_folder
            path = sky_folder.save_log(res.new_doc)
        except Exception as exc:
            if profile_saved:
                self._say(f"⚠ Saved to the system profile, but the night's on-sky log could not be written ({exc}), "
                          "so the night is not in the run history. Save again to add it.")
            else:
                self._say(f"✖ The night's on-sky log could not be written ({exc}); nothing was saved.")
            settings.log(f"folder log write failed: {exc!r}")
            return False
        old = os.path.normcase(os.path.abspath(res.new_log))
        for d in res.datasets:
            if d.path and os.path.normcase(os.path.abspath(d.path)) == old:
                d.path = path
        res.new_log, res.log_written = path, True
        return True

    def _record_nights(self, res: SkyRun, pid: str | None = None) -> int:
        """Each night used goes into the run history once (keyed by its log's path, size and time), on Save."""
        pid = pid or self.system_id(res, ask=False)
        if pid is None and res.datasets:            # no telescope name yet: camera + focal still join it to its setup
            pid = sp.system_id("", focal_of(res.datasets), camera_of(res))
        n = 0
        for r in res.summary.nights:
            path = r.ds.path
            try:
                st = os.stat(path)
            except OSError:
                continue
            if history.onsky_file_known(path, st.st_size, st.st_mtime):
                continue
            summ = onsky.night_summary(r)
            status = night_status(r)
            key = history.record_run("onsky", path, summ, camera_id=r.ds.camera_id, system_id=pid,
                                     n_frames=r.n_frames, status=status, app=settings.WRITTEN_BY,
                                     values=onsky.night_values(r, pid))
            if key:
                history.mark_onsky_file(path, st.st_size, st.st_mtime, key)
                n += 1
        return n

    # ---- the system profile
    def system_id(self, res: SkyRun | None = None, ask: bool = True) -> str | None:
        """<telescope>-<focal>mm__<camera id> for the run's nights (asks the telescope name once when empty)."""
        res = res or self.run_res
        if res is None or not res.datasets:
            return None
        name = self.name_var.get().strip()
        if not name and ask:
            got = simpledialog.askstring(settings.APP_NAME, "What is the telescope called? (the system profile is "
                                         "named after it and the focal length; asked once)", parent=self.app)
            if not got or not got.strip():
                return None
            name = got.strip()
            self.name_var.set(name)
            self.app.prefs["sky_telescope_name"] = name
            settings.save(self.app.prefs)
            self._tel_store()
        if not name:
            return None
        return resolve_system_id(name, focal_of(res.datasets), camera_of(res))

    def current_profile(self, datasets=None, name=None):
        """The system profile these nights would go into: the one named after the telescope when it exists, else the
        one for this camera at this focal length, else None."""
        datasets = datasets if datasets is not None else (self.run_res.datasets if self.run_res else [])
        if not datasets:
            return None
        name = (name if name is not None else self.name_var.get()).strip()
        cam = Counter(d.camera_id for d in datasets).most_common(1)[0][0]
        focal = focal_of(datasets)
        if name:
            path = sp.profile_path("systems", resolve_system_id(name, focal, cam), make=False)
            if os.path.exists(path):
                try:
                    return sp.load(path)
                except sp.ProfileError:
                    return None
        try:
            return sp.find_system_profile(cam, focal)
        except Exception:
            return None

    def save(self):
        res = self.run_res
        if res is None or res.summary is None:
            return
        pid = self.system_id(res, ask=True)
        if not pid:
            self._say("⚠ Not saved: the system profile needs the telescope's name.")
            return
        path = sp.profile_path("systems", pid, make=False)
        old = None
        if os.path.exists(path):
            try:
                old = sp.load(path)
                if old.get("format") != sp.SYSTEM_FORMAT:
                    raise sp.ProfileError("it is not a system profile")
            except sp.ProfileError as exc:
                self._say(f"✖ The system profile {pid} exists but cannot be read ({exc}), so it was not replaced. "
                          f"Fix or move {path} first (Profiles page shows why).")
                return
        changes = onsky.changes_for(res.summary, old, header_words(res))
        if not changes:
            self._say("⚠ These nights measured nothing that goes in the system profile.")
            return
        other = None
        if old is None:                                   # another name already holds this camera at this focal?
            try:
                other = sp.find_system_profile(camera_of(res), focal_of(res.datasets))
            except Exception:
                other = None
        dlg = SaveDialog(self, pid, old, changes, other=other)
        dlg.what = (res, self.run_key)                   # the run on screen now, whatever is showing at OK

    def do_save(self, pid: str, old: dict | None, keep_user: bool, what=None):
        res, run_key = what or (self.run_res, self.run_key)
        s = res.summary
        ds = max(res.datasets, key=lambda d: (d.night, d.written))
        focal = focal_of(res.datasets)
        aperture = sp._finite(self.aperture_var.get().strip().replace(",", "") or None)
        telescope = {"name": self.name_var.get().strip(), "aperture_mm": aperture,
                     "focal_mm": round(focal) if focal else None, **header_words(res)}
        cam = camera_of(res)
        doc = onsky.apply_to_profile(s, old, settings.WRITTEN_BY, run_key, keep_user, system_id=pid, cam_id=cam,
                                     telescope=telescope, setup=ds.setup)
        doc["profile_id"] = pid
        try:                                             # the profile first: a failed save leaves no log behind
            path = sp.save_profile(doc, settings.WRITTEN_BY)
        except (sp.ProfileError, OSError) as exc:
            self._say(f"✖ The system profile could not be saved ({exc}).")
            return
        if not self._write_folder_log(res, profile_saved=True):
            return                                       # the profile is saved; the night is not in the history
        if not res.recorded:                             # the nights used go into the run history now, once
            n_new = self._record_nights(res, pid)
            res.recorded = True
            settings.log(f"on-sky run {run_key}: {n_new} night run{'s' if n_new != 1 else ''} new in the history")
        if not (self.sky_recorded and run_key == self.run_key):
            history.record_run("sky", saved_source(res), sky_summary(s), camera_id=cam,
                               system_id=pid, n_frames=sum(r.n_frames for r in s.nights), status=sky_status(s),
                               app=settings.WRITTEN_BY, values=sky_values(s, pid), run_key=run_key)
            self.sky_recorded = run_key == self.run_key
        settings.log(f"on-sky run {run_key} saved to {path}")
        if res.log_written:
            self.scan_logs()                             # the folder run's log is one of the setup's logs now
        self._say(f"✔ Saved to system profile {pid}" + (" (the previous revision is in the history folder)."
                                                         if old else "."))
        self.app.set_status(f"✔ System profile {pid} saved.")
        self.show_values()

    # ---- showing a run
    def show_all(self):
        self.show_nights()
        self.show_lines()
        self.show_values()
        self.redraw()

    def show_nights(self):
        tr = self.tree
        tr.delete(*tr.get_children())
        self.rows = {}
        res = self.run_res
        if res is None:
            return
        s = res.summary
        if s is not None:
            for r in s.nights:
                iid = tr.insert("", "end", values=night_row(r, s), tags=("bad",) if not r.usable else ())
                self.rows[iid] = r
            for d in sorted(s.replaced, key=lambda d: (d.night, d.written), reverse=True):
                tr.insert("", "end", values=(d.night, d.target, d.band, len(d.jd), _from(d), "", "", "", "", "", "",
                                             "", OLD_RUN), tags=("old",))
        for path, why in res.bad:
            newer = str(why).startswith(onsky.NEWER_WORDS)
            tr.insert("", "end", values=("", os.path.basename(path), "", "", "", "", "", "", "", "", "", "",
                                         ("⚠ " if newer else "✖ ") + why), tags=("old" if newer else "bad",))

    def night_selected(self):
        sel = self.tree.selection()
        r = self.rows.get(sel[0]) if sel else None
        if r is None and sel:
            return                                        # an older run or an unreadable log: nothing to show
        self.selected = r
        self.show_lines()
        self.redraw()

    def select_night(self, r):
        """Select a night in the table (tests, and the newest night after a run)."""
        for iid, q in self.rows.items():
            if q is r:
                self.tree.selection_set(iid)
                self.tree.see(iid)
                self.selected = r
                self.show_lines()
                self.redraw()
                return True
        return False

    def show_lines(self):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        res = self.run_res
        order = {"bad": 0, "warn": 1, "ok": 2, "info": 3}
        n = 0

        def put(kind, line):
            nonlocal n
            t.insert("end", line + "\n", (kind, "gap") if n else (kind,))
            n += 1
        if res is not None:
            if self.selected is not None:
                if res.folder_lines and self.selected is self.new_night(res):
                    put("head", f"The folder of lights ({os.path.basename(res.folder)})")
                    for kind, line in sorted(res.folder_lines, key=lambda kl: order.get(kl[0], 3)):
                        put(kind, line)
                put("head", self.selected.ds.label + (" · imaging focus" if self.selected.imaging else ""))
                for kind, line in sorted(self.selected.lines, key=lambda kl: order.get(kl[0], 3)):
                    put(kind, line)
            else:
                if res.folder_lines:
                    put("head", f"The folder of lights ({os.path.basename(res.folder)})")
                    for kind, line in sorted(res.folder_lines, key=lambda kl: order.get(kl[0], 3)):
                        put(kind, line)
                if res.summary is not None:
                    put("head", "All nights")
                    for kind, line in run_lines(res):
                        put(kind, line)
        t.configure(state="disabled")

    def show_values(self):
        tr = self.val_tree
        tr.delete(*tr.get_children())
        s = self.summary
        if s is None:
            self.foot.configure(text="")
            return
        old = self.current_profile()
        parents: dict = {}
        for label, ov, osrc, nv, unit, nd in onsky.changes_for(s, old, header_words(self.run_res)):
            group, _sep, what = label.partition(": ")
            if not what:
                group, what = "Cadence", label
            if group not in parents:
                parents[group] = tr.insert("", "end", text=group, open=True)
            o = "—" if ov is None else f"{_fmt(ov, nd)} ({'typed by you' if osrc == 'user' else osrc})"
            tr.insert(parents[group], "end", text=what, values=(o, f"{_fmt(nv, nd)} {unit}".strip()))
        pid = old.get("profile_id") if old else None
        self.foot.configure(text=(f"Now = in the system profile {pid}" if pid else
                                  "Now = in the system profile (there is none for this setup yet)") +
                            "; Measured = what “Save to system profile…” puts there. Zero point for 1 e⁻/s at "
                            "airmass 1.")

    def redraw(self):
        if self.summary is None:
            return
        self._figure()
        import sky_plots
        sky_plots.draw(self.fig, self.summary, self.selected)
        self.canvas.draw_idle()

    def _figure(self):
        if self.canvas is None:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            self.hint.pack_forget()
            self.fig = Figure(figsize=(7, 5), dpi=100)
            self.canvas = FigureCanvasTkAgg(self.fig, master=self.pic)
            w = self.canvas.get_tk_widget()
            w.configure(width=px(self.app, 300), height=px(self.app, 200))     # let the pane, not the figure, decide
            w.pack(fill=tk.BOTH, expand=True)
            self.app.after_idle(self._balance)

    def _balance(self):
        """Nights and plots on the left ~60 %, findings on the right; the nights table a third of the left."""
        try:
            self.pane.update_idletasks()
            w = self.pane.winfo_width()
            if w > 100:
                self.pane.sashpos(0, int(w * 0.6))
            h = self.left.winfo_height()
            if h > 100:
                self.left.sashpos(0, int(h * 0.3))
        except tk.TclError:
            pass


# ---- words and numbers for the page ----------------------------------------------------------------------------------

def _from(ds) -> str:
    return "FLUX" if ds.source == "SHOBS-FLUX" else "SHOBS-P"


def focal_of(datasets) -> float | None:
    f = [sp._finite(d.setup.get("focal_header_mm")) or sp._finite(d.setup.get("focal_mm")) for d in datasets]
    f = sorted(x for x in f if x)
    return f[len(f) // 2] if f else None


def focal_header_of(datasets) -> float | None:
    """The focal length the NEWEST night's headers give (FOCALLEN; the profile's "Focal length in the header"), or
    None when that night's headers have none. 0.6.0: refreshed on every save, never left at the first run's."""
    for d in sorted(datasets, key=lambda d: (d.night, d.written), reverse=True):
        f = sp._finite(d.setup.get("focal_header_mm"))
        if f:
            return f
    return None


def header_words(res) -> dict:
    """{"focal_header", "telescop_header"} the run's newest night's headers give (what Save refreshes)."""
    if res is None or not res.datasets:
        return {}
    ds = max(res.datasets, key=lambda d: (d.night, d.written))
    return {"focal_header": focal_header_of(res.datasets), "telescop_header": ds.setup.get("telescop_header")}


def camera_of(res: SkyRun) -> str:
    ids = [d.camera_id for d in res.datasets]
    return Counter(ids).most_common(1)[0][0] if ids else ""


def night_status(r) -> str:
    """ok / warn / bad for one night (the run history's status)."""
    if not r.usable:
        return "bad"
    if any(k == "bad" for k, _t in r.lines):
        return "bad"
    if not r.photometric or any(k == "warn" for k, _t in r.lines):
        return "warn"
    return "ok"


def night_row(r, s) -> tuple:
    ds = r.ds
    if math.isfinite(r.k_used) and r.k_used_source:
        k = _fmt(r.k_used, 3) if r.k_used_source == "this night" else f"{r.k_used:.3f} ({r.k_used_source})"
    else:
        k = "—"
    if math.isfinite(r.fwhm_arcsec):
        see = f"{r.fwhm_arcsec:.2f}"
        if r.imaging:
            see += " imaging"
        elif r.defocused:
            key = r.defocus_key or "defocused"
            see += " defocused" if key == "defocused" else f" defocus {key.replace(' steps', '')}"
    else:
        see = "—"
    status = onsky.night_summary(r)["worst"]
    bs = s.bands.get(ds.band)
    why = (bs.zp_out.get(f"{ds.night} {ds.target}") or bs.zp_out.get(ds.night)) if bs else None
    if not status and why and r.photometric:
        status = f"⚠ set aside: {why}"
    return (ds.night + (" imaging" if r.imaging else ""), ds.target, ds.band, r.n_frames or len(ds.jd), _from(ds),
            _fmt(r.zp1, 3), k,
            ("marginal" if r.marginal else "✔") if r.photometric else "—", see, _fmt(r.sky_mag, 2), _fmt(r.cy, 2), _fmt(r.overhead_s, 1),
            status or "used")


def run_lines(res: SkyRun) -> list:
    """The run's summary for the Findings tab when no night is picked: [(kind, line)]."""
    s = res.summary
    out = []
    used = [r for r in s.nights if r.usable]
    phot = [r for r in used if r.photometric]
    n_all, n_used, n_phot = (len(onsky_nights(x)) for x in (s.nights, used, phot))
    bits = [f"{_plural(n_all, 'night')} read", f"{n_used} used", f"{n_phot} photometric"]
    if len(s.nights) > n_all:
        bits.append(f"from {_plural(len(s.nights), 'run')} (runs of the same night count as one night)")
    img = onsky_nights([r for r in s.nights if r.imaging])
    if img:
        bits.append(f"{_plural(len(img), 'night')} of imaging focus")
    if s.replaced:
        bits.append(f"{_plural(len(s.replaced), 'older run')} of the same nights replaced (not counted)")
    out.append(("info", "Nights: " + ", ".join(bits) + "."))
    newer = [p for p, why in res.bad if str(why).startswith(onsky.NEWER_WORDS)]
    damaged = len(res.bad) - len(newer)
    if newer:
        out.append(("warn", f"⚠ {_plural(len(newer), 'on-sky log')} {'was' if len(newer) == 1 else 'were'} written by "
                            "a newer SHOBS-P than this SHOBS-FLUX can read, so they were left out. The files are "
                            "fine: leave them where they are and update SHOBS-FLUX."))
    if damaged:
        out.append(("bad", f"✖ {_plural(damaged, 'on-sky log')} could not be read (listed with ✖ in the table). "
                           "Reducing the night again in SHOBS-P writes a fresh log; the old file can then go."))
    for band, b in sorted(s.bands.items()):
        w = onsky.BAND_WORDS.get(band, band)
        parts = []
        if math.isfinite(b.zp1):
            parts.append(f"zero point {b.zp1:.3f} ± {b.zp1_sigma:.3f} from {_plural(len(b.zp_nights), 'night')}")
        else:
            parts.append("no zero point yet (no photometric night)")
        if math.isfinite(b.k):
            parts.append(f"extinction {b.k:.3f} ± {b.k_sigma:.3f} from {_plural(len(b.k_nights), 'night')}")
        if math.isfinite(b.fwhm_in_focus):
            parts.append(f"seeing {b.fwhm_in_focus:.2f}″ in focus from {_plural(b.fwhm_in_focus_n, 'night')}")
        if math.isfinite(b.fwhm_imaging):
            parts.append(f"seeing {b.fwhm_imaging:.2f}″ at imaging focus from {_plural(b.fwhm_imaging_n, 'night')} "
                         "(kept apart)")
        for key, (v, _sg, n) in sorted(b.fwhm_defocus.items()):
            parts.append(f"{v:.2f}″ {'defocused' if key == 'defocused' else 'defocused ' + key} from "
                         f"{_plural(n, 'night')}")
        if math.isfinite(b.sky):
            parts.append(f"sky {b.sky:.2f} mag/″² from {_plural(b.sky_n, 'night')}"
                         + ("" if b.sky_dark else " (no dark-Moon night yet)"))
        if math.isfinite(b.cy):
            parts.append(f"C_Y {b.cy:.2f} from {_plural(b.cy_n, 'night')}")
        out.append(("info", f"{w[0].upper() + w[1:]}: the profile would get " + "; ".join(parts) + "."))
        scale_out = [what for what, why in sorted(b.zp_out.items()) if why.startswith("its electron scale")]
        for what, why in sorted(b.zp_out.items()):
            if what in scale_out:
                continue
            kind = "warn" if why.startswith("disagrees") else "info"
            out.append((kind, ("⚠ " if kind == "warn" else "") + f"{what} set aside for the zero point: {why}."))
        if scale_out:
            out.append(("info", f"{_plural(len(scale_out), 'night')} left out of the zero point only (their extinction, "
                                "seeing, sky and C_Y still count): logs whose electron scale is not the real one, "
                                "mostly older SHOBS-P logs that leave out the master flat (" + ", ".join(scale_out)
                                + ")."))
        if b.zp_scale_inexact_only and math.isfinite(b.zp1):
            out.append(("warn", "⚠ This zero point rests only on logs whose electron scale is not the real one "
                                "(older SHOBS-P logs leave out the master flat: up to ~0.4 mag too faint on a color "
                                "camera). In the run history these nights are a trend of their own, 'Zero point, "
                                "older logs'. Reduce a photometric night again in SHOBS-P 2.3.0 or later (one color "
                                "band) before saving it."))
    if math.isfinite(s.overhead_s):
        out.append(("info", f"Overhead per frame {s.overhead_s:.1f} s: the lowest over "
                            f"{_plural(s.overhead_runs, 'night')} ({s.overhead_from}). A delay you set in the capture "
                            "software only adds to it, so the quickest run is the camera's own."))
    out += list(s.lines)
    out.append(("info", "Pick a night in the list to see what it says."))
    return out


def onsky_nights(results) -> list:
    """The distinct nights of some night runs (several runs of one night, e.g. mosaic panels, are one night)."""
    return sorted({r.ds.night for r in results}, reverse=True)


def status_text(s) -> str:
    """The status line after a run: 'On-sky run: 3 nights (2 photometric); zero point TG 20.18 ± 0.02, k 0.194,
    seeing 5.2″.'"""
    used = [r for r in s.nights if r.usable]
    n_phot = len(onsky_nights([r for r in used if r.photometric]))
    head = f"On-sky run: {_plural(len(onsky_nights(s.nights)), 'night')} ({n_phot} photometric)"
    parts = []
    for band, b in sorted(s.bands.items(), key=lambda kv: -len(kv[1].zp_nights)):
        bits = []
        if math.isfinite(b.zp1):
            bits.append(f"zero point {band} {b.zp1:.2f} ± {b.zp1_sigma:.2f}")
        else:
            bits.append(f"no zero point {band} yet")
        if math.isfinite(b.k):
            bits.append(f"k {b.k:.3f}")
        if math.isfinite(b.fwhm_in_focus):
            bits.append(f"seeing {b.fwhm_in_focus:.1f}″")
        parts.append(", ".join(bits))
    return head + ("; " + "; ".join(parts) if parts else "") + "."


def sky_summary(s) -> dict:
    """The sky run's summary for the run history: each band's combined values."""
    def n(v, nd=4):
        return round(v, nd) if v is not None and math.isfinite(v) else None
    bands = {}
    for band, b in s.bands.items():
        bands[band] = {"zp1": n(b.zp1), "zp1_sigma": n(b.zp1_sigma), "zp_nights": b.zp_nights, "zp_out": b.zp_out,
                       "k": n(b.k), "k_sigma": n(b.k_sigma), "k_nights": b.k_nights,
                       "fwhm_in_focus": n(b.fwhm_in_focus, 3),
                       "fwhm_defocus": {k: n(v[0], 3) for k, v in b.fwhm_defocus.items()},
                       "fwhm_imaging": n(b.fwhm_imaging, 3),
                       "sky": n(b.sky, 3), "cy": n(b.cy, 3)}
    worst = next((f"⚠ {night} set aside: {why}" for b in s.bands.values() for night, why in b.zp_out.items()
                  if why.startswith("disagrees")), "")
    if not worst and not any(math.isfinite(b.zp1) for b in s.bands.values()):
        worst = "⚠ no photometric night yet"
    newest = sorted(s.nights, key=lambda r: (r.ds.night, r.ds.written), reverse=True)
    logs = []
    for r in newest:                                 # the logs it read, newest first (the History page names them)
        b = os.path.basename(r.ds.path or "")
        if b and b not in logs:
            logs.append(b)
    return {"nights": len(onsky_nights(s.nights)), "runs": len(s.nights),
            "logs": logs, "night_list": sorted(onsky_nights(s.nights)),
            "photometric": len(onsky_nights([r for r in s.nights if r.photometric])),
            "imaging_nights": len(onsky_nights([r for r in s.nights if r.imaging])),
            "replaced": len(s.replaced), "bands": bands, "overhead_s": n(s.overhead_s, 3), "worst": worst}


def saved_source(res: SkyRun) -> str:
    """What a saved run read, for the run history's source: a folder run's folder of lights; a logs run's NEWEST
    on-sky log (its file name holds the night and target, as a night run's does; 0.5 wrote only the onsky folder)."""
    if res.source == "folder" and res.folder:
        return res.folder
    s = res.summary
    best = max((r for r in (s.nights if s else []) if r.ds.path), key=lambda r: (r.ds.night, r.ds.written),
               default=None)
    return best.ds.path if best is not None else sp.onsky_dir(make=False)


def sky_status(s) -> str:
    return "warn" if sky_summary(s)["worst"] else "ok"


def sky_values(s, pid: str) -> list:
    """The combined values as run-history rows (key bands.<key>, channel = the band, dated today)."""
    bands, cad = onsky.measured_records(s)
    today = sp.now_iso()[:10]
    rows = []

    def add(key, rec, channel):
        if rec.get("value") is not None:
            rows.append({"key": key, "value": rec["value"], "sigma": rec.get("sigma"), "unit": rec.get("unit"),
                         "source": "measured", "channel": channel, "profile_id": pid, "date": today})
    for band, recs in bands.items():
        for key, rec in recs.items():
            if key == "fwhm_defocus":
                for _dk, dr in rec.items():
                    add("bands.fwhm_defocus", dr, band)
            else:
                add(f"bands.{key}", rec, band)
    for key, rec in cad.items():
        add(f"cadence.{key}", rec, None)
    return rows


def scan_line(info) -> str:
    """One short line about a folder of lights from sky_folder.scan (a sentence, or the facts it found)."""
    if info is None:
        return ""
    if isinstance(info, str):
        return info
    for attr in ("line", "text", "summary_line"):
        v = getattr(info, attr, None)
        if isinstance(v, str) and v:
            return v
    get = info.get if isinstance(info, dict) else (lambda k, d=None: getattr(info, k, d))
    bits = []
    n = get("n_lights")
    if n is not None:
        bits.append(_plural(int(n), "light"))
    osc = get("osc")
    if osc is not None:
        bits.append("one-shot color" if osc else "mono")
    band = get("band") or get("filter")
    if band:
        bits.append(f"band {band}")
    bits.append(solution_words(get))
    for k in ("night", "target"):
        if get(k):
            bits.append(str(get(k)))
    return ", ".join(b for b in bits if b)


def solution_words(get) -> str:
    """'plate-solved (TAN-SIP)', '11 of 12 plate-solved (TAN-SIP)', 'no plate solution', or '' when the scan does not
    say. A folder counts as solved when ANY light carries a solution (the run uses them; ASIAIR often leaves the
    last light of a sequence unsolved), not only when every one does."""
    n_wcs, n, solved = get("n_wcs"), get("n_lights"), get("solved")
    proj = get("projection") or ""
    tail = f" ({proj})" if proj else ""
    if isinstance(n_wcs, (int, float)) and not isinstance(n_wcs, bool):
        if n_wcs <= 0:
            return "no plate solution"
        if isinstance(n, (int, float)) and 0 < n_wcs < n:
            return f"{int(n_wcs)} of {int(n)} plate-solved{tail}"
        return f"plate-solved{tail}"
    if solved is None:
        return f"plate-solved{tail}" if proj else ""
    return f"plate-solved{tail}" if solved or proj else "no plate solution"


class SaveDialog(tk.Toplevel):
    """What saving changes, value by value; keep your own typed values or not; Save."""

    def __init__(self, page: SkyPage, pid: str, old, changes, other=None):
        super().__init__(page.app)
        self.page, self.pid, self.old = page, pid, old
        app = page.app
        self.title("Save to system profile")
        self.configure(bg=style.CARD)
        self.transient(app)
        body = ttk.Frame(self, style="Card.TFrame", padding=px(app, 14))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=f"System profile {pid}", style="Section.TLabel").pack(anchor="w")
        ttk.Label(body, text=("It exists: the measured values below replace the old ones (the old revision is "
                              "kept in the history folder); values these nights did not measure stay as they are."
                              if old else "There is no profile for this telescope and camera yet: a new one is made."),
                  style="Hint.TLabel", wraplength=px(app, 560), justify=tk.LEFT).pack(anchor="w", pady=(0, px(app, 6)))
        if other and other.get("profile_id") != pid:
            oname = (other.get("telescope") or {}).get("name") or "?"
            ttk.Label(body, text=f"⚠ There is already a system profile for this camera at this focal length: "
                                 f"{other.get('profile_id')} (telescope “{oname}”). Its telescope name is different, so saving here makes a second one; "
                                 f"Cancel and type “{oname}” as the telescope name to update that one instead.",
                      style="Warn.TLabel", wraplength=px(app, 560), justify=tk.LEFT).pack(anchor="w",
                                                                                         pady=(0, px(app, 6)))
        tr = ttk.Treeview(body, columns=("old", "new"), show="tree headings", height=min(len(changes), 14) or 1,
                          selectmode="none")
        tr.heading("#0", text="Value")
        tr.heading("old", text="Now")
        tr.heading("new", text="Measured")
        tr.column("#0", width=px(app, 260))
        tr.column("old", width=px(app, 170), anchor="e")
        tr.column("new", width=px(app, 140), anchor="e")
        self.has_user = False
        for label, ov, osrc, nv, unit, nd in changes:
            o = "—" if ov is None else f"{_fmt(ov, nd)} ({'typed by you' if osrc == 'user' else osrc})"
            if osrc == "user":
                self.has_user = True
            tr.insert("", "end", text=label, values=(o, f"{_fmt(nv, nd)} {unit}".strip()))
        tr.pack(fill=tk.X)
        ttk.Label(body, text="The site is never stored in a profile: each app keeps its own.", style="Hint.TLabel",
                  wraplength=px(app, 560), justify=tk.LEFT).pack(anchor="w", pady=(px(app, 6), 0))
        self.keep = tk.BooleanVar(value=False)
        if self.has_user:
            ttk.Checkbutton(body, text="Keep the values you typed yourself (don't replace them)",
                            variable=self.keep, style="Card.TCheckbutton").pack(anchor="w", pady=(px(app, 8), 0))
        btns = ttk.Frame(body, style="Card.TFrame")
        btns.pack(fill=tk.X, pady=(px(app, 12), 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side=tk.RIGHT)
        style.green_button(btns, "Save", self.ok).pack(side=tk.RIGHT, padx=(0, px(app, 8)))
        if not app.testing:
            self.grab_set()

    def ok(self):
        keep = bool(self.keep.get())
        self.destroy()
        self.page.do_save(self.pid, self.old, keep, getattr(self, "what", None))
