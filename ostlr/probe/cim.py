"""CIM probe - the only blocking call in the sampler.

One `powershell -NoProfile -Command "Get-CimInstance Win32_Process | Select ... |
ConvertTo-Json -Compress"` per tick, on a worker thread, with a hard timeout and
a last-good cache. Why this shape:

  * CIM is the only way to get a command line, a parent pid and a working set
    without reading another process's PEB (undocumented offsets, same-privilege
    only). One call for the whole machine, never one per row.
  * It can hang (a wedged WMI repository), so it is bounded by a timeout and the
    previous good sample is kept. A hang must degrade to `stale, last good
    HH:MM:SS`, never to a frozen panel and never to a guessed state.
  * At most one probe is in flight. A tick that arrives while a probe is running
    is skipped, not queued - a queue would grow without bound on a slow machine.

Two `CreationDate` shapes are accepted, because the same field arrives
differently depending on how PowerShell serialises it:

    "/Date(1793415260890)/"          PowerShell DateTime -> ms since epoch
    "20261030201103.000000+000"      CIM_DATETIME text   -> UTC, no separators

A row whose date cannot be parsed keeps `started_at = 0`. That is the honest
answer and it is load-bearing: the identity guard refuses kill and restart when
`started_at` is 0, so an unparsed date can never let us kill a recycled pid.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Sequence

# The exact command from the plan (section 7.1). Kept as a list so no shell
# quoting is involved and a test can substitute a fake command.
CIM_COMMAND: List[str] = [
    "powershell",
    "-NoProfile",
    "-Command",
    "Get-CimInstance Win32_Process | "
    "Select ProcessId,ParentProcessId,CreationDate,WorkingSetSize,CommandLine,"
    "ExecutablePath,Name | ConvertTo-Json -Compress",
]

# --- CreationDate parsing -------------------------------------------------

_MS_DATE_RE = re.compile(r"/Date\(\s*(-?\d+)\s*\)/")
# CIM_DATETIME: YYYYMMDDHHMMSS.ffffff[+-]UUU  (UUU = minutes offset from UTC)
_CIM_DATETIME_RE = re.compile(
    r"^\s*(\d{4})(\d{2})(\d{2})(\d{2})(\d{2})(\d{2})(?:\.(\d{1,6}))?"
    r"(?:([+-])(\d{3}))?\s*$"
)


def parse_creation_date(value: Any) -> int:
    """Convert a CIM `CreationDate` to epoch seconds, or 0 when unparseable.

    0 is a sentinel, not a timestamp: callers must treat it as "no identity".
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        # Already numeric: PowerShell may hand back a raw epoch in ms or s.
        number = float(value)
        if number <= 0:
            return 0
        return int(number / 1000.0) if number > 1e11 else int(number)

    text = str(value).strip()
    if not text:
        return 0
    # A raw payload (not yet through json.loads) escapes the slashes as
    # `\/Date(...)\/`; unescape so both forms parse.
    if "\\/" in text:
        text = text.replace("\\/", "/")

    match = _MS_DATE_RE.search(text)
    if match:
        try:
            ms = int(match.group(1))
        except ValueError:
            return 0
        return int(ms // 1000) if ms > 0 else 0

    match = _CIM_DATETIME_RE.match(text)
    if match:
        year, month, day, hour, minute, second = (int(match.group(i)) for i in range(1, 7))
        micro = match.group(7) or "0"
        microsecond = int(micro.ljust(6, "0")[:6])
        sign = match.group(8)
        offset_minutes = int(match.group(9)) if match.group(9) else 0
        if sign == "-":
            offset_minutes = -offset_minutes
        try:
            stamp = datetime(
                year, month, day, hour, minute, second, microsecond,
                tzinfo=timezone(timedelta(minutes=offset_minutes)),
            )
        except ValueError:
            return 0
        return int(stamp.timestamp())

    return 0


# --- row normalisation ----------------------------------------------------

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
    if isinstance(value, str):
        return value
    return str(value)


def normalise_row(raw: Any) -> Optional[Dict[str, Any]]:
    """One CIM object -> the row dict other modules code against.

    Returns None for anything that is not a usable process row (not a dict, no
    pid). A single bad row must never abort the sample: the panel would then
    show nothing at all, which reads as "no agents running".
    """
    if not isinstance(raw, dict):
        return None
    pid = _as_int(raw.get("ProcessId"), -1)
    if pid < 0:
        return None
    return {
        "pid": pid,
        "ppid": _as_int(raw.get("ParentProcessId"), 0),
        "started_at": parse_creation_date(raw.get("CreationDate")),
        "mem_bytes": max(0, _as_int(raw.get("WorkingSetSize"), 0)),
        "cmdline": _as_text(raw.get("CommandLine")),
        "exe": _as_text(raw.get("ExecutablePath")),
        "name": _as_text(raw.get("Name")),
    }


class CimParseError(ValueError):
    """The payload was not the JSON we expect."""


class CimTimeout(RuntimeError):
    """The probe exceeded its timeout."""


def parse_payload(text: str) -> List[Dict[str, Any]]:
    """Parse the PowerShell JSON payload into normalised rows.

    Accepts the three shapes that actually occur:
      * a JSON array (a full query),
      * a single JSON object (`-First 1`, or exactly one process),
      * an empty string / `null` (no processes, or a failed query).
    Anything else raises `CimParseError` - a garbage payload is a real fault and
    the caller must be able to tell it apart from "zero processes".
    """
    if text is None:
        return []
    stripped = text.strip()
    if not stripped:
        return []
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise CimParseError("payload is not JSON: %s" % exc) from exc

    if data is None:
        return []
    if isinstance(data, dict):
        row = normalise_row(data)
        return [row] if row else []
    if isinstance(data, list):
        out: List[Dict[str, Any]] = []
        for item in data:
            row = normalise_row(item)
            if row is not None:
                out.append(row)
        return out
    raise CimParseError("payload is neither an object nor an array: %s" % type(data).__name__)


# --- the probe ------------------------------------------------------------

@dataclass
class Sample:
    """One probe result plus the provenance the panel needs to be honest."""

    rows: List[Dict[str, Any]] = field(default_factory=list)
    ok: bool = False
    error: str = ""
    taken_at: float = 0.0          # epoch seconds of this attempt
    good_at: float = 0.0           # epoch seconds of the last good sample
    stale: bool = False            # True when `rows` came from the cache

    @property
    def count(self) -> int:
        return len(self.rows)

    def staleness_text(self) -> str:
        """`stale, last good HH:MM:SS`, or empty when the sample is fresh."""
        if not self.stale:
            return ""
        if not self.good_at:
            return "stale, no good sample yet"
        return "stale, last good %s" % time.strftime("%H:%M:%S", time.localtime(self.good_at))


class CimProbe:
    """Runs the CIM query with a timeout and keeps the last good sample.

    Thread-safe for the intended use: one sampler thread calls `sample()`, the
    UI may call `last()` to read the cache. `sample()` refuses to run twice
    concurrently - the second caller gets the cached sample with `stale=True`
    instead of a second PowerShell process.
    """

    def __init__(self, command: Optional[Sequence[str]] = None,
                 timeout_ms: int = 3000,
                 runner: Optional[Callable[[Sequence[str], float], str]] = None,
                 clock: Callable[[], float] = time.time) -> None:
        self.command = list(command) if command else list(CIM_COMMAND)
        self.timeout_ms = max(1, int(timeout_ms))
        self._runner = runner or _run_command
        self._clock = clock
        self._lock = threading.Lock()
        self._busy = False
        self._last_good: List[Dict[str, Any]] = []
        self._last_good_at = 0.0
        self._last_error = ""

    # --- public -------------------------------------------------------
    @property
    def timeout_s(self) -> float:
        return self.timeout_ms / 1000.0

    def last(self) -> Sample:
        """The cached sample, without probing."""
        with self._lock:
            return Sample(
                rows=list(self._last_good),
                ok=bool(self._last_good),
                error=self._last_error,
                taken_at=self._last_good_at,
                good_at=self._last_good_at,
                stale=bool(self._last_good),
            )

    def sample(self) -> Sample:
        """Probe once. Never raises; failures come back as a stale Sample."""
        with self._lock:
            if self._busy:
                # A probe is already in flight: skip this tick rather than
                # queue a second PowerShell process.
                return Sample(
                    rows=list(self._last_good),
                    ok=bool(self._last_good),
                    error=self._last_error,
                    taken_at=self._clock(),
                    good_at=self._last_good_at,
                    stale=True,
                )
            self._busy = True
        try:
            return self._probe_once()
        finally:
            with self._lock:
                self._busy = False

    # --- internals ----------------------------------------------------
    def _probe_once(self) -> Sample:
        now = self._clock()
        try:
            text = self._runner(self.command, self.timeout_s)
        except CimTimeout as exc:
            return self._fail(now, str(exc) or "timeout")
        except FileNotFoundError:
            return self._fail(now, "powershell not found on PATH")
        except OSError as exc:
            return self._fail(now, "cannot run powershell: %s" % exc)
        except subprocess.TimeoutExpired:
            return self._fail(now, "timeout after %.1fs" % self.timeout_s)

        try:
            rows = parse_payload(text)
        except CimParseError as exc:
            return self._fail(now, str(exc))

        with self._lock:
            self._last_good = rows
            self._last_good_at = now
            self._last_error = ""
        return Sample(rows=list(rows), ok=True, taken_at=now, good_at=now, stale=False)

    def _fail(self, now: float, error: str) -> Sample:
        with self._lock:
            self._last_error = error
            rows = list(self._last_good)
            good_at = self._last_good_at
        return Sample(rows=rows, ok=False, error=error, taken_at=now,
                      good_at=good_at, stale=True)


def _run_command(command: Sequence[str], timeout_s: float) -> str:
    """Run the probe command with a hard timeout; raise CimTimeout on expiry.

    `subprocess.run(timeout=...)` kills the child on expiry, which is what we
    want: a wedged PowerShell must not outlive the tick that started it.
    """
    creationflags = 0
    if sys.platform.startswith("win"):
        # No console window for the probe; it is a background call.
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        completed = subprocess.run(
            list(command),
            capture_output=True,
            timeout=timeout_s,
            creationflags=creationflags,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise CimTimeout("timeout after %.1fs" % timeout_s) from exc
    if completed.returncode != 0:
        detail = (completed.stderr or b"").decode("utf-8", "replace").strip()
        raise CimParseError("powershell exited %d: %s" % (completed.returncode, detail[:200]))
    return (completed.stdout or b"").decode("utf-8", "replace")


# --- convenience ----------------------------------------------------------

def probe_once(timeout_ms: int = 3000,
               command: Optional[Sequence[str]] = None) -> Sample:
    """One-shot probe with no cache - for `--selftest` and the live DoD check."""
    return CimProbe(command=command, timeout_ms=timeout_ms).sample()


def live_check(timeout_ms: int = 3000) -> str:
    """One line for the selftest: a count, or an explicit failure reason.

    The load-bearing assertion is `started_at > 0` for every row: if the live
    `CreationDate` shape ever disagrees with the parser, the identity guard
    silently stops working, so this check exists to catch that instead of
    letting it ship.
    """
    sample = probe_once(timeout_ms=timeout_ms)
    if not sample.ok:
        return "FAIL cim probe: %s" % (sample.error or "unknown error")
    if not sample.rows:
        return "SKIP (no processes returned by CIM)"
    missing = [r["pid"] for r in sample.rows if r["started_at"] <= 0]
    if missing:
        return "FAIL cim: %d/%d rows have started_at == 0 (pids %s)" % (
            len(missing), len(sample.rows), missing[:5])
    return "PASS cim rows: %d (all started_at > 0)" % len(sample.rows)
