"""Is a newer release out? One small module shared by every SHOBS app (SHOBS-P, SHOBS-SCHED, SHOBS-FLUX): keep the
copies identical, like shobs_profile.py. GitHub cannot tell someone who downloaded a release zip that a new one is
out, so each app asks for itself (GitHub SHOBS-FLUX #2, SHOBS-P #57; John, 8 Nov 2026).

What it does, and what it never does:
  - at start, in the background (never slows the start, never blocks the window), asks GitHub's public API for the
    repository's latest release: GET https://api.github.com/repos/<owner>/<repo>/releases/latest. No account, no
    token, nothing about the user is sent (only the app's name and version as the User-Agent GitHub requires).
  - at most one question a day: the time and the answer are kept in the app's own settings; between checks the kept
    answer is used, so the note shows again at every start until the app is updated.
  - silent on no internet, a timeout (5 s), an HTTP error or the rate limit. Drafts and pre-releases are ignored.
  - says a plain note ("SHOBS-FLUX 1.0.1 is available — see the release notes"), with the release page's link.
  - never downloads or installs anything.

The app supplies its settings as a dict: check_updates (on by default), update_checked_at (seconds), update_tag,
update_url."""
from __future__ import annotations

import json
import re
import threading
import time
import urllib.request

API = "https://api.github.com/repos/{repo}/releases/latest"
TIMEOUT_S = 5.0
EVERY_S = 24 * 3600.0            # one question a day at most


def parse_version(text) -> tuple | None:
    """'v1.2.3', '1.2', 'SHOBS-FLUX 1.0.0' → (1, 2, 3) / (1, 2, 0) / (1, 0, 0); None when there is no version."""
    # 1–3 digits a part: a date-like tag (2026.11.09) is not a version (Fable 1.0.0)
    m = re.search(r"(?<![\d.])v?(\d{1,3})\.(\d{1,3})(?:\.(\d{1,3}))?(?:\.(\d{1,3}))?(?![\d.])", str(text or ""))
    if not m:
        return None
    return tuple(int(g) for g in m.groups(default="0"))


def is_newer(latest, current) -> bool:
    a, b = parse_version(latest), parse_version(current)
    return a is not None and b is not None and a > b


def fetch_latest(repo: str, user_agent: str = "SHOBS", timeout: float = TIMEOUT_S, opener=None) -> dict | None:
    """{'tag', 'url', 'name'} of the repository's latest full release, or None (no internet, error, rate limit, a draft
    or pre-release, or an answer that makes no sense). Never raises."""
    req = urllib.request.Request(API.format(repo=repo), headers={"Accept": "application/vnd.github+json",
                                                                 "User-Agent": user_agent})
    try:
        with (opener or urllib.request.urlopen)(req, timeout=timeout) as resp:
            doc = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception:                                   # offline, timeout, HTTP 403/404/5xx, bad JSON: say nothing
        return None
    if not isinstance(doc, dict) or doc.get("draft") or doc.get("prerelease"):
        return None
    tag = str(doc.get("tag_name") or "")
    url = str(doc.get("html_url") or "")
    if not parse_version(tag) or not url.startswith("https://github.com/"):
        return None
    return {"tag": tag, "url": url, "name": str(doc.get("name") or tag)}


def check(app_name: str, repo: str, current: str, state: dict, now: float | None = None, fetch=None) -> dict | None:
    """The note to show, or None. state (the app's settings) is updated in place with the time and the answer when
    GitHub was asked; a failed question keeps the old answer and asks again at the next start."""
    if not state.get("check_updates", True):
        return None
    now = time.time() if now is None else now
    last = state.get("update_checked_at")
    if not isinstance(last, (int, float)) or not 0 <= now - last < EVERY_S:
        got = (fetch or (lambda: fetch_latest(repo, f"{app_name}/{current}")))()
        if got:
            state["update_checked_at"] = now
            state["update_tag"] = got["tag"]
            state["update_url"] = got["url"]
    tag, url = state.get("update_tag"), state.get("update_url")
    if tag and url and is_newer(tag, current):
        v = ".".join(str(x) for x in parse_version(tag)[:3])
        return {"text": f"{app_name} {v} is available — see the release notes", "url": url, "tag": tag}
    return None


def start(root, app_name: str, repo: str, current: str, state: dict, save, show) -> None:
    """Run check() on a background thread and, on the window's own thread, save(state) when it changed and show(note)
    when there is one. Tk is touched only from root.after."""
    if not state.get("check_updates", True):
        return
    box: dict = {}
    before = (state.get("update_checked_at"), state.get("update_tag"), state.get("update_url"))
    work = dict(state)

    def run():
        try:
            box["note"] = check(app_name, repo, current, work)
        except Exception:
            box["note"] = None
        box["done"] = True

    threading.Thread(target=run, daemon=True).start()
    tries = [0]

    def poll():
        if not box.get("done"):
            tries[0] += 1
            if tries[0] < 100:                          # ~20 s at most, then give up quietly
                root.after(200, poll)
            return
        for k in ("update_checked_at", "update_tag", "update_url"):
            if k in work:
                state[k] = work[k]
        if (state.get("update_checked_at"), state.get("update_tag"), state.get("update_url")) != before:
            try:
                save(state)
            except Exception:
                pass
        if box.get("note"):
            show(box["note"])

    root.after(200, poll)
