"""L3 - window titles as the last-resort state source.

Why this module exists: when a CLI writes no L2 status file we still want to
know that a session is *alive* and roughly what it is doing. A window title is
real evidence of liveness, but it is weak evidence of state, so the classifier
here is deliberately crippled:

    classify() may return ONLY "working", "done" or "error".

It can never return "blocked" (a title cannot prove that a human is needed) and
it can never return "idle" (a title cannot prove that nothing is happening).
That restriction is the whole reason L3 is allowed to exist at all - see
docs/sop/steve-ostlr.md section 8.3 and the never-idle rule in section 1.

The module is importable headless: on a non-Windows host, or when user32 is
unavailable, `enumerate_titles()` returns an empty dict instead of raising.
"""

from __future__ import annotations

import sys
from typing import Dict, Iterable, List, Optional, Tuple

# --- the classifier ------------------------------------------------------

# Ordered: the first matching group wins, so a title that says both "error" and
# "done" is reported as an error (a failure must never be read as success).
_ERROR_MARKERS: Tuple[str, ...] = (
    "error",
    "failed",
    "failure",
    "panic",
    "fatal",
    "exception",
    "traceback",
    "crashed",
    "aborted",
)

_WORKING_MARKERS: Tuple[str, ...] = (
    "thinking",
    "working",
    "running",
    "esc to interrupt",
    "esc to cancel",
    "in progress",
    "generating",
    "streaming",
    "executing",
    "loading",
    "compiling",
    "building",
)

_DONE_MARKERS: Tuple[str, ...] = (
    "done",
    "complete",
    "completed",
    "finished",
    "ready",
    "success",
    "succeeded",
)

# The only three values classify() is allowed to produce.
L3_STATES: Tuple[str, ...] = ("working", "done", "error")


def classify(title: Optional[str]) -> Optional[str]:
    """Map a window title to one of L3_STATES, or None when it says nothing.

    None is the honest answer for "the title does not tell us" - the caller
    then falls through to `unknown`, never to `idle`.
    """
    if not title:
        return None
    text = title.casefold()
    for marker in _ERROR_MARKERS:
        if marker in text:
            return "error"
    for marker in _WORKING_MARKERS:
        if marker in text:
            return "working"
    for marker in _DONE_MARKERS:
        if marker in text:
            return "done"
    return None


# --- Windows enumeration -------------------------------------------------

_IS_WINDOWS = sys.platform.startswith("win")

# GetWindowTextW truncates at the buffer size; 512 UTF-16 units is far beyond
# any real title and keeps the per-window cost trivial.
_TITLE_BUF = 512


def _user32():
    """Return the user32 handle, or None when it cannot be used.

    Kept as a function so tests can monkeypatch it and so a headless import
    never touches ctypes.windll.
    """
    if not _IS_WINDOWS:
        return None
    try:
        import ctypes

        return ctypes.windll.user32
    except Exception:  # pragma: no cover - only on a broken Windows install
        return None


def enumerate_titles() -> Dict[int, str]:
    """{pid: title} for every *visible top-level* window that has a title.

    A pid with several titled windows keeps the first one seen (EnumWindows
    order is z-order, so that is the most recently raised window).
    """
    user32 = _user32()
    if user32 is None:
        return {}

    import ctypes
    from ctypes import wintypes

    result: Dict[int, str] = {}
    buf = ctypes.create_unicode_buffer(_TITLE_BUF)

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

    def _callback(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            length = user32.GetWindowTextW(hwnd, buf, _TITLE_BUF)
            if length <= 0:
                return True
            title = buf.value.strip()
            if not title:
                return True
            pid = wintypes.DWORD(0)
            user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if pid.value and pid.value not in result:
                result[pid.value] = title
        except Exception:
            # One bad window must never abort the enumeration.
            pass
        return True

    try:
        user32.EnumWindows(WNDENUMPROC(_callback), 0)
    except Exception:  # pragma: no cover - defensive
        return {}
    return result


def titles_for(pid: int, titles: Optional[Dict[int, str]] = None) -> List[str]:
    """All titles belonging to one pid.

    `enumerate_titles()` keeps one title per pid (the panel needs one row per
    session); this helper answers the other question - "does this pid have a
    window at all" - which T12 uses to prove a launched console exists.
    """
    if titles is None:
        titles = enumerate_titles()
    if not pid:
        return []
    title = titles.get(int(pid))
    return [title] if title else []


def has_window(pid: int, titles: Optional[Dict[int, str]] = None) -> bool:
    """True when the pid owns at least one visible titled window."""
    return bool(titles_for(pid, titles))


def classify_pid(pid: int, titles: Optional[Dict[int, str]] = None) -> Optional[str]:
    """Convenience: classify the title of one pid (None when it has none)."""
    found = titles_for(pid, titles)
    return classify(found[0]) if found else None


def live_check(titles: Optional[Dict[int, str]] = None) -> str:
    """One line for the selftest / DoD: a count, or an explicit SKIP.

    A windowless session (CI, session 0) must never read as a failure, so the
    zero case is a SKIP with a reason, not an error.
    """
    if titles is None:
        titles = enumerate_titles()
    count = len(titles)
    if count == 0:
        return "SKIP (no titled window in this session)"
    classified = sum(1 for t in titles.values() if classify(t) is not None)
    return "PASS titled windows: %d (classified: %d)" % (count, classified)


def iter_classified(titles: Iterable[Tuple[int, str]]):
    """Yield (pid, title, state) for the titles that classify to something."""
    for pid, title in titles:
        state = classify(title)
        if state is not None:
            yield pid, title, state
