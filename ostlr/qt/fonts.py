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

# Exact 2:1 families first. Consolas stays as the last resort: it is available
# everywhere on Windows, it just cannot be the primary face next to Chinese.
DEFAULT_FAMILY = ('MS Gothic, NSimSun, SimSun, Consolas, "Cascadia Mono", monospace')

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
    """The CSS font stack: bundled font first, then the exact system families."""
    configured = str(getattr(cfg, "font_family", "") or "").strip() if cfg else ""
    if bundled_font(directory):
        base = 'OstlrMono, ' + (configured or DEFAULT_FAMILY)
    else:
        base = configured or DEFAULT_FAMILY
    return base


def term_url(query_string: str = "") -> str:
    """Kept for callers that only need the resource name."""
    return "term.html" + (("?" + query_string) if query_string else "")
