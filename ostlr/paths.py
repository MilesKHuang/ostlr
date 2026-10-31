"""Where Ostlr keeps its files.

Two roots, both overridable by environment variable so a test never touches the
real user profile:

    config   %APPDATA%\\ostlr\\config.toml        OSTLR_CONFIG
    status   %LOCALAPPDATA%\\ostlr\\status        OSTLR_STATUS_DIR
    logs     %LOCALAPPDATA%\\ostlr\\logs          OSTLR_LOG_DIR

Why two roots: config is roaming user data (small, worth syncing), status files
and logs are machine-local and disposable. That split is the Windows convention
and it also keeps a roaming profile from carrying another machine's pids.

Every function is pure with respect to the environment it reads at call time -
nothing is cached at import, so a test can set the override and call again.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

APP_NAME = "ostlr"

ENV_CONFIG = "OSTLR_CONFIG"
ENV_STATUS_DIR = "OSTLR_STATUS_DIR"
ENV_LOG_DIR = "OSTLR_LOG_DIR"


def _env(name: str) -> Optional[str]:
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _appdata() -> Path:
    """%APPDATA%, falling back to ~/AppData/Roaming on a stripped environment."""
    raw = _env("APPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Roaming"


def _localappdata() -> Path:
    """%LOCALAPPDATA%, falling back to ~/AppData/Local."""
    raw = _env("LOCALAPPDATA")
    if raw:
        return Path(raw)
    return Path.home() / "AppData" / "Local"


def config_dir() -> Path:
    """The directory holding config.toml (the parent of OSTLR_CONFIG when set)."""
    override = _env(ENV_CONFIG)
    if override:
        return Path(override).expanduser().parent
    return _appdata() / APP_NAME


def config_path() -> Path:
    """The config file itself."""
    override = _env(ENV_CONFIG)
    if override:
        return Path(override).expanduser()
    return config_dir() / "config.toml"


def status_dir() -> Path:
    """Where L2 status files live (`<pid>.json`)."""
    override = _env(ENV_STATUS_DIR)
    if override:
        return Path(override).expanduser()
    return _localappdata() / APP_NAME / "status"


def log_dir() -> Path:
    """Where our own logs live."""
    override = _env(ENV_LOG_DIR)
    if override:
        return Path(override).expanduser()
    return _localappdata() / APP_NAME / "logs"


def ensure_dir(path: Path) -> Path:
    """Create `path` (and parents) if needed; return it.

    A failure is not raised: a read-only or denied directory must degrade to
    "we cannot write there", which the caller reports, not crash the panel.
    """
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
    except OSError:
        pass
    return Path(path)


def ensure_status_dir() -> Path:
    return ensure_dir(status_dir())


def ensure_log_dir() -> Path:
    return ensure_dir(log_dir())


def ensure_config_dir() -> Path:
    return ensure_dir(config_dir())


def status_file(pid: int) -> Path:
    """The status file for one pid (the L2 contract's path)."""
    return status_dir() / ("%d.json" % int(pid))


def describe() -> dict:
    """Resolved paths, for `--selftest` and for a bug report."""
    return {
        "config": str(config_path()),
        "status": str(status_dir()),
        "logs": str(log_dir()),
    }
