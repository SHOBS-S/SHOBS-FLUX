"""SHOBS-FLUX: Shiloh Hill Observatory Optical System Characterization.

The window: a blue sidebar with the six pages, a white card in the middle, the status strip with the busy bar at the
bottom (SHOBS House Style, as SHOBS-SCHED). Milestone 0.1 (M1) brings the Check image page, the Profiles page and
the foundation every later page stands on; Camera, Sky and History say what they will do and when.

Start it with the Desktop shortcut (Create shortcuts.bat), run_windows.bat, or `python flux_app.py`."""
from __future__ import annotations

import math
import os
import queue
import sys
import threading
import traceback
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk

APP_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, APP_DIR)

import credits as cr          # noqa: E402
import history                # noqa: E402
import settings               # noqa: E402
import shobs_profile as sp    # noqa: E402
import style                  # noqa: E402
from style import px          # noqa: E402

APP_USER_MODEL_ID = "ShilohHill.SHOBS-FLUX"
PAGES = ("Check image", "Camera", "Sky", "Profiles", "History")
SUBTITLES = {
    "Check image": "Open one frame: photon counts, saturation, the sky and the stars, judged against your camera's "
                   "values. Every number says where it came from.",
    "Camera": "Bench runs: flat pairs and bias frames → gain, read noise, full well, linearity; darks → dark "
              "current (per color for one-shot-color cameras).",
    "Sky": "On-sky runs: zero point, extinction, seeing, sky brightness and scintillation per band, from SHOBS-P's "
           "on-sky log or a folder of lights.",
    "Profiles": "The camera and system profiles SHOBS-P and SHOBS-SCHED read, value by value with its source.",
    "History": "Every run FLUX has made; trend plots of any value over time.",
}
SOURCE_TAG = {"measured": "measured", "header": "header", "spec": "spec", "estimate": "estimate", "user": "you",
              "derived": "worked out", "none": "—"}


def set_app_id() -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass


def follow_wrap(label, container, pad: int = 0, least: int = 200) -> None:
    def on(e):
        try:
            label.configure(wraplength=max(least, int(e.width) - pad))
        except tk.TclError:
            pass
    container.bind("<Configure>", on, add="+")


def set_msg(var: tk.StringVar, label, text: str) -> None:
    var.set(text or "")
    try:
        label.configure(style=style.status_style(style.kind_of(text)) if text else "Card.TLabel")
    except tk.TclError:
        pass


def open_folder(path: str) -> None:
    try:
        if sys.platform == "win32":
            os.startfile(path)                 # noqa: S606
        elif sys.platform == "darwin":
            import subprocess
            subprocess.Popen(["open", path])
        else:
            import subprocess
            subprocess.Popen(["xdg-open", path])
    except Exception as exc:
        settings.log(f"open folder {path}: {exc!r}")


class Jobs:
    """Work in a thread, its result back on the main thread (Tk is not thread-safe); every job shows on the busy bar
    with its words, step by step (House Style)."""

    def __init__(self, root):
        self.root = root
        self.q: queue.Queue = queue.Queue()
        self.active = 0
        self._poll()

    def run(self, work, done, label: str):
        """work(step) runs in a thread (step("words") changes the busy bar's words); done(result, exc) runs here."""
        self.active += 1
        bar = self.root.busy_bar
        tok = bar.begin(label + "…")

        def step(text):
            self.q.put(("words", tok, str(text).rstrip(".…") + "…"))

        def target():
            try:
                self.q.put(("done", (done, tok), (work(step), None)))
            except Exception as exc:
                settings.log(f"{label} failed: {exc!r}\n{traceback.format_exc()}")
                self.q.put(("done", (done, tok), (None, exc)))

        threading.Thread(target=target, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, a, b = self.q.get_nowait()
                if kind == "words":
                    self.root.busy_bar.words(a, b)
                    continue
                done, tok = a
                self.active -= 1
                self.root.busy_bar.end(tok)
                try:
                    done(*b)
                except Exception:
                    self.root.report_callback_exception(*sys.exc_info())
        except queue.Empty:
            pass
        try:
            self.root.after(60, self._poll)
        except tk.TclError:
            pass

    def wait(self, timeout: float = 60.0) -> bool:
        """Tests: pump the event loop until no job is running."""
        import time
        t0 = time.time()
        while self.active and time.time() - t0 < timeout:
            self.root.update()
            time.sleep(0.02)
        self.root.update()
        return not self.active


# ---- Check image ---------------------------------------------------------------------------------------------------

class CheckPage:
    title = "Check image"

    def __init__(self, app: "App", parent):
        self.app = app
        self.res = None
        self.selected = None
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        top = ttk.Frame(self.frame, style="Card.TFrame")
        top.pack(fill=tk.X)
        self.open_btn = style.green_button(top, "Open frame…", self.choose)
        self.open_btn.pack(side=tk.LEFT)
        self.recheck_btn = ttk.Button(top, text="Check again", command=self.recheck, state="disabled")
        self.recheck_btn.pack(side=tk.LEFT, padx=(px(app, 8), 0))
        self.path_var = tk.StringVar(value="No frame open. Any FITS or XISF frame: a light, or a flat, bias or dark.")
        ttk.Label(top, textvariable=self.path_var, style="Hint.TLabel").pack(side=tk.LEFT, padx=px(app, 10))
        self.save_btn = style.green_button(top, "Save as starting camera profile", self.save_profile)
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(self.frame, textvariable=self.msg_var, style="Card.TLabel")
        self.msg.pack(anchor="w", fill=tk.X, pady=(px(app, 4), 0))

        pane = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, pady=(px(app, 6), 0))
        left = ttk.Frame(pane, style="Card.TFrame")
        right = ttk.Frame(pane, style="Card.TFrame")
        pane.add(left, weight=3)
        pane.add(right, weight=2)

        # the frame picture (matplotlib, made on first use so the window opens fast), the star panel under it
        self.star_box = ttk.Frame(left, style="Card.TFrame")
        self.star_box.pack(side=tk.BOTTOM, fill=tk.X)
        self.pic_holder = ttk.Frame(left, style="Card.TFrame")
        self.pic_holder.pack(fill=tk.BOTH, expand=True)
        self.pic_hint = ttk.Label(self.pic_holder, text="The frame shows here, its stars ringed: blue ✔ under the "
                                  "linear limit, amber ⚠ over it, red ✖ saturated. Click a star to see its numbers.",
                                  style="Hint.TLabel", justify=tk.LEFT)
        self.pic_hint.pack(anchor="nw", pady=px(app, 8))
        follow_wrap(self.pic_hint, self.pic_holder, px(app, 10))
        self.canvas = None
        self.fig = None

        # findings
        ttk.Label(right, text="Findings", style="Section.TLabel").pack(anchor="w")
        tf = ttk.Frame(right, style="Card.TFrame")
        tf.pack(fill=tk.BOTH, expand=True)
        self.text = tk.Text(tf, wrap="word", font=style.font(10), background="#ffffff", foreground=style.TEXT,
                            relief="flat", padx=px(app, 6), pady=px(app, 4), height=12, cursor="arrow")
        vb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=vb.set)
        vb.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.text.tag_configure("bad", foreground=style.BAD)
        self.text.tag_configure("warn", foreground=style.WARN_FG, background=style.WARN_BG)
        self.text.tag_configure("ok", foreground=style.OK)
        self.text.tag_configure("info", foreground=style.TEXT)
        self.text.tag_configure("gap", spacing1=px(app, 4))
        self.text.configure(state="disabled")

        # camera values in use
        ttk.Label(right, text="Camera values in use", style="Section.TLabel").pack(anchor="w", pady=(px(app, 8), 0))
        self.cam_tree = ttk.Treeview(right, columns=("value", "source"), show="tree headings", height=8,
                                     selectmode="none")
        self.cam_tree.heading("#0", text="")
        self.cam_tree.heading("value", text="Value")
        self.cam_tree.heading("source", text="Source")
        self.cam_tree.column("#0", width=px(app, 130), stretch=False)
        self.cam_tree.column("value", width=px(app, 140), anchor="e")
        self.cam_tree.column("source", width=px(app, 120))
        self.cam_tree.pack(fill=tk.X)

        # one star (under the picture)
        ttk.Label(self.star_box, text="Star", style="Section.TLabel").pack(anchor="w", pady=(px(app, 6), 0))
        self.star_tree = ttk.Treeview(self.star_box, columns=("v", "l2", "v2"), show="tree", height=6,
                                      selectmode="none")
        self.star_tree.column("#0", width=px(app, 140), minwidth=px(app, 100))
        self.star_tree.column("v", width=px(app, 190), minwidth=px(app, 120))
        self.star_tree.column("l2", width=px(app, 150), minwidth=px(app, 100))
        self.star_tree.column("v2", width=px(app, 150), minwidth=px(app, 100))
        self.star_tree.pack(fill=tk.X)

    def refresh(self):
        pass

    # ---- actions
    def choose(self):
        start = self.app.prefs.get("last_folder") or os.path.expanduser("~")
        path = filedialog.askopenfilename(
            parent=self.app, title="Open a frame", initialdir=start if os.path.isdir(start) else None,
            filetypes=[("FITS or XISF frames", "*.fit *.fits *.fts *.xisf *.FIT *.FITS *.FTS *.XISF"),
                       ("All files", "*.*")])
        if path:
            self.app.prefs["last_folder"] = os.path.dirname(path)
            settings.save(self.app.prefs)
            self.check(path)

    def recheck(self):
        if self.res is not None:
            self.check(self.res.path)

    def check(self, path: str, keep_msg: bool = False):
        import image_check
        if not keep_msg:
            set_msg(self.msg_var, self.msg, "")
        self.path_var.set(os.path.basename(path))
        self.open_btn.configure(state="disabled")
        fill = dict(self.app.prefs.get("header_answers") or {})

        def work(step):
            return image_check.check_image(path, step=step, fill=fill)

        self.app.jobs.run(work, self.checked, f"Checking {os.path.basename(path)}")

    def checked(self, res, exc):
        self.open_btn.configure(state="normal")
        if exc is not None:
            import frames
            why = str(exc) if isinstance(exc, frames.FrameError) else f"{type(exc).__name__}: {exc}"
            set_msg(self.msg_var, self.msg, f"✖ This frame could not be checked: {why}.")
            self.app.set_status(f"✖ Check image failed ({why}). Details are in {settings.log_path()}.")
            return
        self.res = res
        self.selected = None
        self.recheck_btn.configure(state="normal")
        self.show_lines()
        self.show_camera()
        self.draw()
        self.show_star(None)
        if res.cam.profile_path:
            self.save_btn.pack_forget()
        else:
            self.save_btn.pack(side=tk.RIGHT)
        worst = "bad" if any(k == "bad" for k, _ in res.lines) else (
            "warn" if any(k == "warn" for k, _ in res.lines) else "ok")
        key = history.record_run(
            "check", res.path, res.summary, camera_id=res.cam.cam_id, n_frames=1, status=worst,
            app=settings.WRITTEN_BY)
        settings.log(f"checked {res.path} ({worst}; run {key})")
        n_bad = sum(1 for k, _ in res.lines if k == "bad")
        n_warn = sum(1 for k, _ in res.lines if k == "warn")
        flags = ", ".join(x for x in (f"{n_bad} ✖" if n_bad else "", f"{n_warn} ⚠" if n_warn else "") if x)
        self.app.set_status(f"Checked {os.path.basename(res.path)}: {len(res.stars)} stars measured"
                            + (f"; {flags} in the findings." if flags else "; all ✔."))

    def show_lines(self):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        for i, (kind, line) in enumerate(self.res.lines):
            t.insert("end", line + "\n", (kind, "gap") if i else (kind,))
        t.configure(state="disabled")

    def show_camera(self):
        import instrument
        tr = self.cam_tree
        tr.delete(*tr.get_children())
        cam = self.res.cam
        for key, (label, unit) in instrument.CAMERA_KEYS.items():
            rec = cam.records.get(key) or {}
            v = rec.get("value")
            src = rec.get("source", "none")
            if key == "bias_level_adu" and v is None:
                v, src = self.res.bias[0], self.res.bias[1]
            if v is None:
                txt = "not known"
            elif isinstance(v, float) and abs(v) < 0.1:
                txt = f"{v:.4g} {unit}"
            elif isinstance(v, (int, float)):
                txt = f"{v:,.4g} {unit}" if abs(v) < 1000 else f"{v:,.0f} {unit}"
            else:
                txt = f"{v} {unit}"
            tr.insert("", "end", text=label, values=(txt.replace("fraction", "").strip(), SOURCE_TAG.get(src, src)))

    def draw(self):
        import numpy as np
        res = self.res
        img = res.preview
        if img is None:
            return
        if self.canvas is None:
            from matplotlib.figure import Figure
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            self.pic_hint.pack_forget()
            self.fig = Figure(figsize=(6, 4), dpi=100)
            self.fig.subplots_adjust(0.01, 0.01, 0.99, 0.99)
            self.ax = self.fig.add_subplot(111)
            self.canvas = FigureCanvasTkAgg(self.fig, master=self.pic_holder)
            self.canvas.get_tk_widget().pack(fill=tk.BOTH, expand=True)
            self.canvas.mpl_connect("button_press_event", self.on_click)
        # display copy: block-averaged to at most ~1400 px across, asinh stretch
        f = max(1, int(math.ceil(max(img.shape) / 1400)))
        h, w = img.shape[0] // f * f, img.shape[1] // f * f
        small = img[:h, :w].reshape(h // f, f, w // f, f).mean(axis=(1, 3))
        lo, hi = np.nanpercentile(small[::2, ::2], [5.0, 99.8])
        z = np.clip((small - lo) / max(hi - lo, 1e-6), 0, 1)
        z = np.arcsinh(10 * z) / np.arcsinh(10)
        self.disp_f = f
        ax = self.ax
        ax.clear()
        ax.imshow(z, cmap="gray", origin="lower", interpolation="nearest")
        ax.set_axis_off()
        colors = {"ok": style.OK, "warn": "#e0a100", "bad": style.BAD}
        for st in res.stars:
            r = max(3.0, st.r95 / f)
            ax.add_patch(_circle((st.x / f, st.y / f), r, colors[st.status], 1.2))
            ax.text(st.x / f + r + 2, st.y / f + r + 2, str(st.n), color=colors[st.status], fontsize=7)
        if self.selected is not None:
            st = self.selected
            ax.add_patch(_circle((st.x / f, st.y / f), max(6.0, st.r95 / f * 1.6), "#ffd54f", 2.0))
        self.canvas.draw_idle()

    def on_click(self, event):
        if self.res is None or event.xdata is None or event.inaxes is None:
            return
        import image_check
        x, y = event.xdata * self.disp_f, event.ydata * self.disp_f
        st = image_check.star_near(self.res, x, y, max_d=max(25.0, 12.0 * self.disp_f))
        if st is None:
            st = image_check.measure_at(self.res, x, y)
        if st is None:
            set_msg(self.msg_var, self.msg, "⚠ No star there: click on a star.")
            return
        set_msg(self.msg_var, self.msg, "")
        self.selected = st
        self.show_star(st)
        self.draw()

    def show_star(self, st):
        import image_check
        tr = self.star_tree
        tr.delete(*tr.get_children())
        if st is None:
            tr.insert("", "end", text="Click a star on the frame", values=("", "", ""))
            return
        rows = [(f"Star #{st.n}", "")] + image_check.star_detail(self.res, st)
        half = (len(rows) + 1) // 2
        for i in range(half):
            a = rows[i]
            b = rows[half + i] if half + i < len(rows) else ("", "")
            tr.insert("", "end", text=a[0], values=(a[1], b[0], b[1]))

    def save_profile(self):
        import instrument
        res = self.res
        if res is None:
            return
        s = res.cam.setup
        if not s.get("instrume"):
            name = simpledialog.askstring(settings.APP_NAME, "The frame's header does not name the camera "
                                          "(INSTRUME). What is it called? (asked once; used for frames without it)",
                                          parent=self.app)
            if not name or not name.strip():
                return
            self.app.prefs.setdefault("header_answers", {})["INSTRUME"] = name.strip()
            settings.save(self.app.prefs)
            self.check(res.path)
            return
        doc = instrument.starting_camera_profile(res.cam, res.frame.header, settings.WRITTEN_BY)
        if os.path.exists(sp.profile_path("cameras", doc["profile_id"])):
            set_msg(self.msg_var, self.msg, "⚠ A camera profile with this name already exists: see the Profiles page.")
            return
        try:
            path = sp.save_profile(doc, settings.WRITTEN_BY)
        except (sp.ProfileError, OSError) as exc:
            set_msg(self.msg_var, self.msg, f"✖ The profile could not be saved ({exc}).")
            return
        settings.log(f"saved starting camera profile {path}")
        set_msg(self.msg_var, self.msg, f"✔ Saved camera profile {doc['profile_id']}. Its values are labelled "
                                        "header / spec / estimate until bench runs measure them; type your own on "
                                        "the Profiles page.")
        self.check(res.path, keep_msg=True)


def _circle(xy, r, color, lw):
    from matplotlib.patches import Circle
    return Circle(xy, r, fill=False, edgecolor=color, linewidth=lw)


# ---- Profiles ------------------------------------------------------------------------------------------------------

class ProfilesPage:
    title = "Profiles"

    def __init__(self, app: "App", parent):
        self.app = app
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        top = ttk.Frame(self.frame, style="Card.TFrame")
        top.pack(fill=tk.X)
        ttk.Label(top, text="Profile", style="Card.TLabel").pack(side=tk.LEFT)
        self.pick_var = tk.StringVar()
        self.pick = ttk.Combobox(top, textvariable=self.pick_var, state="readonly", width=60)
        self.pick.pack(side=tk.LEFT, padx=px(app, 8))
        self.pick.bind("<<ComboboxSelected>>", lambda _e: self.show())
        self.set_btn = style.green_button(top, "Set a value…", self.set_value)
        self.set_btn.pack(side=tk.LEFT)
        ttk.Button(top, text="Open the data folder", command=lambda: open_folder(sp.data_dir())).pack(side=tk.RIGHT)
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(self.frame, textvariable=self.msg_var, style="Card.TLabel")
        self.msg.pack(anchor="w", fill=tk.X, pady=(px(app, 4), 0))
        self.info_var = tk.StringVar()
        info = ttk.Label(self.frame, textvariable=self.info_var, style="Hint.TLabel", justify=tk.LEFT)
        info.pack(anchor="w", fill=tk.X)
        follow_wrap(info, self.frame, px(app, 10))
        tf = ttk.Frame(self.frame, style="Card.TFrame")
        tf.pack(fill=tk.BOTH, expand=True, pady=(px(app, 6), 0))
        cols = ("value", "unit", "source", "date", "note")
        self.tree = ttk.Treeview(tf, columns=cols, show="tree headings", selectmode="browse")
        self.tree.heading("#0", text="Name")
        for c, w in zip(cols, (110, 90, 100, 100, 420)):
            self.tree.heading(c, text=c.capitalize())
            self.tree.column(c, width=px(app, w), anchor="e" if c == "value" else "w", stretch=(c == "note"))
        self.tree.column("#0", width=px(app, 260), stretch=False)
        vb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vb.set)
        vb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        hint = ttk.Label(self.frame, style="Hint.TLabel", justify=tk.LEFT,
                         text="Roll back to an earlier revision and the spec-curve editor come in later milestones. "
                              "Every save keeps the previous revision in the history folder.")
        hint.pack(anchor="w", fill=tk.X, pady=(px(app, 4), 0))
        follow_wrap(hint, self.frame, px(app, 10))
        self.paths: dict[str, str] = {}
        self.doc = None
        self.rec_paths: dict[str, tuple] = {}

    def refresh(self):
        self.paths = {}
        for kind, word in (("cameras", "Camera"), ("systems", "System"), ("specs", "Spec")):
            for p in sp.list_profiles(kind):
                self.paths[f"{word}: {os.path.basename(p)[:-5]}"] = p
        names = list(self.paths)
        self.pick.configure(values=names)
        if self.pick_var.get() not in self.paths:
            self.pick_var.set(names[0] if names else "")
        if not names:
            self.info_var.set(f"No profiles yet in {sp.data_dir()}. Check a frame and use “Save as starting camera "
                              "profile”, or put profile files in the cameras / systems / specs folders.")
        self.show()

    def show(self):
        tr = self.tree
        tr.delete(*tr.get_children())
        self.rec_paths = {}
        path = self.paths.get(self.pick_var.get())
        self.doc = None
        if not path:
            return
        try:
            doc = sp.load(path)
        except sp.ProfileError as exc:
            set_msg(self.msg_var, self.msg, f"✖ {os.path.basename(path)} cannot be read: {exc}.")
            return
        self.doc = (path, doc)
        probs = sp.validate(doc)
        set_msg(self.msg_var, self.msg, ("⚠ " + "; ".join(probs[:3])) if probs else "")
        hist = sp.history_of(os.path.basename(os.path.dirname(path)), os.path.basename(path)[:-5])
        bits = [f"{doc.get('format')} v{doc.get('format_version')}"]
        if doc.get("revision"):
            bits.append(f"revision {doc['revision']}")
        if doc.get("updated"):
            bits.append(f"updated {doc['updated']}")
        if doc.get("written_by"):
            bits.append(f"by {doc['written_by']}")
        bits.append(f"{len(hist)} earlier revision{'s' if len(hist) != 1 else ''} kept")
        self.info_var.set(" · ".join(bits) + f"\n{path}")
        if doc.get("format") == sp.SPEC_FORMAT:
            for name, pts in (doc.get("curves") or {}).items():
                if name == "x":
                    continue
                tr.insert("", "end", text=name, values=(f"{len(pts)} points", "", "spec", doc.get("entered", ""),
                                                        doc.get("source", "")))
            return
        parents: dict = {}
        for rpath, rec in sp.iter_records(doc):
            parent = ""
            for i in range(len(rpath) - 1):
                key = ".".join(str(p) for p in rpath[: i + 1])
                if key not in parents:
                    parents[key] = tr.insert(parent, "end", text=str(rpath[i]), open=True)
                parent = parents[key]
            v = rec.get("value")
            iid = tr.insert(parent, "end", text=str(rpath[-1]), values=(
                "—" if v is None else (f"{v:g}" if isinstance(v, (int, float)) else str(v)), rec.get("unit") or "",
                SOURCE_TAG.get(rec.get("source"), rec.get("source")), rec.get("date") or "", rec.get("note") or ""))
            self.rec_paths[iid] = rpath

    def set_value(self):
        if not self.doc:
            set_msg(self.msg_var, self.msg, "⚠ Pick a profile first.")
            return
        sel = self.tree.selection()
        if not sel or sel[0] not in self.rec_paths:
            set_msg(self.msg_var, self.msg, "⚠ Pick a value in the list first (a row with a unit).")
            return
        path, doc = self.doc
        if doc.get("format") not in (sp.CAMERA_FORMAT, sp.SYSTEM_FORMAT):
            return
        rpath = self.rec_paths[sel[0]]
        rec = doc
        for p in rpath:
            rec = rec[p]
        ans = simpledialog.askstring(
            settings.APP_NAME, f"Your value for {'.'.join(str(p) for p in rpath)} ({rec.get('unit')}).\n"
            "It is saved with the source “user” and wins over header, spec and estimates until a run measures it.",
            initialvalue="" if rec.get("value") is None else str(rec.get("value")), parent=self.app)
        if ans is None:
            return
        try:
            val = float(ans.replace(",", ""))
            if not math.isfinite(val):
                raise ValueError
        except ValueError:
            set_msg(self.msg_var, self.msg, f"✖ “{ans}” is not a number.")
            return
        rec.update(sp.record(val, rec.get("unit") or "", "user", date=sp.now_iso()[:10],
                             note="typed on the Profiles page"))
        try:
            sp.save_profile(doc, settings.WRITTEN_BY)
        except (sp.ProfileError, OSError) as exc:
            set_msg(self.msg_var, self.msg, f"✖ Not saved ({exc}).")
            return
        settings.log(f"user value {'.'.join(str(p) for p in rpath)} = {val:g} in {path}")
        self.show()
        set_msg(self.msg_var, self.msg, f"✔ Saved {val:g} {rec.get('unit') or ''}; the previous revision is in the "
                                        "history folder.")


# ---- the pages still to come ---------------------------------------------------------------------------------------

class ComingPage:
    """Camera, Sky and History in 0.1: what the page will do, when, and what is already in place for it."""

    def __init__(self, app: "App", parent, name: str, text: str, extra=None):
        self.app = app
        self.title = name
        self.extra = extra
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        lab = ttk.Label(self.frame, text=text, style="Card.TLabel", justify=tk.LEFT)
        lab.pack(anchor="w", fill=tk.X)
        follow_wrap(lab, self.frame, px(app, 10))
        self.var = tk.StringVar()
        self.lab2 = ttk.Label(self.frame, textvariable=self.var, style="Ok.TLabel", justify=tk.LEFT)
        self.lab2.pack(anchor="w", fill=tk.X, pady=(px(app, 10), 0))
        follow_wrap(self.lab2, self.frame, px(app, 10))
        self.tree = None
        if name == "History":
            cols = ("kind", "when", "camera", "status", "source")
            self.tree = ttk.Treeview(self.frame, columns=cols, show="headings", height=14, selectmode="none")
            for c, w in zip(cols, (70, 190, 260, 70, 420)):
                self.tree.heading(c, text=c.capitalize())
                self.tree.column(c, width=px(app, w), stretch=(c == "source"))
            self.tree.pack(fill=tk.BOTH, expand=True, pady=(px(app, 8), 0))

    def refresh(self):
        if self.extra:
            try:
                self.var.set(self.extra())
            except Exception as exc:
                self.var.set(f"(could not look: {exc})")
        if self.tree is not None:
            self.tree.delete(*self.tree.get_children())
            for r in history.recent_runs(200):
                icon = style.ICON.get(r.get("status") or "", "")
                self.tree.insert("", "end", values=(r["kind"], r["started"], r.get("camera_id") or "",
                                                    f"{icon} {r.get('status') or ''}", r.get("source") or ""))


def onsky_status() -> str:
    d = sp.onsky_dir(make=False)
    try:
        n = len([x for x in os.listdir(d) if x.endswith(".json")]) if os.path.isdir(d) else 0
    except OSError:
        n = 0
    if n:
        return (f"✔ SHOBS-P's on-sky log is filling: {n} reduction{'s' if n != 1 else ''} logged so far in {d}. "
                "The Sky page will learn from all of them.")
    return (f"No SHOBS-P on-sky log files yet in {d}. SHOBS-P 2.2.19.1 and later write one each time a reduction "
            "finishes; the Sky page will learn from all of them.")


CAMERA_TEXT = (
    "Coming in 0.2 (milestone 2): the bench photon transfer curve. Point FLUX at a folder of flat pairs (two frames at "
    "each exposure, from very short to past saturation) and bias pairs at one gain, offset and temperature. FLUX "
    "measures gain (e⁻/ADU), read noise, full well, the linearity curve and the safe linear limit, and the "
    "exposure-timing offset, per color (R, G, B) for one-shot-color cameras, in a central region it shows you, "
    "with a check that the panel stayed steady. The result goes into your camera profile.\n\n"
    "Darks and dark current, with the manufacturer's curves drawn under the measured points, follow in 0.3.")
SKY_TEXT = (
    "Coming in 0.4 (milestone 4): on-sky characterization. From SHOBS-P's on-sky log (written after every reduction) "
    "or a folder of lights: zero point per band (APASS DR9 and Gaia DR3 synthetic magnitudes; saturated, crowded and "
    "known-variable stars left out), extinction, seeing by focus state, sky brightness, scintillation and cadence. "
    "Nights that disagree are set aside, not averaged in. The result goes into your system profile.")
HISTORY_TEXT = (
    "Coming in 0.5 (milestone 5): trend plots of any value per setup and date range (zero point, extinction, seeing, "
    "read noise against the camera's spec line…), exported as PNG with the date and time, or as CSV; profile "
    "roll-back. Every run FLUX makes is already recorded:")


# ---- Credits -------------------------------------------------------------------------------------------------------

class CreditsPage:
    title = "Credits and sources"
    subtitle = "The methods, designs and libraries this app uses, with the citation each asks for."

    def __init__(self, app: "App", parent):
        self.app = app
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        nav = ttk.Frame(self.frame, style="Card.TFrame")
        nav.pack(side=tk.BOTTOM, fill=tk.X, pady=(px(app, 8), 0))
        ttk.Button(nav, text="Save credits as text…", command=self.save_text).pack(side=tk.LEFT)
        ttk.Button(nav, text="Back", command=self.back).pack(side=tk.RIGHT)
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(nav, textvariable=self.msg_var, style="Card.TLabel")
        self.msg.pack(side=tk.LEFT, padx=px(app, 10))
        head = ttk.Frame(self.frame, style="Card.TFrame")
        head.pack(fill=tk.X, pady=(0, px(app, 8)))
        ttk.Label(head, text=f"SHOBS-FLUX {settings.APP_VERSION}   {cr.COPYRIGHT}", style="Bold.TLabel").pack(anchor="w")
        lic = ttk.Label(head, text=cr.LICENSE_LINE, style="Card.TLabel", justify=tk.LEFT)
        lic.pack(anchor="w", fill=tk.X)
        follow_wrap(lic, head, px(app, 10))
        cite = cr.citation_text(settings.APP_VERSION)
        if cite:
            ttk.Label(head, text="How to cite SHOBS-FLUX", style="Section.TLabel").pack(anchor="w")
            cl = ttk.Label(head, text=cite, style="Card.TLabel", justify=tk.LEFT)
            cl.pack(anchor="w", fill=tk.X)
            follow_wrap(cl, head, px(app, 10))
        tf = ttk.Frame(self.frame, style="Card.TFrame")
        tf.pack(fill=tk.BOTH, expand=True)
        self.text = tk.Text(tf, wrap="word", font=style.font(10), background="#ffffff", foreground=style.TEXT,
                            relief="flat", padx=px(app, 8), pady=px(app, 8), height=10)
        vb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.text.yview)
        self.text.configure(yscrollcommand=vb.set)
        vb.pack(side=tk.RIGHT, fill=tk.Y)
        self.text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.text.insert("1.0", cr.credits_text())
        self.text.configure(state="disabled")

    def refresh(self):
        pass

    def back(self):
        self.app.show_page(self.app.page_index if self.app.page_index is not None else 0)

    def save_text(self):
        path = filedialog.asksaveasfilename(parent=self.app, defaultextension=".txt",
                                            initialfile="SHOBS-FLUX credits.txt",
                                            filetypes=[("Text", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(f"SHOBS-FLUX {settings.APP_VERSION}\n{cr.COPYRIGHT}\n{cr.LICENSE_LINE}\n\n"
                         + cr.credits_text())
        except OSError as exc:
            set_msg(self.msg_var, self.msg, f"✖ Not saved ({exc.strerror or exc}).")
            return
        set_msg(self.msg_var, self.msg, f"✔ Saved {os.path.basename(path)}.")


# ---- the app -------------------------------------------------------------------------------------------------------

class App(tk.Tk):
    def __init__(self, testing: bool = False):
        style.enable_windows_dpi()
        set_app_id()
        super().__init__(className="SHOBS-FLUX")
        self.testing = testing
        self.title(settings.APP_TITLE)
        self.configure(bg=style.FRAME_BG)
        self._set_window_icon()
        style.setup(self)
        self.prefs = settings.load()
        self.jobs = Jobs(self)
        self._build()
        self._place_window()
        self.show_page(0)
        bad = sp.self_check()
        self.set_status(f"✖ The shared profile code failed its own check ({', '.join(bad)})." if bad else
                        f"Data folder: {sp.data_dir()}")
        settings.log(f"opened {settings.WRITTEN_BY}")

    def _set_window_icon(self):
        ico = os.path.join(APP_DIR, "shobs_flux.ico")
        png = os.path.join(APP_DIR, "shobs_flux.png")
        try:
            if sys.platform == "win32" and os.path.exists(ico):
                self.iconbitmap(default=ico)
            elif os.path.exists(png):
                self._icon_image = tk.PhotoImage(master=self, file=png)
                self.iconphoto(True, self._icon_image)
        except Exception:
            pass

    def report_callback_exception(self, exc, val, tb):
        text = "".join(traceback.format_exception(exc, val, tb))
        settings.log("Error in the window; traceback follows.\n" + text)
        try:
            self.set_status(f"✖ Something went wrong: {val}. Details are in {settings.log_path()}.")
        except Exception:
            pass
        if self.testing:
            raise val

    def destroy(self):
        try:
            for aid in self.tk.splitlist(self.tk.call("after", "info")):
                try:
                    self.tk.call("after", "cancel", aid)
                except tk.TclError:
                    pass
        except tk.TclError:
            pass
        super().destroy()

    def set_status(self, text: str):
        self.status_var.set(text or "")
        kind = style.kind_of(text or "")
        self.status_label.configure(style={"bad": "StatusBad.TLabel", "warn": "StatusWarn.TLabel"}.get(
            kind, "Status.TLabel"))

    def _build(self):
        self.status_var = tk.StringVar(value="")
        status = ttk.Frame(self, style="Status.TFrame")
        status.pack(side=tk.BOTTOM, fill=tk.X)
        row = ttk.Frame(status, style="Status.TFrame")
        row.pack(fill=tk.X, padx=px(self, 12), pady=(px(self, 4), 0))
        self.busy_bar = style.BusyBar(row, label_style="Status.TLabel", frame_style="Status.TFrame",
                                      bar_style="Status.Horizontal.TProgressbar")
        self.busy_bar.pack(side=tk.LEFT)
        self.status_label = ttk.Label(row, textvariable=self.status_var, style="Status.TLabel")
        self.status_label.pack(side=tk.LEFT, anchor="w", fill=tk.X, expand=True)
        line = ttk.Label(status, text=cr.STATUS_LINE, style="Status.TLabel", foreground=style.SIDE_HINT)
        line.pack(anchor="w", fill=tk.X, padx=px(self, 12), pady=(0, px(self, 4)))

        outer = ttk.Frame(self)
        outer.pack(fill=tk.BOTH, expand=True)
        side = ttk.Frame(outer, style="Side.TFrame")
        side.pack(side=tk.LEFT, fill=tk.Y)
        ttk.Label(side, text="Shiloh Hill\nObservatory\nOptical System\nCharacterization", style="SideTitle.TLabel",
                  justify=tk.LEFT).pack(anchor="w", padx=px(self, 16), pady=(px(self, 18), px(self, 2)))
        ttk.Label(side, text="(SHOBS-FLUX)", style="SideHint.TLabel").pack(anchor="w", padx=px(self, 16))
        ttk.Label(side, text="Measures your camera\nand telescope, and\nkeeps their numbers\nfor SHOBS-P and\nSHOBS-SCHED",
                  style="SideHint.TLabel", justify=tk.LEFT).pack(anchor="w", padx=px(self, 16),
                                                                 pady=(px(self, 8), px(self, 12)))
        ttk.Label(side, text="PAGES", style="SideHead.TLabel").pack(anchor="w", padx=px(self, 16))
        self.step_buttons = []
        for i, name in enumerate(PAGES):
            b = ttk.Button(side, text=f"{i + 1}   {name}", style="Step.TButton", command=lambda n=i: self.show_page(n))
            b.pack(fill=tk.X, padx=px(self, 10), pady=px(self, 2))
            self.step_buttons.append(b)
        ttk.Label(side, text=f"Version {settings.APP_VERSION}", style="SideHint.TLabel").pack(
            side=tk.BOTTOM, anchor="w", padx=px(self, 16), pady=px(self, 10))
        self.credits_button = ttk.Button(side, text="Credits and sources", style="Step.TButton",
                                         command=self.show_credits)
        self.credits_button.pack(side=tk.BOTTOM, fill=tk.X, padx=px(self, 10), pady=(0, px(self, 4)))

        right = ttk.Frame(outer)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        card = ttk.Frame(right, style="Card.TFrame", padding=(px(self, 16), px(self, 12)))
        card.pack(fill=tk.BOTH, expand=True, padx=px(self, 12), pady=px(self, 12))
        self.title_var = tk.StringVar()
        self.subtitle_var = tk.StringVar()
        ttk.Label(card, textvariable=self.title_var, style="Title.TLabel").pack(anchor="w")
        sub = ttk.Label(card, textvariable=self.subtitle_var, style="Hint.TLabel", justify=tk.LEFT)
        sub.pack(anchor="w", fill=tk.X, pady=(0, px(self, 8)))
        follow_wrap(sub, card, px(self, 40))
        holder = ttk.Frame(card, style="Card.TFrame")
        holder.pack(fill=tk.BOTH, expand=True)
        self.check_page = CheckPage(self, holder)
        self.profiles_page = ProfilesPage(self, holder)
        self.pages = [self.check_page,
                      ComingPage(self, holder, "Camera", CAMERA_TEXT),
                      ComingPage(self, holder, "Sky", SKY_TEXT, extra=onsky_status),
                      self.profiles_page,
                      ComingPage(self, holder, "History", HISTORY_TEXT)]
        for p in self.pages:
            p.frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.credits_page = CreditsPage(self, holder)
        self.credits_page.frame.place(relx=0, rely=0, relwidth=1, relheight=1)
        self.page_index = None

    def _place_window(self):
        try:
            sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
            w, h = int(sw * 0.8), int(sh * 0.82)
            self.geometry(f"{w}x{h}+{(sw - w) // 2}+{max(0, (sh - h) // 3)}")
            self.minsize(min(w, px(self, 900)), min(h, px(self, 560)))
        except tk.TclError:
            pass

    def show_page(self, i: int):
        self.page_index = i
        page = self.pages[i]
        page.frame.tkraise()
        self.title_var.set(PAGES[i])
        self.subtitle_var.set(SUBTITLES[PAGES[i]])
        for k, b in enumerate(self.step_buttons):
            b.configure(style="StepOn.TButton" if k == i else "Step.TButton")
        self.credits_button.configure(style="Step.TButton")
        page.refresh()

    def show_credits(self):
        self.credits_page.frame.tkraise()
        self.title_var.set(CreditsPage.title)
        self.subtitle_var.set(CreditsPage.subtitle)
        for b in self.step_buttons:
            b.configure(style="Step.TButton")
        self.credits_button.configure(style="StepOn.TButton")


def main():
    try:
        app = App()
    except Exception:
        settings.log("could not start; traceback follows.\n" + traceback.format_exc())
        raise
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):     # a frame dropped on the shortcut / given on the line
        app.after(300, lambda: app.check_page.check(sys.argv[1]))
    app.mainloop()


if __name__ == "__main__":
    main()
