"""Which font the terminal uses, and where a bundled one would come from.

A terminal grid only stays aligned when `advance(CJK) == 2 * advance(ASCII)`.
Measured at 14 px on this machine:

    Consolas       7.70 / 14.00 -> 1.819   (borders drift next to Chinese)
    Cascadia Mono  8.20 / 14.00 -> 1.707
    MS Gothic      7.00 / 14.00 -> 2.000   (exact)
    NSimSun        7.00 / 14.00 -> 2.000   (exact)

So the default family starts with a CJK-capable monospace, and a font file dropped
into `ostlr/qt/fonts/` wins over every system font - the same order proJV uses
(bundled first, system second). We do not ship a Microsoft font: those cannot be
redistributed. `fonts/README.md` names openly licensed alternatives.
"""

from __future__ import annotations

import os
from typing import Optional, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
FONT_DIR = os.path.join(HERE, "fonts")

# The stack has to mix two fonts, because no single installed font is right:
#
#   family                     M(ASCII)  CJK     bar/line/corner
#   MS Gothic / NSimSun / SimHei   7.00   14.00   14.00   <- box chars are FULL width
#   Consolas                       7.70   14.00    7.70   <- box chars are one cell
#   Microsoft YaHei (proJV ships) 13.68   14.00    9.92   <- proportional, unusable
#
# A terminal gives a box-drawing character ONE cell. In a CJK font that glyph is
# drawn two cells wide, so every `|`, `-`, corner shifts the rest of the line by a
# cell - which is exactly "the separators break when Chinese appears". Putting a
# western monospace first fixes the frame (bar == M == 7.70) and lets Chinese fall
# back to a CJK face (14.00: a small gap inside its two-cell box, but no shift).
#
# This is also what Windows Terminal does, and why proJV looks right there.
DEFAULT_FAMILY = ('"LXGW WenKai Mono", "SimHei", "NSimSun", "Microsoft YaHei", '
                  '"Cascadia Mono", Consolas, monospace')

_FONT_SUFFIXES = (".ttf", ".otf", ".ttc")


def bundled_font(directory: Optional[str] = None) -> str:
    """The first font file in the font directory, or "" when none is there.

    One file is enough: the page registers it as `OstlrMono` and puts it first in
    the stack.
    """
    folder = directory or FONT_DIR
    try:
        names = sorted(os.listdir(folder))
    except OSError:
        return ""
    for name in names:
        if name.lower().endswith(_FONT_SUFFIXES):
            return os.path.join(folder, name)
    return ""


def family_for(cfg=None, directory: Optional[str] = None) -> str:
    """The CSS font stack. Bundled web fonts are referenced by family, not by file.

    LXGW WenKai Mono (OFL-1.1) is bundled as subsetted woff2 chunks loaded by
    `fonts/wenkai/mono.css`, so it is already the first family in the default
    stack; `bundled_font()` covers the older single-file (.ttf/.ttc) route and, if
    a file is present, registers it as `OstlrMono` in front.
    """
    configured = str(getattr(cfg, "font_family", "") or "").strip() if cfg else ""
    base = configured or DEFAULT_FAMILY
    if bundled_font(directory):
        parts = [p.strip() for p in base.split(",") if p.strip()]
        if "OstlrMono" not in base:
            parts.insert(0, "OstlrMono")
        base = ", ".join(parts)
    return base


def term_url(query_string: str = "") -> str:
    """Kept for callers that only need the resource name."""
    return "term.html" + (("?" + query_string) if query_string else "")
