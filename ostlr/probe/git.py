"""Git branch lookup - one subprocess, cached, never on the tick path.

`git -C <cwd> branch --show-current` costs 100-300 ms cold on Windows. Running
it once per row per tick would freeze the panel, so:

  * it runs on the sampler thread only (the caller decides; this module never
    spawns anything on import),
  * results are cached per cwd for `ttl_s` (30 s by default),
  * only the first 20 ranked rows are asked about, so a 60-row list does not
    spawn 60 processes.

Two different "no branch" answers, and they must not be confused:

    "?"   git is absent, the directory is not a repo, or the command failed
    "..." the value has not been fetched yet (the row is past the fetch window)

The caller renders "..." for a cache miss it has not asked about; this module
returns "?" only when it actually asked and got nothing.
"""

from __future__ import annotations

import subprocess
import sys
from typing import Dict, Optional, Tuple

# How many ranked rows get a branch lookup per tick (plan T4).
FETCH_LIMIT = 20

# The two sentinel strings. Kept here so the view and the tests share them.
UNKNOWN_BRANCH = "?"
PENDING_BRANCH = "..."

DEFAULT_TTL_S = 30


class BranchCache:
    """{cwd: (branch, fetched_at)} with a TTL and a per-tick fetch budget."""

    def __init__(self, ttl_s: int = DEFAULT_TTL_S, limit: int = FETCH_LIMIT) -> None:
        self.ttl_s = max(0, int(ttl_s))
        self.limit = max(0, int(limit))
        self._entries: Dict[str, Tuple[str, int]] = {}
        self._fetches_this_tick = 0

    # --- cache --------------------------------------------------------
    def get(self, cwd: str, now: int) -> Optional[str]:
        """The cached branch for `cwd`, or None when absent/expired."""
        entry = self._entries.get(cwd)
        if entry is None:
            return None
        branch, fetched_at = entry
        if self.ttl_s and (int(now) - fetched_at) > self.ttl_s:
            return None
        return branch

    def put(self, cwd: str, branch: str, now: int) -> None:
        self._entries[cwd] = (branch, int(now))

    def clear(self) -> None:
        self._entries.clear()
        self._fetches_this_tick = 0

    # --- per-tick budget ----------------------------------------------
    def begin_tick(self) -> None:
        """Reset the per-tick fetch budget. Called once per sampler tick."""
        self._fetches_this_tick = 0

    @property
    def fetches_this_tick(self) -> int:
        return self._fetches_this_tick

    def budget_left(self) -> int:
        return max(0, self.limit - self._fetches_this_tick)

    def note_fetch(self) -> None:
        self._fetches_this_tick += 1


def _run_git(cwd: str, timeout_s: float = 5.0) -> str:
    """Run the branch query. Returns the branch name or UNKNOWN_BRANCH."""
    creationflags = 0
    if sys.platform.startswith("win"):
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            ["git", "-C", cwd, "branch", "--show-current"],
            capture_output=True,
            timeout=timeout_s,
            creationflags=creationflags,
            check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        # git absent, cwd gone, or a wedged repo: all the same answer.
        return UNKNOWN_BRANCH
    if completed.returncode != 0:
        return UNKNOWN_BRANCH
    branch = (completed.stdout or b"").decode("utf-8", "replace").strip()
    return branch or UNKNOWN_BRANCH


def branch_for(cwd: str, cache: Optional[BranchCache] = None, now: int = 0,
               ttl_s: int = DEFAULT_TTL_S,
               runner=None) -> str:
    """The branch for `cwd`: a cached value, a fresh lookup, or "?".

    `runner` is injectable so a test can count calls without spawning git.
    A cwd of "?" (unknown) is never looked up - there is nothing to ask.
    """
    if not cwd or cwd == UNKNOWN_BRANCH:
        return UNKNOWN_BRANCH
    if cache is None:
        cache = BranchCache(ttl_s=ttl_s)
    cached = cache.get(cwd, now)
    if cached is not None:
        return cached
    if cache.budget_left() <= 0:
        # Past the per-tick window: the caller renders PENDING_BRANCH.
        return PENDING_BRANCH
    cache.note_fetch()
    branch = (runner or _run_git)(cwd)
    cache.put(cwd, branch, now)
    return branch


def branches_for(cwds, cache: Optional[BranchCache] = None, now: int = 0,
                 ttl_s: int = DEFAULT_TTL_S, runner=None) -> Dict[str, str]:
    """Branch for each cwd in order, honouring the per-tick budget.

    Returns {cwd: branch}. A cwd past the budget maps to PENDING_BRANCH, which
    is a different string from UNKNOWN_BRANCH on purpose: "not asked yet" and
    "asked and there is no branch" are different facts.
    """
    if cache is None:
        cache = BranchCache(ttl_s=ttl_s)
    out: Dict[str, str] = {}
    for cwd in cwds:
        if cwd in out:
            continue
        out[cwd] = branch_for(cwd, cache, now, ttl_s, runner)
    return out


def live_check(cwd: str = ".") -> str:
    """One line for the selftest: the branch of the current directory."""
    branch = _run_git(cwd)
    if branch == UNKNOWN_BRANCH:
        return "SKIP (git absent or not a repository)"
    return "PASS git branch: %s" % branch
