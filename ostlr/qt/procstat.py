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
    """CPU%, memory% and RSS for one process tree, summed over its children."""
    psutil = _psutil()
    out: Dict[str, Optional[float]] = {"cpu": None, "mem": None, "rss": None}
    if psutil is None or not pid:
        return out
    try:
        root = psutil.Process(pid)
        children = root.children(recursive=True)
    except Exception:
        return out
    cpu = 0.0
    rss = 0
    for process in [root] + children:
        try:
            cpu += float(process.cpu_percent(interval=None))
            rss += int(process.memory_info().rss)
        except Exception:
            continue
    cores = max(1, cpu_count())
    out["cpu"] = max(0.0, min(100.0, cpu / cores))
    ram = total_ram()
    out["mem"] = (100.0 * rss / ram) if ram else None
    out["rss"] = rss
    return out


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
