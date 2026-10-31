"""Row layout - pure helpers, no toolkit.

Everything here is a function of plain values, so the whole row layout is
testable headless (the Tk widget arrives in T11 and only calls these).

Two rules from the plan (section 8.7) live here:

  * a row is two text lines: (glyph, agent, project) / (state word, elapsed,
    memory, branch);
  * markers - `(title)`, `(no identity)`, `exit=N` - are NEVER truncated, while
    agent / project / cwd text yields first. A trust or failure signal must not
    disappear because a path was long.
"""

from __future__ import annotations

from typing import Callable, Dict, List, NamedTuple, Optional, Sequence, Tuple

from . import glyphs, theme, type as type_mod

# mirrors schema.py (dev_1 owns the constant; hardcoded here on purpose)
LIST_CAP = 60
# mirrors config.py
DROP_COLUMNS_BELOW_PX = 1100
LIST_COLUMN_PX = 320
MIN_WINDOW = (900, 560)

ELLIPSIS = "\u2026"

# Optional columns, dropped whole (never squeezed) below the breakpoint.
OPTIONAL_COLUMNS: Tuple[str, ...] = ("mem", "branch")

# Markers that must survive elision, in the order they are appended.
MARKER_TITLE = "(title)"
MARKER_NO_IDENTITY = "(no identity)"


# --- elide ---------------------------------------------------------------

def elide(text: str, max_px: int, measure: Callable[[str], int],
          from_left: bool = False) -> str:
    """Shorten `text` to fit `max_px`, using `measure` for width.

    `from_left=True` keeps the tail (a path keeps its leaf name, which is the
    part a human reads). `from_left=False` keeps the head (a message keeps its
    beginning).

    Degenerate inputs are handled, not raised: an empty string, a non-positive
    budget and a `measure` that reports 0 for everything all return something
    printable rather than looping.
    """
    if not text:
        return ""
    if max_px <= 0:
        return ELLIPSIS
    if measure(text) <= max_px:
        return text

    budget = max_px - measure(ELLIPSIS)
    if budget <= 0:
        return ELLIPSIS

    if from_left:
        lo, hi = 0, len(text)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if measure(text[len(text) - mid:]) <= budget:
                lo = mid
            else:
                hi = mid - 1
        if lo <= 0:
            return ELLIPSIS
        return ELLIPSIS + text[len(text) - lo:]

    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if measure(text[:mid]) <= budget:
            lo = mid
        else:
            hi = mid - 1
    if lo <= 0:
        return ELLIPSIS
    return text[:lo] + ELLIPSIS


def elide_path(path: str, max_px: int, measure: Callable[[str], int]) -> str:
    """A path elides from the left so the leaf name survives."""
    return elide(path, max_px, measure, from_left=True)


# --- markers -------------------------------------------------------------

def markers_for(row: Dict) -> List[str]:
    """The markers a row must show, in a fixed order.

    `(title)`  - the state was inferred from a window title (L3), not reported.
    `(no identity)` - `started_at` is 0, so kill and restart are refused.
    `exit=N`   - the process ended with a non-zero code (a failure, not a win).
    """
    out: List[str] = []
    if row.get("source") == "l3":
        out.append(MARKER_TITLE)
    if row.get("started_at") in (0, None):
        out.append(MARKER_NO_IDENTITY)
    exit_code = row.get("exit_code")
    if exit_code not in (None, 0):
        out.append("exit=%d" % int(exit_code))
    return out


def marker_text(row: Dict) -> str:
    """All markers as one space-joined string (empty when there are none)."""
    return " ".join(markers_for(row))


# --- cells ---------------------------------------------------------------

class Cell(NamedTuple):
    text: str
    role: str
    font: str          # a name from ui/type.py: "row" | "meta" | "title" | "mono"
    elide_from_left: bool


class RowLayout(NamedTuple):
    line1: List[Cell]
    line2: List[Cell]
    markers: List[str]
    height_px: int
    glyph: str
    glyph_role: str


def format_mem(mem_bytes: Optional[int]) -> str:
    """Human memory: 212 MB, 1.2 GB, or an em dash when unknown."""
    if mem_bytes is None:
        return "\u2014"
    try:
        value = float(mem_bytes)
    except (TypeError, ValueError):
        return "\u2014"
    if value < 0:
        return "\u2014"
    if value >= 1024 ** 3:
        return "%.1f GB" % (value / 1024 ** 3)
    if value >= 1024 ** 2:
        return "%d MB" % int(round(value / 1024 ** 2))
    if value >= 1024:
        return "%d KB" % int(round(value / 1024))
    return "%d B" % int(value)


def format_elapsed(seconds: Optional[int]) -> str:
    """Session age: 42s, 3m, 2h05m."""
    if seconds is None or seconds < 0:
        return "\u2014"
    s = int(seconds)
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


def format_waiting(seconds: Optional[int]) -> str:
    """Time in state: `0s`-`59s` in seconds, `>= 60s` in minutes (plan 8.3)."""
    if seconds is None or seconds < 0:
        return "waiting \u2014"
    s = int(seconds)
    if s < 60:
        return "waiting %ds" % s
    return "waiting %dm" % (s // 60)


def state_word(row: Dict) -> str:
    """The word shown for a row: the transient wins, then the state."""
    transient = row.get("transient")
    if transient == "starting":
        return "starting..."
    return str(row.get("state") or "unknown")


def row_height_px(mono_linespace: int) -> int:
    """One mono linespace plus the fixed padding - identical with or without
    a marker, because markers live on line 2 and never add a line."""
    return int(mono_linespace) + type_mod.ROW_PADDING_PX


def build_row(row: Dict, mono_linespace: int = 16) -> RowLayout:
    """Turn a row dict into the two lines the widget draws.

    Pure: no toolkit, no clock, no I/O. `row` is a schema v1 row (section 8.1)
    plus the optional `exit_code` the ladder carries for a finished process.
    """
    state = row.get("state")
    transient = row.get("transient")
    inferred = row.get("source") == "l3"
    exit_code = row.get("exit_code")

    g = glyphs.glyph_for(state, transient)
    role = theme.role_for_state(state, exit_code, inferred=inferred)

    word = state_word(row)
    if inferred:
        word = word + " " + MARKER_TITLE

    line1 = [
        Cell(g.char, role, "row", False),
        Cell(str(row.get("agent") or "?"), "fg", "row", False),
        Cell(str(row.get("project") or "?"), "fg_dim", "row", False),
    ]
    line2 = [
        Cell(word, role, "meta", False),
        Cell(format_elapsed(row.get("elapsed_s")), "mono", "mono", False),
        Cell(format_mem(row.get("mem_bytes")), "mono", "mono", False),
        Cell(str(row.get("branch") or "?"), "fg_dim", "meta", False),
    ]
    return RowLayout(
        line1=line1,
        line2=line2,
        markers=markers_for(row),
        height_px=row_height_px(mono_linespace),
        glyph=g.char,
        glyph_role=role,
    )


def render_line(cells: Sequence[Cell], max_px: int,
                measure: Callable[[str], int]) -> List[str]:
    """Elide a line's cells to fit `max_px`, in the plan's priority order.

    Priority: the glyph cell (index 0) is never elided; then the state word
    (index 1 on line 2); then the agent; then project / branch / path text
    yields first. Returns the final strings, one per cell, same order.
    """
    texts = [c.text for c in cells]
    if not cells:
        return texts

    def total() -> int:
        return sum(measure(t) for t in texts)

    if total() <= max_px:
        return texts

    # Yield order: everything except the glyph and the state word.
    yieldable = [i for i in range(len(cells)) if i > 1]
    for i in reversed(yieldable):
        if total() <= max_px:
            break
        overflow = total() - max_px
        current = measure(texts[i])
        target = max(0, current - overflow)
        texts[i] = elide(texts[i], target, measure,
                         from_left=cells[i].elide_from_left)
    return texts


def render_row(row: Dict, max_px: int, measure: Callable[[str], int],
               mono_linespace: int = 16) -> Dict:
    """The full render of one row: two elided lines plus the marker string.

    The marker string is appended to line 2 *after* elision and is never
    truncated, so `(title)` / `(no identity)` / `exit=N` always survive.
    """
    layout = build_row(row, mono_linespace)
    line1 = render_line(layout.line1, max_px, measure)
    line2 = render_line(layout.line2, max_px, measure)
    markers = " ".join(layout.markers)
    if markers:
        line2 = line2 + [markers]
    return {
        "line1": line1,
        "line2": line2,
        "markers": layout.markers,
        "height_px": layout.height_px,
        "glyph": layout.glyph,
        "glyph_role": layout.glyph_role,
    }


# --- columns and cap -----------------------------------------------------

def drop_columns(width_px: int) -> List[str]:
    """Which optional columns to drop at this window width.

    Below the breakpoint the optional columns are dropped whole - never
    squeezed - so the state column keeps its width.
    """
    if width_px < DROP_COLUMNS_BELOW_PX:
        return list(OPTIONAL_COLUMNS)
    return []


def visible_columns(width_px: int) -> List[str]:
    """The columns that survive at this width, in display order."""
    dropped = set(drop_columns(width_px))
    return [c for c in ("state", "elapsed", "mem", "branch") if c not in dropped]


def apply_cap(rows: Sequence[Dict], cap: int = LIST_CAP) -> Tuple[List[Dict], int]:
    """Split rows into (rendered, truncated_count).

    The cap and the `showing N of M` footer are one unit: a caller that renders
    the cap must render the footer, otherwise the list silently contradicts the
    header total.
    """
    if cap <= 0:
        return list(rows), 0
    rendered = list(rows[:cap])
    return rendered, max(0, len(rows) - len(rendered))


def footer_text(rendered: int, truncated: int) -> str:
    """`showing 60 of 75`, or empty when nothing was truncated."""
    if truncated <= 0:
        return ""
    return "showing %d of %d" % (rendered, rendered + truncated)


# --- re-exports (so a view imports one module) ---------------------------

TRANSIENT_MAP = glyphs.TRANSIENT_MAP
STATE_MAP = glyphs.STATE_MAP
