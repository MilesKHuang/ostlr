"""Process tree and per-tree memory - pure functions, no I/O.

The panel shows one row per *session*, and a session is a process tree: an agent
CLI plus the shells, node workers and compilers it spawned. So the two questions
here are:

  * which rows are roots (a session head) and which are children of another row,
  * how much memory the whole tree of a root is using.

Both are answered from one CIM sample, in O(N), with no syscalls.

Three edge cases are the reason this is a module and not three lines inline:

  * **A cycle.** A recycled pid can make A the parent of B and B the parent of A.
    A naive DFS recurses forever; the visited set makes it terminate.
  * **An orphan.** A root whose parent has already exited is still a session -
    it must appear, not vanish.
  * **A recycled pid.** The same pid appearing twice in one sample (or a child
    claiming a pid that is also a root) must yield one row, not two.

Depth is capped at 32: a deeper chain is not a session, it is a runaway, and
walking it adds nothing but risk.
"""

from __future__ import annotations

from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

# A session tree deeper than this is not a session; stop walking.
MAX_DEPTH = 32


def _pid(row: Dict) -> int:
    try:
        return int(row.get("pid", -1))
    except (TypeError, ValueError):
        return -1


def _ppid(row: Dict) -> int:
    try:
        return int(row.get("ppid", 0))
    except (TypeError, ValueError):
        return 0


def _mem(row: Dict) -> int:
    try:
        value = int(row.get("mem_bytes", 0))
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def index_by_pid(rows: Iterable[Dict]) -> Dict[int, Dict]:
    """{pid: row}, first occurrence wins.

    First-wins is deliberate: a duplicate pid in one sample is a sampling
    artefact, and keeping the first keeps the result stable between ticks
    instead of flickering between two rows.
    """
    out: Dict[int, Dict] = {}
    for row in rows:
        pid = _pid(row)
        if pid < 0 or pid in out:
            continue
        out[pid] = row
    return out


def children_map(rows: Iterable[Dict]) -> Dict[int, List[int]]:
    """{ppid: [child pid, ...]} for every parent that has at least one child.

    A child whose parent is not in the sample is not listed here - it is a root
    (see `roots`), which is exactly how an orphan stays visible.
    """
    by_pid = index_by_pid(rows)
    out: Dict[int, List[int]] = {}
    for pid, row in by_pid.items():
        parent = _ppid(row)
        if parent == pid:
            # A process that claims to be its own parent is a root, not a child.
            continue
        if parent not in by_pid:
            continue
        out.setdefault(parent, []).append(pid)
    for kids in out.values():
        kids.sort()
    return out


def roots(rows: Iterable[Dict]) -> List[int]:
    """Pids whose parent is absent from the sample (or is themselves).

    Sorted ascending so the result is deterministic - a golden test and a
    stable row order both depend on it.
    """
    by_pid = index_by_pid(rows)
    out: List[int] = []
    for pid, row in by_pid.items():
        parent = _ppid(row)
        if parent == pid or parent not in by_pid:
            out.append(pid)
    out.sort()
    return out


def tree_pids(root_pid: int, rows: Iterable[Dict],
              max_depth: int = MAX_DEPTH) -> List[int]:
    """Every pid in the tree under `root_pid`, root first, cycle-safe.

    The visited set is what makes a cyclic ppid fixture terminate; the depth cap
    bounds a pathological chain even when the graph is acyclic.
    """
    by_pid = index_by_pid(rows)
    if root_pid not in by_pid:
        return []
    kids = children_map(by_pid.values())

    out: List[int] = []
    seen: Set[int] = set()
    stack: List[Tuple[int, int]] = [(root_pid, 0)]
    while stack:
        pid, depth = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        out.append(pid)
        if depth >= max_depth:
            continue
        for child in reversed(kids.get(pid, [])):
            if child not in seen:
                stack.append((child, depth + 1))
    return out


def tree_mem(root_pid: int, rows: Iterable[Dict],
             max_depth: int = MAX_DEPTH) -> int:
    """Aggregated working set of the whole tree under `root_pid`."""
    by_pid = index_by_pid(rows)
    total = 0
    for pid in tree_pids(root_pid, by_pid.values(), max_depth=max_depth):
        total += _mem(by_pid[pid])
    return total


def tree_size(root_pid: int, rows: Iterable[Dict],
              max_depth: int = MAX_DEPTH) -> int:
    """How many processes the tree contains (the kill confirmation's count)."""
    return len(tree_pids(root_pid, rows, max_depth=max_depth))


def build_trees(rows: Sequence[Dict],
                max_depth: int = MAX_DEPTH) -> List[Dict]:
    """One entry per root: {pid, pids, mem_bytes, depth_capped}.

    `depth_capped` is True when the walk stopped at the cap, so a caller can say
    "the tree is deeper than we walked" instead of implying it saw everything.
    """
    by_pid = index_by_pid(rows)
    out: List[Dict] = []
    for root_pid in roots(by_pid.values()):
        pids = tree_pids(root_pid, by_pid.values(), max_depth=max_depth)
        mem = sum(_mem(by_pid[p]) for p in pids)
        capped = False
        if pids:
            # A tree is capped when any leaf sits exactly at the depth limit and
            # still has children we did not walk.
            kids = children_map(by_pid.values())
            for pid in pids:
                if kids.get(pid) and _depth_of(pid, root_pid, by_pid) >= max_depth:
                    capped = True
                    break
        out.append({
            "pid": root_pid,
            "pids": pids,
            "mem_bytes": mem,
            "depth_capped": capped,
        })
    return out


def _depth_of(pid: int, root_pid: int, by_pid: Dict[int, Dict]) -> int:
    """Distance from `root_pid` to `pid`, walking parents; -1 when unreachable."""
    depth = 0
    seen: Set[int] = set()
    current = pid
    while current != root_pid:
        if current in seen or current not in by_pid:
            return -1
        seen.add(current)
        current = _ppid(by_pid[current])
        depth += 1
        if depth > MAX_DEPTH * 2:
            return -1
    return depth


def summarise(rows: Sequence[Dict]) -> Dict[str, int]:
    """Counts for a selftest line: rows, roots, and the largest tree."""
    by_pid = index_by_pid(rows)
    root_list = roots(by_pid.values())
    biggest = 0
    for root_pid in root_list:
        biggest = max(biggest, tree_size(root_pid, by_pid.values()))
    return {
        "rows": len(by_pid),
        "roots": len(root_list),
        "largest_tree": biggest,
    }
