"""Colour roles - the ONLY file in the project allowed to contain a colour.

Every other module names a role (`"danger"`, `"fg_dim"`); the hex value lives
here and nowhere else. `tests/unit/test_ui_discipline.py` enforces that with a
grep, so a stray hex literal or a named colour in a view fails the suite.

Roles are semantic, not literal: `ok` means "this went well", `danger` means
"this failed". A view must never pick a role from a state name directly - it
asks `role_for_state()`, which is where the exit-code rule lives.
"""

from __future__ import annotations

from typing import Dict, Optional

# --- the palette ---------------------------------------------------------
# Dark theme, tuned for a 1440p panel at 125% scaling. Contrast of fg on bg is
# ~13:1 and fg_dim on bg ~5.5:1, so the dim variant stays readable.
# Values are hex only: a Tk colour name here would bypass the palette just as
# badly as one in a view, and the discipline test rejects both.
ROLES: Dict[str, str] = {
    "bg": "#1b1d23",          # window background
    "bg_row": "#22252c",      # a normal row
    "bg_selected": "#2d323c",  # the selected row
    "fg": "#e6e8ec",          # primary text
    "fg_dim": "#9aa1ad",      # secondary text, inferred rows, hints
    "mono": "#c8cdd6",        # paths, commands, elapsed time
    "accent": "#4c9aff",      # selection border, focus ring, copied flash
    "ok": "#3fb950",          # success
    "warn": "#d29922",        # needs a human
    "danger": "#f85149",      # failure
}

# The role a state word is drawn in when nothing else is known.
STATE_ROLES: Dict[str, str] = {
    "blocked": "warn",
    "working": "fg",
    "idle": "fg_dim",
    "done": "ok",
    "error": "danger",
    "unknown": "fg_dim",
    "starting": "fg_dim",
}

# The dim variant used for a state that was *inferred* from a window title
# (L3). Same hue, less authority - an inference must not look like a report.
INFERRED_ROLE: Dict[str, str] = {
    "working": "fg_dim",
    "done": "fg_dim",
    "error": "danger",
}


def role(name: str) -> str:
    """Return the hex for a role name; unknown names fall back to `fg`.

    Falling back instead of raising is deliberate: a typo must not blank the
    panel, and the discipline test is what catches the typo.
    """
    return ROLES.get(name, ROLES["fg"])


def role_for_state(state: Optional[str], exit_code: Optional[int] = None,
                   inferred: bool = False) -> str:
    """The colour role for a row.

    The one rule that matters: a `done` row whose process exited non-zero is a
    *failure* and must never render in the success colour. The JSON `state`
    stays `done` (the ladder owns that); only the visual role changes.
    """
    key = (state or "unknown").strip().lower()
    if key == "done" and exit_code not in (None, 0):
        return "danger"
    if inferred and key in INFERRED_ROLE:
        return INFERRED_ROLE[key]
    return STATE_ROLES.get(key, "fg_dim")


def is_success_role(role_name: str) -> bool:
    """True when a role claims success - used by the tests, not by the view."""
    return role_name == "ok"
