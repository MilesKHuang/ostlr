"""The state ladder - the one function that decides what a row says.

Precedence (plan section 8.3):

    1. the process is gone            -> done (retained by the store, not here)
    2. a fresh L2 record (age <= l2_stale_s) -> its state, source "l2"
    3. an L3 window title             -> working / done / error, source "l3"
    4. nothing                        -> unknown, source "none", with a reason

The never-idle rule is structural, not a convention: `idle` is not in the L3
vocabulary and `unknown` is the default branch, so the only way to get `idle`
out of this function is a fresh L2 file that literally says `idle`. A property
test walks the whole (state x age x alive) grid to prove it.

`combine` is pure: it takes `now` as an argument and never reads the clock, so
the 89 s / 91 s boundary is testable without sleeping.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from . import schema
from .probe import l2 as l2_mod
from .probe import l3 as l3_mod

# The reasons a row can be `unknown`. They are user-visible (the row hint and
# the status bar), so they are short and specific rather than one generic word.
REASON_NO_DATA = "no status file"
REASON_STALE = "stale status file"
REASON_NO_TITLE = "no status file, no title"
REASON_DEAD = "process exited"
REASON_STATUS_DIR = "status directory unreadable"

SOURCE_L2 = "l2"
SOURCE_L3 = "l3"
SOURCE_NONE = "none"


def _default_stale_s() -> int:
    # Imported lazily so this module stays importable without config, and so a
    # test can pass a cfg object with any shape.
    from . import config as config_mod
    return config_mod.DEFAULT_L2_STALE_S


def _stale_s(cfg: Any) -> int:
    if cfg is None:
        return _default_stale_s()
    value = getattr(cfg, "l2_stale_s", None)
    if value is None and isinstance(cfg, dict):
        value = cfg.get("l2_stale_s")
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return _default_stale_s()


def effective_state(state: Any, source: Any, age_s: Any,
                    cfg: Any = None) -> Dict[str, Any]:
    """The ONE choke point that re-derives a state from its provenance.

    `combine()` is correct, but a verdict can also arrive from a store row, a
    fixture or a caller that built the dict by hand. Copying `state` verbatim
    from such a dict is how a STALE L2 record ends up rendering `idle` - the
    exact thing the never-idle rule forbids. So every consumer passes its
    (state, source, age_s) through here first.

    The rule: a `source="l2"` state is only believable while the record is
    fresh. Past `l2_stale_s` the state is `unknown` with the stale reason, no
    matter what the record said. `l3` and `none` are already inference/absence,
    so they pass through unchanged.
    """
    key = str(state or "unknown").strip().lower()
    src = str(source or SOURCE_NONE).strip().lower()
    if src not in (SOURCE_L2, SOURCE_L3, SOURCE_NONE):
        src = SOURCE_NONE
    if key not in ("idle", "working", "blocked", "done", "error", "unknown"):
        key = "unknown"

    try:
        age = None if age_s is None else max(0, int(age_s))
    except (TypeError, ValueError):
        age = None

    if src == SOURCE_L2 and age is not None and age > _stale_s(cfg):
        return {
            "state": "unknown",
            "source": SOURCE_NONE,
            "reason": "%s %d" % (REASON_STALE, age),
            "age_s": age,
        }
    # `idle` is only ever a REPORT. L3 (a window title) cannot prove that
    # nothing is happening, and `none` is the absence of data - so an `idle`
    # arriving from either is a caller bug, and it degrades to `unknown` here
    # rather than reaching the panel.
    if key == "idle" and src != SOURCE_L2:
        return {
            "state": "unknown",
            "source": SOURCE_NONE,
            "reason": REASON_NO_DATA,
            "age_s": age,
        }
    return {"state": key, "source": src, "reason": "", "age_s": age}


def combine(l2_record: Optional[Dict[str, Any]],
            l3_title: Optional[str],
            alive: bool,
            now: int,
            cfg: Any = None) -> Dict[str, Any]:
    """Decide one row's state.

    Returns {"state", "source", "reason", "age_s", "kind", "msg"}.

    `alive=False` means the process is gone; the caller (the store) is
    responsible for retaining the `done` row for `done_retain_s`.
    """
    stale_s = _stale_s(cfg)

    if not alive:
        return {
            "state": "done",
            "source": SOURCE_NONE,
            "reason": REASON_DEAD,
            "age_s": l2_mod.age_s(l2_record, now) if l2_record else 0,
            "kind": "",
            "msg": "",
        }

    if l2_record:
        age = l2_mod.age_s(l2_record, now)
        if age <= stale_s:
            return {
                "state": l2_record.get("state") or "unknown",
                "source": SOURCE_L2,
                "reason": "",
                "age_s": age,
                "kind": l2_record.get("kind") or "",
                "msg": l2_record.get("msg") or "",
            }
        # A stale record is evidence that a producer exists but has stopped
        # talking. That is not `idle` and it is not the record's old state.
        stale_age = age
    else:
        stale_age = 0

    inferred = l3_mod.classify(l3_title) if l3_title else None
    if inferred is not None:
        return {
            "state": inferred,
            "source": SOURCE_L3,
            "reason": "",
            "age_s": stale_age,
            "kind": "",
            "msg": "",
        }

    if l2_record:
        return {
            "state": "unknown",
            "source": SOURCE_NONE,
            "reason": REASON_STALE,
            "age_s": stale_age,
            "kind": "",
            "msg": "",
        }

    return {
        "state": "unknown",
        "source": SOURCE_NONE,
        "reason": REASON_NO_TITLE if l3_title else REASON_NO_DATA,
        "age_s": 0,
        "kind": "",
        "msg": "",
    }


def unknown_for_dir(reason: str) -> Dict[str, Any]:
    """The state for every row when the status directory itself is unusable.

    Still `unknown`, never `idle`: an unreadable directory means we know
    nothing, which is exactly what `unknown` is for.
    """
    return {
        "state": "unknown",
        "source": SOURCE_NONE,
        "reason": reason or REASON_STATUS_DIR,
        "age_s": 0,
        "kind": "",
        "msg": "",
    }


def sort_key(row: Dict[str, Any]) -> tuple:
    """Ordering: rank, then oldest state_since first, then project, then pid.

    `state_since` is the epoch when the state last changed; a smaller value is
    an older wait, so it sorts first inside a state. That is what makes "who
    has waited longest" answerable at a glance.
    """
    state = row.get("state")
    rank = schema.rank_for(state)
    since = row.get("state_since")
    try:
        since_int = int(since) if since is not None else 0
    except (TypeError, ValueError):
        since_int = 0
    project = str(row.get("project") or "")
    try:
        pid = int(row.get("pid") or 0)
    except (TypeError, ValueError):
        pid = 0
    return (rank, since_int, project, pid)


def order(rows: list) -> list:
    """Sort rows by the ladder's ordering rule."""
    return sorted(rows, key=sort_key)


def rank_of(state: Optional[str]) -> int:
    """The rank number for a state (0 = blocked, 5 = idle)."""
    return schema.rank_for(state)
