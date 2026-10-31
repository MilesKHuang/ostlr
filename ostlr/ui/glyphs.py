"""Glyphs - the ONLY file in the project allowed to contain a codepoint.

Six state glyphs and one transient glyph. The glyph column carries the whole
legend, so two rules are enforced by tests:

  * `STATE_MAP` has exactly 6 entries, `TRANSIENT_MAP` exactly 1;
  * the two maps share no codepoint and no colour role - a transient row must
    not be mistakable for a state row (that is why `starting` is U+22EF and not
    the idle circle).

The family is "Segoe UI Symbol"; when it is missing the ASCII fallback below
covers all seven codepoints, so the legend degrades to readable text instead of
tofu boxes.
"""

from __future__ import annotations

from typing import Dict, NamedTuple, Optional

FAMILY = "Segoe UI Symbol"

# --- codepoints ----------------------------------------------------------
# Written as escapes so this file stays pure ASCII and cannot be mangled by an
# editor or a code page.
BLOCKED = "\u25cf"   # BLACK CIRCLE
WORKING = "\u25d0"   # CIRCLE WITH LEFT HALF BLACK
IDLE = "\u25cb"      # WHITE CIRCLE
DONE = "\u2713"      # CHECK MARK
ERROR = "\u2715"     # MULTIPLICATION X
UNKNOWN = "\u25cc"   # DOTTED CIRCLE
STARTING = "\u22ef"  # MIDLINE HORIZONTAL ELLIPSIS


class Glyph(NamedTuple):
    char: str
    role: str


# Exactly six. Order is the rank order of the ladder, for readability only.
STATE_MAP: Dict[str, Glyph] = {
    "blocked": Glyph(BLOCKED, "warn"),
    "working": Glyph(WORKING, "fg"),
    "idle": Glyph(IDLE, "fg_dim"),
    "done": Glyph(DONE, "ok"),
    "error": Glyph(ERROR, "danger"),
    "unknown": Glyph(UNKNOWN, "fg_dim"),
}

# Exactly one. `spawn_failed` is NOT here: a failed start becomes `error`
# within one tick, so it never persists as a transient (plan section 11).
# The role is `mono`, not `fg_dim`: a transient must share NO colour role with
# STATE_MAP (idle and unknown are fg_dim), otherwise a launching row and an
# idle row are the same colour and the legend decays at the first launch.
TRANSIENT_MAP: Dict[str, Glyph] = {
    "starting": Glyph(STARTING, "mono"),
}

# ASCII fallback: every codepoint above must appear here. Used when the symbol
# family is unavailable, so the legend never renders as tofu.
ASCII_FALLBACK: Dict[str, str] = {
    BLOCKED: "!",
    WORKING: ">",
    IDLE: ".",
    DONE: "+",
    ERROR: "x",
    UNKNOWN: "?",
    STARTING: "~",
}

ALL_CODEPOINTS = tuple(ASCII_FALLBACK.keys())


def glyph_for(state: Optional[str], transient: Optional[str] = None) -> Glyph:
    """The glyph for a row: a transient wins, then the state, then unknown."""
    if transient and transient in TRANSIENT_MAP:
        return TRANSIENT_MAP[transient]
    key = (state or "unknown").strip().lower()
    return STATE_MAP.get(key, STATE_MAP["unknown"])


def char_for(state: Optional[str], transient: Optional[str] = None,
             ascii_only: bool = False) -> str:
    """The character to draw, optionally forced to the ASCII fallback."""
    g = glyph_for(state, transient)
    if ascii_only:
        return ASCII_FALLBACK.get(g.char, "?")
    return g.char


def fallback_covers_all() -> bool:
    """True when every glyph in both maps has an ASCII fallback."""
    for g in list(STATE_MAP.values()) + list(TRANSIENT_MAP.values()):
        if g.char not in ASCII_FALLBACK:
            return False
    return True


def family_available(families) -> bool:
    """True when the symbol family is present in an iterable of family names.

    `families` is whatever the toolkit reports (Tk: `tkinter.font.families()`).
    Kept toolkit-free so this module stays importable headless.
    """
    if not families:
        return False
    wanted = FAMILY.casefold()
    return any(str(f).casefold() == wanted for f in families)
