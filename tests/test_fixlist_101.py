"""FLUX 1.0.1 (13 Nov 2026): the What's new blurb of the new-version check (GitHub #2, shared shobs_update.py, the
same file as SHOBS-P's and SHOBS-SCHED's), its status-strip note and New version window; the concept DOI on the
Credits page and in CITATION.cff; the History page hint."""
from __future__ import annotations

import io
import json
import os
import unittest

from tests.base import APP, TempData

import shobs_update as U  # noqa: E402

NOTES = """## SHOBS-FLUX v1.0.2

Highlights:
- Sky page reads mosaic folders, one field per panel
- The History page plots two values against each other
- A third highlight
- (a fourth one is not shown)

### Fixes
- a fix
"""


def reply(tag="v1.0.2", body=NOTES, **kw):
    doc = {"tag_name": tag, "html_url": f"https://github.com/SHOBS-S/SHOBS-FLUX/releases/tag/{tag}",
           "name": tag, "draft": False, "prerelease": False, "body": body, "published_at": "2026-12-01T15:00:00Z"}
    doc.update(kw)

    class R(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False
    return lambda req, timeout=None: R(json.dumps(doc).encode())


class SharedCode(unittest.TestCase):
    def test_same_file_as_the_other_apps(self):
        """shobs_update.py is one file copied unchanged into SHOBS-P, SHOBS-SCHED and SHOBS-FLUX: this copy is the
        SCHED 1.4.1 / SHOBS-P 2.3.3 one (Highlights, published date, note_text)."""
        for name in ("parse_highlights", "note_text", "HIGHLIGHTS_MAX", "STATE_KEYS"):
            self.assertTrue(hasattr(U, name), name)
        src = open(U.__file__, encoding="utf-8").read()
        self.assertNotIn("urlretrieve", src)
        self.assertNotIn("assets", src)

    def test_fetch_check_and_kept_answer(self):
        got = U.fetch_latest("SHOBS-S/SHOBS-FLUX", opener=reply())
        self.assertEqual(got["published"], "2026-12-01")
        self.assertEqual(len(got["highlights"]), 3)
        st = {"check_updates": True}
        note = U.check("SHOBS-FLUX", "SHOBS-S/SHOBS-FLUX", "1.0.1", st, now=1000.0, fetch=lambda: got)
        self.assertEqual(note["version"], "1.0.2")
        self.assertTrue(note["text"].startswith("SHOBS-FLUX 1.0.2 is available: Sky page reads mosaic"))
        self.assertTrue(note["text"].endswith("— click for details"))
        again = U.check("SHOBS-FLUX", "SHOBS-S/SHOBS-FLUX", "1.0.1", st, now=2000.0,
                        fetch=lambda: self.fail("asked twice in a day"))
        self.assertEqual(again["highlights"], got["highlights"])
        self.assertIsNone(U.check("SHOBS-FLUX", "SHOBS-S/SHOBS-FLUX", "1.0.2", st, now=3000.0))

    def test_settings_kept_by_1_0_0(self):
        """A 1.0.0 settings file (no highlights kept) still gives the plain note."""
        st = {"update_checked_at": 1000.0, "update_tag": "v1.0.2",
              "update_url": "https://github.com/SHOBS-S/SHOBS-FLUX/releases/tag/v1.0.2"}
        note = U.check("SHOBS-FLUX", "r", "1.0.1", st, now=1001.0)
        self.assertEqual(note["text"], "SHOBS-FLUX 1.0.2 is available — see the release notes")
        self.assertEqual(note["highlights"], [])

    def test_version(self):
        import settings
        self.assertEqual(settings.APP_VERSION, "1.0.1")
        self.assertEqual(settings.REPO, "SHOBS-S/SHOBS-FLUX")


class Citing(unittest.TestCase):
    DOI = "10.5281/zenodo.24115980"

    def test_credits_line(self):
        import credits as cr
        line = cr.citation_text("1.0.1")
        self.assertIn(f"doi:{self.DOI}", line)
        self.assertIn("version 1.0.1", line)
        self.assertNotIn("github.com", line)            # the DOI, not the repository, once there is one

    def test_citation_cff_and_readme(self):
        cff = open(os.path.join(APP, "CITATION.cff"), encoding="utf-8").read()
        self.assertIn(f"doi: {self.DOI}", cff)
        self.assertIn(f"value: {self.DOI}", cff)
        self.assertIn("version: 1.0.1", cff)
        readme = open(os.path.join(APP, "README.md"), encoding="utf-8").read()
        self.assertIn(f"https://zenodo.org/badge/DOI/{self.DOI}.svg", readme)


try:
    import tkinter as _tk
    _r = _tk.Tk()
    _r.destroy()
    HAVE_TK = True
except Exception:
    HAVE_TK = False


@unittest.skipUnless(HAVE_TK, "needs a display (run under xvfb-run)")
class Window(TempData):
    def setUp(self):
        super().setUp()
        import flux_app
        self.fa = flux_app
        self.app = flux_app.App(testing=True)
        self.app.update()
        self.opened = []
        self.app.open_release = lambda url=None: self.opened.append(url or self.app.update_url)

    def tearDown(self):
        try:
            self.app.destroy()
        except Exception:
            pass
        super().tearDown()

    def test_note_window_and_checkbox(self):
        import settings
        app = self.app
        note = U.check("SHOBS-FLUX", "r", "1.0.1", {}, now=1.0, fetch=lambda: U.fetch_latest("r", opener=reply()))
        app.geometry("2200x900+0+0")
        app.update()
        app.show_update(note)
        self.assertTrue(app.update_var.get().startswith("SHOBS-FLUX 1.0.2 is available: Sky page reads"))
        app.geometry("1000x700+0+0")                   # narrow: the short form leaves room for the status line
        app.update()
        app._fit_update_note()
        self.assertEqual(app.update_var.get(), "SHOBS-FLUX 1.0.2 is available — click for details")
        app.set_status("✔ Something else happened.")  # other messages never wipe the note
        self.assertIn("1.0.2 is available", app.update_var.get())
        app.open_update()
        app.update()
        first = app._new_version_win
        self.assertEqual(first.lines, note["highlights"])
        app.open_update()                              # a second click raises the same window
        self.assertIs(app._new_version_win, first)
        first.open_page()
        self.assertEqual(self.opened, [note["url"]])
        # no highlights: the click goes straight to the release page
        app.show_update(dict(note, highlights=[], text="SHOBS-FLUX 1.0.2 is available — see the release notes"))
        app.open_update()
        self.assertEqual(self.opened[-1], note["url"])
        # the Credits page checkbox: off clears the note, saved at once; a check still running says nothing then
        app.show_update(note)
        app.open_update()
        app.update()
        win = app._new_version_win.win
        app.show_credits()
        cp = app.credits_page
        cp.check_var.set(False)
        cp.set_check()
        self.assertEqual(app.update_var.get(), "")
        self.assertFalse(settings.load()["check_updates"])
        self.assertEqual(app.update_url, "")
        self.assertFalse(win.winfo_exists())
        app.show_update(note)
        self.assertEqual(app.update_var.get(), "")

    def test_history_hint(self):
        import history_page
        src = open(history_page.__file__, encoding="utf-8").read()
        self.assertNotIn("Every run FLUX makes goes into the history", src)
        self.assertIn("the Sky nights you save", src)

    def test_credits_page_cites_the_doi(self):
        app = self.app
        app.show_credits()
        app.update()
        texts = []

        def walk(w):
            try:
                texts.append(str(w.cget("text")))
            except Exception:
                pass
            for c in w.winfo_children():
                walk(c)
        walk(app.credits_page.frame)
        self.assertTrue(any("doi:10.5281/zenodo.24115980" in t for t in texts))
