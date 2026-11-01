"""Where a program comes from: the user's list, plus a file dialog.

There is no machine scan here, and that is a decision, not an omission. The panel
manages the panes it owns; a program becomes available by being named once -
`Browse...` in the launcher, or a `[[agents]]` entry in config.toml - and from then
on it is in the list because the user put it there.

    launcher_entries(cfg)   the config.toml entries, in the user's order
    resolve(command)        an absolute path for a command, or None
    home_of(exe)            where that exe keeps its data, and whether it has a key
    remember(cfg, path)     write a picked program into config.toml
    entry_for(command,cfg)  the entry for a path, remembered if it is new

The previous version also walked PATH and asked the process table which copies
were running. Both were removed with the machine scan: a list of ten agents the
user never chose is noise, and "which copy is alive" stopped being a question the
panel is allowed to answer.
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, Iterable, List, Optional


def _stem(path: str) -> str:
    name = os.path.basename(path or "")
    return os.path.splitext(name)[0].lower()


def remembered(entries: Optional[Iterable[Dict[str, object]]] = None) -> List[Dict[str, object]]:
    """Launcher entries the user already chose (config.toml `[[agents]]`)."""
    out: List[Dict[str, object]] = []
    for entry in entries or ():
        if not isinstance(entry, dict):
            continue
        command = str(entry.get("command") or "").strip()
        if not command:
            continue
        agent_id = str(entry.get("id") or _stem(command) or "agent").strip()
        out.append({
            "id": agent_id,
            "label": str(entry.get("label") or agent_id),
            "command": command,
            "args": list(entry.get("args") or []),
            "source": "remembered",
        })
    return out


def launcher_entries(cfg=None) -> List[Dict[str, object]]:
    """The programs the user chose, and nothing else.

    A program appears here because somebody pointed at its exe once (`remember`
    writes it into config.toml). What you named is what you get.
    """
    return remembered(getattr(cfg, "agents", None) if cfg is not None else None)


def entry_for(command: str, cfg=None) -> Dict[str, object]:
    """The launcher entry for a path, remembered if it is not in the list yet."""
    target = os.path.normcase(os.path.abspath(command or ""))
    for item in launcher_entries(cfg):
        existing = os.path.normcase(os.path.abspath(str(item["command"])))
        if existing == target:
            return item
    agent_id = _stem(command) or "agent"
    return {"id": agent_id, "label": agent_id, "command": command,
            "args": [], "source": "browse"}


def home_of(command: str) -> Dict[str, object]:
    """Where a TUI keeps its data: the exe's folder and a sibling projv_files.

    This is what the launch dialog shows, so "which projV am I starting, and does
    it have my API key" is answered before the click instead of after a fresh
    install quietly asks for the key again.
    """
    path = os.path.abspath(command or "")
    directory = os.path.dirname(path)
    files = os.path.join(directory, "projv_files")
    config = os.path.join(files, "config.toml")
    return {
        "exe": path,
        "dir": directory,
        "files_dir": files if os.path.isdir(files) else "",
        "config": config if os.path.isfile(config) else "",
        "has_key": _has_api_key(config),
    }


def _has_api_key(config: str) -> bool:
    try:
        with open(config, "r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.strip().startswith("api_key") and "=" in line:
                    value = line.split("=", 1)[1].strip().strip('"')
                    return bool(value)
    except OSError:
        return False
    return False


def remember(cfg, command: str, label: Optional[str] = None) -> None:
    """Add (or update) one `[[agents]]` entry so the next run offers it."""
    entry = entry_for(command, cfg)
    if label:
        entry["label"] = label
    agents = list(getattr(cfg, "agents", []) or [])
    for index, existing in enumerate(agents):
        if str(existing.get("id")) == str(entry["id"]):
            agents[index] = {"id": entry["id"], "label": entry["label"],
                             "command": entry["command"],
                             "args": list(entry.get("args") or [])}
            break
    else:
        agents.append({"id": entry["id"], "label": entry["label"],
                       "command": entry["command"],
                       "args": list(entry.get("args") or [])})
    cfg.agents = agents


def resolve(command: str) -> Optional[str]:
    """A callable absolute path for `command`, or None when it is not there."""
    if not command:
        return None
    if os.path.isabs(command):
        return command if os.path.exists(command) else None
    return shutil.which(command)
