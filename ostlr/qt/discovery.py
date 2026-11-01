"""Discovery: which TUIs are on this machine?

The first build shipped a fixed template list plus "set an absolute path in
config.toml", which meant a new agent (Hermes, whatever comes next) was
invisible until the user edited a config file. That is backwards. This module
answers the question from the machine, in three layers:

  1. **Running**: processes that already have a console - these are sessions the
     panel can watch and focus right now (it cannot adopt their PTY).
  2. **Installed**: executables found by walking PATH once, matched against a
     small set of name hints. Pure name matching: no absolute paths anywhere, so
     a new install is picked up with no configuration.
  3. **Remembered**: whatever the user picked before, including anything that is
     not on PATH at all (a "Browse..." choice). Stored in config.toml.

Nothing here is a whitelist of locations: a program only has to be callable by
name, or chosen once by hand.
"""

from __future__ import annotations

import os
import shutil
from typing import Dict, Iterable, List, Optional

# Name hints, not paths: an exe whose stem contains one of these is offered as a
# launcher candidate. Adding a new agent to this list is optional - "Browse..."
# covers anything missing, and a remembered pick survives.
NAME_HINTS = (
    "claude", "codex", "projv", "hermes", "herd", "aider", "ollama", "gemini",
    "qwen", "opencode", "crush", "pi", "cursor", "windsurf", "continue",
    "goose", "plandex", "mods", "sgpt", "llm",
)

# Names that are almost certainly a launcher and never an agent.
NEVER = ("uninstall", "setup", "update", "helper", "crashpad", "node", "python")


def _stem(path: str) -> str:
    name = os.path.basename(path or "")
    return os.path.splitext(name)[0].lower()


def path_candidates() -> List[Dict[str, object]]:
    """Every PATH executable whose name looks like an agent.

    One pass over PATH, no recursion, no absolute path baked in: install the
    same tool anywhere on PATH and it appears here.
    """
    seen: Dict[str, Dict[str, object]] = {}
    for directory in (os.environ.get("PATH") or "").split(os.pathsep):
        if not directory or not os.path.isdir(directory):
            continue
        try:
            entries = os.listdir(directory)
        except OSError:
            continue
        for name in entries:
            if not name.lower().endswith(".exe"):
                continue
            stem = _stem(name)
            if any(bad in stem for bad in NEVER):
                continue
            if not any(hint in stem for hint in NAME_HINTS):
                continue
            full = os.path.join(directory, name)
            if stem not in seen:
                seen[stem] = {"id": stem, "label": name, "command": full,
                              "source": "path"}
    return sorted(seen.values(), key=lambda item: str(item["id"]))


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
    """Remembered picks first (they were chosen on purpose), then PATH finds.

    De-duplicated by id, so a remembered absolute path wins over a PATH hit for
    the same tool - that is how "use the projV I already have" stays true.
    """
    entries = remembered(getattr(cfg, "agents", None) if cfg is not None else None)
    seen = {str(item["id"]) for item in entries}
    for candidate in path_candidates():
        if str(candidate["id"]) in seen:
            continue
        entries.append(candidate)
        seen.add(str(candidate["id"]))
    return entries


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
