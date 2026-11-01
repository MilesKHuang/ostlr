"""Age of a status record, in one place.

`app.pane_rows` needs this to stop repeating a state nobody re-measured: a record
with an old `ts` must not keep a row saying `working`. Every consumer that ages a
record goes through here, so the two readings ("is it stale?" in sessions.py and
"how old is it?" in the window) cannot drift apart.
"""

from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional


def _parse_iso(text: str) -> Optional[int]:
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            moment = datetime.strptime(str(text), fmt).replace(tzinfo=timezone.utc)
            return int(moment.timestamp())
        except (TypeError, ValueError):
            continue
    return None


def record_epoch(record: Optional[Dict[str, Any]]) -> Optional[int]:
    """When the record was written, as a unix epoch, or None if undatable."""
    if not isinstance(record, dict):
        return None
    for key in ("ts_epoch", "epoch"):
        value = record.get(key)
        if isinstance(value, (int, float)):
            return int(value)
    stamp = record.get("ts")
    if isinstance(stamp, str) and stamp:
        return _parse_iso(stamp)
    return None


def record_age(record: Optional[Dict[str, Any]], now: Optional[int] = None) -> Optional[int]:
    """Seconds since the record was written, or None when it cannot be dated."""
    epoch = record_epoch(record)
    if epoch is None:
        return None
    return max(0, int(now if now is not None else time.time()) - epoch)


def is_fresh(record: Optional[Dict[str, Any]], stale_s: int = 90,
             now: Optional[int] = None) -> bool:
    """A record nobody can date is not fresh: guessing hides a dead producer."""
    age = record_age(record, now)
    return age is not None and age <= max(0, int(stale_s))
