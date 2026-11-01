"""The few strings the panel shows, decided once and never in the view.

This module used to build a whole JSON envelope (`build_snapshot`, buckets, a
rank per row) for `--once --json`. That output described a different world from
the window - a process scan instead of the panes we own - and keeping two
definitions of "a session" in one repository is how the panel ended up stating
things it had not measured. The envelope went with the scan; what people actually
read stayed:

    state_word(row)                 the word in the row
    format_mem(bytes)               memory, in the unit Task Manager uses
    unknown_banner(rows, ...)       why every row is unknown
    receipt(kind, path, ...)        the three-part copy receipt
    clip(text, limit)               the one place a string is cut
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# `msg` and receipts are clipped to this many characters.
CLIP_CHARS = 120

# The status bar's idle text.
READY = "Ready"


# --- small helpers --------------------------------------------------------

def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def clip(text: Any, limit: int = CLIP_CHARS) -> str:
    """Clip a user-visible string, ending with an ellipsis when it was cut."""
    if not text:
        return ""
    value = str(text)
    if limit <= 0 or len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "\u2026"


def clip_middle(text: Any, limit: int = CLIP_CHARS) -> str:
    """Clip the MIDDLE, keeping both ends.

    A receipt ends with the important part - which program, which project, and
    whether it was a cross-project send. Cutting the tail would throw exactly that
    away, so the middle of a long path goes instead.
    """
    if not text:
        return ""
    value = str(text)
    if limit <= 0 or len(value) <= limit:
        return value
    head = max(1, (limit - 1) // 2)
    tail = max(1, limit - 1 - head)
    return value[:head] + "\u2026" + value[-tail:]


def format_mem(mem_bytes: Optional[int]) -> str:
    """Human memory: 212 MB, 1.2 GB, or an em dash when unknown."""
    if mem_bytes is None:
        return "\u2014"
    value = _as_int(mem_bytes, -1)
    if value < 0:
        return "\u2014"
    if value >= 1024 ** 3:
        return "%.1f GB" % (value / 1024 ** 3)
    if value >= 1024 ** 2:
        return "%d MB" % int(round(value / 1024 ** 2))
    if value >= 1024:
        return "%d KB" % int(round(value / 1024))
    return "%d B" % value


def format_elapsed(seconds: Optional[int]) -> str:
    """Session age: 42s, 3m, 2h05m."""
    if seconds is None:
        return "\u2014"
    s = max(0, _as_int(seconds, 0))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


# --- the row --------------------------------------------------------------

def state_word(row: Dict[str, Any]) -> str:
    """The word shown for a row: the transient wins, then the state."""
    if row.get("transient") == "starting":
        return "starting..."
    return _as_text(row.get("state")) or "unknown"


def unknown_banner(total: int, unknown: int, known: int) -> str:
    """The line above the list when nothing can be said about the sessions.

    It says *why* instead of leaving a grey wall: a program that has not reported
    is not idle, and the panel is not allowed to guess which it is.
    """
    if total <= 0:
        return ""
    if unknown <= 0:
        return ""
    if known <= 0:
        return ("Nothing is reporting yet: %d session(s) started here, none has "
                "said what it is doing (a pane reports once its program writes an "
                "OSC 7501 sequence or a status file)." % total)
    return "%d of %d session(s) have not reported a state." % (unknown, total)


# --- the copy receipt -----------------------------------------------------

def receipt(kind: str, path: str, agent: str, project: str,
            same_project: Optional[bool]) -> str:
    """The three-part receipt: what was written, to whom, same or cross project.

    `same_project` is None when there was no target pane at all - a different
    statement from "cross project", and the one that tells the user their path went
    to the clipboard and nowhere else.
    """
    if same_project is None:
        mark = "no target"
    else:
        mark = "same project" if same_project else "cross project"
    target = "%s / %s" % (agent or "?", project or "?")
    return clip_middle("%s %s -> %s (%s)" % (kind, path, target, mark))
