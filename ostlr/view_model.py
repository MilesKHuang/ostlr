"""The view model - the last pure stage before anything is drawn or printed.

Everything a user reads is decided here, once, so `--once --json` and the panel
cannot disagree about the same field (plan section 8.1). The module is pure:
`now` is a parameter, there is no clock, no I/O and no toolkit.

Two rules are structural rather than conventional:

  * `sessions == waiting+running+error+done+idle+unknown+starting` and that sum
    equals `rows rendered + rows truncated`. A header that disagrees with the
    list destroys the one claim the product exists for, so the arithmetic is
    computed from the same list the rows come from.
  * a `done` row whose process exited non-zero renders the ERROR colour role
    while its JSON `state` stays `done`. A failure must never look like success.

The row dict is schema v1 (section 8.1) plus the few fields the view needs and
the JSON keeps: `exit_code`, `waiting_s`, `done_highlight_until`.
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from . import ladder, schema
from .ui import rows as ui_rows
from .ui import theme
# mirrors config.py (dev_1 owns the constant; hardcoded here on purpose)
CLIP_CHARS = schema.CLIP_CHARS
LIST_CAP = schema.LIST_CAP

# A `done` row flashes for this long, then renders normally. It never becomes
# `idle`: that would invent a state the ladder did not report.
DONE_HIGHLIGHT_S = 3

# How long a transient receipt stays in the status bar (display duration, not a
# latency bound).
RECEIPT_TTL_S = 5

# The status bar's idle text.
READY = "Ready"

# The capability line's fixed first token: L1 needs pywinpty, which cannot be
# installed here, so the panel says so instead of showing a grey wall.
L1_UNSUPPORTED = "L1: unsupported"

# Branch markers: `...` means "not fetched yet", `?` means "asked, no answer".
BRANCH_PENDING = "..."
BRANCH_UNKNOWN = "?"


# --- small helpers --------------------------------------------------------

def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def clip(text: Any, limit: int = CLIP_CHARS) -> str:
    """Clip a user-visible string, ending with an ellipsis when cut."""
    return schema.clip(_as_text(text), limit)


def format_waiting(seconds: Optional[int]) -> str:
    """`0s`-`59s` in seconds, `>= 60s` in minutes (plan 8.3)."""
    if seconds is None:
        return "waiting ?"
    s = max(0, _as_int(seconds, 0))
    if s < 60:
        return "waiting %ds" % s
    return "waiting %dm" % (s // 60)

def format_elapsed(seconds: Optional[int]) -> str:
    """Session age: 42s, 3m, 2h05m."""
    if seconds is None:
        return "\u2014"
    s = max(0, _as_int(seconds, 0))
    if s < 60:
        return "%ds" % s
    if s < 3600:
        return "%dm" % (s // 60)
    return "%dh%02dm" % (s // 3600, (s % 3600) // 60)


def format_mem(mem_bytes: Optional[int]) -> str:
    """Human memory: 212 MB, 1.2 GB, or an em dash when unknown."""
    if mem_bytes is None:
        return "\u2014"
    value = _as_int(mem_bytes, -1)
    if value < 0:
        return "\u2014"
    if value >= 1024 ** 3:
        return "%.1f GB" % (value / 1024 ** 3)
    if value >= 1024 ** 2:
        return "%d MB" % int(round(value / 1024 ** 2))
    if value >= 1024:
        return "%d KB" % int(round(value / 1024))
    return "%d B" % value


def branch_text(branch: Any) -> str:
    """`...` pending, `?` unknown, otherwise the branch name."""
    text = _as_text(branch).strip()
    if not text:
        return BRANCH_UNKNOWN
    return text


# --- markers and roles ----------------------------------------------------

def markers_for(row: Dict[str, Any]) -> List[str]:
    """The markers a row must show, in a fixed order.

    `(title)`       - the state was inferred from a window title (L3).
    `(no identity)` - `started_at` is 0, so kill and restart are refused.
    `exit=N`        - the process ended with a non-zero code.
    """
    out: List[str] = []
    if row.get("source") == "l3":
        out.append("(title)")
    if _as_int(row.get("started_at"), 0) == 0:
        out.append("(no identity)")
    exit_code = row.get("exit_code")
    if exit_code is not None and _as_int(exit_code, 0) != 0:
        out.append("exit=%d" % _as_int(exit_code, 0))
    return out


def role_for(row: Dict[str, Any]) -> str:
    """The colour role for a row.

    A `done` row with a non-zero exit code is a failure and takes the error
    role; an inferred row takes the dim variant of its role. The JSON `state`
    is never rewritten by this function.
    """
    return theme.role_for_state(
        row.get("state"),
        row.get("exit_code"),
        inferred=row.get("source") == "l3",
    )


def state_word(row: Dict[str, Any]) -> str:
    """The word shown for a row: the transient wins, then the state."""
    if row.get("transient") == "starting":
        return "starting..."
    return _as_text(row.get("state")) or "unknown"


# --- ordering -------------------------------------------------------------

def sort_key(row: Dict[str, Any]) -> Tuple[int, int, str, int]:
    """rank, then oldest `state_since` first, then project, then pid."""
    rank = schema.rank_for(row.get("state"))
    since = _as_int(row.get("state_since"), 0)
    project = _as_text(row.get("project"))
    pid = _as_int(row.get("pid"), 0)
    return (rank, since, project, pid)


def order(rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return sorted(rows, key=sort_key)


# --- row building ---------------------------------------------------------

def build_row(session: Dict[str, Any], state_row: Dict[str, Any],
              branch: Any = None, now: int = 0, cfg: Any = None) -> Dict[str, Any]:
    """Merge one session with its store row into a schema v1 row.

    `session` supplies the facts (pid, agent, project, cwd, memory); `state_row`
    supplies the verdict (state, source, transient, kind, msg, state_since,
    exit_code). Neither is mutated.
    """
    pid = _as_int(session.get("pid"), 0)
    started_at = _as_int(session.get("started_at"), 0)
    state = _as_text(state_row.get("state")) or "unknown"
    source = _as_text(state_row.get("source")) or "none"
    transient = state_row.get("transient") or None
    state_since = _as_int(state_row.get("state_since"), 0)

    # The choke point: a stale L2 record can never keep its state, so a row
    # cannot render `idle` from a 200 s old file. `combine()` already does this
    # for the live path; this covers a store row, a fixture or a hand-built
    # dict, which is where the verifier found the leak.
    effective = ladder.effective_state(state, source, state_row.get("age_s"), cfg)
    state = effective["state"]
    source = effective["source"]
    if effective["reason"]:
        state_row = dict(state_row)
        state_row["reason"] = effective["reason"]
    elapsed = None
    if started_at > 0 and now > 0:
        elapsed = max(0, now - started_at)
    waiting = None
    if state_since > 0 and now > 0:
        waiting = max(0, now - state_since)

    exit_code = state_row.get("exit_code")
    exit_code = None if exit_code is None else _as_int(exit_code, 0)

    highlight_until = 0
    if state == "done" and state_since > 0:
        highlight_until = state_since + DONE_HIGHLIGHT_S

    row = {
        "pid": pid,
        "started_at": started_at,
        "state_since": state_since,
        "agent": _as_text(session.get("agent")) or "?",
        "project": _as_text(session.get("project")) or "?",
        "cwd": _as_text(session.get("cwd")) or "?",
        "branch": branch_text(branch if branch is not None else session.get("branch")),
        "state": state,
        "source": source,
        "transient": transient,
        "kind": _as_text(state_row.get("kind")),
        "msg": clip(state_row.get("msg")),
        "age_s": None if state_row.get("age_s") is None else _as_int(state_row.get("age_s"), 0),
        "mem_bytes": _as_int(session.get("mem_bytes"), 0),
        "elapsed_s": elapsed,
        "rank": schema.rank_for(state),
        "hint": "",
        # --- fields the view needs, kept in the JSON on purpose ---
        "exit_code": exit_code,
        "waiting_s": waiting,
        "done_highlight_until": highlight_until,
        "reason": _as_text(state_row.get("reason")),
        "template_id": _as_text(session.get("template_id")),
    }
    row["hint"] = hint_for(row)
    return row


def hint_for(row: Dict[str, Any]) -> str:
    """The one-line next step for a row, shown in the status bar only.

    A hint never enters a row: the row has a fixed height and a 320 px column,
    and a hint is a sentence. It is the user's route from `unknown` to a real
    state, so it names the literal command.
    """
    state = row.get("state")
    if state == "unknown":
        reason = row.get("reason") or "no status file"
        return ("%s - run: py -3 -m ostlr.status --state working --pid %d"
                % (reason, _as_int(row.get("pid"), 0)))
    if _as_int(row.get("started_at"), 0) == 0:
        return "identity unverified: kill and restart are disabled for this row"
    if row.get("exit_code") not in (None, 0):
        return "exited with code %d" % _as_int(row.get("exit_code"), 0)
    return ""


# --- header ---------------------------------------------------------------

def header_for(rows: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """The seven buckets, the inferred sub-counts and the total.

    `sessions` is the sum of the seven buckets, computed from the same list the
    rows come from, so the header cannot contradict the list.
    """
    header = schema.empty_header()
    # `done_inferred` is not in schema.HEADER_KEYS (dev_1 owns that tuple), but
    # L3 can yield `done`, so the sub-count must exist or an inferred `done`
    # would be counted as authoritative. Added here, never silently dropped.
    for bucket in schema.INFERRED_BUCKETS:
        header.setdefault(bucket + "_inferred", 0)
    for row in rows:
        bucket = schema.bucket_for(row.get("state"), row.get("transient"))
        header[bucket] = header.get(bucket, 0) + 1
        if row.get("source") == "l3":
            key = bucket + "_inferred"
            if key in header:
                header[key] = header[key] + 1
    header["sessions"] = sum(header[b] for b in schema.BUCKETS)
    return header


# --- receipts and the status bar -----------------------------------------

def receipt(kind: str, path: str, agent: str, project: str,
            same_project: Optional[bool] = None) -> str:
    """The three-part receipt for a copy or an injection.

    `copied <path> -> <agent> / <project> (same project | cross project | no target)`

    The WHOLE string is clipped to `clip_chars`, and the clip keeps the tail:
    the target and the same/cross-project verdict are the part that prevents
    pasting into the wrong window, so a long path must lose its middle, never
    its destination.
    """
    target = "%s / %s" % (agent or "?", project or "?")
    if same_project is None:
        suffix = "no target"
    elif same_project:
        suffix = "same project"
    else:
        suffix = "cross project"
    tail = " -> %s (%s)" % (target, suffix)
    head = "%s " % kind
    budget = CLIP_CHARS - len(head) - len(tail) - 1  # 1 for the ellipsis
    if budget < 0:
        # Even the tail does not fit: keep the tail, it is the load-bearing part.
        return (head + tail)[:CLIP_CHARS]
    return head + clip(path, budget) + tail


def status_bar(receipt_text: str = "", receipt_age_s: Optional[int] = None,
               hint: str = "") -> str:
    """The status bar's one line.

    Precedence: a transient receipt (5 s) > the selected row's hint > `Ready`.
    A receipt that has expired must not keep the bar occupied.
    """
    if receipt_text and (receipt_age_s is None or receipt_age_s < RECEIPT_TTL_S):
        return clip(receipt_text)
    if hint:
        return clip(hint)
    return READY


def capability_line(producers_found: int, inferred_rows: int) -> str:
    """The empty state's capability line, in the user's words.

    It states the limit instead of hiding it: L1 is unsupported here, N
    producers were found, N rows are inferred from window titles.
    """    """The empty state's capability line, in the user's words.

    It states the limit instead of hiding it: L1 is unsupported here, N
    producers were found, N rows are inferred from window titles.
    """
    return "%s | producers found: %d | inferred rows: %d" % (
        L1_UNSUPPORTED, max(0, _as_int(producers_found, 0)),
        max(0, _as_int(inferred_rows, 0)))


def empty_state_lines(producers_found: int = 0, inferred_rows: int = 0) -> List[str]:
    """The three text lines of the empty state, in display order.

    headline, capability line, the literal command (the buttons are drawn by the
    view). The command is copy-paste exact.
    """
    return [
        "No agent sessions yet",
        capability_line(producers_found, inferred_rows),
        "py -3 -m ostlr run -- claude",
    ]


# --- the snapshot ---------------------------------------------------------

def build_snapshot(sessions: Sequence[Dict[str, Any]],
                   state_rows: Sequence[Dict[str, Any]],
                   branches: Optional[Dict[str, str]] = None,
                   now: int = 0,
                   list_cap: int = LIST_CAP,
                   cfg: Any = None) -> Dict[str, Any]:
    """Build the v1 envelope from sessions + store rows.

    `state_rows` are the store's published rows; a session with no matching row
    is `unknown` (never `idle`). Rows are ordered, capped, and the header is
    computed from the FULL list so `sessions` counts what exists, while the
    footer states how many are rendered.
    """
    branch_map = branches or {}
    by_identity: Dict[Tuple[int, int], Dict[str, Any]] = {}
    by_pid: Dict[int, Dict[str, Any]] = {}
    for state_row in state_rows or []:
        if not isinstance(state_row, dict):
            continue
        key = (_as_int(state_row.get("pid"), 0), _as_int(state_row.get("started_at"), 0))
        by_identity[key] = state_row
        by_pid.setdefault(key[0], state_row)

    built: List[Dict[str, Any]] = []
    for session in sessions or []:
        if not isinstance(session, dict):
            continue
        pid = _as_int(session.get("pid"), 0)
        if pid <= 0:
            continue
        started_at = _as_int(session.get("started_at"), 0)
        state_row = by_identity.get((pid, started_at)) or by_pid.get(pid) or {}
        cwd = _as_text(session.get("cwd"))
        branch = branch_map.get(cwd, session.get("branch"))
        built.append(build_row(session, state_row, branch=branch, now=now, cfg=cfg))

    ordered = order(built)
    header = header_for(ordered)

    rendered, truncated = ui_rows.apply_cap(ordered, list_cap)
    envelope = schema.envelope(header, rendered)
    envelope["footer"] = ui_rows.footer_text(len(rendered), truncated)
    envelope["truncated"] = truncated
    return envelope


def summarise(envelope: Dict[str, Any]) -> Dict[str, Any]:
    """A few derived numbers for `--selftest` and for a bug report."""
    header = envelope.get("header") or {}
    rows = envelope.get("rows") or []
    return {
        "sessions": header.get("sessions", 0),
        "rendered": len(rows),
        "truncated": envelope.get("truncated", 0),
        "inferred": sum(1 for r in rows if r.get("source") == "l3"),
        "waiting": header.get("waiting", 0),
    }


# --- the panel's own text (the view owns no literal) -----------------------

# The legend shown beside the list. One line per state, in rank order, in the
# user's words rather than the protocol's.
LEGEND_LINES = (
    "blocked   a human is needed (permission / question / auth)",
    "working   busy right now",
    "unknown   not enough data - never guessed as idle",
    "done      finished just now",
    "idle      alive, nothing in flight",
    "error     failed; the last line is in the detail",
)

HELP_LINES = (
    "Toolbar: Start opens the launcher, Refresh re-samples, Copy path puts the",
    "selected path on the clipboard, Help shows this.",
    "",
    "Click or the arrow keys select a row; Enter or double-click jumps to its",
    "window. Ctrl+Shift+C copies the path, Ctrl+Shift+R copies the hint in the",
    "status bar. Kill and restart live in the right-click menu.",
    "",
    "Rows read unknown until something reports their state. Anything started",
    "with the Start button reports automatically: the launcher runs the agent",
    "through `ostlr run`, which writes the status file for it.",
)


def legend_lines() -> List[str]:
    """The state legend, one line per state, for the detail column."""
    return list(LEGEND_LINES)


def help_text() -> str:
    """What the panel can do, in plain words, for the Help button."""
    return "\n".join(HELP_LINES)


def unknown_banner(total: int, unknown: int, producers: int = 0) -> str:
    """The one-line explanation shown when nothing knows its state.

    Empty when at least one row has a real state: the banner is for the case
    where the whole list is grey, which is exactly when a user concludes the
    tool is broken. Naming the reason is the difference between "broken" and
    "not connected yet".
    """
    total = max(0, _as_int(total, 0))
    unknown = max(0, _as_int(unknown, 0))
    if total <= 0 or unknown < total:
        return ""
    if producers <= 0:
        return ("Nothing is reporting a state yet. Sessions started with the Start "
                "button report automatically; %d running process(es) were launched "
                "some other way." % total)
    return ("No fresh state for %d session(s) (last report older than %ds). "
            "They will go live again on the next report."
            % (total, _as_int(_stale_seconds(), 90)))


def _stale_seconds() -> int:
    """The staleness bound, from config, without importing it at module load."""
    try:
        from . import config as config_mod
        return int(config_mod.DEFAULT_L2_STALE_S)
    except Exception:
        return 90

def detail_lines(row: Optional[Dict[str, Any]]) -> List[str]:
    """The selected row, spelled out: what it is, where it is, why the state."""
    if not row:
        return ["No row selected.", "", "Click a row to see its command and why",
                "it reads the state it reads."]
    lines = [
        "%s  (pid %s)" % (_as_text(row.get("agent")) or "unknown", _as_text(row.get("pid"))),
        "state      %s  (%s)" % (state_word(row), _as_text(row.get("source")) or "none"),
        "project    %s" % (_as_text(row.get("project")) or "?"),
        "cwd        %s" % (_as_text(row.get("cwd")) or "?"),
        "branch     %s" % branch_text(row.get("branch")),
        "memory     %s" % format_mem(row.get("mem_bytes")),
        "elapsed    %s" % format_elapsed(row.get("elapsed_s")),
    ]
    reason = _as_text(row.get("reason"))
    if reason:
        lines.append("reason     %s" % clip(reason))
    hint = _as_text(row.get("hint"))
    if hint:
        lines.append("")
        lines.append("hint       %s" % clip(hint))
    return lines
