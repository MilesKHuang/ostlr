"""The store - cross-tick session memory, owned by the sampler thread.

Why this module exists at all: a dead process is simply *absent* from the next
CIM sample, so "done, retained 600 s", "already_exited" and "the restart kept
its slot" cannot be derived from one snapshot. Something has to remember.

The ownership rule (plan section 8.4) is the whole design:

    the sampler thread is the ONLY writer of store state.

The UI thread never touches a dict here. It calls `submit_intent(...)`, which
appends to a queue; the sampler drains that queue at the start of its next tick
(`apply_intents()`). That is why a launch landing mid-iteration can neither
raise `RuntimeError: dictionary changed size during iteration` nor silently drop
a row - the two failure modes a shared mutable dict would give us.

Identity is `(pid, started_at)`, not `pid`. Windows recycles pids, so a bare pid
would let a new process inherit an old row's state - and, worse, let the kill
path target a process it never inspected. A recycled pid therefore produces a
NEW incarnation and the old row is dropped, never merged.

`state_since` moves only when the state actually changes, so "who has waited
longest" is answerable and a tick that re-reports the same state does not reset
the clock.
"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

from . import ladder

# mirrors schema.py (dev_1 owns the constants; hardcoded here on purpose)
STATES = ("idle", "working", "blocked", "done", "error")

# Intent kinds the UI may queue. Anything else is rejected with a reason rather
# than silently ignored - a typo in a caller must be visible.
INTENT_KINDS = ("mark_dirty", "new_slot", "transient", "already_exited")

# Transient treatments (outside STATES on purpose).
TRANSIENTS = ("starting", "spawn_failed")

# How many intents we are willing to hold. A runaway caller must not grow the
# queue without bound; the oldest are dropped and counted.
MAX_QUEUE = 4096


class StoreError(ValueError):
    """An intent was malformed. Raised to the caller, never swallowed."""


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def identity(pid: Any, started_at: Any) -> Tuple[int, int]:
    """The incarnation key: a pid alone is not an identity."""
    return (_as_int(pid, 0), _as_int(started_at, 0))


class Store:
    """Sampler-owned session memory.

    Public surface (the UI may call all of it from any thread):

        submit_intent(kind, **payload)   queue a mutation
        apply_intents()                  drain the queue (sampler only)
        observe(sessions, ladder, now)   publish a new snapshot (sampler only)
        snapshot()                       the last published rows (any thread)
    """

    def __init__(self, cfg: Any = None) -> None:
        self.cfg = cfg
        self._lock = threading.Lock()          # guards the queue and the snapshot
        self._queue: List[Dict[str, Any]] = []
        self._dropped_intents = 0

        # Sampler-owned state. Only touched inside observe()/apply_intents(),
        # which the sampler calls; the lock above is not held while mutating it.
        self._rows: Dict[Tuple[int, int], Dict[str, Any]] = {}
        self._slots: Dict[Tuple[str, str], Tuple[int, int]] = {}
        self._dirty: set = set()
        self._published: List[Dict[str, Any]] = []
        self._applied = 0
        self._now = 0

    # --- config helpers ------------------------------------------------
    def _done_retain_s(self) -> int:
        return max(0, _as_int(getattr(self.cfg, "done_retain_s", 600), 600))

    def _spawn_error_retain_s(self) -> int:
        return max(0, _as_int(getattr(self.cfg, "spawn_error_retain_s", 30), 30))

    # --- intents (any thread) ------------------------------------------
    def submit_intent(self, kind: str, **payload: Any) -> bool:
        """Queue a mutation for the next sampler tick.

        Returns True when queued. Raises `StoreError` for an unknown kind or a
        missing required field: a caller bug must be loud, not a lost row.
        """
        if kind not in INTENT_KINDS:
            raise StoreError("unknown intent kind: %r (expected one of %s)"
                             % (kind, ", ".join(INTENT_KINDS)))
        if kind in ("mark_dirty", "already_exited", "new_slot", "transient"):
            if "pid" not in payload:
                raise StoreError("%s requires a pid" % kind)
        if kind == "transient" and payload.get("transient") not in TRANSIENTS:
            raise StoreError("transient must be one of %s, got %r"
                             % (", ".join(TRANSIENTS), payload.get("transient")))

        item = {"kind": kind}
        item.update(payload)
        with self._lock:
            if len(self._queue) >= MAX_QUEUE:
                self._queue.pop(0)
                self._dropped_intents += 1
            self._queue.append(item)
        return True

    def pending_intents(self) -> int:
        with self._lock:
            return len(self._queue)

    def dropped_intents(self) -> int:
        with self._lock:
            return self._dropped_intents

    def applied_intents(self) -> int:
        return self._applied

    # --- sampler: drain the queue --------------------------------------
    def apply_intents(self) -> int:
        """Apply every queued intent. Called at sampler tick start only.

        Returns the number applied. Safe to call with an empty queue.
        """
        with self._lock:
            batch = self._queue
            self._queue = []
        for item in batch:
            self._apply_one(item)
        self._applied += len(batch)
        # Publish immediately: an intent's effect must be visible in
        # `snapshot()` without waiting for the next `observe()`. Otherwise a
        # caller that queues `already_exited` and reads the row back sees the
        # old state, which is exactly the defect the verifier found.
        if batch:
            self._publish(self._last_now())
        return len(batch)

    def _last_now(self) -> int:
        """The clock the last publish used, so a publish never goes backwards."""
        return self._now if self._now > 0 else int(time.time())

    def _apply_one(self, item: Dict[str, Any]) -> None:
        kind = item.get("kind")
        pid = _as_int(item.get("pid"), 0)
        started_at = _as_int(item.get("started_at"), 0)
        key = identity(pid, started_at)

        if kind == "mark_dirty":
            # The action path (kill) says "re-judge this row next tick". It does
            # not decide the outcome - the sampler does, from a fresh sample.
            self._dirty.add(key)
            row = self._rows.get(key)
            if row is not None:
                row["dirty"] = True
            return

        if kind == "already_exited":
            # A second kill on a pid we already know is gone. Recorded so the
            # caller can read `already_exited` back instead of re-running
            # taskkill on a pid that may have been recycled.
            row = self._rows.get(key)
            if row is not None:
                row["already_exited"] = True
                row["state"] = "done"
                row["source"] = "l2"
                row["reason"] = "already_exited"
                # A second kill must not be re-run on a pid that may have been
                # recycled, so the row is marked dead here and the sampler's
                # "absent from the sample" pass leaves the reason alone.
                row["alive"] = False
                row["died_at"] = _as_int(item.get("now"), 0) or int(time.time())
            else:
                self._rows[key] = self._new_row(pid, started_at, {
                    "state": "done",
                    "source": "l2",
                    "reason": "already_exited",
                    "alive": False,
                    "already_exited": True,
                })
            return

        if kind == "new_slot":
            # A launch: the row exists immediately as `starting`, so the panel
            # never shows "clicked launch, nothing happened".
            row = self._rows.get(key)
            if row is None:
                row = self._new_row(pid, started_at, {
                    "state": "unknown",
                    "source": "none",
                    "reason": "starting",
                    "alive": True,
                })
                self._rows[key] = row
            row["transient"] = "starting"
            row["transient_since"] = _as_int(item.get("now"), 0) or int(time.time())
            row["template_id"] = _as_text(item.get("template_id"))
            row["cwd"] = _as_text(item.get("cwd"))
            self._adopt_slot(row)
            return

        if kind == "transient":
            row = self._rows.get(key)
            if row is None:
                row = self._new_row(pid, started_at, {
                    "state": "unknown",
                    "source": "none",
                    "reason": "transient",
                    "alive": True,
                })
                self._rows[key] = row
            row["transient"] = item.get("transient")
            row["transient_since"] = _as_int(item.get("now"), 0) or int(time.time())
            if item.get("reason"):
                row["reason"] = _as_text(item.get("reason"))
            return

    # --- sampler: observe ----------------------------------------------
    def observe(self, sessions: Iterable[Dict[str, Any]],
                ladder_results: Optional[Dict[Any, Dict[str, Any]]] = None,
                now: Optional[int] = None) -> List[Dict[str, Any]]:
        """Fold one sample into memory and publish an immutable snapshot.

        `sessions` are the session dicts from `sessions.py`; `ladder_results`
        maps a pid (or an identity tuple) to the ladder's verdict. A session
        with no ladder result is `unknown` - never `idle`.
        """
        if now is None:
            now = int(time.time())
        now = _as_int(now, 0)
        ladder_results = ladder_results or {}
        self._now = now
        seen: set = set()
        for session in sessions or []:
            if not isinstance(session, dict):
                continue
            pid = _as_int(session.get("pid"), 0)
            if pid <= 0:
                continue
            started_at = _as_int(session.get("started_at"), 0)
            key = identity(pid, started_at)
            seen.add(key)

            verdict = self._verdict_for(ladder_results, pid, key)
            row = self._rows.get(key)
            if row is None:
                row = self._new_row(pid, started_at, {})
                self._rows[key] = row

            # Session fields are refreshed every tick; they are facts, not state.
            row["alive"] = True
            row["template_id"] = _as_text(session.get("template_id")) or row.get("template_id", "")
            row["agent"] = _as_text(session.get("agent"))
            row["project"] = _as_text(session.get("project"))
            row["cwd"] = _as_text(session.get("cwd"))
            row["cmdline"] = _as_text(session.get("cmdline"))
            row["exe"] = _as_text(session.get("exe"))
            row["mem_bytes"] = _as_int(session.get("mem_bytes"), 0)
            row["ppid"] = _as_int(session.get("ppid"), 0)

            # The slot is (template, cwd), so it can only be adopted once those
            # fields are known - not at row creation, when they are still empty.
            self._adopt_slot(row)

            # An incarnation we already know has exited must not be turned back
            # into `working` by a later verdict while its pid is still in the
            # sample (a recycled pid, or a producer that keeps reporting). The
            # exit is the more specific fact, so it wins.
            if row.get("already_exited"):
                row["state"] = "done"
                row["source"] = "l2"
                row["reason"] = "already_exited"
                row["alive"] = False
                if row.get("died_at") in (0, None):
                    row["died_at"] = now
                row["dirty"] = False
                continue

            self._set_state(row, verdict, now)
            row["dirty"] = False
        # Anything we knew about that is absent from this sample is dead.
        # A row still marked `starting` is exempt: the launcher queued it before
        # the child existed, so one tick of absence is expected, not a death.
        for key, row in list(self._rows.items()):
            if key in seen:
                continue
            if row.get("transient") == "starting":
                continue
            if row.get("alive"):
                row["alive"] = False
                row["died_at"] = now
                # A process that disappears is `done`; the exit code (if the
                # wrapper wrote one) decides whether it renders as a failure.
                # A row already marked `already_exited` keeps that reason: it is
                # the more specific fact and the caller reads it back.
                if not row.get("already_exited"):
                    self._set_state(row, {"state": "done", "source": "l2",
                                          "reason": "exited"}, now)
            self._expire(row, now)

        self._publish(now)
        return self.snapshot()

    def _verdict_for(self, ladder_results: Dict[Any, Dict[str, Any]],
                     pid: int, key: Tuple[int, int]) -> Dict[str, Any]:
        """Look a ladder verdict up by identity first, then by pid."""
        if key in ladder_results:
            return ladder_results[key] or {}
        if pid in ladder_results:
            return ladder_results[pid] or {}
        return {}

    def _new_row(self, pid: int, started_at: int, extra: Dict[str, Any]) -> Dict[str, Any]:
        row: Dict[str, Any] = {
            "pid": pid,
            "started_at": started_at,
            "state": "unknown",
            "source": "none",
            "transient": None,
            "kind": "",
            "msg": "",
            "age_s": None,
            "state_since": 0,
            "exit_code": None,
            "reason": "",
            "alive": True,
            "template_id": "",
            "agent": "",
            "project": "",
            "cwd": "",
            "cmdline": "",
            "exe": "",
            "mem_bytes": 0,
            "ppid": 0,
            "dirty": False,
            "already_exited": False,
            "died_at": 0,
            "transient_since": 0,
        }
        row.update(extra)
        return row

    def _set_state(self, row: Dict[str, Any], verdict: Dict[str, Any], now: int) -> None:
        """Apply a ladder verdict, moving `state_since` only on a real change."""
        state = _as_text(verdict.get("state")) or "unknown"
        if state not in STATES and state != "unknown":
            state = "unknown"
        source = _as_text(verdict.get("source")) or "none"
        if source not in ("l2", "l3", "none"):
            source = "none"

        # The same choke point the view model uses: a stale L2 verdict can never
        # keep its state, so the store cannot publish `idle` from a 200 s old
        # record either. Fixing only one of the two call sites would leave the
        # never-idle rule broken on the other path.
        effective = ladder.effective_state(state, source, verdict.get("age_s"),
                                           self.cfg)
        state = effective["state"]
        source = effective["source"]
        if effective["reason"]:
            verdict = dict(verdict)
            verdict["reason"] = effective["reason"]
        previous = row.get("state")
        if state != previous:
            row["state_since"] = now
        row["state"] = state
        row["source"] = source
        row["reason"] = _as_text(verdict.get("reason"))
        row["kind"] = _as_text(verdict.get("kind"))
        row["msg"] = _as_text(verdict.get("msg"))
        age = verdict.get("age_s")
        row["age_s"] = None if age is None else _as_int(age, 0)
        exit_code = verdict.get("exit_code")
        if exit_code is not None:
            row["exit_code"] = _as_int(exit_code, 0)

        # A real state supersedes a transient: `starting` lasts until the first
        # verdict arrives, and a failed start becomes `error` within one tick.
        # A verdict of `unknown` does NOT clear it - `unknown` is the absence of
        # a verdict, and clearing on it would make a launching row flicker back
        # to a plain unknown row before the wrapper's first status file lands.
        if row.get("transient") == "starting" and state in STATES:
            row["transient"] = None
            row["transient_since"] = 0
        elif row.get("transient") == "starting" and source == "none":
            # No verdict at all this tick: keep the transient, but do not let the
            # state word fall back to a bare `unknown` while we are launching.
            row["state"] = "unknown"

    def _adopt_slot(self, row: Dict[str, Any]) -> None:
        """Remember which slot a row occupies, keyed by (template, cwd).

        A restart is a new identity, so the slot is how the row keeps its place
        in the list instead of jumping to the bottom.
        """
        template = _as_text(row.get("template_id"))
        cwd = _as_text(row.get("cwd"))
        if not template and not cwd:
            return
        self._slots[(template, cwd)] = identity(row.get("pid"), row.get("started_at"))

    def slot_of(self, template_id: str, cwd: str) -> Optional[Tuple[int, int]]:
        """The identity currently occupying a (template, cwd) slot."""
        return self._slots.get((_as_text(template_id), _as_text(cwd)))

    def _expire(self, row: Dict[str, Any], now: int) -> None:
        """Drop a dead row once its retention window has passed."""
        if row.get("alive"):
            return
        died_at = _as_int(row.get("died_at"), 0)
        if died_at <= 0:
            return
        retain = self._done_retain_s()
        if row.get("state") == "error":
            retain = max(retain, self._spawn_error_retain_s())
        if now - died_at >= retain:
            key = identity(row.get("pid"), row.get("started_at"))
            self._rows.pop(key, None)
            for slot, ident in list(self._slots.items()):
                if ident == key:
                    self._slots.pop(slot, None)

    def _publish(self, now: int) -> None:
        """Freeze the current rows into an immutable snapshot list."""
        out: List[Dict[str, Any]] = []
        for row in self._rows.values():
            published = dict(row)
            started = _as_int(row.get("started_at"), 0)
            since = _as_int(row.get("state_since"), 0)
            published["elapsed_s"] = max(0, now - started) if started > 0 else None
            published["waiting_s"] = max(0, now - since) if since > 0 else None
            out.append(published)
        with self._lock:
            self._published = out

    # --- readers (any thread) ------------------------------------------
    def snapshot(self) -> List[Dict[str, Any]]:
        """The last published rows. A copy, so a reader cannot mutate state."""
        with self._lock:
            return [dict(r) for r in self._published]

    def row_for(self, pid: int, started_at: Optional[int] = None) -> Optional[Dict[str, Any]]:
        """One row by identity (or by pid when `started_at` is omitted)."""
        for row in self.snapshot():
            if _as_int(row.get("pid"), 0) != _as_int(pid, 0):
                continue
            if started_at is None or _as_int(row.get("started_at"), 0) == _as_int(started_at, 0):
                return row
        return None

    def is_already_exited(self, pid: int, started_at: Optional[int] = None) -> bool:
        row = self.row_for(pid, started_at)
        return bool(row and row.get("already_exited"))

    def row_count(self) -> int:
        with self._lock:
            return len(self._published)

    def reset(self) -> None:
        """Forget everything (a new session, or a test)."""
        with self._lock:
            self._queue = []
            self._published = []
        self._rows = {}
        self._slots = {}
        self._dirty = set()
