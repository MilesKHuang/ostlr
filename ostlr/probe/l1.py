"""L1 - OSC 7501, the Program Status Protocol, from the PTY we own.

Spec: https://www.superlogical.com/rex/docs/build/program-status (revision 0.3).
We are the terminal side. Two obligations:

1. **Answer the probe.** `ESC ] 7501 ; ? ST` means "do you understand this?".
   A producer that gets no reply may stay silent for the whole session, so the
   reply is written the moment the query is seen.
2. **Parse reports and keep records.** The body is `key=value` pairs separated by
   `:`, `state` is the only required key, `msg` and `title` are standard base64,
   and one sequence may be split across reads.

A report replaces the record for its `id` (missing fields are gone from that
record), `clear` removes a record and its subtree, and a report that breaks any
limit is discarded whole - a partly-read record is worse than none.

The pane shows one state, so this module also owns the summary rule: of the
records in one terminal, the most urgent wins (blocked > error > working > done >
idle), oldest first inside a state.
"""

from __future__ import annotations

import base64
import binascii
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

OSC_START = "\x1b]7501;"
ST = "\x1b\\"
BEL = "\x07"

PROBE = OSC_START + "?" + ST
MAX_BUFFER = 64 * 1024      # a runaway stream must not grow without bound
MAX_SEQUENCE = 4096         # the spec's per-sequence cap

STATES = ("idle", "working", "blocked", "done", "error", "clear")
KINDS = ("permission", "question", "auth")

# Spec limits (revision 0.3). A report that breaks one is discarded whole.
MAX_APP = 32
MAX_ID = 128
MAX_ID_SEGMENT = 32
MAX_ID_DEPTH = 8
MAX_TITLE_BYTES = 192
MAX_MSG_BYTES = 2048
MAX_RECORDS = 256

# Characters the spec allows in `app` and in an `id` segment.
_SEGMENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                     "0123456789_.+-")

# How urgent a state is when the terminal has to pick one record to show.
_URGENCY = {"blocked": 0, "error": 1, "working": 2, "done": 3, "idle": 4}


def supported() -> bool:
    return True


def probe() -> Dict[str, Any]:
    return {"ok": True, "state": "supported", "reason": reason()}


def reason() -> str:
    return "the pane answers the probe and keeps per-id records (OSC 7501 rev 0.3)"


def _decode(value: str) -> Optional[str]:
    """Standard base64 -> text. None when it is not valid base64."""
    text = (value or "").strip()
    if not text:
        return ""
    padding = "=" * (-len(text) % 4)
    try:
        raw = base64.b64decode(text + padding, validate=True)
    except (binascii.Error, ValueError):
        return None
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        return None


def _valid_segment(text: str) -> bool:
    return bool(text) and len(text) <= MAX_ID_SEGMENT and set(text) <= _SEGMENT_CHARS


def _valid_app(text: str) -> bool:
    return text == "" or (len(text) <= MAX_APP and set(text) <= _SEGMENT_CHARS)


def _valid_id(text: str) -> bool:
    if text == "":
        return True
    if len(text) > MAX_ID:
        return False
    segments = text.split("/")
    if len(segments) > MAX_ID_DEPTH:
        return False
    return all(_valid_segment(segment) for segment in segments)


def parse_body(body: str) -> Optional[Dict[str, Any]]:
    """One sequence body -> a report dict, or None when it must be discarded."""
    text = (body or "").strip()
    if not text or text == "?":
        return None
    fields: Dict[str, str] = {}
    for part in text.split(":"):
        if "=" not in part:
            continue
        key, _, value = part.partition("=")
        fields[key.strip().lower()] = value.strip()

    state = fields.get("state", "").lower()
    if state not in STATES:
        return None

    app = fields.get("app", "")
    if not _valid_app(app):
        return None
    record_id = fields.get("id", "")
    if not _valid_id(record_id):
        return None

    if state == "clear":
        # `id` decides the scope: with an id, that record and its subtree; without
        # one, every record in this terminal. Extra fields are ignored rather than
        # discarding the report - the `osc7501` reference library sends `app`
        # alongside, and dropping a clear would leave a stale record on screen.
        return {"state": "clear", "id": record_id, "app": "", "kind": "",
                "title": "", "msg": "", "progress": None}

    kind = fields.get("kind", "").lower()
    if kind and (state != "blocked" or kind not in KINDS):
        return None

    progress: Optional[int] = None
    if "progress" in fields:
        if state not in ("working", "blocked"):
            return None
        try:
            progress = int(fields["progress"])
        except ValueError:
            return None
        if not 0 <= progress <= 100:
            return None

    msg = _decode(fields.get("msg", ""))
    title = _decode(fields.get("title", ""))
    if msg is None or title is None:
        return None
    if len(msg.encode("utf-8")) > MAX_MSG_BYTES:
        return None
    if len(title.encode("utf-8")) > MAX_TITLE_BYTES:
        return None
    if any(ord(ch) < 0x20 and ch != "\t" for ch in msg + title):
        return None

    return {"state": state, "id": record_id, "app": app, "kind": kind,
            "title": title, "msg": msg, "progress": progress}


@dataclass
class Record:
    """One record the terminal keeps for a program."""
    id: str = ""
    app: str = ""
    state: str = "idle"
    kind: str = ""
    title: str = ""
    msg: str = ""
    progress: Optional[int] = None
    since: float = field(default_factory=time.time)

    def as_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "app": self.app, "state": self.state,
                "kind": self.kind, "title": self.title, "msg": self.msg,
                "progress": self.progress, "since": self.since}


class Store:
    """The records of one terminal, and the rule that picks what to show.

    Per-id records exist so a program can report its subagents separately; a pane
    only has one row, so `summary()` applies the spec's rule: the most urgent
    record wins, and the oldest wins inside a state.
    """

    def __init__(self, max_records: int = MAX_RECORDS) -> None:
        self.max_records = max(8, min(int(max_records), 256))
        self._records: Dict[str, Record] = {}

    # --- writes -------------------------------------------------------
    def apply(self, report: Dict[str, Any]) -> Optional[Record]:
        """Fold one report in. Returns the summary record afterwards."""
        state = str(report.get("state") or "")
        record_id = str(report.get("id") or "")
        if state == "clear":
            if record_id:
                self._remove_subtree(record_id)
            else:
                self._records.clear()
            return self.summary()

        record = self._records.get(record_id)
        if record is None:
            record = Record(id=record_id)
            self._records[record_id] = record
        previous = record.state
        record.app = str(report.get("app") or "")
        record.state = state
        record.kind = str(report.get("kind") or "")
        record.title = str(report.get("title") or "")
        record.msg = str(report.get("msg") or "")
        record.progress = report.get("progress")
        if state != previous:
            record.since = time.time()
        self._evict()
        return self.summary()

    def _remove_subtree(self, record_id: str) -> None:
        prefix = record_id + "/"
        for key in [k for k in self._records
                    if k == record_id or k.startswith(prefix)]:
            self._records.pop(key, None)

    def _evict(self) -> None:
        """Over the cap, drop the least urgent and then the oldest."""
        while len(self._records) > self.max_records:
            victim = max(self._records.values(),
                         key=lambda r: (_URGENCY.get(r.state, 9), r.since))
            self._records.pop(victim.id, None)

    def drop_transient(self) -> None:
        """The program exited: the spec drops `working` and `blocked` records.

        `done` and `error` stay - they are the result the user came back for.
        """
        for key in [k for k, r in self._records.items()
                    if r.state in ("working", "blocked")]:
            self._records.pop(key, None)

    # --- reads --------------------------------------------------------
    def records(self) -> List[Record]:
        return sorted(self._records.values(), key=lambda r: r.id)

    def summary(self) -> Optional[Record]:
        if not self._records:
            return None
        return min(self._records.values(),
                   key=lambda r: (_URGENCY.get(r.state, 9), r.since, r.id))

    def count(self) -> int:
        return len(self._records)


class Stream:
    """A byte stream splitter: reports out, everything else passed through.

    The terminal still needs every byte (the sequences are zero-width and xterm
    ignores them), so this never swallows input - it only *also* reports what it
    saw.
    """

    def __init__(self) -> None:
        self._buffer = ""

    def feed(self, chunk: bytes) -> Tuple[List[Dict[str, Any]], bool]:
        """Add bytes; return (reports, saw_probe)."""
        if not chunk:
            return [], False
        text = self._buffer + chunk.decode("utf-8", "replace")
        reports: List[Dict[str, Any]] = []
        probe_seen = False

        while True:
            start = text.find(OSC_START)
            if start < 0:
                # Keep a short tail: a sequence may begin in the next chunk.
                self._buffer = (text[-len(OSC_START):]
                                if len(text) > len(OSC_START) else text)
                break
            end_st = text.find(ST, start + len(OSC_START))
            end_bel = text.find(BEL, start + len(OSC_START))
            candidates = [pos for pos in (end_st, end_bel) if pos >= 0]
            if not candidates:
                # Incomplete: hold from the start marker, bounded.
                self._buffer = text[start:][:MAX_BUFFER]
                break
            end = min(candidates)
            body = text[start + len(OSC_START):end]
            text = text[end + (1 if end == end_bel else len(ST)):]

            if body.strip() == "?":
                probe_seen = True
                continue
            if len(body) > MAX_SEQUENCE:
                continue
            report = parse_body(body)
            if report is not None:
                reports.append(report)

        if len(self._buffer) > MAX_BUFFER:
            self._buffer = self._buffer[-MAX_BUFFER:]
        return reports, probe_seen


def selftest_line() -> str:
    return "PASS l1: %s" % reason()
