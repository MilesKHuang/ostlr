"""Schema v1 - the single source of the program's real API.

Everything that serialises or parses the envelope, a row or an L2 record reads
its field names from here. That is deliberate: `--once --json` is what CI and
the verifier check, so a column rename must be a one-line change in one file,
not a hunt through the view model.

Plan reference: docs/sop/steve-ostlr.md section 8.1 (row schema) and 8.2 (L2
status-file contract).

This module holds constants and tiny pure helpers only - no I/O, no config
loading, no logic that could drift into a second source of truth.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# --- envelope ------------------------------------------------------------

SCHEMA_VERSION = 1

# The header keys, in display order. `sessions` is the total and is always the
# sum of the seven buckets (section 8.1 invariant).
HEADER_KEYS: Tuple[str, ...] = (
    "waiting",
    "running",
    "running_inferred",
    "error",
    "error_inferred",
    "done",
    "idle",
    "unknown",
    "starting",
    "sessions",
)

# The seven buckets a row can sit in. `starting` is the transient bucket; a
# failed start becomes `error` within one tick, so it never persists.
BUCKETS: Tuple[str, ...] = (
    "waiting",
    "running",
    "error",
    "done",
    "idle",
    "unknown",
    "starting",
)

# Buckets L3 (window titles) can produce, and therefore the ones that need an
# `*_inferred` sub-count. `waiting` has none: a title can never prove that a
# human is needed.
INFERRED_BUCKETS: Tuple[str, ...] = ("running", "done", "error")

# --- row -----------------------------------------------------------------

# Field order is the JSON order, so a golden diff is readable.
ROW_KEYS: Tuple[str, ...] = (
    "pid",
    "started_at",
    "state_since",
    "agent",
    "project",
    "cwd",
    "branch",
    "state",
    "source",
    "transient",
    "kind",
    "msg",
    "age_s",
    "mem_bytes",
    "elapsed_s",
    "rank",
    "hint",
)

# The five states the ladder may report. `unknown` is the honest default; there
# is no code path that invents `idle` without a fresh L2 file.
STATES: Tuple[str, ...] = ("idle", "working", "blocked", "done", "error")

# Rank order: blocked first (a human is needed), idle last.
RANK_ORDER: Tuple[str, ...] = ("blocked", "error", "working", "unknown", "done", "idle")

# Where a state came from. `l2` is a report, `l3` is an inference.
SOURCES: Tuple[str, ...] = ("l2", "l3")

# Transient row treatments (outside STATES on purpose - see section 11).
TRANSIENTS: Tuple[str, ...] = ("starting", "spawn_failed")

# --- L2 status file ------------------------------------------------------

L2_KEYS: Tuple[str, ...] = (
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

L2_KINDS: Tuple[str, ...] = ("permission", "question", "auth")

# --- limits --------------------------------------------------------------

# `msg` and receipts are clipped to this many characters (config.clip_chars
# mirrors it; the constant lives here so the JSON and the panel cannot differ).
CLIP_CHARS = 120
# The list renders at most this many rows; the footer states `showing N of M`.
LIST_CAP = 60


# --- helpers -------------------------------------------------------------

def empty_header() -> Dict[str, int]:
    """A header with every key present and zeroed.

    Present-and-zero matters: the panel renders `waiting`/`running`/`unknown`
    at 0 in fixed slots, and a missing key would be a KeyError in the view.
    """
    return {key: 0 for key in HEADER_KEYS}


def empty_envelope() -> Dict[str, Any]:
    """The envelope for "no sessions": `{"v":1,"header":{...},"rows":[]}`."""
    return {"v": SCHEMA_VERSION, "header": empty_header(), "rows": []}


def envelope(header: Dict[str, int], rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Build an envelope, filling any missing header key with 0.

    `sessions` is recomputed from the buckets when the caller did not set it,
    so the invariant `sessions == sum of the seven buckets` cannot be broken by
    a caller that forgot.
    """
    out = empty_header()
    for key, value in (header or {}).items():
        if key in out:
            try:
                out[key] = int(value)
            except (TypeError, ValueError):
                out[key] = 0
    if not out["sessions"]:
        out["sessions"] = sum(out[b] for b in BUCKETS)
    return {"v": SCHEMA_VERSION, "header": out, "rows": list(rows or [])}


def bucket_for(state: Optional[str], transient: Optional[str] = None) -> str:
    """Which header bucket a row belongs to.

    A transient row is counted in `starting` and in no state bucket, so every
    row lands in exactly one bucket and the sum stays true.
    """
    if transient == "starting":
        return "starting"
    key = (state or "unknown").strip().lower()
    if key == "blocked":
        return "waiting"
    if key == "working":
        return "running"
    if key in ("error", "done", "idle"):
        return key
    return "unknown"


def rank_for(state: Optional[str]) -> int:
    """Sort rank: blocked 0 ... idle 5. Unknown states sort as `unknown`."""
    key = (state or "unknown").strip().lower()
    try:
        return RANK_ORDER.index(key)
    except ValueError:
        return RANK_ORDER.index("unknown")


def clip(text: Optional[str], limit: int = CLIP_CHARS) -> str:
    """Clip a user-visible string to `limit` characters.

    Applied in the view model, never in the view, so `--once --json` and the
    panel cannot disagree about the same field. A clipped string ends with an
    ellipsis so a reader can tell it was cut.
    """
    if not text:
        return ""
    s = str(text)
    if limit <= 0 or len(s) <= limit:
        return s
    return s[: max(0, limit - 1)] + "\u2026"


def is_valid_state(state: Any) -> bool:
    return isinstance(state, str) and state.strip().lower() in STATES


def is_valid_kind(kind: Any) -> bool:
    return isinstance(kind, str) and kind.strip().lower() in L2_KINDS
