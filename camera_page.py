"""The Camera page: what the camera does on the bench, measured from folders of frames.

Measure (a drop-down) picks the run:
  Gain and linearity (flat pairs)   the bench run: the photon transfer curve from flat pairs and bias pairs
  Dark current (darks)              the dark run: dark current, hot pixels and edge glow from a dark library
Pick the folders (flats, bias, darks), the central region, Run. The page shows the run's pictures, findings, values
per color, and every pair or exposure; "Against the spec" draws the manufacturer's curves with the measured points on
them instead. "Save to camera profile" writes the run's measured values into the camera profile SHOBS-P and
SHOBS-SCHED read (the old revision is kept), after showing what changes. "Shooting plan…" prints a flat-pair ladder
for the panel: from the bench run's measured brightness, or from one test flat."""
from __future__ import annotations

import math
import os
import tkinter as tk
from tkinter import filedialog, simpledialog, ttk

import bench
import darks
import frames
import history
import settings
import shobs_profile as sp
import style
from style import px


MEASURES = (("Gain and linearity (flat pairs)", "bench"), ("Dark current (darks)", "dark"))
MEASURE_WORDS = {k: w for w, k in MEASURES}


def _fmt(v, nd=0, unit=""):
    if v is None or not isinstance(v, (int, float)) or not math.isfinite(v):
        return "—"
    if nd == 0:
        return f"{v:,.0f}{(' ' + unit) if unit else ''}"
    return f"{v:.{nd}f}{(' ' + unit) if unit else ''}"


class CameraPage:
    title = "Camera"

    def __init__(self, app, parent):
        self.app = app
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        prefs = app.prefs
        self.folder_var = tk.StringVar(value=prefs.get("bench_folder") or "")
        self.bias_var = tk.StringVar(value=prefs.get("bench_bias_folder") or "")
        self.region_var = tk.StringVar(value=prefs.get("bench_region") or bench.DEFAULT_REGION)
        self.darks_var = tk.StringVar(value=prefs.get("dark_folder") or "")
        self.measure_var = tk.StringVar(value=MEASURE_WORDS.get(prefs.get("camera_measure"), MEASURES[0][0]))
        self.picture_var = tk.StringVar(value="run")
        self.res = None                  # the bench run on screen
        self.dres = None                 # the dark run on screen
        self.run_keys = {"bench": None, "dark": None}

        # what to read (grouped: the inputs, then the run)
        inp = ttk.Frame(self.frame, style="Card.TFrame")
        inp.pack(fill=tk.X)
        inp.columnconfigure(2, weight=1)
        ttk.Button(inp, text="Flats folder…", command=self.pick_folder).grid(row=0, column=0, sticky="w")
        ttk.Label(inp, text="flat pairs (and bias pairs)", style="Hint.TLabel").grid(row=0, column=1, sticky="w",
                                                                                     padx=px(app, 8))
        ttk.Label(inp, textvariable=self.folder_var, style="Card.TLabel").grid(row=0, column=2, sticky="w")
        ttk.Button(inp, text="Bias folder…", command=self.pick_bias).grid(row=1, column=0, sticky="we",
                                                                          pady=(px(app, 4), 0))
        ttk.Label(inp, text="optional: bias frames shot on their own", style="Hint.TLabel").grid(
            row=1, column=1, sticky="w", padx=px(app, 8), pady=(px(app, 4), 0))
        brow = ttk.Frame(inp, style="Card.TFrame")
        brow.grid(row=1, column=2, sticky="w", pady=(px(app, 4), 0))
        ttk.Label(brow, textvariable=self.bias_var, style="Card.TLabel").pack(side=tk.LEFT)
        self.clear_bias_btn = ttk.Button(brow, text="Clear", command=self.clear_bias)
        self.clear_bias_btn.pack(side=tk.LEFT, padx=(px(app, 8), 0))
        ttk.Button(inp, text="Darks folder…", command=self.pick_darks).grid(row=2, column=0, sticky="we",
                                                                            pady=(px(app, 4), 0))
        ttk.Label(inp, text="darks at one or more exposures (subfolders too)", style="Hint.TLabel").grid(
            row=2, column=1, sticky="w", padx=px(app, 8), pady=(px(app, 4), 0))
        ttk.Label(inp, textvariable=self.darks_var, style="Card.TLabel").grid(row=2, column=2, sticky="w",
                                                                             pady=(px(app, 4), 0))

        run = ttk.Frame(self.frame, style="Card.TFrame")
        run.pack(fill=tk.X, pady=(px(app, 8), 0))
        ttk.Label(run, text="Measure", style="Card.TLabel").pack(side=tk.LEFT)
        self.measure = ttk.Combobox(run, textvariable=self.measure_var, state="readonly", width=30,
                                    values=[w for w, _k in MEASURES])
        self.measure.pack(side=tk.LEFT, padx=(px(app, 6), px(app, 12)))
        self.measure.bind("<<ComboboxSelected>>", lambda _e: self.measure_changed())
        ttk.Label(run, text="Region", style="Card.TLabel").pack(side=tk.LEFT)
        self.region = ttk.Combobox(run, textvariable=self.region_var, state="readonly", values=list(bench.REGIONS),
                                   width=14)
        self.region.pack(side=tk.LEFT, padx=(px(app, 6), px(app, 12)))
        self.run_btn = style.green_button(run, "Run", self.run)
        self.run_btn.pack(side=tk.LEFT)
        self.plan_btn = ttk.Button(run, text="Shooting plan…", command=self.plan)
        self.plan_btn.pack(side=tk.LEFT, padx=(px(app, 8), 0))
        self.save_btn = style.green_button(run, "Save to camera profile…", self.save)
        self.save_btn.configure(state="disabled")
        self.save_btn.pack(side=tk.RIGHT)
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(self.frame, textvariable=self.msg_var, style="Card.TLabel")
        self.msg.pack(anchor="w", fill=tk.X, pady=(px(app, 4), 0))
        prow = ttk.Frame(self.frame, style="Card.TFrame")
        prow.pack(fill=tk.X, pady=(px(app, 2), 0))
        ttk.Label(prow, text="Picture", style="Card.TLabel").pack(side=tk.LEFT)
        for value, text in (("run", "This run"), ("spec", "Against the spec")):
            ttk.Radiobutton(prow, text=text, value=value, variable=self.picture_var, style="Card.TRadiobutton",
                            command=self.redraw).pack(side=tk.LEFT, padx=(px(app, 8), 0))

        pane = ttk.PanedWindow(self.frame, orient=tk.HORIZONTAL)
        pane.pack(fill=tk.BOTH, expand=True, pady=(px(app, 6), 0))
        self.pane = pane
        left = ttk.Frame(pane, style="Card.TFrame")
        right = ttk.Frame(pane, style="Card.TFrame")
        pane.add(left, weight=3)
        pane.add(right, weight=2)
        self.pic = ttk.Frame(left, style="Card.TFrame")
        self.pic.pack(fill=tk.BOTH, expand=True)
        self.hint = ttk.Label(
            self.pic, style="Hint.TLabel", justify=tk.LEFT,
            text="Point FLUX at a folder of flat pairs: two flats at each of 10–20 exposures, from very short to past "
                 "saturation, at one gain, offset and temperature, plus 10–20 bias pairs shot with the panel OFF and "
                 "the scope capped (here or in a bias folder of their own). Run measures gain, read noise, full "
                 "well, linearity and the exposure-timing offset in the central region, per color.\n\n"
                 "No ladder yet? “Shooting plan…” prints one for your panel from a single test flat.\n\n"
                 "Dark current: choose Measure → Dark current, point the Darks folder at your dark library (darks "
                 "at one or more exposures, each in a subfolder of its own or all together), Run. Bias frames make "
                 "it more exact. “Against the spec” shows the manufacturer's curves with what you measured.")
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

        vf = ttk.Frame(nb, style="Card.TFrame")
        nb.add(vf, text="Values")
        self.val_tree = ttk.Treeview(vf, columns=("cam", "c1", "c2", "c3"), show="tree headings", selectmode="none")
        self.val_tree.heading("#0", text="")
        self.val_tree.column("#0", width=px(app, 150), minwidth=px(app, 120), stretch=False)
        for c in ("cam", "c1", "c2", "c3"):
            self.val_tree.column(c, width=px(app, 70), minwidth=px(app, 60), anchor="e", stretch=True)
        self.val_tree.heading("cam", text="Camera")
        self.val_tree.pack(fill=tk.BOTH, expand=True)
        self.foot = foot = ttk.Label(vf, style="Hint.TLabel", justify=tk.LEFT,
                                     text="~ = worked out from the flats (no clean bias frames); ≥ = at least (no "
                                          "bend seen before saturation).")
        foot.pack(anchor="w", fill=tk.X, pady=(px(app, 4), 0))
        vf.bind("<Configure>", lambda e: foot.configure(wraplength=max(150, e.width - px(app, 8))), add="+")

        pf = ttk.Frame(nb, style="Card.TFrame")
        self.pairs_tab = pf
        nb.add(pf, text="Pairs")
        prow = ttk.Frame(pf, style="Card.TFrame")
        prow.pack(fill=tk.X, pady=(px(app, 2), px(app, 4)))
        self.pair_ch = tk.StringVar(value="G")
        self.pair_label = ttk.Label(prow, text="Color", style="Card.TLabel")
        self.pair_pick = ttk.Combobox(prow, textvariable=self.pair_ch, state="readonly", width=5)
        self.pair_pick.bind("<<ComboboxSelected>>", lambda _e: self.show_pairs())
        self.pair_note = ttk.Label(prow, text="Off % = off the straight line; Used in = the fits a pair went into", style="Hint.TLabel")
        self.pair_note.pack(side=tk.RIGHT)
        tw = ttk.Frame(pf, style="Card.TFrame")
        tw.pack(fill=tk.BOTH, expand=True)
        cols = ("exp", "level", "var", "gainpt", "dev", "flag")
        self.pair_tree = ttk.Treeview(tw, columns=cols, show="headings", selectmode="none")
        for c, (head, w) in zip(cols, (("Exposure", 70), ("Level", 70), ("Variance", 75), ("Sig/var", 60),
                                       ("Off %", 55), ("Used in", 90))):
            self.pair_tree.heading(c, text=head)
            self.pair_tree.column(c, width=px(app, w), minwidth=px(app, 45), anchor="e" if c != "flag" else "w",
                                  stretch=True)
        pv = ttk.Scrollbar(tw, orient=tk.VERTICAL, command=self.pair_tree.yview)
        self.pair_tree.configure(yscrollcommand=pv.set)
        pv.pack(side=tk.RIGHT, fill=tk.Y)
        self.pair_tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._update_clear()
        self.plan_btn.configure(state="normal" if self.kind == "bench" else "disabled")

    # ---- inputs
    def refresh(self):
        pass

    @property
    def kind(self) -> str:
        return dict(MEASURES).get(self.measure_var.get(), "bench")

    @property
    def current(self):
        return self.res if self.kind == "bench" else self.dres

    @property
    def run_key(self):
        return self.run_keys.get(self.kind)

    def _update_clear(self):
        state = "normal" if self.bias_var.get() else "disabled"
        try:
            self.clear_bias_btn.configure(state=state)
        except tk.TclError:
            pass

    def _start_dir(self, v: str) -> str | None:
        for d in (v, self.folder_var.get(), self.darks_var.get(), self.app.prefs.get("last_folder")):
            if d and os.path.isdir(d):
                return d
        return None

    def pick_folder(self):
        d = filedialog.askdirectory(parent=self.app, title="The folder of flat pairs",
                                    initialdir=self._start_dir(self.folder_var.get()))
        if d:
            self.folder_var.set(os.path.normpath(d))
            self.app.prefs["bench_folder"] = self.folder_var.get()
            settings.save(self.app.prefs)

    def pick_bias(self):
        d = filedialog.askdirectory(parent=self.app, title="The folder of bias frames (optional)",
                                    initialdir=self._start_dir(self.bias_var.get()))
        if d:
            self.bias_var.set(os.path.normpath(d))
            self.app.prefs["bench_bias_folder"] = self.bias_var.get()
            settings.save(self.app.prefs)
        self._update_clear()

    def pick_darks(self):
        d = filedialog.askdirectory(parent=self.app, title="The folder of darks (a dark library: subfolders too)",
                                    initialdir=self._start_dir(self.darks_var.get()))
        if d:
            self.darks_var.set(os.path.normpath(d))
            self.app.prefs["dark_folder"] = self.darks_var.get()
            settings.save(self.app.prefs)
            if self.kind != "dark":
                self.measure_var.set(MEASURES[1][0])
                self.measure_changed()

    def clear_bias(self):
        self.bias_var.set("")
        self.app.prefs["bench_bias_folder"] = ""
        settings.save(self.app.prefs)
        self._update_clear()

    def measure_changed(self):
        self.app.prefs["camera_measure"] = self.kind
        settings.save(self.app.prefs)
        self._say("")
        self.save_btn.configure(state="normal" if self.current is not None else "disabled")
        self.plan_btn.configure(state="normal" if self.kind == "bench" else "disabled")
        self.show_all()

    def _say(self, text: str):
        self.msg_var.set(text or "")
        try:
            self.msg.configure(style=style.status_style(style.kind_of(text)) if text else "Card.TLabel")
        except tk.TclError:
            pass

    # ---- the run
    def run(self, folder: str | None = None):
        kind = self.kind
        if kind == "dark":
            return self.run_darks(folder)
        folder = folder or self.folder_var.get()
        if not folder or not os.path.isdir(folder):
            self._say("⚠ Pick the folder of flat pairs first.")
            return
        bias_dir = self.bias_var.get() or None
        if bias_dir and not os.path.isdir(bias_dir):
            self._say("⚠ The bias folder is not there any more; clear it or pick it again.")
            return
        region = self._region()
        self._busy()

        def work(step):
            return bench.run_bench(folder, region, step=step, bias_folder=bias_dir)

        self.app.jobs.run(work, self.done, f"Bench run on {os.path.basename(folder)}")

    def run_darks(self, folder: str | None = None):
        folder = folder or self.darks_var.get()
        if not folder or not os.path.isdir(folder):
            self._say("⚠ Pick the folder of darks first (Darks folder…).")
            return
        bias_dir = self.bias_var.get() or None
        if bias_dir and not os.path.isdir(bias_dir):
            self._say("⚠ The bias folder is not there any more; clear it or pick it again.")
            return
        region = self._region()
        self._busy()

        def work(step):
            return darks.run_darks(folder, region, step=step, bias_folder=bias_dir)

        self.app.jobs.run(work, self.done_darks, f"Dark run on {os.path.basename(folder)}")

    def _region(self) -> str:
        region = self.region_var.get() if self.region_var.get() in bench.REGIONS else bench.DEFAULT_REGION
        self.app.prefs["bench_region"] = region
        settings.save(self.app.prefs)
        return region

    def _busy(self):
        self._say("")
        self.run_btn.configure(state="disabled")
        self.save_btn.configure(state="disabled")

    def _failed(self, what: str, exc):
        self.run_btn.configure(state="normal")
        self.save_btn.configure(state="normal" if self.current is not None else "disabled")
        known = (bench.BenchError, darks.DarkError, frames.FrameError)
        why = str(exc) if isinstance(exc, known) else f"{type(exc).__name__}: {exc}"
        self._say(f"✖ The {what} could not be done: {why}.")
        self.app.set_status(f"✖ {what.capitalize()} failed ({why}). Details are in {settings.log_path()}.")

    def done(self, res, exc):
        if exc is not None:
            return self._failed("bench run", exc)
        self.run_btn.configure(state="normal")
        self.res = res
        pid = self.camera_id(res)
        self.run_keys["bench"] = history.record_run(
            "bench", res.folder, res.summary, camera_id=pid, n_frames=2 * (res.n_flat_pairs + res.n_bias_pairs),
            status=res.status, app=settings.WRITTEN_BY, values=bench.run_values(res, pid))
        settings.log(f"bench run {res.folder} ({res.status}; run {self.run_keys['bench']}): gain {res.gain:.4f}")
        self._show_new("bench")
        g = f"gain {res.gain:.4f} e⁻/ADU"
        rn = f", read noise {res.read_noise_e:.2f} e⁻" if math.isfinite(res.read_noise_e) else ""
        self.app.set_status(f"Bench run: {g}{rn}; {res.n_flat_pairs} flat pairs, {res.n_bias_pairs} bias pairs."
                            + (f" Worst: {res.worst}." if res.worst else ""))

    def done_darks(self, res, exc):
        if exc is not None:
            return self._failed("dark run", exc)
        self.run_btn.configure(state="normal")
        self.dres = res
        pid = self.camera_id(res)
        n = res.n_bias + sum(tr.n_darks for tr in res.temps.values())
        self.run_keys["dark"] = history.record_run(
            "darks", res.folder, res.summary, camera_id=pid, n_frames=n, status=res.status, app=settings.WRITTEN_BY,
            values=darks.run_values(res, pid))
        tr = res.main
        settings.log(f"dark run {res.folder} ({res.status}; run {self.run_keys['dark']}): "
                     f"{darks.rate_text(tr.rate)} e-/px/s{' (at most)' if tr.upper_limit else ''}")
        self._show_new("dark")
        where = "" if math.isnan(tr.temp_c) else f" at {tr.temp_c:g} °C"
        val = (f"at most {darks.rate_text(tr.rate)}" if tr.upper_limit else
               f"{darks.rate_text(tr.rate)} ± {darks.rate_text(tr.sigma)}")
        self.app.set_status(f"Dark run: dark current{where} {val} e⁻/px/s; {tr.n_darks} darks, {res.n_bias} bias "
                            "frames." + (f" Worst: {res.worst}." if res.worst else ""))

    def _show_new(self, kind: str):
        if self.kind != kind:
            self.measure_var.set(next(w for w, k in MEASURES if k == kind))
        self.picture_var.set("run")
        self.save_btn.configure(state="normal")
        self.plan_btn.configure(state="normal" if kind == "bench" else "disabled")
        try:
            self.show_all()
        except Exception as exc:                          # the run is measured and saveable even if a view fails
            import traceback
            settings.log(f"showing {kind} run {self.run_key} failed: {exc!r}\n{traceback.format_exc()}")
            self._say(f"⚠ The run finished, but part of the page could not be drawn ({exc}). Details are in "
                      f"{settings.log_path()}.")

    def camera_id(self, res=None) -> str:
        res = res if res is not None else self.current
        s = res.setup if res is not None else {}
        if s.get("instrume"):
            return s.get("camera_id")
        name = (self.app.prefs.get("header_answers") or {}).get("INSTRUME")
        return sp.camera_id(name, s.get("gain_setting"), s.get("offset"), s.get("binning"))

    # ---- showing a run
    def show_all(self):
        self.show_lines()
        self.show_values()
        self.show_pairs()
        self.redraw()

    def show_lines(self):
        t = self.text
        t.configure(state="normal")
        t.delete("1.0", "end")
        cur = self.current
        if cur is not None:
            order = {"bad": 0, "warn": 1, "ok": 2, "info": 3}
            for i, (kind, line) in enumerate(sorted(cur.lines, key=lambda kl: order.get(kl[0], 3))):
                t.insert("end", line + "\n", (kind, "gap") if i else (kind,))
        t.configure(state="disabled")

    def show_values(self):
        tr = self.val_tree
        tr.delete(*tr.get_children())
        cur = self.current
        if cur is None:
            return
        if self.kind == "dark":
            return self._dark_values(cur)
        res = cur
        chans = list(res.channels)
        cols = ("c1", "c2", "c3")
        for c, col in zip(cols, chans + [""] * 3):
            tr.heading(c, text=col)
        rows = [
            ("Gain (e⁻/ADU)", _fmt(res.gain, 4), [_fmt(res.channels[c].gain, 4) for c in chans]),
            ("  ± (e⁻/ADU)", _fmt(res.gain_sigma, 4), [_fmt(res.channels[c].gain_sigma, 4) for c in chans]),
            ("Read noise (e⁻)", _fmt(res.read_noise_e, 2), [_fmt(res.channels[c].read_noise_e, 2) for c in chans]),
            ("Bias level (ADU)", _fmt(res.bias_adu, 1), [_fmt(res.channels[c].bias_adu, 1) +
                                                          (" ~" if res.channels[c].bias_source == "derived" else "")
                                                          for c in chans]),
            ("Full well (e⁻)", _fmt(res.full_well_e), [_fmt(res.channels[c].full_well_e) for c in chans]),
            ("Saturation (ADU)", _fmt(res.saturation_adu), ["" for _ in chans]),
            ("Linear limit (ADU)", ("≥ " if res.linear_is_bound else "") + _fmt(res.linear_limit_adu),
             [("≥ " if res.channels[c].linear_is_bound else "") + _fmt(res.channels[c].linear_limit_adu)
              for c in chans]),
            ("Signal rate (ADU/s)", "", [_fmt(res.channels[c].rate) for c in chans]),
            ("Timing offset (ms)", _fmt(1000 * res.t_offset if math.isfinite(res.t_offset) else float("nan"), 1),
             [_fmt(1000 * res.channels[c].t_offset if math.isfinite(res.channels[c].t_offset) else float("nan"), 1)
              for c in chans]),
            ("Pairs in the gain fit", "", [str(res.channels[c].n_gain) for c in chans]),
        ]
        for label, cam, per in rows:
            tr.insert("", "end", text=label, values=(cam, *(per + ["", "", ""])[:3]))
        self.foot.configure(text="~ = worked out from the flats (no clean bias frames); ≥ = at least (no bend seen "
                                 "before saturation).")

    def _dark_values(self, res):
        tree = self.val_tree
        tr = res.main
        chans = list(tr.channels) if tr is not None else []
        for c, col in zip(("c1", "c2", "c3"), chans + [""] * 3):
            tree.heading(c, text=col)
        rt = darks.rate_text

        def per(f):
            return [f(tr.channels[c]) for c in chans]
        rows = [("Dark current", ("≤ " if tr.upper_limit else "") + rt(tr.rate), per(lambda ch: rt(ch.rate))),
                ("  ±", "" if tr.upper_limit else rt(tr.sigma), per(lambda ch: rt(ch.sigma))),
                ("  level way", "", per(lambda ch: rt(ch.mean_rate))),
                ("  noise way", "", per(lambda ch: rt(ch.noise_rate))),
                ("Spec", rt(tr.spec) if tr.spec else "—", []),
                ("Sensor (°C)", "—" if math.isnan(tr.temp_c) else f"{tr.temp_c:g}", []),
                ("Read noise (e⁻)", _fmt(res.read_noise_e, 2), per(lambda ch: _fmt(ch.read_noise_e, 2))),
                ("Bias (ADU)", "", [_fmt(res.bias_adu.get(c), 1) for c in chans]),
                ("Hot pixels / million", _fmt(tr.hot_ppm), []),
                ("Edge glow", rt(tr.glow_e_s) if tr.glow_seen(max(s.exptime for s in tr.exposures)) else "none", []),
                ("Darks read", str(tr.n_darks), []),
                ("Gain (e⁻/ADU)", _fmt(res.gain, 4), per(lambda ch: _fmt(ch.gain, 4)))]
        if len(res.temps) > 1:
            for g, t in sorted(res.temps.items()):
                if g != res.main_temp:
                    rows.append((f"Dark current at {g:g} °C", ("≤ " if t.upper_limit else "") + rt(t.rate), []))
            if math.isfinite(res.doubling_c):
                rows.append(("Doubles every (°C)", f"{res.doubling_c:.1f}", []))
        for label, cam, p in rows:
            tree.insert("", "end", text=label, values=(cam, *(p + ["", "", ""])[:3]))
        self.foot.configure(text="Dark current and glow in e⁻/px/s. ≤ = at most: too small to measure (under twice "
                                 "its uncertainty). The level way and the noise way are independent; the more precise "
                                 "one is used.")

    def show_pairs(self):
        if self.kind == "dark":
            return self._show_exposures()
        self.nb.tab(self.pairs_tab, text="Pairs")
        self._columns(("exp", "level", "var", "gainpt", "dev", "flag"),
                      (("Exposure", 70), ("Level", 70), ("Variance", 75), ("Sig/var", 60), ("Off %", 55),
                       ("Used in", 90)))
        self.pair_note.configure(text="Off % = off the straight line; Used in = the fits a pair went into")
        res = self.res
        tr = self.pair_tree
        tr.delete(*tr.get_children())
        if res is None:
            return
        chans = list(res.channels)
        self._color_pick(chans)
        ch = res.channels[self.pair_ch.get()]
        for p in sorted(ch.pairs, key=lambda p: (p.exptime, p.order)):
            flag = "saturated" if p.saturated else " + ".join(
                x for x in ("gain" if p.used_gain else "", "line" if p.used_lin else "",
                            f"(differ {100 * (p.ratio - 1):+.1f} %)" if math.isfinite(p.ratio)
                            and abs(p.ratio - 1) > bench.PAIR_RATIO_TOL else "") if x)
            exp = f"{p.exptime * 1000:g} ms" if p.exptime < 1 else f"{p.exptime:g} s"
            tr.insert("", "end", values=(exp, _fmt(p.mean_raw), _fmt(p.var) if p.var > 0 else "—",
                                         _fmt(p.signal / p.var, 4) if p.var > 0 and not p.saturated else "—",
                                         f"{round(p.deviation_pct, 2) + 0.0:+.2f}" if not p.saturated and
                                         math.isfinite(p.deviation_pct) else "—", flag))

    def _columns(self, cols, heads):
        tr = self.pair_tree
        if tuple(tr["columns"]) == tuple(cols):
            return
        tr.configure(columns=cols)
        for c, (head, w) in zip(cols, heads):
            tr.heading(c, text=head)
            tr.column(c, width=px(self.app, w), minwidth=px(self.app, 45), anchor="w" if c in ("flag", "when")
                      else "e", stretch=True)

    def _color_pick(self, chans):
        self.pair_pick.configure(values=chans)
        if self.pair_ch.get() not in chans:
            self.pair_ch.set("G" if "G" in chans else chans[0])
        if len(chans) > 1 and not self.pair_pick.winfo_manager():
            self.pair_label.pack(side=tk.LEFT)
            self.pair_pick.pack(side=tk.LEFT, padx=(px(self.app, 6), 0))
        elif len(chans) == 1 and self.pair_pick.winfo_manager():
            self.pair_label.pack_forget()
            self.pair_pick.pack_forget()

    def _show_exposures(self):
        self.nb.tab(self.pairs_tab, text="Exposures")
        self._columns(("exp", "n", "level", "se", "var", "temp", "when"),
                      (("Exposure", 72), ("Frames", 58), ("Level", 70), ("±", 50), ("Var e⁻²", 64),
                       ("°C", 44), ("Shot", 120)))
        self.pair_note.configure(text="Level in ADU; Var from one pair")
        tree = self.pair_tree
        tree.delete(*tree.get_children())
        res = self.dres
        if res is None or res.main is None:
            return
        tr = res.main
        chans = list(tr.channels)
        self._color_pick(chans)
        c = self.pair_ch.get()
        ch = tr.channels.get(c)
        g2 = (ch.gain if ch else res.gain) ** 2
        if c in res.bias_adu:
            tree.insert("", "end", values=("bias", str(res.n_bias), _fmt(res.bias_adu[c], 2),
                                           _fmt(res.bias_se.get(c), 3), _fmt(res.bias_var.get(c, float("nan")) * g2, 3)
                                           if c in res.bias_var else "—", "", " – ".join(dict.fromkeys(res.bias_dates))))
        for st in tr.exposures:
            tree.insert("", "end", values=(darks._exp(st.exptime), f"{st.n} of {st.n_found}" if st.n_found > st.n
                                           else str(st.n), _fmt(st.level.get(c), 2), _fmt(st.level_se.get(c), 3),
                                           _fmt(st.var[c] * g2, 3) if c in st.var else "—",
                                           "" if st.temp is None else f"{st.temp:.1f}",
                                           " – ".join(dict.fromkeys(st.dates))))

    def redraw(self):
        cur = self.current
        if self.picture_var.get() != "spec" and cur is None:
            return
        self._figure()
        if self.picture_var.get() == "spec":
            import spec_plots
            import instrument
            setup = (cur.setup if cur is not None else None) or (self.res.setup if self.res else None) or \
                (self.dres.setup if self.dres else {})
            inst = setup.get("instrume") or (self.app.prefs.get("header_answers") or {}).get("INSTRUME")
            spec = sp.find_spec(inst, (instrument.BUILTIN_SPECS,))
            spec_plots.draw(self.fig, spec, spec_plots.gather(spec, inst, self.res, self.dres))
        elif self.kind == "dark":
            import dark_plots
            dark_plots.draw(self.fig, cur)
        else:
            import bench_plots
            bench_plots.draw(self.fig, cur)
        self.canvas.draw_idle()

    def draw(self):
        self.redraw()

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
        """Plots on the left ~60 %, findings on the right."""
        try:
            self.pane.update_idletasks()
            w = self.pane.winfo_width()
            if w > 100:
                self.pane.sashpos(0, int(w * 0.6))
        except tk.TclError:
            pass

    # ---- saving
    def save(self):
        res = self.current
        if res is None:
            return
        name = None
        if not res.setup.get("instrume"):
            name = (self.app.prefs.get("header_answers") or {}).get("INSTRUME")
            if not name:
                name = simpledialog.askstring(settings.APP_NAME, "The frames' header does not name the camera "
                                              "(INSTRUME). What is it called? (asked once; used for frames without "
                                              "it)", parent=self.app)
                if not name or not name.strip():
                    return
                name = name.strip()
                self.app.prefs.setdefault("header_answers", {})["INSTRUME"] = name
                settings.save(self.app.prefs)
        pid = self.camera_id(res)
        old = sp.find_camera_profile(pid)
        path = sp.profile_path("cameras", pid, make=False)
        if old is None and os.path.exists(path):
            self._say(f"✖ The camera profile {pid} exists but cannot be read, so it was not replaced. Fix or move "
                      f"{path} first (Profiles page shows why).")
            return
        mod = darks if self.kind == "dark" else bench
        changes = mod.changes_for(res, old)
        if not changes:
            self._say("⚠ This run measured nothing that goes in the profile.")
            return
        SaveDialog(self, pid, old, changes, name, upper=self.kind == "dark" and res.main is not None
                   and res.main.upper_limit)

    def do_save(self, pid: str, old: dict | None, keep_user: bool, name: str | None):
        res = self.current
        mod = darks if self.kind == "dark" else bench
        doc = mod.apply_to_profile(res, old, settings.WRITTEN_BY, run_key=self.run_key, keep_user=keep_user,
                                   instrume_fallback=name)
        doc["profile_id"] = pid
        try:
            path = sp.save_profile(doc, settings.WRITTEN_BY)
        except (sp.ProfileError, OSError) as exc:
            self._say(f"✖ The profile could not be saved ({exc}).")
            return
        settings.log(f"{'dark' if mod is darks else 'bench'} run {self.run_key} saved to {path}")
        self._say(f"✔ Saved to camera profile {pid}" + (" (the previous revision is in the history folder)."
                                                         if old else "."))
        self.app.set_status(f"✔ Camera profile {pid} saved.")
        if self.picture_var.get() == "spec":
            self.redraw()

    # ---- the shooting plan
    def plan(self):
        PlanDialog(self)


class SaveDialog(tk.Toplevel):
    """What saving changes, value by value; keep your own typed values or not; Save."""

    LABELS = {"gain_e_per_adu": ("Gain", "e⁻/ADU", 4), "read_noise_e": ("Read noise", "e⁻", 2),
              "full_well_e": ("Full well", "e⁻", 0), "saturation_adu": ("Saturation", "ADU", 0),
              "linear_limit_adu": ("Linear limit", "ADU", 0), "bias_level_adu": ("Bias level", "ADU", 1),
              "exposure_offset_s": ("Timing offset", "s", 4),
              "dark_current_e_per_px_s": ("Dark current", "e⁻/px/s", None),
              "hot_pixel_ppm": ("Hot pixels", "per million", 0), "glow_e_per_px_s": ("Edge glow", "e⁻/px/s", None)}

    def __init__(self, page: CameraPage, pid: str, old, changes, name, upper: bool = False):
        super().__init__(page.app)
        self.page, self.pid, self.old, self.name = page, pid, old, name
        app = page.app
        self.title("Save to camera profile")
        self.configure(bg=style.CARD)
        self.transient(app)
        body = ttk.Frame(self, style="Card.TFrame", padding=px(app, 14))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text=f"Camera profile {pid}", style="Section.TLabel").pack(anchor="w")
        ttk.Label(body, text=("It exists: the measured values below replace the old ones (the old revision is "
                              "kept in the history folder)." if old else
                              "There is no profile for this camera setting yet: a new one is made."),
                  style="Hint.TLabel", wraplength=px(app, 520), justify=tk.LEFT).pack(anchor="w", pady=(0, px(app, 6)))
        tr = ttk.Treeview(body, columns=("old", "new"), show="tree headings", height=len(changes) or 1,
                          selectmode="none")
        tr.heading("#0", text="Value")
        tr.heading("old", text="Now")
        tr.heading("new", text="Measured")
        tr.column("#0", width=px(app, 140))
        tr.column("old", width=px(app, 200), anchor="e")
        tr.column("new", width=px(app, 140), anchor="e")
        self.has_user = False
        for key, ov, osrc, nv in changes:
            label, unit, nd = self.LABELS.get(key, (key, "", 3))

            def num(v):
                return darks.rate_text(v) if nd is None else _fmt(v, nd)
            o = "—" if ov is None else f"{num(ov)} ({osrc if osrc != 'user' else 'typed by you'})"
            if osrc == "user":
                self.has_user = True
            at_most = "at most " if upper and key == "dark_current_e_per_px_s" else ""
            tr.insert("", "end", text=label, values=(o, f"{at_most}{num(nv)} {unit}"))
        tr.pack(fill=tk.X)
        if upper:
            ttk.Label(body, text="The dark current was too small to measure: the profile gets its upper limit (at "
                                 "most), so SHOBS-P and SHOBS-SCHED never count on less dark noise than there may be.",
                      style="Hint.TLabel", wraplength=px(app, 520), justify=tk.LEFT).pack(anchor="w",
                                                                                         pady=(px(app, 6), 0))
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
        self.page.do_save(self.pid, self.old, keep, self.name)


class PlanDialog(tk.Toplevel):
    """A flat-pair ladder for the panel, from this run's brightness or from one test flat."""

    def __init__(self, page: CameraPage):
        super().__init__(page.app)
        self.page = page
        app = page.app
        self.title("Bench ladder: shooting plan")
        self.configure(bg=style.CARD)
        self.transient(app)
        body = ttk.Frame(self, style="Card.TFrame", padding=px(app, 14))
        body.pack(fill=tk.BOTH, expand=True)
        row = ttk.Frame(body, style="Card.TFrame")
        row.pack(fill=tk.X)
        self.src = tk.StringVar(value="run" if page.res is not None else "flat")
        ttk.Radiobutton(row, text="From this bench run", value="run", variable=self.src, style="Card.TRadiobutton",
                        command=self.make, state="normal" if page.res is not None else "disabled").pack(side=tk.LEFT)
        ttk.Radiobutton(row, text="From one test flat…", value="flat", variable=self.src, style="Card.TRadiobutton",
                        command=self.pick_flat).pack(side=tk.LEFT, padx=(px(app, 12), 0))
        self.text = tk.Text(body, wrap="word", font=style.font(10), width=78, height=22, relief="flat",
                            background="#ffffff", foreground=style.TEXT, padx=px(app, 6), pady=px(app, 6))
        self.text.pack(fill=tk.BOTH, expand=True, pady=(px(app, 8), 0))
        btns = ttk.Frame(body, style="Card.TFrame")
        btns.pack(fill=tk.X, pady=(px(app, 10), 0))
        ttk.Button(btns, text="Close", command=self.destroy).pack(side=tk.RIGHT)
        style.green_button(btns, "Save as text…", self.save).pack(side=tk.RIGHT, padx=(0, px(app, 8)))
        style.green_button(btns, "Copy", self.copy).pack(side=tk.RIGHT, padx=(0, px(app, 8)))
        self.msg = tk.StringVar()
        ttk.Label(btns, textvariable=self.msg, style="Card.TLabel").pack(side=tk.LEFT)
        self.flat_path = None
        self.make()

    def _set(self, text):
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        self.text.insert("1.0", text)

    def pick_flat(self):
        path = filedialog.askopenfilename(
            parent=self, title="One test flat (any exposure, below saturation)",
            initialdir=self.page._start_dir(self.page.folder_var.get()),
            filetypes=[("FITS or XISF frames", "*.fit *.fits *.fts *.xisf *.FIT *.FITS *.FTS *.XISF")])
        if path:
            self.flat_path = path
        self.make()

    def make(self):
        page = self.page
        try:
            if self.src.get() == "run" and page.res is not None:
                res = page.res
                rates = {c: ch.rate for c, ch in res.channels.items() if math.isfinite(ch.rate)}
                finite = [ch.bias_adu for ch in res.channels.values() if math.isfinite(ch.bias_adu)]
                off = res.setup.get("offset")
                bias = res.bias_adu if math.isfinite(res.bias_adu) else (
                    min(finite) if finite else (10.0 * off if off else 0.0))
                sat = res.saturation_adu if math.isfinite(res.saturation_adu) else 65535.0
                s = res.setup
                head = (f"From the bench run on {os.path.basename(res.folder)} (signal rates per second: " +
                        ", ".join(f"{c} {r:,.0f}" for c, r in rates.items()) + " ADU).\n\n")
            elif self.flat_path:
                rates, bias, t = bench.rates_from_test_flat(self.flat_path)
                sat = 65535.0
                s = sp.setup_key(frames.read_header(self.flat_path))
                head = (f"From the test flat {os.path.basename(self.flat_path)} ({t:g} s; bias taken as {bias:,.0f} "
                        "ADU from the OFFSET).\n\n")
            else:
                self._set("Pick one test flat: any exposure that is well below saturation (for example 1 s). The "
                          "plan scales the exposures to your panel's brightness.")
                return
            plan = bench.plan_ladder(rates, bias, sat)
            self._set(head + bench.plan_text(plan, s.get("gain_setting"), s.get("offset"), s.get("set_temp_c")))
        except (bench.BenchError, frames.FrameError, OSError, ValueError) as exc:
            self._set(f"✖ No plan: {exc}.")

    def copy(self):
        self.clipboard_clear()
        self.clipboard_append(self.text.get("1.0", "end").strip())
        self.msg.set("✔ Copied.")

    def save(self):
        path = filedialog.asksaveasfilename(parent=self, defaultextension=".txt", initialfile="FLUX ladder plan.txt",
                                            filetypes=[("Text", "*.txt")])
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(self.text.get("1.0", "end").strip() + "\n")
        except OSError as exc:
            self.msg.set(f"✖ Not saved ({exc.strerror or exc}).")
            return
        self.msg.set(f"✔ Saved {os.path.basename(path)}.")
