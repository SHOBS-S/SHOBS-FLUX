"""The Sky page: what the telescope, camera and sky do, learned from ordinary nights (milestone 4).

Source (a drop-down) picks where the nights come from:
  SHOBS-P's on-sky logs (every night)   the log SHOBS-P writes after every reduction (the data folder's onsky/)
  A folder of lights                    FLUX reduces one night's lights itself (sky_folder.py) and writes the same
                                        kind of log, then learns from it together with every other night
The logs can hold several cameras and telescopes: Setup picks one (camera setting + focal length). Telescope name,
aperture and site elevation are typed here (aperture and elevation are for scintillation; the site is never stored
in a profile). Run reads every night of that setup (onsky.py has the whole method), lists the nights with what each
says, draws them, and shows what the system profile would get; "Save to system profile…" writes it after showing what
changes. Each night FLUX uses goes into the run history once (a log SHOBS-P writes again counts again)."""
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
FOCAL_TOL = 0.03                     # a log belongs to a setup when its focal length is within 3 % (plate solves)
GREY = "#8a969c"
OLD_RUN = "older run of this night (not counted)"


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
    except sp.ProfileError as exc:
        return {"path": path, "why": f"it cannot be read: {exc}"}
    if doc.get("format") != sp.ONSKY_FORMAT:
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
        self.new_log = ""                # the log the folder run wrote


# ---- the page --------------------------------------------------------------------------------------------------------

class SkyPage:
    title = "Sky"

    def __init__(self, app, parent):
        self.app = app
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        prefs = app.prefs
        tel = shobs_p_telescope()
        self.source_var = tk.StringVar(value=SOURCE_WORDS.get(prefs.get("sky_source"), SOURCES[0][0]))
        self.folder_var = tk.StringVar(value=prefs.get("sky_folder") or "")
        self.color_var = tk.StringVar(value=COLOR_WORDS.get(prefs.get("sky_color"), COLORS[0][0]))
        self.name_var = tk.StringVar(value=prefs.get("sky_telescope_name") or tel.get("name") or "")
        self.aperture_var = tk.StringVar(value=_num(prefs.get("sky_aperture_mm")) or _num(tel.get("aperture_mm")))
        self.elevation_var = tk.StringVar(value=_num(prefs.get("sky_elevation_m")) or _num(tel.get("elevation_m")))
        self.lat_var = tk.StringVar(value=_num(prefs.get("sky_lat")) or _num(tel.get("lat")))
        self.lon_var = tk.StringVar(value=_num(prefs.get("sky_lon")) or _num(tel.get("lon")))
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
        ttk.Label(crow, text="for one-shot-color cameras; a mono camera uses its FILTER", style="Hint.TLabel").pack(
            side=tk.LEFT)
        self.scan_label = ttk.Label(self.folder_row, textvariable=self.scan_var, style="Hint.TLabel", justify=tk.LEFT)
        self.scan_label.pack(anchor="w", fill=tk.X, pady=(px(app, 4), 0))
        src.bind("<Configure>", lambda e: (self.logs_label.configure(wraplength=max(200, e.width - px(app, 20))),
                                           self.scan_label.configure(wraplength=max(200, e.width - px(app, 20)))),
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
        heads = (("Night", 86), ("Target", 110), ("Band", 44), ("Frames", 54), ("From", 64), ("Zero point", 76),
                 ("k", 92), ("Photometric", 82), ("Seeing ″", 110), ("Sky mag/″²", 80), ("C_Y", 46),
                 ("Between frames s", 108), ("Status", 320))
        self.tree = ttk.Treeview(tw, columns=cols, show="headings", selectmode="browse", height=6)
        for c, (head, w) in zip(cols, heads):
            self.tree.heading(c, text=head)
            self.tree.column(c, width=px(app, w), minwidth=px(app, 36), stretch=(c == "status"),
                             anchor="w" if c in ("night", "target", "band", "from", "seeing", "status", "phot")
                             else "e")
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

    # ---- inputs
    @property
    def mode(self) -> str:
        return dict(SOURCES).get(self.source_var.get(), "logs")

    @property
    def channel(self) -> str:
        return dict(COLORS).get(self.color_var.get(), "TG")

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

    def scan_logs(self) -> list:
        """Read what each log says about its setup (cached by size and time), fill the Setup drop-down."""
        files = onsky.log_files()
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
        bad = sum(1 for i in infos if i.get("why"))
        if files:
            self.logs_var.set(f"{_plural(len(files), 'on-sky log file')} in {d}"
                              + (f" ({bad} cannot be read)" if bad else "")
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
        self.scan_var.set("")

        def work(step):
            step(f"Looking at {os.path.basename(folder)}")
            import sky_folder
            return sky_folder.scan(folder)

        def done(info, exc):
            if exc is not None:
                self.scan_var.set(f"⚠ The folder could not be looked at: {self._why(exc)}.")
                return
            self.scan_var.set(scan_line(info))

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
        p["sky_color"] = self.channel
        settings.save(p)

    # ---- the run
    def run(self):
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
            channel = self.channel
            site = self._site()
            self._busy()

            def work(step):
                return self._folder_work(folder, channel, aperture, elevation, name, step, site)

            self.app.jobs.run(work, self.done, f"On-sky run on {os.path.basename(folder)}")
            return
        infos = self.scan_logs()
        key = self._setup_key()
        if key is None:
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

    def _folder_work(self, folder, channel, aperture, elevation, name, step, site=None) -> SkyRun:
        import sky_folder
        res = SkyRun()
        res.source, res.folder = "folder", folder
        fr = sky_folder.run_folder(folder, channel=channel, step=step, elevation_m=elevation, site=site)
        res.folder_lines = list(getattr(fr, "lines", None) or [])
        doc = getattr(fr, "doc", None)
        if not doc:
            return res
        step("Writing the night's on-sky log")
        res.new_log = sky_folder.save_log(doc)
        info = log_info(res.new_log)
        if info.get("why"):
            raise onsky.OnSkyError(f"the log FLUX wrote cannot be read back: {info['why']}")
        key = setup_key(info)
        res.key = key
        paths, bad = [], []
        for p in onsky.log_files():
            i = log_info(p) if p != res.new_log else info
            if i.get("why"):
                bad.append((p, i["why"]))
            elif setup_key(i)[0] == key[0]:
                paths.append(p)                    # the focal length is checked on the datasets (3 %)
        res.bad = bad
        self._learn(res, paths, aperture, elevation, name, step)
        return res

    def _learn(self, res: SkyRun, paths, aperture, elevation, name, step):
        """Read the setup's logs and learn from them (in the worker thread)."""
        datasets = []
        for i, p in enumerate(paths):
            step(f"Reading on-sky log {i + 1} of {len(paths)}")
            got, bad = onsky.read_logs([p])
            datasets += got
            res.bad += bad
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
        for w in [self.folder_btn, self.color] + self._entries:
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
            self.color.configure(state="readonly")
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
        n_new = self._record_nights(res)
        s = res.summary
        settings.log(f"on-sky run ({res.source}; {len(s.nights)} nights, {n_new} new in the history; "
                     f"setup {res.key})")
        self.save_btn.configure(state="normal" if onsky.changes_for(s, None) else "disabled")
        try:
            self.show_all()
        except Exception as exc:                          # the run is learned and saveable even if a view fails
            import traceback
            settings.log(f"showing the on-sky run failed: {exc!r}\n{traceback.format_exc()}")
            self._say(f"⚠ The run finished, but part of the page could not be drawn ({exc}). Details are in "
                      f"{settings.log_path()}.")
        self.app.set_status(status_text(s))

    def _record_nights(self, res: SkyRun) -> int:
        """Each night used goes into the run history once (keyed by its log's path, size and time)."""
        pid = self.system_id(res, ask=False)
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
        if not name:
            return None
        return sp.system_id(name, focal_of(res.datasets), camera_of(res))

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
            path = sp.profile_path("systems", sp.system_id(name, focal, cam), make=False)
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
        changes = onsky.changes_for(res.summary, old)
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
        heads = [sp._finite(d.setup.get("focal_header_mm")) for d in res.datasets]
        heads = [h for h in heads if h]
        aperture = sp._finite(self.aperture_var.get().strip().replace(",", "") or None)
        telescope = {"name": self.name_var.get().strip(), "aperture_mm": aperture,
                     "focal_mm": round(focal) if focal else None,
                     "focal_header": sorted(heads)[len(heads) // 2] if heads else None,
                     "telescop_header": ds.setup.get("telescop_header")}
        cam = camera_of(res)
        doc = onsky.apply_to_profile(s, old, settings.WRITTEN_BY, run_key, keep_user, system_id=pid, cam_id=cam,
                                     telescope=telescope, setup=ds.setup)
        doc["profile_id"] = pid
        try:
            path = sp.save_profile(doc, settings.WRITTEN_BY)
        except (sp.ProfileError, OSError) as exc:
            self._say(f"✖ The system profile could not be saved ({exc}).")
            return
        if not (self.sky_recorded and run_key == self.run_key):
            history.record_run("sky", res.folder or sp.onsky_dir(make=False), sky_summary(s), camera_id=cam,
                               system_id=pid, n_frames=sum(r.n_frames for r in s.nights), status=sky_status(s),
                               app=settings.WRITTEN_BY, values=sky_values(s, pid), run_key=run_key)
            self.sky_recorded = run_key == self.run_key
        settings.log(f"on-sky run {run_key} saved to {path}")
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
            tr.insert("", "end", values=("", os.path.basename(path), "", "", "", "", "", "", "", "", "", "",
                                         f"✖ {why}"), tags=("bad",))

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
                put("head", self.selected.ds.label)
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
        for label, ov, osrc, nv, unit, nd in onsky.changes_for(s, old):
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
        if r.defocused:
            key = r.defocus_key or "defocused"
            see += " defocused" if key == "defocused" else f" defocus {key.replace(' steps', '')}"
    else:
        see = "—"
    status = onsky.night_summary(r)["worst"]
    bs = s.bands.get(ds.band)
    why = (bs.zp_out.get(f"{ds.night} {ds.target}") or bs.zp_out.get(ds.night)) if bs else None
    if not status and why and r.photometric:
        status = f"⚠ set aside: {why}"
    return (ds.night, ds.target, ds.band, r.n_frames or len(ds.jd), _from(ds), _fmt(r.zp1, 3), k,
            "✔" if r.photometric else "—", see, _fmt(r.sky_mag, 2), _fmt(r.cy, 2), _fmt(r.overhead_s, 1),
            status or "used")


def run_lines(res: SkyRun) -> list:
    """The run's summary for the Findings tab when no night is picked: [(kind, line)]."""
    s = res.summary
    out = []
    used = [r for r in s.nights if r.usable]
    phot = [r for r in used if r.photometric]
    bits = [f"{_plural(len(s.nights), 'night')} read", f"{len(used)} used", f"{len(phot)} photometric"]
    if s.replaced:
        bits.append(f"{_plural(len(s.replaced), 'older run')} of the same nights replaced (not counted)")
    out.append(("info", "Nights: " + ", ".join(bits) + "."))
    if res.bad:
        out.append(("bad", f"✖ {_plural(len(res.bad), 'on-sky log')} could not be read (listed with ✖ in the "
                           "table): move or delete them, or let SHOBS-P write them again."))
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
            parts.append(f"seeing {b.fwhm_in_focus:.2f}″ in focus")
        for key, (v, _sg, _n) in sorted(b.fwhm_defocus.items()):
            parts.append(f"{v:.2f}″ {'defocused' if key == 'defocused' else 'defocused ' + key}")
        if math.isfinite(b.sky):
            parts.append(f"sky {b.sky:.2f} mag/″²" + ("" if b.sky_dark else " (no dark-Moon night yet)"))
        if math.isfinite(b.cy):
            parts.append(f"C_Y {b.cy:.2f}")
        out.append(("info", f"{w[0].upper() + w[1:]}: the profile would get " + "; ".join(parts) + "."))
        for what, why in sorted(b.zp_out.items()):
            kind = "warn" if why.startswith("disagrees") else "info"
            out.append((kind, ("⚠ " if kind == "warn" else "") + f"{what} set aside for the zero point: {why}."))
    if math.isfinite(s.overhead_s):
        out.append(("info", f"Overhead per frame {s.overhead_s:.1f} s: the lowest of "
                            f"{_plural(s.overhead_runs, 'run')} ({s.overhead_from}). A delay you set in the capture "
                            "software only adds to it, so the quickest run is the camera's own."))
    out += list(s.lines)
    out.append(("info", "Pick a night in the list to see what it says."))
    return out


def status_text(s) -> str:
    """The status line after a run: 'On-sky run: 3 nights (2 photometric); zero point TG 20.18 ± 0.02, k 0.194,
    seeing 5.2″.'"""
    used = [r for r in s.nights if r.usable]
    n_phot = sum(1 for r in used if r.photometric)
    head = f"On-sky run: {_plural(len(s.nights), 'night')} ({n_phot} photometric)"
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
                       "sky": n(b.sky, 3), "cy": n(b.cy, 3)}
    worst = next((f"⚠ {night} set aside: {why}" for b in s.bands.values() for night, why in b.zp_out.items()
                  if why.startswith("disagrees")), "")
    if not worst and not any(math.isfinite(b.zp1) for b in s.bands.values()):
        worst = "⚠ no photometric night yet"
    return {"nights": len(s.nights), "photometric": sum(1 for r in s.nights if r.photometric),
            "replaced": len(s.replaced), "bands": bands, "overhead_s": n(s.overhead_s, 3), "worst": worst}


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
    solved = get("solved")
    if solved is not None:
        bits.append("plate-solved" if solved else "no plate solution")
    for k in ("night", "target"):
        if get(k):
            bits.append(str(get(k)))
    return ", ".join(bits)


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
                                 f"{other.get('profile_id')} (telescope “{oname}”). Saving here makes a second one; "
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
