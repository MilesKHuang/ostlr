"""Per-pane workload: CPU%, memory% and GPU memory, measured not guessed.

The user's complaint about the old "Resources" panel was exact: bars of absolute
megabytes tell nobody anything, because there is no ceiling to compare against.
This module answers the question the eye actually asks - *how much of the machine
is this agent using?* - in percentages, per process tree:

    cpu   percent of one core (psutil, normalised to the machine's cores)
    mem   percent of physical RAM
    gpu   MiB of device memory, best effort (nvidia-smi); absent when there is no
          NVIDIA device or no nvidia-smi, and the column then disappears

Nothing here is inferred: a value that cannot be measured is None and the view
prints a dash.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from typing import Dict, List, Optional

_CACHE: Dict[str, object] = {"smi": None}


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
    """CPU%, memory% and RSS for one process tree.

    `cpu_percent` on a tree is the sum of each descendant's share; it is divided
    by the core count so 100% means "one whole machine", which is the number a
    person reads.
    """
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


def nvidia_smi() -> Optional[str]:
    """Path to nvidia-smi once, or None. Cached: the lookup is not free."""
    if "smi" in _CACHE and _CACHE["smi"] is not None:
        return str(_CACHE["smi"]) or None
    path = shutil.which("nvidia-smi")
    _CACHE["smi"] = path or ""
    return path or None


def gpu_by_pid() -> Dict[int, int]:
    """{pid: MiB} from nvidia-smi, or {} when there is no NVIDIA tool.

    Best effort by design: Windows has no public per-process GPU accounting, and
    a fabricated number would be worse than a missing one.
    """
    smi = nvidia_smi()
    if not smi:
        return {}
    try:
        result = subprocess.run(
            [smi, "--query-compute-apps=pid,used_memory", "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=2.0,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except Exception:
        return {}
    out: Dict[int, int] = {}
    for line in (result.stdout or "").splitlines():
        parts = [piece.strip() for piece in line.split(",")]
        if len(parts) != 2:
            continue
        try:
            out[int(parts[0])] = int(float(parts[1]))
        except ValueError:
            continue
    return out


def sample(pids: List[int]) -> Dict[int, Dict[str, Optional[float]]]:
    """One snapshot for every pane pid, GPU included when it exists."""
    gpu = gpu_by_pid()
    result: Dict[int, Dict[str, Optional[float]]] = {}
    for pid in pids:
        stats = tree_stats(pid)
        stats["gpu"] = float(gpu.get(pid)) if pid in gpu else None
        result[int(pid)] = stats
    return result


def format_cpu(value: Optional[float]) -> str:
    return "-" if value is None else "%d%%" % min(100, int(round(value)))


def format_mem(value: Optional[float]) -> str:
    return "-" if value is None else "%d%%" % min(100, int(round(value * 10)) / 10)


def format_gpu(value: Optional[float]) -> str:
    return "-" if value is None else "%d MiB" % int(value)
