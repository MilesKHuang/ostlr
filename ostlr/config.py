"""Configuration: defaults, a tolerant TOML loader, and a writer.

Rules that matter:

  * A missing file is not an error - the defaults are the product's defaults.
  * A malformed file is not fatal either: we keep the defaults, record the
    reason in `load_error`, and the panel shows it. A config typo must never
    stop the panel from starting.
  * A wrong-typed value falls back to the default for that key only, so one bad
    line cannot silently reset the rest.
  * Nothing here reads the environment except through `paths`, so a test can
    point OSTLR_CONFIG at a temp file.

`last_dirs` / `last_agents` are the launcher's memory (T14). They are stored
here now so the file format does not change later.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import paths

# --- defaults ------------------------------------------------------------

DEFAULT_TICK_S = 5
DEFAULT_L2_STALE_S = 90
DEFAULT_DONE_RETAIN_S = 600
DEFAULT_SPAWN_ERROR_RETAIN_S = 30
DEFAULT_CIM_TIMEOUT_MS = 3000
DEFAULT_LIST_CAP = 60
DEFAULT_CLIP_CHARS = 120

# How many entries the launcher remembers.
LAST_MEMORY = 3


@dataclass
class Config:
    """The whole configuration. Every field has a working default."""

    tick_s: int = DEFAULT_TICK_S
    l2_stale_s: int = DEFAULT_L2_STALE_S
    done_retain_s: int = DEFAULT_DONE_RETAIN_S
    spawn_error_retain_s: int = DEFAULT_SPAWN_ERROR_RETAIN_S
    cim_timeout_ms: int = DEFAULT_CIM_TIMEOUT_MS
    list_cap: int = DEFAULT_LIST_CAP
    clip_chars: int = DEFAULT_CLIP_CHARS

    # Launcher memory (T14): most recent first, at most LAST_MEMORY entries.
    last_dirs: List[str] = field(default_factory=list)
    last_agents: List[str] = field(default_factory=list)

    # `[[agents]]` overrides (baseline 4.5): an entry with the id of a default
    # template replaces its command / args, so an agent that is not on PATH can
    # be launched from an absolute path. An entry with a new id adds a template.
    agents: List[Dict[str, Any]] = field(default_factory=list)

    # Set by load(): where it came from and why it failed, if it did.
    path: str = ""
    load_error: str = ""

    # --- derived ------------------------------------------------------
    @property
    def tick_ms(self) -> int:
        return max(1, int(self.tick_s)) * 1000

    @property
    def cim_timeout_s(self) -> float:
        return max(1, int(self.cim_timeout_ms)) / 1000.0

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _as_int(value: Any, default: int, minimum: int = 0) -> int:
    """Coerce to int, clamping at `minimum`; a bad value keeps the default."""
    if isinstance(value, bool):  # bool is an int subclass - reject it
        return default
    try:
        out = int(value)
    except (TypeError, ValueError):
        return default
    return out if out >= minimum else default


def _as_str_list(value: Any, limit: int = LAST_MEMORY) -> List[str]:
    """A list of non-empty strings, de-duplicated, most recent first."""
    if not isinstance(value, (list, tuple)):
        return []
    out: List[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        text = item.strip()
        if text and text not in out:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _as_agent_list(value: Any) -> List[Dict[str, Any]]:
    """Parse `[[agents]]` tables (baseline 4.5).

    Only the keys the launcher consumes survive: id, label, command, args.
    An entry without an id or without a command is dropped - a half-written
    override must not silently grey out a working agent.
    """
    if not isinstance(value, (list, tuple)):
        return []
    out: List[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        agent_id = str(item.get("id") or "").strip()
        command = str(item.get("command") or "").strip()
        if not agent_id or not command:
            continue
        args_raw = item.get("args")
        args = [str(a) for a in args_raw] if isinstance(args_raw, (list, tuple)) else []
        out.append({
            "id": agent_id,
            "label": str(item.get("label") or agent_id).strip() or agent_id,
            "command": command,
            "args": args,
        })
    return out


def from_mapping(data: Dict[str, Any]) -> Config:
    """Build a Config from a parsed TOML table, tolerating bad values."""
    cfg = Config()
    if not isinstance(data, dict):
        return cfg
    cfg.tick_s = _as_int(data.get("tick_s"), DEFAULT_TICK_S, minimum=1)
    cfg.l2_stale_s = _as_int(data.get("l2_stale_s"), DEFAULT_L2_STALE_S, minimum=1)
    cfg.done_retain_s = _as_int(data.get("done_retain_s"), DEFAULT_DONE_RETAIN_S, minimum=0)
    cfg.spawn_error_retain_s = _as_int(
        data.get("spawn_error_retain_s"), DEFAULT_SPAWN_ERROR_RETAIN_S, minimum=0
    )
    cfg.cim_timeout_ms = _as_int(data.get("cim_timeout_ms"), DEFAULT_CIM_TIMEOUT_MS, minimum=1)
    cfg.list_cap = _as_int(data.get("list_cap"), DEFAULT_LIST_CAP, minimum=0)
    cfg.clip_chars = _as_int(data.get("clip_chars"), DEFAULT_CLIP_CHARS, minimum=1)
    cfg.last_dirs = _as_str_list(data.get("last_dirs"))
    cfg.last_agents = _as_str_list(data.get("last_agents"))
    cfg.agents = _as_agent_list(data.get("agents"))
    return cfg


def load(path: Optional[str] = None) -> Config:
    """Load the config, or return the defaults with `load_error` set.

    Never raises: a missing file, an unreadable file and a syntax error all
    produce a usable Config.
    """
    target = Path(path) if path else paths.config_path()
    cfg = Config()
    cfg.path = str(target)

    try:
        raw = target.read_bytes()
    except FileNotFoundError:
        return cfg
    except OSError as exc:
        cfg.load_error = "cannot read %s: %s" % (target, exc)
        return cfg

    try:
        data = tomllib.loads(raw.decode("utf-8"))
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        cfg.load_error = "invalid TOML in %s: %s" % (target, exc)
        return cfg

    loaded = from_mapping(data)
    loaded.path = str(target)
    return loaded


def _toml_value(value: Any) -> str:
    """Render one value as TOML. Only the types this file uses."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (list, tuple)):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    text = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return '"%s"' % text


def dumps(cfg: Config) -> str:
    """Serialise a Config to TOML text (stable key order, one key per line)."""
    lines = [
        "# Ostlr configuration",
        "# Written by the panel; safe to edit by hand.",
        "",
        "tick_s = %s" % _toml_value(cfg.tick_s),
        "l2_stale_s = %s" % _toml_value(cfg.l2_stale_s),
        "done_retain_s = %s" % _toml_value(cfg.done_retain_s),
        "spawn_error_retain_s = %s" % _toml_value(cfg.spawn_error_retain_s),
        "cim_timeout_ms = %s" % _toml_value(cfg.cim_timeout_ms),
        "list_cap = %s" % _toml_value(cfg.list_cap),
        "clip_chars = %s" % _toml_value(cfg.clip_chars),
        "last_dirs = %s" % _toml_value(list(cfg.last_dirs)),
        "last_agents = %s" % _toml_value(list(cfg.last_agents)),
        "",
    ]
    for agent in cfg.agents:
        lines.append("[[agents]]")
        lines.append('id = %s' % _toml_value(agent.get("id", "")))
        lines.append('label = %s' % _toml_value(agent.get("label", "")))
        lines.append('command = %s' % _toml_value(agent.get("command", "")))
        lines.append("args = %s" % _toml_value(list(agent.get("args") or [])))
        lines.append("")
    return "\n".join(lines)


def save(cfg: Config, path: Optional[str] = None) -> bool:
    """Write the config atomically (temp file + os.replace).

    Returns False instead of raising: a read-only profile is a condition the
    panel reports, not a crash. The temp file is removed on failure so a
    half-written config can never be read back.
    """
    target = Path(path) if path else (Path(cfg.path) if cfg.path else paths.config_path())
    tmp = target.with_name(target.name + ".tmp")
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(dumps(cfg), encoding="utf-8")
        tmp.replace(target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    cfg.path = str(target)
    return True


def remember(cfg: Config, *, directory: Optional[str] = None,
             agent: Optional[str] = None) -> Config:
    """Record a launcher choice, most recent first, capped at LAST_MEMORY.

    Pure with respect to the file: the caller decides whether to `save()`.
    """
    if directory:
        cfg.last_dirs = _push(cfg.last_dirs, directory)
    if agent:
        cfg.last_agents = _push(cfg.last_agents, agent)
    return cfg


def _push(items: List[str], value: str) -> List[str]:
    text = str(value).strip()
    if not text:
        return list(items)
    out = [text] + [i for i in items if i != text]
    return out[:LAST_MEMORY]
