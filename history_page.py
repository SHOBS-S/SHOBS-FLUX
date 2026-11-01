"""The History page (0.5): every run FLUX has made, and any value of any setup over time.

Top: the setup (a system = telescope + camera, or a camera on its own), the value, the dates, and an optional event
date (a mirror cleaning, a collimation) to compare before and after. Export PNG (stamped with the date-time group)
and Export CSV save what is plotted; System report… makes the PDF report of a telescope + camera.
Middle: the trend plot (trend_plots.draw_trend). Bottom: the runs of this setup; a run can be taken off the page
(a bad night, a test) and put back: nothing is deleted."""
from __future__ import annotations

import os
import re
import tkinter as tk
from datetime import datetime, timedelta
from tkinter import filedialog, messagebox, ttk

import history
import settings
import shobs_profile as sp
import style
from style import px

RANGES = ("All dates", "Last 30 days", "Last 90 days", "Last year", "These dates")
MONTHS = {m: i for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
                                      "dec"), 1)}


def parse_date(text: str) -> str | None:
    """'2026-10-30', '30 Oct 2026', '30Oct26', '10/30/2026' → '2026-10-30'; '' → ''; anything else → None."""
    t = (text or "").strip()
    if not t:
        return ""
    for fmt in ("%Y-%m-%d", "%d %b %Y", "%d %B %Y", "%m/%d/%Y", "%d%b%y", "%d %b %y"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    m = re.match(r"^(\d{1,2})\s*([A-Za-z]{3})[a-z]*\s*(\d{2,4})$", t)
    if m and m.group(2).lower() in MONTHS:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        try:
            return datetime(y, MONTHS[m.group(2).lower()], int(m.group(1))).strftime("%Y-%m-%d")
        except ValueError:
            return None
    return None


def safe_name(text: str) -> str:
    return re.sub(r"\s+", " ", "".join(c if c.isalnum() or c in " .+-_()" else "-" for c in text)).strip()


def downloads_dir() -> str:
    d = os.path.join(os.path.expanduser("~"), "Downloads")
    return d if os.path.isdir(d) else os.path.expanduser("~")


def now_record(setup: dict, key: str, channel):
    """The value the setup's profile holds now for key (and channel), as a value record, or None."""
    path = setup.get("path")
    if not path:
        return None
    try:
        doc = sp.load(path)
    except sp.ProfileError:
        return None
    if setup["kind"] == "system":
        part, _, name = key.partition(".")
        if part == "bands":
            rec = ((doc.get("bands") or {}).get(channel) or {}).get(name)
            if name == "fwhm_defocus" and isinstance(rec, dict) and not sp.is_record(rec):
                return None                       # one value per defocus setting: no single line
            return rec if sp.is_record(rec) else None
        rec = (doc.get(part) or {}).get(name)
        return rec if sp.is_record(rec) else None
    if channel and not str(channel).endswith("C"):
        _v, rec = sp.camera_value(doc, key, channel)
    else:
        _v, rec = sp.camera_value(doc, key)
    return rec


def spec_value(setup: dict, key: str):
    """The maker's spec at the camera's gain setting, for gain / read noise / full well (camera setups)."""
    if setup["kind"] != "camera" or not setup.get("path") or key not in ("gain_e_per_adu", "read_noise_e",
                                                                          "full_well_e"):
        return None
    try:
        doc = sp.load(setup["path"])
    except sp.ProfileError:
        return None
    import report
    inst = (doc.get("camera") or {}).get("instrume")
    spec = sp.find_spec(inst, (report.BUILTIN_SPECS,)) if inst else None
    return sp.spec_at(spec, key, (doc.get("mode") or {}).get("gain_setting"))


def runs_for(setup: dict, removed: bool = False) -> list[dict]:
    """The runs of one setup, newest first (system: its on-sky nights and saves; camera: its checks, bench and dark
    runs)."""
    out = []
    for r in history.recent_runs(2000, removed=removed):
        if setup["kind"] == "system":
            if r["kind"] in ("onsky", "sky") and history.same_system(r.get("system_id"), setup["id"]):
                out.append(r)
        elif r["kind"] in ("check", "bench", "darks") and r.get("camera_id") == setup["id"]:
            out.append(r)
    return out


KIND_WORDS = {"onsky": "night", "sky": "saved", "check": "check", "bench": "bench", "darks": "darks"}


class HistoryPage:
    title = "History"

    def __init__(self, app, parent):
        self.app = app
        self.frame = ttk.Frame(parent, style="Card.TFrame")
        self.setups: list[dict] = []
        self.series: list[tuple] = []
        self.points: list[dict] = []
        self.fig = self.canvas = None
        self.run_rows: dict[str, dict] = {}

        top = ttk.Frame(self.frame, style="Card.TFrame")
        top.pack(fill=tk.X)
        ttk.Label(top, text="Setup", style="Card.TLabel").pack(side=tk.LEFT)
        self.setup_var = tk.StringVar()
        self.setup_box = ttk.Combobox(top, textvariable=self.setup_var, state="readonly", width=48)
        self.setup_box.pack(side=tk.LEFT, padx=(px(app, 6), px(app, 12)))
        self.setup_box.bind("<<ComboboxSelected>>", lambda _e: self.pick_setup())
        ttk.Label(top, text="Value", style="Card.TLabel").pack(side=tk.LEFT)
        self.value_var = tk.StringVar()
        self.value_box = ttk.Combobox(top, textvariable=self.value_var, state="readonly", width=44)
        self.value_box.pack(side=tk.LEFT, padx=(px(app, 6), 0))
        self.value_box.bind("<<ComboboxSelected>>", lambda _e: self.redraw())

        row2 = ttk.Frame(self.frame, style="Card.TFrame")
        row2.pack(fill=tk.X, pady=(px(app, 6), 0))
        ttk.Label(row2, text="Dates", style="Card.TLabel").pack(side=tk.LEFT)
        self.range_var = tk.StringVar(value=RANGES[0])
        rb = ttk.Combobox(row2, textvariable=self.range_var, state="readonly", values=RANGES, width=14)
        rb.pack(side=tk.LEFT, padx=(px(app, 6), px(app, 6)))
        rb.bind("<<ComboboxSelected>>", lambda _e: self.pick_range())
        self.from_var, self.to_var, self.event_var = tk.StringVar(), tk.StringVar(), tk.StringVar()
        self._dates_seen = ("", "", "")         # what the date boxes held at the last redraw
        ttk.Label(row2, text="from", style="Card.TLabel").pack(side=tk.LEFT)
        self.from_entry = ttk.Entry(row2, textvariable=self.from_var, width=12)
        self.from_entry.pack(side=tk.LEFT, padx=px(app, 4))
        ttk.Label(row2, text="to", style="Card.TLabel").pack(side=tk.LEFT)
        self.to_entry = ttk.Entry(row2, textvariable=self.to_var, width=12)
        self.to_entry.pack(side=tk.LEFT, padx=px(app, 4))
        ttk.Label(row2, text="   Event", style="Card.TLabel").pack(side=tk.LEFT)
        self.event_entry = ttk.Entry(row2, textvariable=self.event_var, width=12)
        self.event_entry.pack(side=tk.LEFT, padx=px(app, 4))
        ttk.Label(row2, text="(a cleaning, a collimation: before / after)", style="Hint.TLabel").pack(side=tk.LEFT)
        for e in (self.from_entry, self.to_entry, self.event_entry):
            e.bind("<Return>", lambda _e: self.typed_dates())
            e.bind("<FocusOut>", lambda _e: self.typed_dates())
        row3 = ttk.Frame(self.frame, style="Card.TFrame")
        row3.pack(fill=tk.X, pady=(px(app, 6), 0))
        self.report_btn = style.green_button(row3, "System report…", self.open_report)
        self.report_btn.pack(side=tk.LEFT)
        ttk.Button(row3, text="Export PNG…", command=self.export_png).pack(side=tk.LEFT, padx=(px(app, 12), 0))
        ttk.Button(row3, text="Export CSV…", command=self.export_csv).pack(side=tk.LEFT, padx=(px(app, 6), 0))
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(row3, textvariable=self.msg_var, style="Card.TLabel", justify=tk.LEFT)
        self.msg.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(px(app, 12), 0))
        from flux_app import follow_wrap
        follow_wrap(self.msg, row3, px(app, 420))

        self.pane = ttk.PanedWindow(self.frame, orient=tk.VERTICAL)
        self.pane.pack(fill=tk.BOTH, expand=True, pady=(px(app, 4), 0))
        self.pic = ttk.Frame(self.pane, style="Card.TFrame")
        self.pane.add(self.pic, weight=3)
        self.hint = ttk.Label(self.pic, style="Hint.TLabel", justify=tk.LEFT,
                              text="Pick a setup and a value. Every run FLUX makes goes into the history: bench and "
                                   "dark runs for a camera, every night the Sky page reads for a telescope + camera.")
        self.hint.pack(anchor="nw")
        lower = ttk.Frame(self.pane, style="Card.TFrame")
        self.pane.add(lower, weight=2)
        bar = ttk.Frame(lower, style="Card.TFrame")
        bar.pack(fill=tk.X, pady=(px(app, 4), px(app, 2)))
        ttk.Label(bar, text="Runs of this setup", style="Section.TLabel").pack(side=tk.LEFT)
        self.removed_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(bar, text="Show runs taken off", variable=self.removed_var,
                        command=self.show_runs).pack(side=tk.LEFT, padx=px(app, 12))
        self.restore_btn = ttk.Button(bar, text="Put back", command=lambda: self.take_off(False))
        self.restore_btn.pack(side=tk.RIGHT)
        self.remove_btn = ttk.Button(bar, text="Take off the page…", command=lambda: self.take_off(True))
        self.remove_btn.pack(side=tk.RIGHT, padx=px(app, 6))
        tf = ttk.Frame(lower, style="Card.TFrame")
        tf.pack(fill=tk.BOTH, expand=True)
        cols = ("kind", "when", "status", "source")
        self.tree = ttk.Treeview(tf, columns=cols, show="headings", height=6, selectmode="browse")
        for c, w, words in zip(cols, (70, 170, 330, 420), ("Kind", "When", "Status", "What it read")):
            self.tree.heading(c, text=words)
            self.tree.column(c, width=px(app, w), stretch=(c in ("status", "source")))
        self.tree.tag_configure("off", foreground="#9aa7b0")
        vb = ttk.Scrollbar(tf, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=vb.set)
        vb.pack(side=tk.RIGHT, fill=tk.Y)
        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._buttons())

    # ---- what is shown
    def say(self, text: str):
        from flux_app import set_msg
        set_msg(self.msg_var, self.msg, text)

    def setup(self) -> dict | None:
        i = self.setup_box.current()
        return self.setups[i] if 0 <= i < len(self.setups) else None

    def refresh(self):
        old = self.setup()
        self.setups = history.setups()
        self.setup_box.configure(values=[s["words"] for s in self.setups])
        idx = 0
        if old:
            for i, s in enumerate(self.setups):
                if s["kind"] == old["kind"] and s["id"] == old["id"]:
                    idx = i
                    break
        if self.setups:
            self.setup_box.current(idx)
        else:
            self.setup_var.set("")
        self.pick_setup(keep_value=True)

    def pick_setup(self, keep_value: bool = False):
        st = self.setup()
        old = self.value_var.get() if keep_value else ""
        self.series = history.available(st["kind"], st["id"]) if st else []
        names = [f"{w}   ({n} point{'s' if n != 1 else ''})" for _k, _c, w, n in self.series]
        self.value_box.configure(values=names)
        pick = 0
        for i, n in enumerate(names):
            if old and n.split("   (")[0] == old.split("   (")[0]:
                pick = i
                break
        if names:
            self.value_box.current(pick)
        else:
            self.value_var.set("")
        self.redraw()
        self.show_runs()

    def pick_range(self):
        r = self.range_var.get()
        self._range_set = None
        today = datetime.now()
        if r == "All dates":
            self.from_var.set("")
            self.to_var.set("")
        elif r != "These dates":
            days = {"Last 30 days": 30, "Last 90 days": 90, "Last year": 365}[r]
            self.from_var.set((today - timedelta(days=days)).strftime("%Y-%m-%d"))
            self.to_var.set("")
        self._range_set = (self.from_var.get(), self.to_var.get())
        self.redraw()

    def typed_dates(self, force: bool = False):
        """Return or leaving a date box: redraw only when something changed (leaving a box with a bad date in it
        would otherwise warn again at every click)."""
        try:
            if not self.frame.winfo_exists():
                return
        except tk.TclError:
            return
        now = (self.from_var.get().strip(), self.to_var.get().strip(), self.event_var.get().strip())
        if now == self._dates_seen and not force:
            return
        if (now[0], now[1]) != getattr(self, "_range_set", None) and (now[0] or now[1]):
            self.range_var.set("These dates")
        elif not (now[0] or now[1]):
            self.range_var.set("All dates")
        self.redraw()

    def dates(self):
        """(from, to, event) as YYYY-MM-DD ('' when empty), or None after saying which one is not a date."""
        self._dates_seen = (self.from_var.get().strip(), self.to_var.get().strip(), self.event_var.get().strip())
        out = []
        for var, words in ((self.from_var, "From"), (self.to_var, "To"), (self.event_var, "Event")):
            d = parse_date(var.get())
            if d is None:
                self.say(f"⚠ {words}: “{var.get().strip()}” is not a date (type it as 2026-10-30 or 30 Oct 2026).")
                return None
            out.append(d)
        return tuple(out)

    def chosen(self):
        i = self.value_box.current()
        return self.series[i] if 0 <= i < len(self.series) else None

    def redraw(self):
        st = self.setup()
        ser = self.chosen()
        dts = self.dates()
        if dts is None:
            return
        self.say("")
        d_from, d_to, event = dts
        if not st:
            self.points = []
            self.say("Nothing in the history yet: Check image, the Camera page and the Sky page each record their "
                     "runs here.")
            return
        if not ser:
            self.points = []
            self._figure()
            import trend_plots
            trend_plots.draw_trend(self.fig, [], "No values yet", "")
            self.canvas.draw_idle()
            return
        key, channel, words, _n = ser
        self.points = history.series_points(st["kind"], st["id"], key, channel, d_from, d_to)
        unit = self.points[0]["unit"] if self.points else ""
        now = now_record(st, key, channel)
        spec = spec_value(st, key)
        self._figure()
        import trend_plots
        trend_plots.draw_trend(self.fig, self.points, words, unit, now=now, spec=spec, event=event or None)
        self.canvas.draw_idle()
        if event:
            ba = trend_plots.before_after(self.points, event)
            if ba:
                e = ba["change_err"]
                tp = trend_plots
                self.say(f"Before {event}: {tp.num(ba['before']['mean'])} ({ba['before']['n']}); from it on: "
                         f"{tp.num(ba['after']['mean'])} ({ba['after']['n']}); change {tp.num(ba['change'], True)}"
                         + (f" ± {tp.num(e)}" if e == e else "") + f" {unit}.")
            else:
                self.say(f"⚠ No point on one side of {event} in these dates, so there is no before / after yet.")

    def _figure(self):
        if self.canvas is None:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure
            self.hint.pack_forget()
            self.fig = Figure(figsize=(8, 4), dpi=100)
            self.canvas = FigureCanvasTkAgg(self.fig, master=self.pic)
            w = self.canvas.get_tk_widget()
            w.configure(width=px(self.app, 300), height=px(self.app, 200))
            w.pack(fill=tk.BOTH, expand=True)

    def show_runs(self):
        tr = self.tree
        tr.delete(*tr.get_children())
        self.run_rows = {}
        st = self.setup()
        if not st:
            self._buttons()
            return
        for r in runs_for(st, removed=self.removed_var.get()):
            off = bool(r.get("removed"))
            iid = tr.insert("", "end", values=(KIND_WORDS.get(r["kind"], r["kind"]),
                                               str(r["started"])[:16].replace("T", " "),
                                               ("(taken off)  " if off else "") + history.status_text(r),
                                               r.get("source") or ""), tags=("off",) if off else ())
            self.run_rows[iid] = r
        self._buttons()

    def _buttons(self):
        sel = self.tree.selection()
        r = self.run_rows.get(sel[0]) if sel else None
        self.remove_btn.state(["!disabled"] if r and not r.get("removed") else ["disabled"])
        self.restore_btn.state(["!disabled"] if r and r.get("removed") else ["disabled"])

    def take_off(self, remove: bool):
        sel = self.tree.selection()
        r = self.run_rows.get(sel[0]) if sel else None
        if not r:
            return
        if remove and not self.app.testing and not messagebox.askyesno(
                settings.APP_NAME, f"Take this {KIND_WORDS.get(r['kind'], r['kind'])} off the History page?\n\n"
                f"{r.get('source') or ''}\n\nIts values leave the trend plots and the system report. Nothing is "
                "deleted: tick “Show runs taken off” to put it back. (The system profile itself does not change: "
                "re-run the Sky page and save to leave a night out of it.)", parent=self.app):
            return
        if not history.set_removed(r["run_key"], remove):
            self.say("✖ The run history could not be written (is another FLUX open?).")
            return
        settings.log(f"run {r['run_key']} {'taken off' if remove else 'put back'}")
        before = self.value_var.get().split("   (")[0]
        self.pick_setup(keep_value=True)
        after = self.value_var.get().split("   (")[0]
        text = "✔ Taken off the page." if remove else "✔ Put back."
        if before and after != before:
            text += f" {before} has no point left, so {after or 'nothing'} is shown."
        self.say(text)

    # ---- saving
    def _export_name(self, ext: str) -> str:
        st, ser = self.setup(), self.chosen()
        return safe_name(f"{st['words'] if st else 'FLUX'} - {ser[2] if ser else 'history'} - "
                         f"{datetime.now():%d %b %Y}") + ext

    def export_png(self):
        if not self.points or self.fig is None:
            self.say("⚠ Nothing plotted to save.")
            return
        path = filedialog.asksaveasfilename(parent=self.app, defaultextension=".png", initialdir=downloads_dir(),
                                            initialfile=self._export_name(".png"), filetypes=[("PNG picture", "*.png")])
        if path:
            self.save_png(path)

    def save_png(self, path: str) -> bool:
        import trend_plots
        old = self.fig.get_size_inches().copy()
        mark = None
        try:
            self.fig.set_size_inches(10, 5.4, forward=False)
            st = self.setup()
            mark = trend_plots.stamp(self.fig, settings.APP_VERSION, st["words"] if st else "")
            self.fig.savefig(path, dpi=130, facecolor="#ffffff")
        except OSError as exc:
            self.say(f"✖ Not saved ({exc.strerror or exc}).")
            return False
        finally:
            if mark is not None:
                mark.remove()
            self.fig.set_size_inches(old, forward=False)
            self.canvas.draw_idle()
        self.say(f"✔ Saved {os.path.basename(path)}.")
        return True

    def export_csv(self):
        if not self.points:
            self.say("⚠ Nothing plotted to save.")
            return
        path = filedialog.asksaveasfilename(parent=self.app, defaultextension=".csv", initialdir=downloads_dir(),
                                            initialfile=self._export_name(".csv"), filetypes=[("CSV", "*.csv")])
        if path:
            self.save_csv(path)

    def save_csv(self, path: str) -> bool:
        st = self.setup()
        try:
            history.write_csv(path, self.points, st["words"] if st else "", settings.APP_VERSION)
        except OSError as exc:
            self.say(f"✖ Not saved ({exc.strerror or exc}: is the file open in Excel?).")
            return False
        self.say(f"✔ Saved {os.path.basename(path)} ({len(self.points)} rows).")
        return True

    def open_report(self):
        old = getattr(self, "report_dialog", None)
        try:
            if old is not None and old.winfo_exists():
                old.lift()
                old.focus_set()
                return
        except tk.TclError:
            pass
        st = self.setup()
        pick = st["path"] if st and st["kind"] == "system" else None
        self.report_dialog = ReportDialog(self.app, pick, event=parse_date(self.event_var.get()) or "")


# ---- the system report window ----------------------------------------------------------------------------------------

class ReportDialog(tk.Toplevel):
    """Pick a system, check the optics the photon budget needs, an optional event date, shared or full; Make report."""

    def __init__(self, app, system_path: str | None = None, event: str = ""):
        import report
        super().__init__(app)
        self.app = app
        self.title(f"{settings.APP_NAME}: System report")
        self.configure(bg=style.CARD)
        self.transient(app)
        body = ttk.Frame(self, style="Card.TFrame", padding=(px(app, 16), px(app, 12)))
        body.pack(fill=tk.BOTH, expand=True)
        ttk.Label(body, text="System report", style="Title.TLabel").grid(row=0, column=0, columnspan=3, sticky="w")
        intro = ttk.Label(body, style="Hint.TLabel", justify=tk.LEFT, wraplength=px(app, 560),
                          text="A PDF of one telescope + camera: the setup, every number with where it came from, the "
                               "nights behind them, the throughput against a photon budget, how it changed over "
                               "time, and the methods and caveats. Made from the saved profiles and the run history.")
        intro.grid(row=1, column=0, columnspan=3, sticky="w", pady=(0, px(app, 10)))
        ttk.Label(body, text="System", style="Card.TLabel").grid(row=2, column=0, sticky="w")
        self.paths = sp.list_profiles("systems")
        self.names = []
        for p in self.paths:
            try:
                doc = sp.load(p)
                tel = doc.get("telescope") or {}
                self.names.append(f"{tel.get('name') or os.path.basename(p)[:-5]} + "
                                  f"{str(doc.get('camera_profile') or '').split('__')[0]}")
            except sp.ProfileError:
                self.names.append(os.path.basename(p)[:-5] + " (cannot be read)")
        self.sys_var = tk.StringVar()
        self.sys_box = ttk.Combobox(body, textvariable=self.sys_var, state="readonly", values=self.names, width=52)
        self.sys_box.grid(row=2, column=1, columnspan=2, sticky="w", pady=px(app, 2))
        self.sys_box.bind("<<ComboboxSelected>>", lambda _e: self.load_optics())
        ttk.Label(body, text="For the photon budget (blank = assumed, shown in the report):",
                  style="Section.TLabel").grid(row=3, column=0, columnspan=3, sticky="w", pady=(px(app, 10), 2))
        self.opt_vars: dict[str, tk.StringVar] = {}
        self.opt_hints: dict[str, tk.StringVar] = {}
        for i, (key, words, _default, _why) in enumerate(report.OPTICS):
            ttk.Label(body, text=words, style="Card.TLabel").grid(row=4 + i, column=0, sticky="w")
            v = tk.StringVar()
            ttk.Entry(body, textvariable=v, width=10).grid(row=4 + i, column=1, sticky="w", padx=px(app, 6))
            h = tk.StringVar()
            ttk.Label(body, textvariable=h, style="Hint.TLabel").grid(row=4 + i, column=2, sticky="w")
            self.opt_vars[key], self.opt_hints[key] = v, h
        r = 4 + len(report.OPTICS)
        ttk.Label(body, text="Event date (optional)", style="Card.TLabel").grid(row=r, column=0, sticky="w",
                                                                                pady=(px(app, 10), 0))
        self.event_var = tk.StringVar(value=event)
        ttk.Entry(body, textvariable=self.event_var, width=12).grid(row=r, column=1, sticky="w", padx=px(app, 6),
                                                                    pady=(px(app, 10), 0))
        ttk.Label(body, text="a cleaning or collimation: before / after", style="Hint.TLabel").grid(
            row=r, column=2, sticky="w", pady=(px(app, 10), 0))
        self.share_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(body, text="Shared copy: leave out the site (latitude, longitude, elevation) and the file "
                                   "locations on this computer", variable=self.share_var).grid(
            row=r + 1, column=0, columnspan=3, sticky="w", pady=(px(app, 10), 0))
        self.msg_var = tk.StringVar()
        self.msg = ttk.Label(body, textvariable=self.msg_var, style="Card.TLabel", wraplength=px(app, 560),
                             justify=tk.LEFT)
        self.msg.grid(row=r + 2, column=0, columnspan=3, sticky="w", pady=(px(app, 8), 0))
        btns = ttk.Frame(body, style="Card.TFrame")
        btns.grid(row=r + 3, column=0, columnspan=3, sticky="ew", pady=(px(app, 10), 0))
        self.make_btn = style.green_button(btns, "Make report…", self.make)
        self.make_btn.pack(side=tk.LEFT)
        ttk.Button(btns, text="Close", command=self.destroy).pack(side=tk.RIGHT)
        if not self.paths:
            self.say("⚠ No system profile yet: run the Sky page on your nights and save to a system profile first.")
            self.make_btn.state(["disabled"])
        else:
            i = self.paths.index(system_path) if system_path in self.paths else 0
            self.sys_box.current(i)
            self.load_optics()
        self.bind("<Escape>", lambda _e: self.destroy())

    def say(self, text):
        from flux_app import set_msg
        set_msg(self.msg_var, self.msg, text)

    def path(self) -> str | None:
        i = self.sys_box.current()
        return self.paths[i] if 0 <= i < len(self.paths) else None

    def load_optics(self):
        import report
        path = self.path()
        try:
            doc = sp.load(path) if path else {}
        except sp.ProfileError as exc:
            self.say(f"✖ That profile cannot be read: {exc}.")
            return
        cam = sp.find_camera_profile(doc.get("camera_profile") or "") if doc else None
        tel = doc.get("telescope") or {}
        assumed = report.optics_values({"telescope": {}}, cam)
        for key, _words, _d, _w in report.OPTICS:
            v = sp._finite(tel.get(key))
            self.opt_vars[key].set("" if v is None else f"{v:g}")
            a = assumed[key]
            self.opt_hints[key].set(f"blank = {a['value']:g} ({a['how']}" + (f": {a['why']}" if a.get("why") else "")
                                    + ")")
        self.say("")

    def optics(self) -> dict | None:
        """The typed optics ({key: float}); None after saying what is wrong."""
        limits = {"central_obstruction_fraction": (0.0, 0.9), "mirrors": (0, 8)}
        out = {}
        import report
        for key, words, _d, _w in report.OPTICS:
            t = self.opt_vars[key].get().strip().replace(",", ".")
            if not t:
                out[key] = None
                continue
            try:
                v = float(t.rstrip("%")) / (100.0 if t.endswith("%") else 1.0)
            except ValueError:
                self.say(f"✖ {words}: “{t}” is not a number.")
                return None
            lo, hi = limits.get(key, (0.0, 1.0))
            if not lo <= v <= hi:
                self.say(f"✖ {words}: {v:g} is outside {lo:g} to {hi:g}" +
                         (" (a fraction: 96 % is 0.96)." if hi == 1.0 else "."))
                return None
            out[key] = int(round(v)) if key == "mirrors" else v
        return out

    def save_optics(self, path: str, optics: dict) -> bool:
        """Keep the typed optics in the system profile (a new revision only when something changed)."""
        try:
            doc = sp.load(path)
        except sp.ProfileError as exc:
            self.say(f"✖ That profile cannot be read: {exc}.")
            return False
        tel = dict(doc.get("telescope") or {})
        changed = False
        for k, v in optics.items():
            if tel.get(k) != v and not (v is None and k not in tel):
                tel[k] = v
                changed = True
        if not changed:
            return True
        doc["telescope"] = tel
        try:
            sp.save_profile(doc, settings.WRITTEN_BY)
        except (sp.ProfileError, OSError) as exc:
            self.say(f"✖ The optics were not saved to the profile ({exc}).")
            return False
        settings.log(f"report optics saved to {path}: {optics}")
        return True

    def make(self, out_path: str | None = None):
        import report
        path = self.path()
        if not path:
            return
        optics = self.optics()
        if optics is None:
            return
        event = parse_date(self.event_var.get())
        if event is None:
            self.say(f"✖ Event date: “{self.event_var.get().strip()}” is not a date (2026-10-30 or 30 Oct 2026).")
            return
        share = self.share_var.get()
        try:
            info = report.gather(path, prefs=self.app.prefs, event=event, optics_override=optics)
        except report.ReportError as exc:
            self.say(f"✖ {exc}")
            return
        if out_path is None:
            out_path = filedialog.asksaveasfilename(parent=self, defaultextension=".pdf", initialdir=downloads_dir(),
                                                    initialfile=report.default_name(info, share),
                                                    filetypes=[("PDF", "*.pdf")])
            if not out_path:
                return                                  # nothing saved, the profile untouched
        if not self.save_optics(path, optics):
            return
        try:
            info = report.gather(path, prefs=self.app.prefs, event=event)
        except report.ReportError as exc:
            self.say(f"✖ {exc}")
            return
        report.prepare_fonts()                          # here, on the window's thread, before the worker draws
        self.last_path = out_path
        self.make_btn.state(["disabled"])
        self.say("Making the report…")

        def work(step):
            step("Laying out the pages")
            return report.write_pdf(info, out_path, share=share, version=settings.APP_VERSION)

        def done(n, exc):
            try:
                self.make_btn.state(["!disabled"])
            except tk.TclError:
                pass
            if exc is not None:
                text = (f"✖ Not saved: {exc.strerror or exc} (is the PDF open in a viewer?)" if isinstance(exc, OSError)
                        else f"✖ The report could not be made ({exc}). Details are in {settings.log_path()}.")
                self._say_safe(text)
                return
            settings.log(f"system report {out_path} ({n} pages, {'shared' if share else 'full'})")
            self._say_safe(f"✔ Saved {os.path.basename(out_path)} ({n} pages).")
            self.app.set_status(f"✔ System report saved: {out_path}")
            if not self.app.testing:
                from flux_app import open_folder
                open_folder(out_path)
        self.app.jobs.run(work, done, "Making the system report")

    def _say_safe(self, text):
        try:
            self.say(text)
        except tk.TclError:                  # the window was closed while the report was being made
            pass
