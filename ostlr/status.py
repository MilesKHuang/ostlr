"""The L2 producer: an atomic status writer and the `run` wrapper.

Two jobs, one file, because they share the contract:

  * `write_status(...)` writes `%LOCALAPPDATA%\\ostlr\\status\\<pid>.json`
    atomically (temp file + `os.replace`). A reader must never see a half-written
    file, so the temp file is created in the same directory and renamed in one
    step; a crash leaves either the old file or the new one, never a fragment.
  * `run(argv)` launches a child in its OWN console and reports its life cycle
    into that same file.

Why the wrapper exists at all (plan section 8.5): the panel must not hold a
handle on the child. If it did, closing the panel would break the child's pipes
and the agent would die with the panel - the exact failure the exit contract
forbids. So the wrapper owns the console, waits for the child, and writes the
exit code; the panel reads it like any other producer.

Consequences that are load-bearing:

  * `CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP`, never `DETACHED_PROCESS`.
    A detached console app gets NO console, so a TUI agent would have nowhere to
    draw - "clicked launch, nothing happened".
  * NO pipes on the child's stdio. Redirecting the child's output would both
    suppress its console rendering and die with us. The child inherits the new
    console, so the wrapper never holds a handle it could break.
  * The heartbeat is 10 s, well under `l2_stale_s` (90 s), so a long-running
    agent never reads as `unknown(stale)` while it is fine.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from . import paths
from . import schema

# mirrors schema.py / config.py (dev_1 owns those; hardcoded here on purpose)
SCHEMA_VERSION = 1
STATES = ("idle", "working", "blocked", "done", "error")
L2_KINDS = ("permission", "question", "auth")
CLIP_CHARS = 120

# The wrapper's own heartbeat. Must stay well under l2_stale_s (90 s).
HEARTBEAT_S = 10

# Windows refuses to replace a file another handle has open, so the atomic
# rename is retried a few times before the write is reported as failed.
REPLACE_RETRIES = 24
REPLACE_RETRY_S = 0.01

# Windows creation flags. CREATE_NEW_CONSOLE gives the child a visible console;
# CREATE_NEW_PROCESS_GROUP keeps Ctrl+C in the panel from reaching it.
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200

SOURCE_WRAPPER = "ostlr-run"


# --- time -----------------------------------------------------------------

def iso_utc(epoch: Optional[float] = None) -> str:
    """ISO-8601 in UTC with a trailing Z, second resolution."""
    moment = datetime.fromtimestamp(
        time.time() if epoch is None else float(epoch), tz=timezone.utc
    )
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


# --- the atomic writer ----------------------------------------------------

def status_path(pid: int, status_dir: Optional[str] = None) -> Path:
    """Where the status file for one pid lives (honours OSTLR_STATUS_DIR)."""
    if status_dir:
        return Path(status_dir) / ("%d.json" % int(pid))
    return paths.status_file(pid)


def build_record(pid: int, state: str, kind: str = "", msg: str = "",
                 cwd: str = "", exit_code: Optional[int] = None,
                 source: str = SOURCE_WRAPPER,
                 ts: Optional[str] = None) -> Dict[str, Any]:
    """The L2 record exactly as plan section 8.2 defines it.

    An invalid `state` is not silently rewritten: the caller gets a ValueError,
    because a producer that writes a state nobody understands is a bug in the
    producer, and the reader would have to guess.
    """
    key = (state or "").strip().lower()
    if key not in STATES:
        raise ValueError("state must be one of %s, got %r" % (", ".join(STATES), state))
    kind_key = (kind or "").strip().lower()
    if kind_key and kind_key not in L2_KINDS:
        raise ValueError("kind must be one of %s, got %r" % (", ".join(L2_KINDS), kind))
    if key == "blocked" and not kind_key:
        # The panel uses `kind` to decide "wake the human"; a blocked record
        # without one is unusable, so refuse it here rather than at the reader.
        raise ValueError("state=blocked requires a kind (%s)" % ", ".join(L2_KINDS))

    text = (msg or "").replace("\r", " ").replace("\n", " ").strip()
    if len(text) > CLIP_CHARS:
        text = text[: CLIP_CHARS - 1] + "\u2026"

    return {
        "v": SCHEMA_VERSION,
        "ts": ts or iso_utc(),
        "source": source or SOURCE_WRAPPER,
        "pid": int(pid),
        "cwd": (cwd or "").replace("\\", "/"),
        "state": key,
        "kind": kind_key,
        "msg": text,
        "exit_code": None if exit_code is None else int(exit_code),
    }


def write_status(pid: int, state: str, kind: str = "", msg: str = "",
                 cwd: str = "", exit_code: Optional[int] = None,
                 status_dir: Optional[str] = None,
                 source: str = SOURCE_WRAPPER) -> Path:
    """Write one status file atomically. Returns the path written.

    Atomicity: the payload goes to `<pid>.json.<pid>.<thread>.tmp` in the same
    directory, is flushed and fsynced, then `os.replace`d over the target. A
    reader therefore sees the old record or the new one, never a fragment, and a
    crash cannot leave a `.tmp` that a later run would mistake for a record.
    """
    record = build_record(pid, state, kind=kind, msg=msg, cwd=cwd,
                          exit_code=exit_code, source=source)
    target = status_path(pid, status_dir)
    target.parent.mkdir(parents=True, exist_ok=True)

    tmp = target.with_name("%s.%d.%d.tmp" % (target.name, os.getpid(),
                                             threading.get_ident()))
    payload = json.dumps(record, ensure_ascii=False)

    # Windows refuses to replace a file another handle has open (a reader, or a
    # concurrent writer's rename), so the rename is retried briefly. Without
    # this, two producers racing on one pid would surface as a spurious
    # PermissionError instead of "last writer wins".
    last_error: Optional[OSError] = None
    for attempt in range(REPLACE_RETRIES):
        try:
            with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, target)
            return target
        except OSError as exc:
            last_error = exc
            # Back off but keep the total bounded: 24 attempts of at most 50 ms
            # is under a second, and that is enough for a contended rename to
            # land. An unbounded sleep here would stall a producer's heartbeat.
            time.sleep(min(REPLACE_RETRY_S * (attempt + 1), 0.05))
            try:
                tmp.unlink()
            except OSError:
                pass

    # A read-only or denied status directory is a condition the panel reports;
    # it must not crash the producer mid-turn.
    assert last_error is not None
    raise last_error


def clear_status(pid: int, status_dir: Optional[str] = None) -> bool:
    """Delete one status file. True when it is gone afterwards."""
    target = status_path(pid, status_dir)
    try:
        target.unlink()
        return True
    except FileNotFoundError:
        return True
    except OSError:
        return False


def read_status(pid: int, status_dir: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Read one status file back (used by the tests and by `--selftest`)."""
    target = status_path(pid, status_dir)
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None

# --- the run wrapper ------------------------------------------------------

def _creation_flags() -> int:
    """CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP on Windows, 0 elsewhere.

    Never DETACHED_PROCESS: a detached console app gets no console at all, so a
    TUI agent would have nowhere to draw.
    """
    if os.name != "nt":
        return 0
    return CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP


def _first_line(text: Optional[str]) -> str:
    """The first non-empty line of a reason, clipped - never a whole traceback."""
    if not text:
        return ""
    for line in str(text).splitlines():
        line = line.strip()
        if line:
            return line[:CLIP_CHARS]
    return ""


class Heartbeat:
    """Rewrites the wrapper's `working` record every HEARTBEAT_S seconds.

    A daemon thread, so a stuck child can never keep the process alive. The
    interval is well under `l2_stale_s`, so a long-running agent never reads as
    `unknown(stale)` while it is fine.
    """

    def __init__(self, pid: int, cwd: str, status_dir: Optional[str],
                 interval_s: float = HEARTBEAT_S) -> None:
        self.pid = pid
        self.cwd = cwd
        self.status_dir = status_dir
        self.interval_s = max(0.05, float(interval_s))
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.beats = 0

    def start(self) -> "Heartbeat":
        self._thread = threading.Thread(target=self._loop, name="ostlr-heartbeat",
                                        daemon=True)
        self._thread.start()
        return self

    def _loop(self) -> None:
        while not self._stop.wait(self.interval_s):
            try:
                write_status(self.pid, "working", msg="running", cwd=self.cwd,
                             status_dir=self.status_dir)
                self.beats += 1
            except OSError:
                # A denied status directory must not kill the wrapper: the child
                # is the user's work, the status file is our convenience.
                pass

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)


def _child_env() -> Dict[str, str]:
    """The environment the launched agent gets.

    The panel injects its own package root into PYTHONPATH so the wrapper
    (`python -m ostlr run`) resolves from any project directory. The agent must
    not inherit that: it would leak our source tree into every launched shell.
    `OSTLR_WRAPPER_ROOT` names the injected entry, so it is removed here and the
    marker itself is dropped.
    """
    env = dict(os.environ)
    root = env.pop(schema.WRAPPER_ROOT_ENV, "")
    if not root:
        return env
    current = env.get("PYTHONPATH", "")
    parts = [p for p in current.split(os.pathsep) if p and os.path.normcase(p) != os.path.normcase(root)]
    if parts:
        env["PYTHONPATH"] = os.pathsep.join(parts)
    else:
        env.pop("PYTHONPATH", None)
    return env


def run(argv: Any, status_dir: Optional[str] = None,
        cwd: Optional[str] = None, heartbeat_s: float = HEARTBEAT_S) -> int:
    """Launch `argv` in its own console, report its life cycle, return its code.

    `argv` is EITHER a list of argv elements (the launcher's path: the template
    already split exe from args) OR a single raw string (the restart path: the
    raw CIM `CommandLine` must reach CreateProcess byte-identically). A list goes
    to `Popen` as a list, a string as a string - never `shell=True`, and never a
    split of a raw string, because splitting would break every quoted path with
    a space in it.

    The wrapper's own pid is the session root the panel sees, so the status file
    is keyed to `os.getpid()` - not to the child's pid, which the panel would
    have to correlate.
    """
    if isinstance(argv, str):
        # The raw-cmdline path. `Popen` on Windows hands a string straight to
        # CreateProcess, which is exactly what preserves the quoting.
        spawn_arg: Any = argv
        label = argv
        if not argv.strip():
            print("[ostlr] run: no command given", file=sys.stderr)
            return 2
    else:
        args = [str(a) for a in (argv or [])]
        if not args:
            print("[ostlr] run: no command given", file=sys.stderr)
            return 2
        spawn_arg = args
        label = args[0]

    wrapper_pid = os.getpid()
    work_dir = cwd or os.getcwd()

    try:
        write_status(wrapper_pid, "working", msg="starting", cwd=work_dir,
                     status_dir=status_dir)
    except OSError as exc:
        print("[ostlr] run: cannot write status: %s" % exc, file=sys.stderr)

    try:
        # No pipes: the child owns its console. A pipe here would both suppress
        # the child's rendering and die with us when the panel closes.
        child = subprocess.Popen(spawn_arg, cwd=work_dir, env=_child_env(),
                                 creationflags=_creation_flags())
    except (OSError, ValueError) as exc:
        # Name the command: "cannot find the file specified" alone does not tell
        # the user which file, and the launcher's whole job is a readable reason.
        reason = "%s: %s" % (label, _first_line(str(exc)) or "spawn failed")
        reason = reason[:CLIP_CHARS]
        try:
            write_status(wrapper_pid, "error", msg=reason, cwd=work_dir,
                         status_dir=status_dir)
        except OSError:
            pass
        print("[ostlr] run: %s" % reason, file=sys.stderr)
        return 127

    beat = Heartbeat(wrapper_pid, work_dir, status_dir, heartbeat_s).start()
    try:
        code = child.wait()
    except KeyboardInterrupt:
        # Ctrl+C in the wrapper's console: pass it on, then report honestly.
        try:
            child.terminate()
        except OSError:
            pass
        code = child.wait()
    finally:
        beat.stop()

    try:
        write_status(wrapper_pid, "done", msg="exited", cwd=work_dir,
                     exit_code=code, status_dir=status_dir)
    except OSError as exc:
        print("[ostlr] run: cannot write final status: %s" % exc, file=sys.stderr)
    return int(code)


# --- CLI ------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ostlr.status",
        description="Write one L2 status record for a pid (the producer CLI).",
    )
    parser.add_argument("--pid", type=int, default=None,
                        help="the session root pid (default: this process)")
    parser.add_argument("--state", default=None,
                        help="one of %s" % ", ".join(STATES))
    parser.add_argument("--kind", default="",
                        help="with --state blocked: %s" % ", ".join(L2_KINDS))
    parser.add_argument("--msg", default="", help="one human-readable line")
    parser.add_argument("--cwd", default="", help="the session's working directory")
    parser.add_argument("--exit-code", type=int, default=None,
                        help="the child's exit code, when it has ended")
    parser.add_argument("--source", default=SOURCE_WRAPPER,
                        help="who reported this (default: %s)" % SOURCE_WRAPPER)
    parser.add_argument("--status-dir", default=None,
                        help="override the status directory (default: OSTLR_STATUS_DIR)")
    parser.add_argument("--clear", action="store_true",
                        help="delete the record for --pid instead of writing one")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    pid = args.pid if args.pid is not None else os.getpid()

    if args.clear:
        ok = clear_status(pid, args.status_dir)
        if not ok:
            print("[ostlr.status] cannot delete the record for pid %d" % pid,
                  file=sys.stderr)
            return 1
        return 0

    if not args.state:
        print("[ostlr.status] --state is required (or use --clear)", file=sys.stderr)
        return 2

    try:
        target = write_status(pid, args.state, kind=args.kind, msg=args.msg,
                              cwd=args.cwd, exit_code=args.exit_code,
                              status_dir=args.status_dir, source=args.source)
    except ValueError as exc:
        print("[ostlr.status] %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("[ostlr.status] cannot write %s: %s" % (status_path(pid, args.status_dir), exc),
              file=sys.stderr)
        return 1

    print(str(target))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
