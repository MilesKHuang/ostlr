"""Layout persistence: what the panel looked like, so it comes back that way.

Stored as JSON next to the config. Only the *shape* is restored - tab titles,
splitter sizes and the command lines that were running - never the processes
themselves: a restored pane is started fresh in the same directory with the same
command, which is what a user means by "my layout came back".
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from .. import paths

MAX_RESTORE = 5  # sessions restored at start-up; matches terminal.MAX_PANES


def layout_path() -> str:
    return os.path.join(os.path.dirname(paths.config_path()), "layout.json")


def save(tabs: List[Dict[str, Any]], active: int = 0, path: Optional[str] = None) -> str:
    """Write the layout atomically. Returns the path written ("" on failure)."""
    target = path or layout_path()
    previous = load(target)
    payload = {"v": 1, "active": int(active), "tabs": list(tabs or [])[:MAX_RESTORE],
               "window": previous.get("window") or {}}
    tmp = target + ".tmp"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
        return target
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return ""


def save_window(geometry_hex: str, maximized: bool = False,
                path: Optional[str] = None) -> str:
    """Remember the window rectangle, so the panel opens where the user left it."""
    target = path or layout_path()
    data = load(target)
    data["window"] = {"geometry": geometry_hex, "maximized": bool(maximized)}
    tmp = target + ".tmp"
    try:
        os.makedirs(os.path.dirname(target), exist_ok=True)
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
        return target
    except OSError:
        return ""


def load(path: Optional[str] = None) -> Dict[str, Any]:
    """Read the layout, or an empty one. Never raises on a broken file."""
    target = path or layout_path()
    try:
        with open(target, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, ValueError):
        return {"v": 1, "active": 0, "tabs": []}
    if not isinstance(data, dict):
        return {"v": 1, "active": 0, "tabs": []}
    tabs = [t for t in (data.get("tabs") or []) if isinstance(t, dict)][:MAX_RESTORE]
    window = data.get("window") if isinstance(data.get("window"), dict) else {}
    return {"v": 1, "active": int(data.get("active") or 0), "tabs": tabs,
            "window": window}


def tab_entry(title: str, panes: List[Dict[str, Any]], split: str = "h",
              sizes: Optional[List[int]] = None) -> Dict[str, Any]:
    """One tab as it is stored: a title, its panes, and the split between them."""
    return {
        "title": str(title or "shell")[:60],
        "split": split if split in ("h", "v") else "h",
        "sizes": [int(s) for s in (sizes or [])][:2],
        "panes": [
            {
                "argv": [str(a) for a in (pane.get("argv") or [])][:8],
                "cwd": str(pane.get("cwd") or ""),
            }
            for pane in (panes or [])[:2]
        ],
    }
