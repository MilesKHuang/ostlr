"""Schema constants that outlived the JSON envelope.

`schema.py` used to be "the single source of the program's API": header buckets,
row field order, rank order, the v1 envelope. That API existed for `--once --json`,
which is gone (see `__main__`), so what is left is what other modules actually
read:

    STATES / L2_KINDS / is_valid_state / is_valid_kind   the status-file contract
    WRAPPER_ROOT_ENV                                     both sides of a launch
    ALLOWED_THIRD_PARTY                                  the dependency allow-list
    clip                                                 one place a string is cut
"""

from __future__ import annotations

from typing import Any, Optional

# --- the status-file contract (plan section 8.2) --------------------------

# The five states a program may report. `unknown` is not one of them: it is what
# the panel says when nobody reported, and a producer claiming it would be noise.
STATES = ("idle", "working", "blocked", "done", "error")

L2_KINDS = ("permission", "question", "auth")

L2_KEYS = (
    "v",
    "ts",
    "source",
    "pid",
    "cwd",
    "state",
    "kind",
    "msg",
    "exit_code",
)

# --- the wrapper ----------------------------------------------------------

# The env var that names the package root the launcher injected for its wrapper,
# so `status.run()` can hand the launched agent a clean PYTHONPATH. It lives here
# because both sides of a launch read it.
WRAPPER_ROOT_ENV = "OSTLR_WRAPPER_ROOT"

# --- the dependency allow-list -------------------------------------------

# The third-party packages this project is allowed to import. Install them from a
# mirror (`py -3 -m pip install -i <mirror> PySide6 pywinpty ...`); `--selftest`
# fails on any import outside stdlib + this list, so a stray dependency cannot
# sneak in unnoticed.
ALLOWED_THIRD_PARTY = (
    "PySide6",    # the Qt shell + QtWebEngine (xterm.js host)
    "shiboken6",  # PySide6's binding runtime
    "winpty",     # ConPTY wrapper: we own the PTY
    "psutil",     # optional: process facts, creation time, CPU
    "PIL",        # optional: tray icon image (pystray)
    "pystray",    # optional: system tray
)


# --- helpers --------------------------------------------------------------

def clip(text: Optional[str], limit: int = 120) -> str:
    """Clip a user-visible string, ending with an ellipsis when it was cut."""
    if not text:
        return ""
    value = str(text)
    if limit <= 0 or len(value) <= limit:
        return value
    return value[: max(0, limit - 1)] + "\u2026"


def is_valid_state(state: Any) -> bool:
    return isinstance(state, str) and state.strip().lower() in STATES


def is_valid_kind(kind: Any) -> bool:
    return isinstance(kind, str) and kind.strip().lower() in L2_KINDS
