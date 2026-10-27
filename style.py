"""SHOBS-FLUX look: colors, fonts and the ttk styles (House Style shared with SHOBS-P and SHOBS-SCHED; copied from
SHOBS-SCHED's style.py, BusyBar unchanged).

Blue frame (sidebar and status bar) around a white centre; the three target-type colors (navy = Variable,
green = Transit, plum = Campaign) for blocks, tags and row tints; GREEN final-action buttons that dim briefly
when pressed; ✖ / ⚠ / ✔ status label styles.

High-DPI: fonts are given in points and paddings are multiplied by ui_scale(), so nothing is a fixed pixel width.
On Windows call enable_windows_dpi() BEFORE the Tk root exists; Tk then sets its own scaling from the monitor's
DPI (125–150 % on a 4K screen) and every point size follows."""
from __future__ import annotations

import sys
import tkinter as tk
from tkinter import ttk

if sys.platform == "win32":
    UI_FONT, MONO_FONT = "Segoe UI", "Consolas"
elif sys.platform == "darwin":
    UI_FONT, MONO_FONT = "Helvetica Neue", "Menlo"
else:
    UI_FONT, MONO_FONT = "DejaVu Sans", "DejaVu Sans Mono"

# ---- frame (SHOBS-P's Variables sidebar family) ---------------------------------------------------------------------
FRAME_BG = "#e7eef2"      # behind the white card
CARD = "#ffffff"
TEXT = "#1c2b33"
HINT = "#5d6d76"
SIDE = "#16324a"          # sidebar
STEP = "#1f4464"          # step buttons
STEP_ACTIVE = "#2a5a80"   # hover / current step
STATUS_BG = "#10283b"     # status bar
SIDE_TEXT = "#f4f7f8"
SIDE_HINT = "#b7c7d1"
LINE = "#c9d6de"          # thin rules and borders

# ---- the three target types: navy = Variable, green = Transit, plum = Campaign (keys V / T / D) ----------------------
MODE_COLORS = {
    "V": {"name": "Variable", "dark": "#16324a", "light": "#2a5a80", "tint": "#e4edf4"},
    "T": {"name": "Transit", "dark": "#1d4a2f", "light": "#357a50", "tint": "#e3f1e7"},
    "D": {"name": "Campaign", "dark": "#4a1d47", "light": "#7a3576", "tint": "#f3e6f2"},
}

# ---- actions and status --------------------------------------------------------------------------------------------
GREEN = "#2e7d32"
GREEN_HOVER = "#256628"
GREEN_DIM = "#8dbb8f"     # briefly after a press
BAD = "#b00020"           # ✖
WARN_FG = "#8a5a00"       # ⚠ (dark amber text on pale yellow: yellow text on white is unreadable)
WARN_BG = "#fff3c4"
OK = "#1f5f99"            # ✔ normal: blue text on white
ICON = {"bad": "✖", "warn": "⚠", "ok": "✔"}

# ---- drawing (block bar, weather strip) ------------------------------------------------------------------------------
SKY_DAY = "#dce8f1"
SKY_CIVIL = "#a9bccd"
SKY_NAUTICAL = "#6d869c"
SKY_ASTRO = "#3e566c"
SKY_DARK = "#1b2a38"
MOON = "#e8c547"
MOON_FILL = "#fbf3d0"       # under the Moon altitude curve
MOON_TEXT = "#86680a"       # Moon label and degrees (dark gold: readable on white)
MERIDIAN = "#ffb74d"
LOCK = "#ffd54f"
CLOUD = "#9aa7b0"
TEMP = "#c0392b"
DEW = "#1f6fb2"
AIRMASS_LIGHT = "#e3f4f1"   # X = 1.0 (pale teal)
AIRMASS_DARK = "#00574b"    # X at or over the max-airmass setting (deep teal)
AIRMASS = "#00796b"         # the Airmass row: its label and numbers share the band's teal (John, 22 Oct)
CLOCK = "#1f5f99"           # the Site clock row: label, zone, ticks and hour numbers in one blue (John, 22 Oct)


def mode_color(mode: str, which: str = "light") -> str:
    return MODE_COLORS.get(mode or "V", MODE_COLORS["V"])[which]


def mix(c1: str, c2: str, f: float) -> str:
    """Blend two #rrggbb colors: f = 0 → c1, 1 → c2."""
    f = min(1.0, max(0.0, float(f)))
    a = [int(c1[i:i + 2], 16) for i in (1, 3, 5)]
    b = [int(c2[i:i + 2], 16) for i in (1, 3, 5)]
    return "#" + "".join(f"{round(x + (y - x) * f):02x}" for x, y in zip(a, b))


def airmass_color(x: float | None, max_airmass: float = 2.0) -> str:
    """Light at the zenith, darker as the airmass rises; full dark at the max-airmass setting."""
    if x is None or x != x:
        return AIRMASS_DARK
    top = max(1.05, float(max_airmass or 2.0))
    return mix(AIRMASS_LIGHT, AIRMASS_DARK, (float(x) - 1.0) / (top - 1.0))


def text_on(bg: str) -> str:
    """Black or white text, whichever reads better on bg."""
    r, g, b = (int(bg[i:i + 2], 16) for i in (1, 3, 5))
    return "#ffffff" if (0.299 * r + 0.587 * g + 0.114 * b) < 150 else TEXT


def enable_windows_dpi() -> None:
    """Per-monitor DPI awareness on Windows, so text is sharp at 125–150 % (call before tk.Tk())."""
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except Exception:
        pass


def ui_scale(widget) -> float:
    """The display's scaling against 96 dpi (at least 1.0), for pixel paddings and drawing sizes."""
    try:
        return max(float(widget.tk.call("tk", "scaling")) / 1.3333, 1.0)
    except Exception:
        return 1.0


def px(widget, n: float) -> int:
    return int(round(n * ui_scale(widget)))


def font(size: int = 10, *extra) -> tuple:
    return (UI_FONT, size, *extra)


def setup(root) -> ttk.Style:
    """Configure every ttk style the app uses."""
    from tkinter import font as tkfont
    st = ttk.Style(root)
    try:
        st.theme_use("clam")
    except Exception:
        pass
    s = lambda n: px(root, n)
    st.configure(".", font=font(10), background=FRAME_BG, foreground=TEXT)
    st.configure("TFrame", background=FRAME_BG)
    st.configure("Card.TFrame", background=CARD)
    st.configure("TLabel", background=FRAME_BG, foreground=TEXT)
    st.configure("Card.TLabel", background=CARD, foreground=TEXT)
    st.configure("Bold.TLabel", background=CARD, foreground=TEXT, font=font(10, "bold"))
    st.configure("Hint.TLabel", background=CARD, foreground=HINT)
    st.configure("Small.TLabel", background=CARD, foreground=HINT, font=font(9))
    st.configure("Title.TLabel", background=CARD, foreground=SIDE, font=font(16, "bold"))
    st.configure("Section.TLabel", background=CARD, foreground=SIDE, font=font(11, "bold"))
    # the small gray source tag beside a Setup field ("from SHOBS-P", "from FITS", "set here")
    st.configure("Tag.TLabel", background="#eceff1", foreground=HINT, font=font(8), padding=(s(4), 0))
    # ✖ / ⚠ / ✔ (color AND icon/word)
    st.configure("Bad.TLabel", background=CARD, foreground=BAD)
    st.configure("Warn.TLabel", background=WARN_BG, foreground=WARN_FG)
    st.configure("Ok.TLabel", background=CARD, foreground=OK)
    # sidebar
    st.configure("Side.TFrame", background=SIDE)
    st.configure("Side.TLabel", background=SIDE, foreground=SIDE_TEXT)
    st.configure("SideTitle.TLabel", background=SIDE, foreground=SIDE_TEXT, font=font(13, "bold"))
    st.configure("SideHint.TLabel", background=SIDE, foreground=SIDE_HINT)
    st.configure("SideHead.TLabel", background=SIDE, foreground=SIDE_HINT, font=font(8, "bold"))
    st.configure("Step.TButton", anchor="w", padding=(s(12), s(8)), background=STEP, foreground=SIDE_TEXT,
                 borderwidth=0)
    st.map("Step.TButton", background=[("active", STEP_ACTIVE)], foreground=[("active", "#ffffff")])
    st.configure("StepOn.TButton", anchor="w", padding=(s(12), s(8)), background=STEP_ACTIVE, foreground="#ffffff",
                 font=font(10, "bold"), borderwidth=0)
    st.map("StepOn.TButton", background=[("active", STEP_ACTIVE)])
    for key, m in MODE_COLORS.items():
        st.configure(f"Mode{key}.TLabel", background=m["light"], foreground="#ffffff", font=font(9, "bold"),
                     padding=(s(8), s(2)))
    st.configure("Status.TFrame", background=STATUS_BG)
    st.configure("Status.TLabel", background=STATUS_BG, foreground="#d5e0e6")
    st.configure("StatusBad.TLabel", background=STATUS_BG, foreground="#ff8a80")
    st.configure("StatusWarn.TLabel", background=STATUS_BG, foreground="#ffd54f")
    st.configure("StatusOk.TLabel", background=STATUS_BG, foreground="#d5e0e6")
    # final-action buttons: GREEN, dim briefly when pressed (see green_button)
    st.configure("Green.TButton", padding=(s(12), s(5)), background=GREEN, foreground="#ffffff",
                 font=font(10, "bold"), bordercolor=GREEN_HOVER, focuscolor="#ffffff")
    st.map("Green.TButton", background=[("disabled", "#b9c9ba"), ("pressed", GREEN_DIM), ("active", GREEN_HOVER)],
           foreground=[("disabled", "#eef3ee"), ("active", "#ffffff")])
    st.configure("GreenDim.TButton", padding=(s(12), s(5)), background=GREEN_DIM, foreground="#ffffff",
                 font=font(10, "bold"), bordercolor=GREEN_DIM)
    st.map("GreenDim.TButton", background=[("active", GREEN_DIM)])
    st.configure("TButton", padding=(s(10), s(4)))
    # forms and containers
    st.configure("TEntry", fieldbackground="#ffffff")
    st.configure("TCombobox", fieldbackground="#ffffff")
    st.configure("Card.TRadiobutton", background=CARD, foreground=TEXT)
    st.map("Card.TRadiobutton", background=[("active", "#f2f5f7")])
    st.configure("Card.TCheckbutton", background=CARD, foreground=TEXT)
    st.map("Card.TCheckbutton", background=[("active", "#f2f5f7")])
    st.configure("Group.TLabelframe", background=CARD, bordercolor=LINE)
    st.configure("Group.TLabelframe.Label", background=CARD, foreground=SIDE, font=font(10, "bold"))
    st.configure("TNotebook", background=CARD, bordercolor=LINE)
    st.configure("TNotebook.Tab", padding=(s(12), s(5)), font=font(10))
    st.map("TNotebook.Tab", background=[("selected", CARD)], foreground=[("selected", SIDE)])
    st.configure("Card.Horizontal.TProgressbar", background=OK, troughcolor="#e6edf2", bordercolor=LINE)
    # the busy bar on the dark status strip
    st.configure("Status.Horizontal.TProgressbar", background=OK, troughcolor=mix(STATUS_BG, "#ffffff", 0.18),
                 bordercolor=STATUS_BG, lightcolor=OK, darkcolor=OK)
    # tables: rows tall enough for the font at any scaling (never clipped on high-DPI Windows)
    body = tkfont.Font(root, font=font(10))
    st.configure("Treeview", rowheight=int(body.metrics("linespace") * 1.55) + 2, font=font(10),
                 background="#ffffff", fieldbackground="#ffffff")
    st.configure("Treeview.Heading", font=font(10, "bold"), padding=(s(4), s(5)))
    st.map("Treeview", background=[("selected", "#cfe0ee")], foreground=[("selected", TEXT)])
    return st


def green_button(parent, text: str, command, **kw) -> ttk.Button:
    """A final-action button (Build, Add, Copy, Print, Save…): green, dims briefly when pressed, then runs."""
    btn = ttk.Button(parent, text=text, style="Green.TButton", **kw)

    def run():
        try:
            btn.configure(style="GreenDim.TButton")
            btn.after(220, lambda: _restore(btn))
        except tk.TclError:
            pass
        command()

    btn.configure(command=run)
    return btn


def _restore(btn):
    try:
        btn.configure(style="Green.TButton")
    except tk.TclError:
        pass


def status_style(kind: str) -> str:
    return {"bad": "Bad.TLabel", "warn": "Warn.TLabel", "ok": "Ok.TLabel"}.get(kind, "Card.TLabel")


def kind_of(text: str) -> str:
    """'bad' / 'warn' / 'ok' from a message's leading icon."""
    t = (text or "").lstrip()
    if t.startswith(ICON["bad"]):
        return "bad"
    if t.startswith(ICON["warn"]):
        return "warn"
    return "ok"


def badge_image(root, mode: str, size: int | None = None) -> tk.PhotoImage:
    """A small square in the mode color (the Targets table's mode tag beside the name)."""
    from tkinter import font as tkfont
    if size is None:
        size = max(8, int(tkfont.Font(root, font=font(10)).metrics("linespace") * 0.7))
    img = tk.PhotoImage(master=root, width=size + 4, height=size)
    fill, edge = mode_color(mode, "light"), mode_color(mode, "dark")
    img.put(edge, to=(0, 0, size, size))
    img.put(fill, to=(1, 1, size - 1, size - 1))
    return img


# ---- the busy bar (House Style for every SHOBS app, John 26 Oct 2026) ----------------------------------------------
# ONE indicator for every delay: a back-and-forth (indeterminate) bar at the lower left of the window (the left end of
# the bottom status strip; a dialog has its own at the dialog's lower left) with words naming what it is doing.
# Multi-step jobs keep the same bar and change the words step by step; when the count is known the words carry it
# ("Finding suggested transits… 12 of 40"): it is never a filling bar. When the last job ends the bar goes away; the
# ✔ / ⚠ / ✖ result is shown where the job was started. The same class is meant to be copied into SHOBS-P unchanged.

class BusyBar(ttk.Frame):
    """The window's (or dialog's) busy bar. begin(words) → token; words(token, text) changes them; end(token) ends
    that job (idempotent). Several jobs at once: the newest one's words show; the bar goes when none is left."""

    SHOW_AFTER_MS = 250          # a job shorter than this never flashes the bar

    def __init__(self, parent, label_style: str = "Card.TLabel", frame_style: str = "Card.TFrame",
                 bar_style: str = "Card.Horizontal.TProgressbar"):
        super().__init__(parent, style=frame_style)
        self.bar = ttk.Progressbar(self, mode="indeterminate", length=px(parent, 110), style=bar_style)
        self.var = tk.StringVar()
        self.label = ttk.Label(self, textvariable=self.var, style=label_style)
        self.jobs: dict[int, str] = {}           # token → words, oldest first
        self._next = 1
        self._show_job = None
        self.shown = False

    def begin(self, words: str) -> int:
        tok = self._next
        self._next += 1
        self.jobs[tok] = words
        self._refresh()
        return tok

    def words(self, tok: int, text: str) -> None:
        if tok in self.jobs:
            self.jobs[tok] = text
            self._refresh()

    def end(self, tok) -> None:
        if self.jobs.pop(tok, None) is not None:
            self._refresh()

    @property
    def text(self) -> str:
        """The words now showing ("" when idle); tests read this."""
        return list(self.jobs.values())[-1] if self.jobs else ""

    def _refresh(self) -> None:
        try:
            if self.jobs:
                self.var.set(self.text)
                if not self.shown and self._show_job is None:
                    self._show_job = self.after(self.SHOW_AFTER_MS, self._show)
            else:
                if self._show_job is not None:
                    self.after_cancel(self._show_job)
                    self._show_job = None
                self._hide()
        except tk.TclError:
            pass

    def _show(self) -> None:
        self._show_job = None
        if not self.jobs or self.shown:
            return
        try:
            self.bar.pack(side=tk.LEFT, padx=(0, px(self, 6)))
            self.label.pack(side=tk.LEFT, padx=(0, px(self, 12)))
            self.bar.start(15)
            self.shown = True
        except tk.TclError:
            pass

    def _hide(self) -> None:
        if not self.shown:
            return
        try:
            self.bar.stop()
            self.bar.pack_forget()
            self.label.pack_forget()
            self.var.set("")
        except tk.TclError:
            pass
        self.shown = False
