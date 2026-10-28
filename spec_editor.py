"""Spec curves…: look at, type, import and export a camera's manufacturer curves (the spec file).

A spec file (format v1, ~/.shobs/specs/<model>.json) holds what the maker's charts say against the gain setting
(gain in e⁻/ADU, read noise, full well, dynamic range) and the dark current against the sensor temperature. FLUX
uses it for values nothing has measured yet ('spec' in the source column) and draws it under the measured points.

The dialog lists your spec files and the ones FLUX ships with. Pick a curve, and its points show in a list and a
plot (with what you measured on it); add, change or delete points by hand, then Save. A built-in spec you change is
saved as your own copy, which FLUX then uses in its place. Import… takes a spec file someone shares (it is checked
first); Export… writes the one on screen to a file to share. Two points at the same gain setting make a jump (a
camera switching to its high-conversion-gain mode): the second one applies from that setting up."""
from __future__ import annotations

import copy
import math
import os
import tkinter as tk
from tkinter import filedialog, messagebox, simpledialog, ttk

import settings
import shobs_profile as sp
import style
from style import px

CURVES = (("gain_e_per_adu", "Gain (e⁻/ADU) by gain setting", "Gain setting", "e⁻/ADU", True),
          ("read_noise_e", "Read noise (e⁻) by gain setting", "Gain setting", "e⁻", False),
          ("full_well_e", "Full well (e⁻) by gain setting", "Gain setting", "e⁻", True),
          ("dynamic_range_stops", "Dynamic range (stops) by gain setting", "Gain setting", "stops", False),
          ("dark", "Dark current (e⁻/px/s) by sensor temperature", "Sensor temperature (°C)", "e⁻/px/s", True))
CURVE_BY_WORD = {w: (k, xl, unit, pos) for k, w, xl, unit, pos in CURVES}


def builtin_dir() -> str:
    import instrument
    return instrument.BUILTIN_SPECS


def list_specs() -> list[tuple[str, str, dict]]:
    """[(label, path, doc)]: your spec files first, then the built-in ones (a built-in one you have your own copy of
    says so)."""
    out = []
    mine = {}
    for path in sp.list_profiles("specs"):
        try:
            doc = sp.load(path)
        except sp.ProfileError:
            continue
        if doc.get("format") != sp.SPEC_FORMAT:
            continue
        sid = doc.get("spec_id") or sp.slug(doc.get("model"))
        mine[sid] = path
        out.append((f"{doc.get('model') or sid} (yours)", path, doc))
    d = builtin_dir()
    if os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(d, name)
            try:
                doc = sp.load(path)
            except sp.ProfileError:
                continue
            sid = doc.get("spec_id") or sp.slug(doc.get("model"))
            word = "built in; your copy is used" if sid in mine else "built in"
            out.append((f"{doc.get('model') or sid} ({word})", path, doc))
    return out


def get_points(doc: dict, key: str) -> list:
    if key == "dark":
        return list(((doc.get("dark_current_curve") or {}).get("points")) or [])
    return list((doc.get("curves") or {}).get(key) or [])


def set_points(doc: dict, key: str, pts: list) -> None:
    pts = [[_num(x), _num(y)] for x, y in pts]
    if key == "dark":
        dc = doc.get("dark_current_curve") if isinstance(doc.get("dark_current_curve"), dict) else {}
        dc.setdefault("x", "sensor_temp_c")
        dc.setdefault("unit", "e-/px/s")
        dc["points"] = pts
        doc["dark_current_curve"] = dc
    else:
        cv = doc.get("curves") if isinstance(doc.get("curves"), dict) else {}
        cv.setdefault("x", "gain_setting")
        if pts:
            cv[key] = pts
        else:
            cv.pop(key, None)
        doc["curves"] = cv


def _num(v):
    f = float(v)
    return int(f) if f == int(f) and abs(f) < 1e6 else f


def new_spec(model: str, instrume: str | None = None) -> dict:
    return {"format": sp.SPEC_FORMAT, "format_version": sp.FORMAT_VERSION, "spec_id": sp.slug(model),
            "model": model, "matches_instrume": [m for m in dict.fromkeys([instrume or model, model]) if m],
            "source": "typed by hand from the maker's charts", "entered": sp.now_iso()[:10],
            "curves": {"x": "gain_setting"},
            "dark_current_curve": {"x": "sensor_temp_c", "unit": "e-/px/s", "points": []}}


def check_spec(doc: dict) -> list[str]:
    """Plain-words problems with a spec ([] = fine): format, curves of [x, y] numbers, positive values on the
    log-scale curves."""
    probs = list(sp.validate(doc))
    if doc.get("format") != sp.SPEC_FORMAT:
        return probs or ["it is not a camera spec file"]
    if not str(doc.get("model") or "").strip():
        probs.append("it has no model name")
    for key, word, _xl, _u, positive in CURVES:
        for p in get_points(doc, key):
            if not (isinstance(p, (list, tuple)) and len(p) == 2 and all(sp._finite(v) is not None for v in p)):
                probs.append(f"{word}: a point is not two numbers")
                break
            if positive and float(p[1]) <= 0:
                probs.append(f"{word}: values must be above zero")
                break
    return probs


class SpecDialog(tk.Toplevel):
    def __init__(self, app, select_instrume: str | None = None):
        super().__init__(app)
        self.app = app
        self.title("Spec curves")
        self.configure(bg=style.CARD)
        self.transient(app)
        self.doc = None              # the spec on screen (a copy, edited in memory until Save)
        self.path = None
        self.dirty = False
        self.entries: list = []
        body = ttk.Frame(self, style="Card.TFrame", padding=px(app, 14))
        body.pack(fill=tk.BOTH, expand=True)

        top = ttk.Frame(body, style="Card.TFrame")
        top.pack(fill=tk.X)
        ttk.Label(top, text="Spec", style="Card.TLabel").pack(side=tk.LEFT)
        self.pick_var = tk.StringVar()
        self.pick = ttk.Combobox(top, textvariable=self.pick_var, state="readonly", width=52)
        self.pick.pack(side=tk.LEFT, padx=px(app, 8))
        self.pick.bind("<<ComboboxSelected>>", lambda _e: self.choose())
        ttk.Button(top, text="New…", command=self.new).pack(side=tk.LEFT)
        ttk.Button(top, text="Import…", command=self.import_file).pack(side=tk.LEFT, padx=(px(app, 6), 0))
        ttk.Button(top, text="Export…", command=self.export_file).pack(side=tk.LEFT, padx=(px(app, 6), 0))

        meta = ttk.Frame(body, style="Card.TFrame")
        meta.pack(fill=tk.X, pady=(px(app, 8), 0))
        meta.columnconfigure(1, weight=1)
        self.model_var, self.match_var, self.source_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        for i, (label, var, hint) in enumerate((
                ("Model", self.model_var, ""),
                ("Cameras it fits", self.match_var, "INSTRUME names, separated by commas"),
                ("Where the numbers come from", self.source_var, ""))):
            ttk.Label(meta, text=label, style="Card.TLabel").grid(row=i, column=0, sticky="w", pady=px(app, 2))
            e = ttk.Entry(meta, textvariable=var)
            e.grid(row=i, column=1, sticky="we", padx=px(app, 8), pady=px(app, 2))
            var.trace_add("write", lambda *_a: self._meta_changed())
            if hint:
                ttk.Label(meta, text=hint, style="Hint.TLabel").grid(row=i, column=2, sticky="w")

        crow = ttk.Frame(body, style="Card.TFrame")
        crow.pack(fill=tk.X, pady=(px(app, 8), 0))
        ttk.Label(crow, text="Curve", style="Card.TLabel").pack(side=tk.LEFT)
        self.curve_var = tk.StringVar(value=CURVES[0][1])
        self.curve = ttk.Combobox(crow, textvariable=self.curve_var, state="readonly", width=44,
                                  values=[w for _k, w, *_r in CURVES])
        self.curve.pack(side=tk.LEFT, padx=px(app, 8))
        self.curve.bind("<<ComboboxSelected>>", lambda _e: self.show_curve())

        pane = ttk.Frame(body, style="Card.TFrame")
        pane.pack(fill=tk.BOTH, expand=True, pady=(px(app, 8), 0))
        left = ttk.Frame(pane, style="Card.TFrame")
        left.pack(side=tk.LEFT, fill=tk.Y)
        tw = ttk.Frame(left, style="Card.TFrame")
        tw.pack(fill=tk.BOTH, expand=True)
        self.tree = ttk.Treeview(tw, columns=("x", "y"), show="headings", height=12, selectmode="browse")
        self.tree.heading("x", text="Gain setting")
        self.tree.heading("y", text="Value")
        self.tree.column("x", width=px(app, 110), anchor="e")
        self.tree.column("y", width=px(app, 110), anchor="e")
        sb = ttk.Scrollbar(tw, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        sb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._fill_entries())
        ed = ttk.Frame(left, style="Card.TFrame")
        ed.pack(fill=tk.X, pady=(px(app, 6), 0))
        self.x_var, self.y_var = tk.StringVar(), tk.StringVar()
        self.x_label = ttk.Label(ed, text="Gain setting", style="Card.TLabel")
        self.x_label.grid(row=0, column=0, sticky="w")
        ttk.Entry(ed, textvariable=self.x_var, width=10).grid(row=0, column=1, padx=px(app, 4))
        self.y_label = ttk.Label(ed, text="Value", style="Card.TLabel")
        self.y_label.grid(row=1, column=0, sticky="w")
        ttk.Entry(ed, textvariable=self.y_var, width=10).grid(row=1, column=1, padx=px(app, 4), pady=px(app, 2))
        bb = ttk.Frame(left, style="Card.TFrame")
        bb.pack(fill=tk.X, pady=(px(app, 4), 0))
        ttk.Button(bb, text="Add", command=self.add_point).pack(side=tk.LEFT)
        ttk.Button(bb, text="Change", command=self.change_point).pack(side=tk.LEFT, padx=(px(app, 4), 0))
        ttk.Button(bb, text="Delete", command=self.delete_point).pack(side=tk.LEFT, padx=(px(app, 4), 0))
        hint = ttk.Label(left, style="Hint.TLabel", wraplength=px(app, 250), justify=tk.LEFT,
                         text="Two points at the same gain setting make a jump (a camera switching to high "
                              "conversion gain): the second applies from there up.")
        hint.pack(anchor="w", pady=(px(app, 6), 0))

        from matplotlib.figure import Figure
        from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
        self.fig = Figure(figsize=(5.6, 3.8), dpi=100)
        self.canvas = FigureCanvasTkAgg(self.fig, master=pane)
        self.canvas.get_tk_widget().pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(px(app, 12), 0))

        btns = ttk.Frame(body, style="Card.TFrame")
        btns.pack(fill=tk.X, pady=(px(app, 10), 0))
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(btns, textvariable=self.msg_var, style="Card.TLabel")
        self.msg.pack(side=tk.LEFT)
        ttk.Button(btns, text="Close", command=self.close).pack(side=tk.RIGHT)
        self.save_btn = style.green_button(btns, "Save", self.save)
        self.save_btn.pack(side=tk.RIGHT, padx=(0, px(app, 8)))
        self.protocol("WM_DELETE_WINDOW", self.close)
        self.refresh(select_instrume=select_instrume)

    # ---- the list
    def refresh(self, select_path: str | None = None, select_instrume: str | None = None):
        self.items = list_specs()
        labels = [lab for lab, _p, _d in self.items]
        self.pick.configure(values=labels)
        pick = None
        if select_path:
            pick = next((lab for lab, p, _d in self.items if os.path.normcase(p) == os.path.normcase(select_path)),
                        None)
        if pick is None and select_instrume:
            want = sp.slug(select_instrume)
            pick = next((lab for lab, _p, d in self.items
                         if any(sp.slug(m) == want for m in d.get("matches_instrume") or [])), None)
        if pick is None and labels:
            pick = self.pick_var.get() if self.pick_var.get() in labels else labels[0]
        self.pick_var.set(pick or "")
        self.choose(force=True)

    def _say(self, text):
        self.msg_var.set(text or "")
        try:
            self.msg.configure(style=style.status_style(style.kind_of(text)) if text else "Card.TLabel")
        except tk.TclError:
            pass

    def _ask_discard(self) -> bool:
        if not self.dirty or self.app.testing:
            return True
        return messagebox.askyesno("Spec curves", "Leave the changes on screen unsaved?", parent=self)

    def choose(self, force: bool = False):
        item = next(((p, d) for lab, p, d in self.items if lab == self.pick_var.get()), None)
        if not force and not self._ask_discard():
            return
        self.doc = copy.deepcopy(item[1]) if item else None
        self.path = item[0] if item else None
        self.dirty = False
        self._loading = True
        d = self.doc or {}
        self.model_var.set(d.get("model") or "")
        self.match_var.set(", ".join(d.get("matches_instrume") or []))
        self.source_var.set(d.get("source") or "")
        self._loading = False
        self.dirty = False
        self.show_curve()
        if self.doc is None:
            self._say("No spec files yet. New… types one from the maker's charts; Import… takes a shared one.")

    def _meta_changed(self):
        if getattr(self, "_loading", False) or self.doc is None:
            return
        self.doc["model"] = self.model_var.get().strip()
        self.doc["matches_instrume"] = [m.strip() for m in self.match_var.get().split(",") if m.strip()]
        self.doc["source"] = self.source_var.get().strip()
        self.dirty = True

    # ---- one curve
    @property
    def curve_key(self):
        return CURVE_BY_WORD.get(self.curve_var.get(), CURVE_BY_WORD[CURVES[0][1]])

    def show_curve(self):
        key, xl, unit, _pos = self.curve_key
        self.tree.heading("x", text="°C" if key == "dark" else xl)
        self.tree.heading("y", text=unit)
        self.x_label.configure(text=xl)
        self.y_label.configure(text=unit)
        self.tree.delete(*self.tree.get_children())
        if self.doc is not None:
            for i, (x, y) in enumerate(get_points(self.doc, key)):
                self.tree.insert("", "end", iid=str(i), values=(f"{x:g}", f"{y:g}"))
        self.draw()

    def draw(self):
        import spec_plots
        key, xl, unit, pos = self.curve_key
        self.fig.clear()
        ax = self.fig.add_subplot(111)
        pts = []
        if self.doc is not None:
            try:
                got = spec_plots.gather(self.doc, (self.doc.get("matches_instrume") or [None])[0])
                pts = got.get("dark" if key == "dark" else key, []) if key != "dynamic_range_stops" else []
            except Exception:
                pts = []
        word = next(w for k, w, *_r in CURVES if k == key)
        spec_plots.draw_panel(ax, self.doc, pts, key, word.split(" by ")[0], xl, pos)
        self.fig.subplots_adjust(left=0.16, right=0.97, top=0.95, bottom=0.14)
        self.canvas.draw_idle()

    def _points(self) -> list:
        key = self.curve_key[0]
        return get_points(self.doc, key) if self.doc is not None else []

    def _read_xy(self):
        try:
            x = float(self.x_var.get().replace(",", ""))
            y = float(self.y_var.get().replace(",", ""))
            if not (math.isfinite(x) and math.isfinite(y)):
                raise ValueError
        except ValueError:
            self._say("✖ Type two numbers: the setting (or temperature) and the value.")
            return None
        if self.curve_key[3] and y <= 0:
            self._say("✖ This curve's values must be above zero.")
            return None
        return x, y

    def _put(self, pts):
        pts = sorted(pts, key=lambda p: float(p[0]))       # stable: two points at one x keep their order
        set_points(self.doc, self.curve_key[0], pts)
        self.dirty = True
        self.show_curve()

    def _fill_entries(self):
        sel = self.tree.selection()
        if sel:
            x, y = self._points()[int(sel[0])]
            self.x_var.set(f"{x:g}")
            self.y_var.set(f"{y:g}")

    def add_point(self, x=None, y=None):
        if self.doc is None:
            self._say("⚠ Pick or make a spec first (New…).")
            return
        if x is None:
            xy = self._read_xy()
            if xy is None:
                return
            x, y = xy
        self._put(self._points() + [[x, y]])
        self._say(f"Added {x:g}, {y:g}: not saved yet.")

    def change_point(self):
        sel = self.tree.selection()
        if not sel or self.doc is None:
            self._say("⚠ Pick a point in the list first.")
            return
        xy = self._read_xy()
        if xy is None:
            return
        pts = self._points()
        pts[int(sel[0])] = list(xy)
        self._put(pts)
        self._say("Changed: not saved yet.")

    def delete_point(self):
        sel = self.tree.selection()
        if not sel or self.doc is None:
            self._say("⚠ Pick a point in the list first.")
            return
        pts = self._points()
        del pts[int(sel[0])]
        self._put(pts)
        self._say("Deleted: not saved yet.")

    # ---- files
    def save(self):
        if self.doc is None:
            return
        doc = self.doc
        doc.setdefault("spec_id", sp.slug(doc.get("model")))
        probs = check_spec(doc)
        if probs:
            self._say("✖ Not saved: " + "; ".join(probs[:3]) + ".")
            return
        builtin = self.path and os.path.normcase(os.path.dirname(os.path.abspath(self.path))) == \
            os.path.normcase(os.path.abspath(builtin_dir()))
        target = sp.profile_path("specs", doc["spec_id"], make=False)
        mine = self.path and os.path.normcase(os.path.abspath(self.path)) == os.path.normcase(os.path.abspath(target))
        if os.path.exists(target) and not mine and not self.app.testing:
            if not messagebox.askyesno("Spec curves", f"You already have a spec file for “{doc.get('model')}” "
                                       f"({os.path.basename(target)}). Replace it? (The old one is kept in the "
                                       "history folder.)", parent=self):
                return
        doc["edited"] = sp.now_iso()[:10]
        doc["edited_by"] = settings.WRITTEN_BY
        try:
            path = sp.save_profile(doc, settings.WRITTEN_BY)
        except (sp.ProfileError, OSError) as exc:
            self._say(f"✖ Not saved ({exc}).")
            return
        settings.log(f"spec saved to {path}")
        self.dirty = False
        self.refresh(select_path=path)
        self._say("✔ Saved as your own copy: FLUX uses it in place of the built-in one." if builtin else
                  "✔ Saved; the previous version is in the history folder.")

    def new(self, model: str | None = None):
        if not self._ask_discard():
            return
        if model is None:
            model = simpledialog.askstring("Spec curves", "The camera model (as the maker names it, e.g. "
                                           "“ZWO ASI533MC Pro (IMX533)”):", parent=self)
        if not model or not model.strip():
            return
        inst = None
        for path in sp.list_profiles("cameras"):
            try:
                inst = (sp.load(path).get("camera") or {}).get("instrume") or inst
            except sp.ProfileError:
                continue
        self.doc = new_spec(model.strip(), None)
        self.path = None
        self.dirty = True
        self._loading = True
        self.model_var.set(self.doc["model"])
        self.match_var.set(", ".join(self.doc["matches_instrume"]))
        self.source_var.set(self.doc["source"])
        self._loading = False
        self.pick_var.set("")
        self.show_curve()
        self._say("New spec: add points curve by curve, check the cameras it fits"
                  + (f" (your camera says “{inst}”)" if inst else "") + ", then Save.")

    def import_file(self, path: str | None = None):
        if path is None:
            path = filedialog.askopenfilename(parent=self, title="A SHOBS camera spec file",
                                              filetypes=[("SHOBS spec", "*.json")])
        if not path:
            return
        try:
            doc = sp.load(path)
        except sp.ProfileError as exc:
            self._say(f"✖ {os.path.basename(path)} cannot be used: {exc}.")
            return
        probs = check_spec(doc)
        if probs:
            self._say(f"✖ {os.path.basename(path)} cannot be used: " + "; ".join(probs[:3]) + ".")
            return
        doc.setdefault("spec_id", sp.slug(doc.get("model")))
        doc["imported"] = sp.now_iso()[:10]
        try:
            out = sp.save_profile(doc, settings.WRITTEN_BY)
        except (sp.ProfileError, OSError) as exc:
            self._say(f"✖ Not imported ({exc}).")
            return
        settings.log(f"spec imported from {path} to {out}")
        self.dirty = False
        self.refresh(select_path=out)
        self._say(f"✔ Imported {doc.get('model')}.")

    def export_file(self, path: str | None = None):
        if self.doc is None:
            return
        if path is None:
            path = filedialog.asksaveasfilename(parent=self, defaultextension=".json",
                                                initialfile=f"{self.doc.get('spec_id') or sp.slug(self.doc.get('model'))}.json",
                                                filetypes=[("SHOBS spec", "*.json")])
        if not path:
            return
        probs = check_spec(self.doc)
        if probs:
            self._say("✖ Not exported: " + "; ".join(probs[:3]) + ".")
            return
        try:
            sp.write_json(path, self.doc)
        except OSError as exc:
            self._say(f"✖ Not exported ({exc.strerror or exc}).")
            return
        self._say(f"✔ Exported {os.path.basename(path)}.")

    def close(self):
        if self._ask_discard():
            self.destroy()
