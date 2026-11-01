"""Sessions - correlate CIM rows into one row per agent session.

A session is a process tree whose root is an agent CLI (or a shell we can name).
This module is pure: it takes the CIM rows, the L2 records, the branch map and
`now`, and returns session dicts. No I/O, no clock, no subprocess - so the whole
correlation is testable headless and a golden can pin it.

The three cwd sources, in order (plan section 7.1):

    1. the L2 record's `cwd`      - exact, free, and the producer knows it
    2. the first absolute path in the command line - a heuristic, but a good one
    3. "?"                        - unknown, and the row says so

`?` is never rendered as a real path and never used to launch anything: T13's
restart refuses `cwd_unknown` precisely because a wrong cwd starts an agent in
the wrong project, which is the most expensive error this product exists to
avoid.

Root selection, in order (each step exists because of a real failure mode):

    1. a process whose parent is a *different* template is a root - a `claude`
       started by a shell is a session, the shell is not its owner;
    2. an unnamed process with a parent in the sample is a helper, not a row
       (a `node` worker, a `git` call) - otherwise the list is mostly noise;
    3. a root that is a child of another root is a helper of that session,
       unless it is a *named* agent (a shell is a launcher, not the agent);
    4. a cycle of same-template processes has no root by step 1 (each one's
       parent is the same agent), so the lowest pid of each uncovered group
       becomes its representative - a session that exists must never vanish
       because its parent chain is broken.

Step 4 runs last on purpose: running it earlier lets step 3 delete the
representative it just chose, and the cycle reappears as two rows.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Sequence

from . import agents
from .probe import procs

UNKNOWN_CWD = "?"

# An absolute Windows path (C:\... or C:/...) or a POSIX one (/...).
# The quoted alternative comes first so `"C:\Program Files\x"` is taken whole
# instead of stopping at the first space - the case a naive split breaks.
_WIN_PATH_RE = re.compile(r'"([A-Za-z]:[\\/][^"]*)"|([A-Za-z]:[\\/][^"\s]*)')
_POSIX_PATH_RE = re.compile(r'"((?:/[^"]*)+)"|((?:/[^/"\s]+)+)')


def _first_path(match: "re.Match[str]") -> str:
    """The first non-empty group of a two-alternative path match."""
    for group in match.groups():
        if group:
            return group
    return ""


def project_name(cwd: str) -> str:
    """The last path component of a cwd, or "?" when the cwd is unknown."""
    if not cwd or cwd == UNKNOWN_CWD:
        return UNKNOWN_CWD
    text = cwd.replace("\\", "/").rstrip("/")
    if not text:
        return UNKNOWN_CWD
    leaf = text.rsplit("/", 1)[-1]
    return leaf or UNKNOWN_CWD


def cwd_from_cmdline(cmdline: str) -> str:
    """The first absolute path in a command line, or "?".

    Windows paths are tried first: on Windows a POSIX-looking fragment is far
    more likely to be a flag (`--config=/x`) than a real directory.
    """
    if not cmdline:
        return UNKNOWN_CWD
    match = _WIN_PATH_RE.search(cmdline)
    if match:
        return _first_path(match)
    match = _POSIX_PATH_RE.search(cmdline)
    if match:
        return _first_path(match)
    return UNKNOWN_CWD


def resolve_cwd(l2_record: Optional[Dict[str, Any]], cmdline: str) -> str:
    """cwd from the three sources, in order. Never invents a path."""
    if l2_record:
        cwd = l2_record.get("cwd")
        if isinstance(cwd, str) and cwd.strip():
            return cwd.strip()
    return cwd_from_cmdline(cmdline)


def _template_ids(by_pid: Dict[int, Dict[str, Any]]) -> Dict[int, str]:
    """{pid: template id} for every row in the sample."""
    return {
        pid: agents.template_id_for(row.get("exe", ""), row.get("cmdline", ""))
        for pid, row in by_pid.items()
    }


def _same_agent_root(pid: int, by_pid: Dict[int, Dict[str, Any]],
                     template_ids: Dict[int, str],
                     reported: Optional[set] = None) -> int:
    """Walk up to the highest ancestor that is still the same agent.

    A `claude` started by a shell is a child of that shell; the session is the
    claude process, not the shell. A `node` worker under claude is part of
    claude's tree, so the walk stops as soon as the parent is a different
    template. An unnamed process is never a root when it has a parent: it is a
    helper of whatever started it - unless a producer reported it, in which case
    it is a session in its own right.
    """
    reported = reported or set()
    template_id = template_ids.get(pid, agents.UNKNOWN_ID)
    if template_id == agents.UNKNOWN_ID:
        if pid in reported:
            return pid
        parent = by_pid.get(int(by_pid[pid].get("ppid", 0)))
        return pid if parent is None else int(parent["pid"])
    current = pid
    seen = set()
    while True:
        if current in seen:
            # A cycle: no member is a root by this walk, so the caller's
            # representative pass (step 4) resolves the group. Returning `pid`
            # here would make every member its own root and the cycle would
            # show up as N rows.
            return -1
        seen.add(current)
        parent = by_pid.get(int(by_pid[current].get("ppid", 0)))
        if parent is None:
            return current
        parent_pid = int(parent["pid"])
        if template_ids.get(parent_pid, agents.UNKNOWN_ID) != template_id:
            return current
        current = parent_pid


def _select_roots(by_pid: Dict[int, Dict[str, Any]],
                  template_ids: Dict[int, str],
                  reported: Optional[set] = None) -> List[int]:
    """The pids that get a row, in ascending order. See the module docstring.

    `reported` is the set of pids that have a FRESH L2 status record. Such a
    process is always a row, whatever its template and whatever its parent: a
    producer that wrote a record for it is telling us "this is a session", and
    that is stronger evidence than our exe-name guess. Without this, a wrapper
    script or a node/python launcher - exactly the agents M1 exists to observe -
    could never appear, and `ostlr.status --pid <pid>` would be unreachable.
    """
    reported = reported or set()

    # 1. A process whose parent is a different agent is a root.
    roots: List[int] = []
    for pid in sorted(by_pid):
        if _same_agent_root(pid, by_pid, template_ids, reported) == pid:
            roots.append(pid)

    # 2. An unnamed process is not a session. This used to keep parentless
    #    processes "so we can still see them", which filled the panel with
    #    msedge / Explorer / dllhost rows reading `unknown` - a wall the user
    #    cannot use and cannot act on. Only a producer can say "this is a
    #    session" for an unrecognised executable.
    roots = [pid for pid in roots
             if template_ids[pid] != agents.UNKNOWN_ID or pid in reported]

    # 3. A root that is a child of another root is a helper of that session -
    #    unless it is a named agent with an unreported parent. A parent that a
    #    producer reported OWNS the session: the wrapper we launch is the row,
    #    and the agent it started is its child, so listing both would show one
    #    launch as two sessions.
    root_set = set(roots)

    def _keep(pid: int) -> bool:
        if pid in reported:
            return True
        parent = int(by_pid[pid].get("ppid", 0))
        if parent not in root_set:
            return True
        if parent in reported:
            return False
        return template_ids[pid] != agents.UNKNOWN_ID

    roots = [pid for pid in roots if _keep(pid)]

    # 4. A cycle of same-template processes has no root by step 1, so the
    #    lowest pid of each uncovered group represents it. This runs last: an
    #    earlier pass would be undone by step 3 and the cycle would show twice.
    covered = set()
    for pid in roots:
        covered.update(procs.tree_pids(pid, by_pid.values()))
    for pid in sorted(by_pid):
        if pid in covered:
            continue
        if template_ids[pid] == agents.UNKNOWN_ID and pid not in reported:
            continue  # not a session, and step 2 said so
        roots.append(pid)
        covered.update(procs.tree_pids(pid, by_pid.values()))

    roots.sort()
    return roots


def _is_fresh(record: Dict[str, Any], now: int, stale_s: int = 90) -> bool:
    """Is this L2 record still believable at `now`?

    A record with no `age_s` and no `ts` cannot be dated, so it counts as stale:
    guessing "probably fresh" is how a leftover file for a reused pid keeps an
    unrelated process on screen for ever.
    """
    if not isinstance(record, dict):
        return False
    age = record.get("age_s")
    if age is None:
        for key in ("ts_epoch", "epoch"):
            if record.get(key) is not None:
                try:
                    age = int(now) - int(record.get(key))
                except (TypeError, ValueError):
                    return False
                break
    if age is None:
        ts = record.get("ts")
        try:
            from datetime import datetime, timezone
            stamp = datetime.strptime(str(ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            age = int(now) - int(stamp.timestamp())
        except (TypeError, ValueError):
            return False
    try:
        return 0 <= int(age) <= stale_s
    except (TypeError, ValueError):
        return False


def build_sessions(cim_rows: Sequence[Dict[str, Any]],
                   l2_records: Optional[Dict[int, Dict[str, Any]]] = None,
                   branches: Optional[Dict[str, str]] = None,
                   now: int = 0) -> List[Dict[str, Any]]:
    """One session dict per agent root, in pid order.

    A process is a session when its executable names a template, or when a
    producer wrote a fresh status record for it. An unrecognised process is not
    listed: the panel is a list of agents, not a process browser.
    """
    records = l2_records or {}
    branch_map = branches or {}
    by_pid = procs.index_by_pid(cim_rows)
    if not by_pid:
        return []

    template_ids = _template_ids(by_pid)
    # A pid with a FRESH L2 record is a session by definition: the producer knows
    # what it is, even when our exe-name guess does not. A stale record must not
    # qualify - a leftover file whose pid was reused would turn an unrelated
    # process into a session for ever.
    reported = {pid for pid, rec in records.items()
                if pid in by_pid and _is_fresh(rec, now)}
    roots = _select_roots(by_pid, template_ids, reported)

    out: List[Dict[str, Any]] = []
    for pid in roots:
        row = by_pid[pid]
        template_id = template_ids[pid]
        cwd = resolve_cwd(records.get(pid), row.get("cmdline", ""))
        out.append({
            "pid": pid,
            "ppid": int(row.get("ppid", 0)),
            "started_at": int(row.get("started_at", 0)),
            "template_id": template_id,
            "agent": agents.label_for(template_id),
            "project": project_name(cwd),
            "cwd": cwd,
            "cmdline": row.get("cmdline", ""),
            "exe": row.get("exe", ""),
            "mem_bytes": procs.tree_mem(pid, by_pid.values()),
            "branch": branch_map.get(cwd, "?"),
        })
    return out


def cwds_to_fetch(sessions: Sequence[Dict[str, Any]], limit: int = 20) -> List[str]:
    """The distinct cwds worth a git lookup, in session order, capped.

    Only the first `limit` sessions are asked about (plan T4): a 60-row list
    must not spawn 60 git processes per tick. Unknown cwds are skipped - there
    is nothing to ask.
    """
    out: List[str] = []
    for session in sessions[: max(0, int(limit))]:
        cwd = session.get("cwd") or UNKNOWN_CWD
        if cwd == UNKNOWN_CWD or cwd in out:
            continue
        out.append(cwd)
    return out


def apply_branches(sessions: Sequence[Dict[str, Any]],
                   branches: Dict[str, str]) -> List[Dict[str, Any]]:
    """Return copies of the sessions with the branch map applied.

    A cwd that was not fetched keeps "..." (pending), which is a different
    string from "?" (asked, no branch) on purpose.
    """
    out: List[Dict[str, Any]] = []
    for session in sessions:
        copy = dict(session)
        cwd = copy.get("cwd") or UNKNOWN_CWD
        if cwd in branches:
            copy["branch"] = branches[cwd]
        elif cwd == UNKNOWN_CWD:
            copy["branch"] = "?"
        else:
            copy["branch"] = "..."
        out.append(copy)
    return out


def summarise(sessions: Sequence[Dict[str, Any]]) -> Dict[str, int]:
    """Counts for a selftest line."""
    named = sum(1 for s in sessions if s.get("template_id") != agents.UNKNOWN_ID)
    known_cwd = sum(1 for s in sessions if s.get("cwd") != UNKNOWN_CWD)
    return {
        "sessions": len(sessions),
        "named": named,
        "cwd_known": known_cwd,
    }
