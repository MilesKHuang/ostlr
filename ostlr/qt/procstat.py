"""Per-pane workload: CPU%, memory%, and GPU% with no vendor tool.

CPU and memory come from psutil's process tree. GPU comes from the same
performance counters the Windows Task Manager reads (`GPU Engine` utilization and
`GPU Process Memory` dedicated bytes), whose instance names carry `pid_<n>`.

They are read through `Get-Counter` in a background thread every few seconds and
cached, for two reasons: the counters are rate counters (a sample needs a second
collection), and a PowerShell call costs about a second - doing that on the UI
thread would freeze the panel for exactly the frames we are trying to measure.
The direct PDH path was tried first and returned no data on this machine, so the
proven path is the one used and `gpu_reason()` says which is in play.

A value that cannot be measured stays None and the view prints a dash. Never a
fabricated 0: "GPU 0%" and "GPU unknown" mean different things.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
from typing import Dict, List, Optional

_LOCK = threading.Lock()
# pid -> (cpu seconds, monotonic time) of the previous tree sample. CPU is a
# difference between two of these; see `percent_between`.
_PREV: Dict[int, "tuple" ] = {}
_GPU: Dict[str, object] = {"pct": {}, "mib": {}, "at": 0.0, "worker": None,
                           "reason": "", "source": ""}
_RE = re.compile(r'pid_(\d+)_')


def _psutil():
    try:
        import psutil
        return psutil
    except Exception:
        return None


def total_ram() -> int:
    psutil = _psutil()
    if psutil is None:
        return 0
    try:
        return int(psutil.virtual_memory().total)
    except Exception:
        return 0


def cpu_count() -> int:
    psutil = _psutil()
    if psutil is None:
        return os.cpu_count() or 1
    try:
        return int(psutil.cpu_count(logical=True) or 1)
    except Exception:
        return os.cpu_count() or 1


def tree_stats(pid: int) -> Dict[str, Optional[float]]:
    """CPU%, memory% and RSS for one process tree, in Task-Manager units.

    The units matter, because the user compares these numbers with Task Manager:

      cpu   percent of the WHOLE machine (the tree's CPU seconds over the wall time
            between two samples, divided by the logical core count). Task Manager's
            process column uses the same denominator.
      mem   percent of physical RAM, which Task Manager shows as a percentage of
            its "In use" tile - they differ by the kernel's own share, so a small
            gap is expected and is not a bug. RSS in bytes is returned as well,
            because "0%" for a 200 MB process tells the user nothing.
      gpu   percent summed over the process's engine instances (3D + Copy + Video),
            which is why it can read higher than the 3D-only figure in Task
            Manager's GPU column.

    CPU is a DIFFERENCE, not a reading. `psutil.cpu_percent(interval=None)` only
    means something when the same `Process` object was sampled before: a fresh
    object per tick always answers 0.0, which is exactly the "cpu is stuck at 0%"
    defect. So the previous (cpu seconds, wall clock) is kept per pid and the
    percentage is computed here. The first sample of a pid therefore reports None
    ("unknown"), never a fabricated 0.
    """
    psutil = _psutil()
    out: Dict[str, Optional[float]] = {"cpu": None, "mem": None, "rss": None}
    if psutil is None or not pid:
        return out
    try:
        root = psutil.Process(pid)
        tree = [root] + root.children(recursive=True)
    except Exception:
        return out

    seconds = 0.0
    rss = 0
    for process in tree:
        try:
            times = process.cpu_times()
            seconds += float(times.user) + float(times.system)
            rss += int(process.memory_info().rss)
        except Exception:
            continue

    now = time.monotonic()
    with _LOCK:
        previous = _PREV.get(int(pid))
        _PREV[int(pid)] = (seconds, now)
    if previous is not None:
        out["cpu"] = percent_between(previous[0], seconds, now - previous[1],
                                     cpu_count())
    ram = total_ram()
    out["mem"] = (100.0 * rss / ram) if ram else None
    out["rss"] = rss
    return out


def percent_between(before_s: float, now_s: float, elapsed_s: float,
                    cores: int) -> Optional[float]:
    """CPU percentage from two CPU-time readings - the whole point, as a function.

    Pure and therefore testable: `percent_between(1.0, 2.0, 1.0, 4)` is 25% of a
    four-core machine, which is what Task Manager would show for a process that
    burned one core for a second.
    """
    try:
        elapsed = float(elapsed_s)
        if elapsed <= 0:
            return None
        used = max(0.0, float(now_s) - float(before_s))
        cores = max(1, int(cores))
    except (TypeError, ValueError):
        return None
    return max(0.0, min(100.0, 100.0 * used / elapsed / cores))


def start_epoch(pid: int) -> int:
    """The process creation time as an epoch (0 when it cannot be read).

    This is the identity a kill is verified against: a pid alone is reusable, and
    killing a recycled pid would end an unrelated process.
    """
    psutil = _psutil()
    if psutil is None or not pid:
        return 0
    try:
        return int(psutil.Process(int(pid)).create_time())
    except Exception:
        return 0


def prime(pids: List[int]) -> None:
    """Take one baseline sample so the first real reading is a real difference."""
    for pid in pids:
        try:
            tree_stats(int(pid))
        except Exception:
            continue


# --- GPU -------------------------------------------------------------------

def gpu_available() -> bool:
    """True when this platform can be asked at all."""
    return os.name == "nt" and shutil.which("powershell") is not None


def _read_gpu_counters() -> Dict[str, object]:
    """One sample of both GPU counter sets, folded per pid."""
    script = (
        "$ErrorActionPreference='SilentlyContinue';"
        "try { $u = Get-Counter -Counter '\\GPU Engine(*)\\Utilization Percentage'"
        " -MaxSamples 1 -ErrorAction Stop;"
        " $u.CounterSamples | ForEach-Object { \"U|\" + $_.InstanceName + \"|\" + $_.CookedValue }"
        " } catch { };"
        "try { $m = Get-Counter -Counter '\\GPU Process Memory(*)\\Dedicated Usage'"
        " -MaxSamples 1 -ErrorAction Stop;"
        " $m.CounterSamples | ForEach-Object { \"M|\" + $_.InstanceName + \"|\" + $_.CookedValue }"
        " } catch { }"
    )
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, text=True, timeout=8.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception as exc:
        return {"pct": {}, "mib": {}, "reason": str(exc)[:80], "source": "get-counter"}
    percents: Dict[int, float] = {}
    memory: Dict[int, float] = {}
    for line in (result.stdout or "").splitlines():
        parts = line.strip().split("|")
        if len(parts) != 3:
            continue
        kind, instance, value = parts
        match = _RE.search(instance)
        if not match:
            continue
        try:
            pid = int(match.group(1))
            number = float(value)
        except ValueError:
            continue
        if kind == "U":
            percents[pid] = percents.get(pid, 0.0) + number
        elif kind == "M":
            memory[pid] = max(memory.get(pid, 0.0), number / (1024 * 1024))
    return {"pct": percents, "mib": memory, "reason": "", "source": "get-counter"}


def _gpu_loop(interval_s: float = 3.0) -> None:
    while True:
        sample = _read_gpu_counters()
        with _LOCK:
            _GPU.update(sample)
            _GPU["at"] = time.time()
        time.sleep(interval_s)


def start_gpu_worker(interval_s: float = 3.0) -> bool:
    """Start the background sampler once. False when the platform cannot."""
    if not gpu_available():
        with _LOCK:
            _GPU["reason"] = "no GPU counters on this platform"
        return False
    with _LOCK:
        if _GPU["worker"] is not None:
            return True
        worker = threading.Thread(target=_gpu_loop, args=(interval_s,),
                                  name="ostlr-gpu", daemon=True)
        _GPU["worker"] = worker
    worker.start()
    return True


def gpu_by_pid() -> Dict[int, float]:
    """{pid: GPU utilisation percent} from the last sample (empty until one lands)."""
    with _LOCK:
        return dict(_GPU["pct"] or {})


def gpu_mib_by_pid() -> Dict[int, float]:
    with _LOCK:
        return dict(_GPU["mib"] or {})


def gpu_reason() -> str:
    with _LOCK:
        return str(_GPU["reason"] or "")


def sample(pids: List[int]) -> Dict[int, Dict[str, Optional[float]]]:
    """One snapshot per pane pid."""
    percents = gpu_by_pid()
    memory = gpu_mib_by_pid()
    result: Dict[int, Dict[str, Optional[float]]] = {}
    for pid in pids:
        stats = tree_stats(pid)
        stats["gpu"] = percents.get(int(pid))
        stats["gpu_mib"] = memory.get(int(pid))
        result[int(pid)] = stats
    return result


def format_cpu(value: Optional[float]) -> str:
    return "-" if value is None else "%d%%" % min(100, int(round(value)))


def format_mem(value: Optional[float]) -> str:
    return "-" if value is None else "%d%%" % min(100, int(round(value * 10)) / 10)


def format_gpu(value: Optional[float]) -> str:
    return "-" if value is None else "%d%%" % min(100, int(round(value)))
