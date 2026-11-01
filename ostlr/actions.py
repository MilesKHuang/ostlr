"""Actions - the four things the panel can DO to a session.

    focus(pid)              bring a window to the front
    launch(template, cwd)   start an agent in its own console
    kill(session, confirm)  end a process tree, after a confirmation
    restart(session)        relaunch the same command line in the same cwd

One rule holds the whole module together: **a failure is a value, never an
exception**. Every function returns a dict with `ok` and `reason`, so the UI can
render "focus refused, taskbar flashed" instead of a traceback, and a test can
assert on the reason string.

The three guards that matter (plan 7.2 / 7.3 / 8.5):

  * `focused` is reported only when `GetForegroundWindow()` really matches the
    window we asked for. Windows silently refuses `SetForegroundWindow` from a
    background process, and claiming success there would be a lie.
  * `kill` re-reads the process's creation time and refuses with
    `identity_unverified` when it is 0, unparsable or different from the stored
    `started_at`. A recycled pid must never be killed - that is the one mistake
    this product cannot make.
  * `restart` passes the RAW CIM `CommandLine` string through unmodified. Any
    `shlex.split` / `.split()` would break every path containing a space, and
    the relaunched agent would start in the wrong place.

Everything is injectable (`backend`, `runner`, `identity_probe`, `popen`) so the
whole module is testable headless: no test opens a window, kills a real process,
or leaves a child running.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import schema

# mirrors config.py (dev_1 owns the constant; hardcoded here on purpose)
SPAWN_ERROR_RETAIN_S = 30

# Windows creation flags. CREATE_NEW_CONSOLE gives the child a visible console;
# CREATE_NEW_PROCESS_GROUP keeps Ctrl+C in the panel from reaching it.
CREATE_NEW_CONSOLE = 0x00000010
CREATE_NEW_PROCESS_GROUP = 0x00000200

# Reasons. They are user-visible, so they are short and specific.
REASON_FOCUSED = "focused"
REASON_FOREGROUND_REFUSED = "foreground_refused"
REASON_NO_WINDOW = "no_window"
REASON_IDENTITY_UNVERIFIED = "identity_unverified"
REASON_CWD_UNKNOWN = "cwd_unknown"
REASON_RESTART_PENDING = "restart_pending"
REASON_ALREADY_EXITED = "already_exited"
REASON_KILL_REQUESTED = "kill requested (pending)"
REASON_KILLED = "killed"
REASON_SURVIVORS = "survivors"
REASON_SPAWN_FAILED = "spawn_failed"
REASON_LAUNCHED = "launched"
REASON_NO_COMMAND = "no_command"
REASON_CANCELLED = "cancelled"

# The cwd marker `sessions.py` uses for "we do not know".
UNKNOWN_CWD = "?"

# The env var that tells the wrapper which package root was injected for it, so
# `status.run()` can hand the launched agent a clean PYTHONPATH again.
WRAPPER_ROOT_ENV = schema.WRAPPER_ROOT_ENV

def ok(reason: str = "", **extra: Any) -> Dict[str, Any]:
    """A success value."""
    out: Dict[str, Any] = {"ok": True, "reason": reason}
    out.update(extra)
    return out


def fail(reason: str, **extra: Any) -> Dict[str, Any]:
    """A failure value. Never raises, never returns None."""
    out: Dict[str, Any] = {"ok": False, "reason": reason}
    out.update(extra)
    return out


def _as_int(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_text(value: Any) -> str:
    return "" if value is None else str(value)


def _format_mem(mem_bytes: int) -> str:
    value = max(0, _as_int(mem_bytes, 0))
    if value >= 1024 ** 3:
        return "%.1f GB" % (value / 1024 ** 3)
    if value >= 1024 ** 2:
        return "%d MB" % int(round(value / 1024 ** 2))
    if value >= 1024:
        return "%d KB" % int(round(value / 1024))
    return "%d B" % value


# --- focus ----------------------------------------------------------------

class Win32FocusBackend:
    """The real backend: ctypes user32.

    Imported lazily so the module loads headless (and on a non-Windows host)
    without touching windll.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self.user32 = ctypes.windll.user32

    def find_window(self, pid: int) -> Optional[int]:
        """The first visible top-level window owned by `pid`, or None."""
        ctypes = self._ctypes
        wintypes = self._wintypes
        found: List[int] = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

        def _cb(hwnd, _lparam):
            try:
                if not self.user32.IsWindowVisible(hwnd):
                    return True
                owner = wintypes.DWORD(0)
                self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                if owner.value == int(pid):
                    found.append(int(hwnd))
                    return False
            except Exception:
                pass
            return True

        try:
            self.user32.EnumWindows(WNDENUMPROC(_cb), 0)
        except Exception:
            return None
        return found[0] if found else None

    def set_foreground(self, hwnd: int) -> bool:
        try:
            return bool(self.user32.SetForegroundWindow(hwnd))
        except Exception:
            return False

    def get_foreground(self) -> int:
        try:
            return int(self.user32.GetForegroundWindow())
        except Exception:
            return 0

    def flash(self, hwnd: int) -> None:
        """Best effort: a taskbar flash when Windows refuses the focus."""
        try:
            import ctypes
            from ctypes import wintypes

            class FLASHWINFO(ctypes.Structure):
                _fields_ = [
                    ("cbSize", wintypes.UINT),
                    ("hwnd", wintypes.HWND),
                    ("dwFlags", wintypes.DWORD),
                    ("uCount", wintypes.UINT),
                    ("dwTimeout", wintypes.DWORD),
                ]

            info = FLASHWINFO()
            info.cbSize = ctypes.sizeof(FLASHWINFO)
            info.hwnd = hwnd
            info.dwFlags = 0x00000003  # FLASHW_ALL
            info.uCount = 3
            info.dwTimeout = 0
            self.user32.FlashWindowEx(ctypes.byref(info))
        except Exception:
            pass


def focus(pid: int, backend: Any = None) -> Dict[str, Any]:
    """Bring the pid's window to the front.

    `focused` only when `GetForegroundWindow()` matches the window we asked for.
    A refusal is `foreground_refused` plus a best-effort flash - never a silent
    no-op, and never a claim of success.
    """
    pid = _as_int(pid, 0)
    if pid <= 0:
        return fail(REASON_NO_WINDOW, pid=pid)

    if backend is None:
        if os.name != "nt":
            return fail(REASON_NO_WINDOW, pid=pid, detail="not windows")
        try:
            backend = Win32FocusBackend()
        except Exception as exc:  # pragma: no cover - broken Windows install
            return fail(REASON_NO_WINDOW, pid=pid, detail=str(exc))

    hwnd = backend.find_window(pid)
    if not hwnd:
        return fail(REASON_NO_WINDOW, pid=pid)

    backend.set_foreground(hwnd)
    if _as_int(backend.get_foreground(), 0) == _as_int(hwnd, 0):
        return ok(REASON_FOCUSED, pid=pid, hwnd=hwnd)

    # Windows refused. Flash so the user can find it, and say so.
    try:
        backend.flash(hwnd)
    except Exception:
        pass
    return fail(REASON_FOREGROUND_REFUSED, pid=pid, hwnd=hwnd)


# --- launch ---------------------------------------------------------------

def creation_flags() -> int:
    """CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP on Windows, 0 elsewhere.

    Never DETACHED_PROCESS: a detached console app gets no console at all, so a
    TUI agent would have nowhere to draw.
    """
    if os.name != "nt":
        return 0
    return CREATE_NEW_CONSOLE | CREATE_NEW_PROCESS_GROUP


def package_root() -> str:
    """The directory `ostlr` is importable from (the repo root when run in place)."""
    here = os.path.abspath(__file__)
    return os.path.dirname(os.path.dirname(here))


def wrapper_env(base: Optional[Dict[str, str]] = None) -> Dict[str, str]:
    """The environment the wrapper is started with.

    The wrapper is `python -m ostlr run -- ...`, and it is started with the
    CHOSEN PROJECT DIRECTORY as its cwd - so without this, `-m ostlr` cannot be
    resolved there and the wrapper dies before the agent starts ("nothing
    happened" with no row and no reason). Prepending the package root to
    PYTHONPATH makes the wrapper importable from any directory, which is the
    whole point of a launcher. `status.run()` strips it again for the child.
    """
    env = dict(os.environ if base is None else base)
    root = package_root()
    current = env.get("PYTHONPATH", "")
    parts = [p for p in current.split(os.pathsep) if p] if current else []
    if root not in parts:
        parts.insert(0, root)
    env["PYTHONPATH"] = os.pathsep.join(parts)
    env[WRAPPER_ROOT_ENV] = root
    return env


def launch(template: Any, cwd: str, popen: Any = None,
           python: Optional[str] = None,
           argv: Optional[Sequence[str]] = None) -> Dict[str, Any]:
    """Start an agent in its own console and return its pid.

    The command runs through our own wrapper (`py -3 -m ostlr run -- <cmd>`), so
    the child's life cycle is reported into the status file and the panel never
    needs a handle on it. No pipes: the child owns its console.

    The argv is a LIST of elements, never one joined string: `Popen` without a
    shell hands the list to CreateProcess, and a single string containing
    "exe /t 60 /nobreak" would be looked up as a file with that literal name
    (WinError 2). The template already stores the command and its args
    separately, so nothing is split here - a raw CIM command line is only ever
    passed through by `restart()`.

    `argv` lets the launcher supply the exact wrapper argv (it owns the
    `command_for` decision); without it the argv is built here.
    """
    if isinstance(template, dict):
        command = _as_text(template.get("command"))
        extra = template.get("args") or []
    else:
        command = _as_text(getattr(template, "command", ""))
        extra = getattr(template, "args", ()) or ()
    if not command:
        return fail(REASON_NO_COMMAND)

    work_dir = _as_text(cwd)
    if not work_dir or work_dir == UNKNOWN_CWD:
        return fail(REASON_CWD_UNKNOWN)

    exe = python or sys.executable
    if argv:
        spawn_argv = [str(a) for a in argv]
    else:
        spawn_argv = [exe, "-m", "ostlr", "run", "--", command]
        spawn_argv.extend(_as_text(a) for a in extra)
    spawn = popen or subprocess.Popen
    try:
        child = spawn(spawn_argv, cwd=work_dir, creationflags=creation_flags(),
                      env=wrapper_env())
    except (OSError, ValueError) as exc:
        return fail(REASON_SPAWN_FAILED, detail=str(exc), command=command)

    pid = _as_int(getattr(child, "pid", 0), 0)
    return ok(REASON_LAUNCHED, pid=pid, command=command, cwd=work_dir,
              argv=spawn_argv)


def start_failed(session: Dict[str, Any], status_record: Optional[Dict[str, Any]],
                 now: int = 0) -> Optional[Dict[str, Any]]:
    """Turn a failed start into an `error` verdict for `spawn_error_retain_s`.

    The wrapper writes the reason into the status file (we hold no pipe), so the
    first line of that record is the message. Returns None when the start did
    not fail - the caller then keeps the normal verdict.
    """
    record = status_record or {}
    if _as_text(record.get("state")) != "error":
        return None
    lines = _as_text(record.get("msg")).splitlines()
    first = lines[0].strip() if lines else "spawn failed"
    return {
        "state": "error",
        "source": "l2",
        "reason": first,
        "msg": first,
        "kind": "",
        "age_s": 0,
        "exit_code": None,
        "retain_s": SPAWN_ERROR_RETAIN_S,
        "since": _as_int(now, 0),
    }


# --- kill -----------------------------------------------------------------

def identity_probe_from_cim(cim_rows: Sequence[Dict[str, Any]]) -> Callable[[int], int]:
    """A probe that reads `started_at` for a pid out of a fresh CIM sample."""
    by_pid: Dict[int, int] = {}
    for row in cim_rows or []:
        if isinstance(row, dict):
            by_pid[_as_int(row.get("pid"), 0)] = _as_int(row.get("started_at"), 0)

    def _probe(pid: int) -> int:
        return by_pid.get(_as_int(pid, 0), 0)

    return _probe


def kill_facts(session: Dict[str, Any], tree_pids: Optional[Sequence[int]] = None,
               tree_mem: Optional[int] = None) -> Dict[str, Any]:
    """The two lines the confirmation dialog shows.

    `pid count` and `aggregated tree memory` - the facts a person needs to
    decide, and the reason the confirmation is not a bare "are you sure?".
    """
    pids = list(tree_pids or [])
    count = len(pids) if pids else 1
    mem = _as_int(tree_mem, 0)
    if mem <= 0:
        mem = _as_int(session.get("mem_bytes"), 0)
    return {
        "pid_count": count,
        "mem_bytes": mem,
        "line1": "%d process%s will be ended" % (count, "" if count == 1 else "es"),
        "line2": "%s of memory" % _format_mem(mem),
    }


def kill(session: Dict[str, Any],
         confirm: Optional[Callable[[Dict[str, Any]], bool]] = None,
         runner: Optional[Callable[[Sequence[str]], Any]] = None,
         identity_probe: Optional[Callable[[int], int]] = None,
         tree_pids: Optional[Sequence[int]] = None,
         tree_mem: Optional[int] = None) -> Dict[str, Any]:
    """End a process tree, after a confirmation and an identity check.

    Order matters and is the whole point:

      1. the confirmation callback receives the facts (pid count, tree memory);
         a False answer is `cancelled` and nothing runs;
      2. the identity guard re-reads the creation time; 0, unparsable, a
         mismatch OR a missing probe is `identity_unverified` and `taskkill` is
         NEVER called;
      3. only then `taskkill /PID <pid> /T /F`, and the receipt is
         `kill requested (pending)` - the sampler judges on the next tick.

    The guard is fail-closed on purpose: a caller that forgets to pass a probe
    gets a refusal, not a kill. That is the difference between "safe by default"
    and "safe if you remembered".
    """
    pid = _as_int(session.get("pid"), 0)
    started_at = _as_int(session.get("started_at"), 0)
    if pid <= 0:
        return fail(REASON_NO_WINDOW, pid=pid)

    facts = kill_facts(session, tree_pids, tree_mem)
    if confirm is not None:
        try:
            approved = bool(confirm(facts))
        except Exception as exc:  # a broken dialog must not kill anything
            return fail("confirm_failed", pid=pid, detail=str(exc))
        if not approved:
            return fail(REASON_CANCELLED, pid=pid, facts=facts)

    # The identity guard. A pid alone is not an identity: Windows recycles pids,
    # and killing a recycled pid would end an unrelated process.
    if started_at <= 0:
        return fail(REASON_IDENTITY_UNVERIFIED, pid=pid, facts=facts,
                    detail="no stored creation time")
    if identity_probe is None:
        # Fail closed. A missing probe used to mean "skip the check", which is
        # exactly the recycled-pid kill the plan's R4 exists to prevent.
        return fail(REASON_IDENTITY_UNVERIFIED, pid=pid, facts=facts,
                    detail="no identity probe")
    current = _as_int(identity_probe(pid), 0)
    if current <= 0 or current != started_at:
        return fail(REASON_IDENTITY_UNVERIFIED, pid=pid, facts=facts,
                    expected=started_at, actual=current)

    argv = ["taskkill", "/PID", str(pid), "/T", "/F"]
    run = runner or _default_runner
    try:
        result = run(argv)
    except Exception as exc:
        return fail("kill_failed", pid=pid, detail=str(exc), facts=facts)

    return ok(REASON_KILL_REQUESTED, pid=pid, facts=facts,
              receipt=REASON_KILL_REQUESTED, taskkill=argv,
              returncode=_as_int(getattr(result, "returncode", 0), 0))


def _default_runner(argv: Sequence[str]) -> Any:
    return subprocess.run(list(argv), capture_output=True, text=True)


def resolve_kill(session: Dict[str, Any], snapshot_rows: Sequence[Dict[str, Any]],
                 tree_pids: Optional[Sequence[int]] = None) -> Dict[str, Any]:
    """Turn the next snapshot into `killed` or a survivors list.

    The action path never re-probes: it marks the row dirty and this helper
    reads the sampler's next verdict, so no CIM call sits on the action path.
    """
    pid = _as_int(session.get("pid"), 0)
    started_at = _as_int(session.get("started_at"), 0)
    alive = False
    for row in snapshot_rows or []:
        if _as_int(row.get("pid"), 0) != pid:
            continue
        if started_at and _as_int(row.get("started_at"), 0) != started_at:
            continue
        alive = bool(row.get("alive"))
        break

    if not alive:
        return ok(REASON_KILLED, pid=pid, survivors=[])

    survivors = list(tree_pids or [pid])
    return fail(REASON_SURVIVORS, pid=pid, survivors=survivors)


# --- restart --------------------------------------------------------------

def restart(session: Dict[str, Any], popen: Any = None,
            pending: Optional[set] = None,
            identity_probe: Optional[Callable[[int], int]] = None) -> Dict[str, Any]:
    """Relaunch the same command line in the same cwd.

    The RAW CIM `CommandLine` string is passed through unmodified: any split
    would break a quoted path containing a space, and the agent would start in
    the wrong place. `cwd == "?"` is refused, and so is a second restart while
    one is still pending.

    The identity guard runs here too, and fails closed: relaunching a recycled
    pid's command line would start an agent in a project the user never chose.
    """
    pid = _as_int(session.get("pid"), 0)
    started_at = _as_int(session.get("started_at"), 0)
    key = (pid, started_at)

    cwd = _as_text(session.get("cwd"))
    if not cwd or cwd == UNKNOWN_CWD:
        return fail(REASON_CWD_UNKNOWN, pid=pid)

    cmdline = _as_text(session.get("cmdline"))
    if not cmdline.strip():
        return fail(REASON_NO_COMMAND, pid=pid)

    if started_at <= 0:
        return fail(REASON_IDENTITY_UNVERIFIED, pid=pid,
                    detail="no stored creation time")
    if identity_probe is None:
        return fail(REASON_IDENTITY_UNVERIFIED, pid=pid,
                    detail="no identity probe")
    current = _as_int(identity_probe(pid), 0)
    if current <= 0 or current != started_at:
        return fail(REASON_IDENTITY_UNVERIFIED, pid=pid,
                    expected=started_at, actual=current)

    if pending is not None:
        if key in pending:
            return fail(REASON_RESTART_PENDING, pid=pid)
        pending.add(key)

    spawn = popen or subprocess.Popen
    try:
        # The raw string, not a list: on Windows `Popen` hands a string straight
        # to CreateProcess, which is exactly what preserves the quoting.
        child = spawn(cmdline, cwd=cwd, creationflags=creation_flags())
    except (OSError, ValueError) as exc:
        if pending is not None:
            pending.discard(key)
        return fail(REASON_SPAWN_FAILED, pid=pid, detail=str(exc))

    new_pid = _as_int(getattr(child, "pid", 0), 0)
    return ok(REASON_LAUNCHED, pid=pid, new_pid=new_pid, cmdline=cmdline, cwd=cwd)


def clear_restart_pending(session: Dict[str, Any], pending: Optional[set]) -> None:
    """Forget a pending restart once the new incarnation has been adopted."""
    if pending is None:
        return
    pending.discard((_as_int(session.get("pid"), 0),
                     _as_int(session.get("started_at"), 0)))


# --- clipboard (T15) ------------------------------------------------------

# The bounded retry from plan 8.6: another process can hold the clipboard for a
# few milliseconds, so a single OpenClipboard failure is not a failure.
CLIP_RETRIES = 10
CLIP_RETRY_S = 0.02

# Two copies of the same path inside this window produce ONE clipboard write.
CLIP_DEDUPE_S = 0.1

CF_UNICODETEXT = 13
GMEM_MOVEABLE = 0x0002

REASON_COPIED = "copied"
REASON_CLIPBOARD_BUSY = "clipboard_busy"
REASON_CLIPBOARD_EMPTY = "clipboard_empty"
REASON_CLIPBOARD_NOT_TEXT = "clipboard_not_text"
REASON_CLIPBOARD_UNAVAILABLE = "clipboard_unavailable"


class Win32Clipboard:
    """The ONE clipboard implementation: ctypes user32/kernel32.

    Used by the panel and by `--selftest --clipboard`, so a verifier compares a
    write and a read of the same mechanism. No tkinter clipboard anywhere: the
    selftest must run with tkinter blocked.
    """

    def __init__(self) -> None:
        import ctypes
        from ctypes import wintypes

        self._ctypes = ctypes
        self._wintypes = wintypes
        self.user32 = ctypes.windll.user32
        self.kernel32 = ctypes.windll.kernel32
        # 64-bit safe signatures: without these, a handle is truncated to int.
        self.user32.OpenClipboard.argtypes = [wintypes.HWND]
        self.user32.OpenClipboard.restype = wintypes.BOOL
        self.user32.CloseClipboard.restype = wintypes.BOOL
        self.user32.GetClipboardData.argtypes = [wintypes.UINT]
        self.user32.GetClipboardData.restype = wintypes.HANDLE
        self.user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
        self.user32.SetClipboardData.restype = wintypes.HANDLE
        self.user32.EmptyClipboard.restype = wintypes.BOOL
        self.kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
        self.kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
        self.kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
        self.kernel32.GlobalLock.restype = wintypes.LPVOID
        self.kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
        self.kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]

    # -- open/close with the bounded retry -----------------------------
    def _open(self) -> bool:
        for attempt in range(CLIP_RETRIES):
            if self.user32.OpenClipboard(None):
                return True
            time.sleep(CLIP_RETRY_S * (attempt + 1))
        return False

    def _close(self) -> None:
        try:
            self.user32.CloseClipboard()
        except Exception:
            pass

    # -- read ----------------------------------------------------------
    def read(self) -> Dict[str, Any]:
        """The clipboard text, or a failure value. Never raises."""
        if not self._open():
            return fail(REASON_CLIPBOARD_BUSY)
        try:
            handle = self.user32.GetClipboardData(CF_UNICODETEXT)
            if not handle:
                return fail(REASON_CLIPBOARD_EMPTY)
            pointer = self.kernel32.GlobalLock(handle)
            if not pointer:
                return fail(REASON_CLIPBOARD_NOT_TEXT)
            try:
                text = self._ctypes.wstring_at(pointer)
            finally:
                self.kernel32.GlobalUnlock(handle)
            return ok(REASON_COPIED, text=text)
        except Exception as exc:
            return fail(REASON_CLIPBOARD_NOT_TEXT, detail=str(exc))
        finally:
            self._close()

    # -- write ---------------------------------------------------------
    def write(self, text: str) -> Dict[str, Any]:
        """Replace the clipboard text. Never raises."""
        payload = str(text)
        if not self._open():
            return fail(REASON_CLIPBOARD_BUSY)
        try:
            if not self.user32.EmptyClipboard():
                return fail(REASON_CLIPBOARD_BUSY)
            size = (len(payload) + 1) * 2  # UTF-16, NUL terminated
            handle = self.kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
            if not handle:
                return fail(REASON_CLIPBOARD_UNAVAILABLE)
            pointer = self.kernel32.GlobalLock(handle)
            if not pointer:
                self.kernel32.GlobalFree(handle)
                return fail(REASON_CLIPBOARD_UNAVAILABLE)
            try:
                self._ctypes.memmove(pointer, self._ctypes.create_unicode_buffer(payload),
                                     size)
            finally:
                self.kernel32.GlobalUnlock(handle)
            if not self.user32.SetClipboardData(CF_UNICODETEXT, handle):
                self.kernel32.GlobalFree(handle)
                return fail(REASON_CLIPBOARD_UNAVAILABLE)
            # Ownership passed to the clipboard; do not free the handle.
            return ok(REASON_COPIED, text=payload)
        except Exception as exc:
            return fail(REASON_CLIPBOARD_UNAVAILABLE, detail=str(exc))
        finally:
            self._close()


def default_clipboard() -> Any:
    """The real clipboard on Windows, or None when it cannot be used."""
    if os.name != "nt":
        return None
    try:
        return Win32Clipboard()
    except Exception:  # pragma: no cover - broken Windows install
        return None


class CopyGate:
    """The counted seam for the dedupe rule.

    Two copies of the same path within `CLIP_DEDUPE_S` produce exactly ONE
    clipboard write. The gate owns the clock and the last write, so a test can
    drive it with a fake clock and count the writes on a writer double.
    """

    def __init__(self, clock: Optional[Callable[[], float]] = None,
                 window_s: float = CLIP_DEDUPE_S) -> None:
        self._clock = clock or time.monotonic
        self.window_s = window_s
        self._last_text = ""
        self._last_at = 0.0
        self.writes = 0

    def should_write(self, text: str) -> bool:
        now = self._clock()
        if text == self._last_text and (now - self._last_at) < self.window_s:
            return False
        self._last_text = text
        self._last_at = now
        return True


def copy_path(path: str, target_row: Optional[Dict[str, Any]] = None,
              current_project: str = "", clipboard: Any = None,
              gate: Optional[CopyGate] = None,
              kind: str = "copied") -> Dict[str, Any]:
    """Copy an absolute path and return the three-part receipt.

    The receipt names the target and whether it is the same project, because
    pasting into the wrong window is the most expensive mistake this product can
    make. A copy with no target is still useful - it just says so.
    """
    return copy_text(path, target_row, current_project, clipboard=clipboard,
                     gate=gate, kind=kind)


def copy_text(text: str, target_row: Optional[Dict[str, Any]] = None,
              current_project: str = "", clipboard: Any = None,
              gate: Optional[CopyGate] = None,
              kind: str = "copied") -> Dict[str, Any]:
    """The ONE copy mechanism: gate, clipboard, receipt.

    `copy_path` and the hint copy both come through here, so there is one dedupe
    window and one receipt shape. A second entry point that wrote the clipboard
    directly would give one of the two chords no dedupe and no target in its
    receipt - the inconsistency the verifier found in Ctrl+Shift+R.
    """
    payload = _as_text(text)
    if not payload:
        return fail("no_path")

    row = target_row or {}
    agent = _as_text(row.get("agent"))
    project = _as_text(row.get("project"))
    if not row:
        same: Optional[bool] = None
    else:
        same = bool(current_project) and project == current_project

    receipt_text = _receipt(kind, payload, agent, project, same)

    writer = clipboard if clipboard is not None else default_clipboard()
    if writer is None:
        return fail(REASON_CLIPBOARD_UNAVAILABLE, receipt=receipt_text)

    if gate is not None and not gate.should_write(payload):
        # Same text, inside the dedupe window: report success, write nothing.
        return ok(REASON_COPIED, receipt=receipt_text, wrote=False, text=payload)

    result = writer.write(payload)
    if gate is not None and result.get("ok"):
        gate.writes += 1
    if not result.get("ok"):
        return fail(result.get("reason") or REASON_CLIPBOARD_UNAVAILABLE,
                    receipt=receipt_text, text=payload)
    return ok(REASON_COPIED, receipt=receipt_text, wrote=True, text=payload)


def _receipt(kind: str, path: str, agent: str, project: str,
             same_project: Optional[bool]) -> str:
    """The receipt string, clipped to 120 chars with the tail preserved."""
    from . import view_model

    return view_model.receipt(kind, path, agent, project, same_project)
