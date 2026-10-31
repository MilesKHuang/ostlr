"""L2 status files - the only *reported* state source.

Contract (plan section 8.2): `%LOCALAPPDATA%\\ostlr\\status\\<pid>.json`, written
by a producer with a temp file plus `os.replace`, so a reader never sees a
half-written file.

The reader's job is to be strict about what it accepts and quiet about what it
rejects:

  * a file that is not valid JSON, is missing a required field, carries an
    unknown `state`, or says `blocked` without a valid `kind` counts as ABSENT -
    the ladder then answers `unknown`, never a guess;
  * each bad file produces exactly ONE log line per read, not one per field.
    A producer that writes garbage every tick must not flood the log.

`read_status_dir` never raises: an unreadable or denied directory comes back as
an empty map plus a reason string the panel shows in its banner.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .. import paths, schema

LOG = logging.getLogger("ostlr.l2")

# The reason prefix the panel renders when the directory itself is unusable.
STATUS_DIR_UNREADABLE = "status_dir_unreadable"


def _as_int(value: Any, default: Optional[int] = None) -> Optional[int]:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def parse_ts(value: Any) -> int:
    """ISO-8601 -> epoch seconds. Returns 0 when unparseable.

    Accepts `2026-10-30T20:11:03Z`, `...+00:00` and a bare epoch number, because
    a producer written in three lines of shell will not always emit the exact
    form. 0 means "no timestamp", which the ladder treats as stale.
    """
    if value is None:
        return 0
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        number = float(value)
        if number <= 0:
            return 0
        return int(number / 1000.0) if number > 1e11 else int(number)
    text = str(value).strip()
    if not text:
        return 0
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        stamp = datetime.fromisoformat(text)
    except ValueError:
        return 0
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return int(stamp.timestamp())


def validate(raw: Any) -> Tuple[Optional[Dict[str, Any]], str]:
    """Validate one parsed L2 object.

    Returns (record, "") on success or (None, reason) on rejection. The reason
    is a short machine-ish token used in the single log line.
    """
    if not isinstance(raw, dict):
        return None, "not_an_object"

    pid = _as_int(raw.get("pid"), None)
    if pid is None or pid < 0:
        return None, "bad_pid"

    state = raw.get("state")
    if not isinstance(state, str) or not schema.is_valid_state(state):
        return None, "bad_state"

    state = state.strip().lower()
    kind = raw.get("kind")
    kind_text = kind.strip().lower() if isinstance(kind, str) else ""
    if state == "blocked":
        if not schema.is_valid_kind(kind_text):
            # A blocked record without a usable kind cannot tell the panel
            # whether a human is needed for permission, a question or auth.
            return None, "blocked_without_kind"
    else:
        kind_text = kind_text if schema.is_valid_kind(kind_text) else ""

    ts_epoch = parse_ts(raw.get("ts"))
    if ts_epoch <= 0:
        return None, "bad_ts"

    cwd = raw.get("cwd")
    msg = raw.get("msg")
    source = raw.get("source")
    exit_code = _as_int(raw.get("exit_code"), None)

    return {
        "pid": pid,
        "ts_epoch": ts_epoch,
        "source": source.strip() if isinstance(source, str) else "",
        "cwd": cwd.strip() if isinstance(cwd, str) else "",
        "state": state,
        "kind": kind_text,
        "msg": msg if isinstance(msg, str) else "",
        "exit_code": exit_code,
    }, ""


def read_file(path: Path) -> Tuple[Optional[Dict[str, Any]], str]:
    """Read and validate one status file. Never raises."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except FileNotFoundError:
        return None, "missing"
    except OSError as exc:
        return None, "unreadable:%s" % exc.__class__.__name__
    try:
        raw = json.loads(text)
    except json.JSONDecodeError:
        return None, "bad_json"
    return validate(raw)


def read_status_dir(directory: Optional[Path] = None,
                    log: Optional[logging.Logger] = None) -> Tuple[Dict[int, Dict[str, Any]], str]:
    """Read every `<pid>.json` in the status directory.

    Returns ({pid: record}, reason). `reason` is "" when the directory was
    readable, otherwise `status_dir_unreadable(<path>)` - the exact string the
    panel's banner shows, so a denied directory is never mistaken for "no
    producer is running".
    """
    logger = log or LOG
    target = Path(directory) if directory is not None else paths.status_dir()

    try:
        entries = sorted(target.iterdir())
    except FileNotFoundError:
        # No directory yet is normal on a fresh machine: no producer has run.
        return {}, ""
    except OSError:
        return {}, "%s(%s)" % (STATUS_DIR_UNREADABLE, target)

    out: Dict[int, Dict[str, Any]] = {}
    for entry in entries:
        try:
            is_file = entry.is_file()
        except OSError:
            continue
        if not is_file or entry.suffix.lower() != ".json":
            continue
        record, reason = read_file(entry)
        if record is None:
            if reason == "missing":
                # Raced with a producer's os.replace; not a fault.
                continue
            # Exactly one line per bad file per read.
            logger.warning("l2: ignoring %s (%s)", entry.name, reason)
            continue
        # The filename is the contract's key; a record whose pid disagrees with
        # its own filename is a producer bug, and the filename wins so the row
        # cannot be attributed to the wrong process.
        stem_pid = _as_int(entry.stem, None)
        if stem_pid is not None and stem_pid != record["pid"]:
            logger.warning("l2: ignoring %s (pid_mismatch:%s)", entry.name, record["pid"])
            continue
        out[record["pid"]] = record
    return out, ""


def write_record(record: Dict[str, Any], directory: Optional[Path] = None) -> bool:
    """Write one status file atomically (temp + os.replace).

    Used by the producer (T8) and by tests. Returns False instead of raising:
    a read-only status directory is a condition to report, not a crash.
    """
    target_dir = Path(directory) if directory is not None else paths.status_dir()
    pid = _as_int(record.get("pid"), None)
    if pid is None:
        return False
    target = target_dir / ("%d.json" % pid)
    tmp = target_dir / ("%d.json.tmp" % pid)
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        try:
            tmp.unlink()
        except OSError:
            pass
        return False
    return True


def age_s(record: Dict[str, Any], now: int) -> int:
    """How old a record is, in seconds. Negative ages clamp to 0.

    A producer whose clock is ahead of ours must not make a record look
    infinitely fresh, and must not make it look stale either - 0 is the honest
    answer for "written in the future".
    """
    ts = _as_int(record.get("ts_epoch"), 0) or 0
    if ts <= 0:
        return 0
    delta = int(now) - int(ts)
    return delta if delta > 0 else 0


def is_fresh(record: Optional[Dict[str, Any]], now: int, stale_s: int) -> bool:
    """True when the record exists and is within the staleness window."""
    if not record:
        return False
    return age_s(record, now) <= max(0, int(stale_s))


def records_for(records: Dict[int, Dict[str, Any]], pid: int) -> Optional[Dict[str, Any]]:
    """The record for one pid, or None."""
    return records.get(int(pid)) if records else None


def summarise(records: Dict[int, Dict[str, Any]]) -> List[str]:
    """One line per record, for `--selftest` and for a bug report."""
    out: List[str] = []
    for pid in sorted(records):
        rec = records[pid]
        out.append("%d %s%s" % (pid, rec["state"],
                                ("/" + rec["kind"]) if rec["kind"] else ""))
    return out
