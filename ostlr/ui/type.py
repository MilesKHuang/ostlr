"""Type scale - the ONLY file in the project allowed to contain a font tuple.

Four specs, no more. A view asks for `type.ROW` / `type.META` / `type.TITLE` /
`type.MONO`; it never writes `("Segoe UI", 9)` inline. The discipline test
greps for inline tuples outside this file.

Point sizes (not pixels) so Tk scales them at 125% / 150% display scaling.

Sizes are also multiplied by a user scale (`ui_scale` in config.toml, 0.8-2.0).
9 pt body text is small on a 1440p panel at 100 % scaling, and the honest fix is
a knob plus a bigger default, not a guess about the user's monitor.
"""

from __future__ import annotations

from typing import Dict, Tuple

# (family, size_pt, style) - the shape Tk's font argument accepts.
FontSpec = Tuple[str, int, str]

SANS_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"

# Base sizes, in points, before `ui_scale`.
ROW: FontSpec = (SANS_FAMILY, 12, "normal")     # one session row, line 1 and 2
META: FontSpec = (SANS_FAMILY, 11, "normal")    # header counters, status bar, hints
TITLE: FontSpec = (SANS_FAMILY, 14, "bold")     # empty-state headline, dialog titles
MONO: FontSpec = (MONO_FAMILY, 11, "normal")    # paths, commands, elapsed, memory

SPECS: Dict[str, FontSpec] = {
    "row": ROW,
    "meta": META,
    "title": TITLE,
    "mono": MONO,
}

# Row height = one mono linespace + this padding (plan section 8.7), scaled.
ROW_PADDING_PX = 8

MIN_SCALE = 0.8
MAX_SCALE = 2.0

_scale = 1.0


def set_scale(value: float) -> float:
    """Set the user scale (clamped) and return the value actually applied."""
    global _scale
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 1.0
    _scale = min(MAX_SCALE, max(MIN_SCALE, number))
    return _scale


def scale() -> float:
    return _scale


def row_padding_px() -> int:
    return max(2, int(round(ROW_PADDING_PX * _scale)))


def spec(name: str) -> FontSpec:
    """Look a spec up by name; unknown names fall back to META."""
    return SPECS.get(name, META)


def as_tk(spec_tuple: FontSpec) -> str:
    """Render a spec as a Tk font string ("{Segoe UI} 10 normal").

    The family is BRACED on purpose. Tk parses an unbraced font string as
    `family size style`, so "Segoe UI 9 normal" is read as family "Segoe",
    size "UI" - which raises `TclError: expected integer but got "UI"` at the
    first widget. Braces keep a multi-word family in one token.
    """
    family, size, style = spec_tuple
    scaled = max(6, int(round(size * _scale)))
    return "{%s} %d %s" % (family, scaled, style)
