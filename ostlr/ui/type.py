"""Type scale - the ONLY file in the project allowed to contain a font tuple.

Four specs, no more. A view asks for `type.ROW` / `type.META` / `type.TITLE` /
`type.MONO`; it never writes `("Segoe UI", 9)` inline. The discipline test
greps for inline tuples outside this file.

Point sizes (not pixels) so Tk scales them at 125% / 150% display scaling.
"""

from __future__ import annotations

from typing import Dict, Tuple

# (family, size_pt, style) - the shape Tk's font argument accepts.
FontSpec = Tuple[str, int, str]

SANS_FAMILY = "Segoe UI"
MONO_FAMILY = "Consolas"

ROW: FontSpec = (SANS_FAMILY, 10, "normal")     # one session row, line 1 and 2
META: FontSpec = (SANS_FAMILY, 9, "normal")     # header counters, status bar, hints
TITLE: FontSpec = (SANS_FAMILY, 11, "bold")     # empty-state headline, dialog titles
MONO: FontSpec = (MONO_FAMILY, 9, "normal")     # paths, commands, elapsed, memory

SPECS: Dict[str, FontSpec] = {
    "row": ROW,
    "meta": META,
    "title": TITLE,
    "mono": MONO,
}

# Row height = one mono linespace + this padding (plan section 8.7).
ROW_PADDING_PX = 8


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
    return "{%s} %d %s" % (family, size, style)
