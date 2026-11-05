"""L1 - OSC 7501, the Program Status Protocol, from the PTY we own.

Spec: https://www.superlogical.com/rex/docs/build/program-status (revision 0.4).
We are the terminal side. Three obligations:

1. **Answer the probe.** `ESC ] 7501 ; ? ST` means "do you understand this?".
   A producer that gets no reply may stay silent for the whole session, so the
   reply goes out the moment the query is seen. Ours names the states and kinds
   we accept, which 0.4 makes legal; a producer written against 0.3 must ignore
   the extra pairs, and the reference `osc7501` library parses both forms.
2. **Parse reports and keep records.** The body is `key=value` pairs separated by
   `:`, `state` is the only required key, `msg` and `title` are standard base64,
   and one sequence may be split across reads. A report is discarded whole only
   where the spec says so: a limit broken, base64 that does not decode, a control
   character, an `id` outside the grammar. A value the spec calls "treated as
   absent" - an unknown `kind`, a `progress` that is not 0-100, an `app` outside
   the character set - must not hide the state the program did report.
3. **Apply the lifetime.** `clear` removes a record and its subtree, a process
   exit or a new shell prompt (OSC 133 A) drops `working` and `blocked` while
   `done` and `error` stay, and a full reset (RIS, `ESC c`) removes every record.

A report replaces the record for its `id` (a field missing from the report is
gone from the record), and a record without `app` inherits it from the nearest
ancestor id that has one.

The pane shows one state, so this module also owns the summary rule: of the
records in one terminal, the most urgent wins (blocked > error > working > done >
idle), oldest first inside a state.
"""

from __future__ import annotations

import base64
import binascii
import time
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Dict, List, NamedTuple, Optional, Tuple

OSC_START = "\x1b]7501;"
ST = "\x1b\\"
BEL = "\x07"

STATES = ("idle", "working", "blocked", "done", "error", "clear")
KINDS = ("permission", "question", "auth")

# The states a report may carry, which is what a reply advertises: `clear` is not
# a state, it is an instruction.
REPORTABLE = tuple(state for state in STATES if state != "clear")

# Both forms are legal: 0.3 knew only the bare `?`, 0.4 lets the terminal list
# what it accepts. A 0.3 producer must ignore the pairs it does not understand.
PROBE = OSC_START + "?" + ST
REPLY = "%s?:states=%s:kinds=%s%s" % (OSC_START, ",".join(REPORTABLE),
                                      ",".join(KINDS), ST)

MAX_BUFFER = 64 * 1024      # a runaway stream must not grow without bound
MAX_SEQUENCE = 4096         # the spec's per-sequence cap

# Spec limits (revision 0.4). A report that breaks one is discarded whole.
MAX_KEY = 16
MAX_APP = 32
MAX_ID = 128
MAX_ID_SEGMENT = 32
MAX_ID_DEPTH = 8
MAX_TITLE_ENCODED = 256
MAX_TITLE_BYTES = 192
MAX_MSG_ENCODED = 2732
MAX_MSG_BYTES = 2048
MAX_RECORDS = 256

# Characters the spec allows in `app` and in an `id` segment.
_SEGMENT_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
                     "0123456789_.+-")
# The value character set: it excludes `:` and `;`, so nothing needs escaping.
_VALUE_CHARS = _SEGMENT_CHARS | set(",/=")
# A key is `[a-z]+` exactly; a key outside that is a malformed pair, not an
# extension, and dropping it keeps `STATE=blocked` from meaning anything.
_KEY_CHARS = set("abcdefghijklmnopqrstuvwxyz")

# How urgent a state is when the terminal has to pick one record to show.
_URGENCY = {"blocked": 0, "error": 1, "working": 2, "done": 3, "idle": 4}


def supported() -> bool:
    return True


def probe() -> Dict[str, Any]:
    return {"ok": True, "state": "supported", "reason": reason()}


def reason() -> str:
    return "the pane answers the probe and keeps per-id records (OSC 7501 rev 0.4)"


def _has_control(text: str) -> bool:
    """U+0000-U+001F, U+007F and U+0080-U+009F, tab included.

    0.4 names the C1 block because a single byte there is still a control
    function to a terminal (`U+009B` is CSI), and this text ends up in a row.
    """
    for char in text:
        code = ord(char)
        if code < 0x20 or code == 0x7F or 0x80 <= code <= 0x9F:
            return True
    return False


def disarm(text: str) -> str:
    """Drop invisible formatting characters: bidi overrides, zero-width joins.

    0.4 asks a terminal to disarm these when a record's text is shown outside the
    grid, and every surface we have - the row, the receipt, the status file - is
    outside it. An RLO in a message would otherwise reverse the rest of the line
    and could make `blocked` read as something else. The grid itself still gets
    the original bytes; this only affects the copy we keep.
    """
    return "".join(char for char in text if unicodedata.category(char) != "Cf")


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


def _valid_id(text: str) -> bool:
    """The grammar, or nothing.

    A report whose `id` does not match is ignored: falling back to the root
    record would let a malformed id overwrite a good one.
    """
    if text == "":
        return True
    if len(text.encode("utf-8")) > MAX_ID:
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
            continue                      # malformed pair: skipped, rest stands
        key, _, value = part.partition("=")
        key = key.strip()
        value = value.strip()
        if not key or set(key) - _KEY_CHARS:
            continue                      # malformed key: skipped
        if len(key.encode("utf-8")) > MAX_KEY:
            return None                   # a limit: the report is discarded
        if set(value) - _VALUE_CHARS:
            continue                      # malformed value: skipped
        fields[key] = value               # a repeated key: the last one wins

    state = fields.get("state", "")
    if state not in STATES:
        # Unrecognized is not guessed at, and `clear` is handled as an
        # instruction below, never stored as a state.
        return None

    record_id = fields.get("id", "")
    if not _valid_id(record_id):
        return None

    app = fields.get("app", "")
    if len(app.encode("utf-8")) > MAX_APP:
        return None
    if set(app) - _SEGMENT_CHARS:
        app = ""                          # the spec: outside the set is absent

    # The text limits hold for every report, `clear` included: a limit is a hard
    # cap on what the terminal has to do for one sequence, not a rule about what
    # the report means.
    raw_msg = fields.get("msg", "")
    raw_title = fields.get("title", "")
    if len(raw_msg) > MAX_MSG_ENCODED or len(raw_title) > MAX_TITLE_ENCODED:
        return None                       # checked encoded, before decoding
    msg = _decode(raw_msg)
    title = _decode(raw_title)
    if msg is None or title is None:
        return None
    if len(msg.encode("utf-8")) > MAX_MSG_BYTES:
        return None
    if len(title.encode("utf-8")) > MAX_TITLE_BYTES:
        return None
    if _has_control(msg) or _has_control(title):
        return None

    if state == "clear":
        # `id` decides the scope: with an id, that record and its subtree; without
        # one, every record in this terminal. Fields other than `id` are ignored
        # rather than discarding the report - the `osc7501` reference library
        # sends `app` alongside, and dropping a clear would leave a stale record.
        return {"state": "clear", "id": record_id, "app": "", "kind": "",
                "title": "", "msg": "", "progress": None}

    kind = fields.get("kind", "")
    if state != "blocked" or kind not in KINDS:
        kind = ""                         # outside blocked, or unknown: absent

    progress: Optional[int] = None
    raw_progress = fields.get("progress", "")
    if raw_progress and state in ("working", "blocked"):
        try:
            candidate = int(raw_progress)
        except ValueError:
            candidate = None
        if candidate is not None and 0 <= candidate <= 100:
            progress = candidate          # anything else is absent, not a discard

    return {"state": state, "id": record_id, "app": app, "kind": kind,
            "title": disarm(title), "msg": disarm(msg), "progress": progress}


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
    since: float = field(default_factory=time.time)     # when the state last changed
    updated: float = field(default_factory=time.time)   # when the record last changed

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

        now = time.time()
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
        record.updated = now
        if state != previous:
            record.since = now
        self._evict()
        return self.summary()

    def _remove_subtree(self, record_id: str) -> None:
        prefix = record_id + "/"
        for key in [k for k in self._records
                    if k == record_id or k.startswith(prefix)]:
            self._records.pop(key, None)

    def _evict(self) -> None:
        """Over the cap, drop the record that was updated least recently.

        0.4 names this rule so a hostile program cannot make the terminal keep
        memory: it is not "the least urgent", which would let a program that
        floods ids erase the `blocked` record the user has to answer.
        """
        while len(self._records) > self.max_records:
            victim = min(self._records.values(), key=lambda r: r.updated)
            self._records.pop(victim.id, None)

    def reset(self) -> None:
        """RIS: a full reset removes every record. A soft reset does not."""
        self._records.clear()

    def drop_transient(self) -> None:
        """The program exited, or a new shell prompt began.

        The spec drops `working` and `blocked` on both events and lets the
        terminal keep or drop `idle`; `done` and `error` stay - they are the
        result the user came back for.
        """
        for key in [k for k, r in self._records.items()
                    if r.state in ("working", "blocked")]:
            self._records.pop(key, None)

    # --- reads --------------------------------------------------------
    def records(self) -> List[Record]:
        return sorted(self._records.values(), key=lambda r: r.id)

    def app_for(self, record: Record) -> str:
        """`app` inherits from the nearest ancestor that has one.

        A program that reports `id=us-east` does not repeat `app` on every child;
        the spec says the child takes the ancestor's value, and a record with no
        app and no ancestor with one has none.
        """
        if record.app:
            return record.app
        parts = record.id.split("/") if record.id else []
        while parts:
            parts.pop()
            ancestor = self._records.get("/".join(parts))
            if ancestor is not None and ancestor.app:
                return ancestor.app
        return ""

    def summary(self) -> Optional[Record]:
        if not self._records:
            return None
        return min(self._records.values(),
                   key=lambda r: (_URGENCY.get(r.state, 9), r.since, r.id))

    def count(self) -> int:
        return len(self._records)


class Feed(NamedTuple):
    """What one read produced.

    `reports` and `probe` are ours to act on; `prompt` and `reset` are the
    lifetime events the terminal has to see, because a record must not outlive
    the thing it describes: a new shell prompt (OSC 133 A) ends a command's
    `working`/`blocked`, and a full reset (RIS) ends every record.
    """
    reports: List[Dict[str, Any]]
    probe: bool
    prompt: bool
    reset: bool


PROMPT_START = "\x1b]133;"
RIS = "\x1bc"

_GROUND = "ground"
_ESC = "esc"
_OSC = "osc"
_OSC_ESC = "osc-esc"
_CSI = "csi"
_CANCEL = ("\x18", "\x1a")        # CAN and SUB abandon a sequence


class _Scanner:
    """Reads the two lifetime signals out of the stream: RIS and OSC 133 A.

    A shortcut would search each read for `ESC c` and `ESC ] 133 ; A`, but an
    OSC payload - a window title, a hyperlink, a notification - holds arbitrary
    text, and a `ESC c` inside one is data, not a reset. Searching would let a
    title wipe every record on screen, so this walks the same states a terminal
    does and keeps its place between reads.
    """

    def __init__(self) -> None:
        self._state = _GROUND
        self._body = ""

    def feed(self, text: str) -> Tuple[bool, bool]:
        """Scan new text; return (a prompt began, a full reset happened)."""
        prompt = False
        reset = False
        for char in text:
            if self._state == _GROUND:
                if char == "\x1b":
                    self._state = _ESC
                continue
            if self._state == _ESC:
                reset = self._after_esc(char) or reset
                continue
            if self._state == _OSC:
                if char == BEL:
                    prompt = self._end_osc() or prompt
                elif char == "\x1b":
                    self._state = _OSC_ESC
                elif char in _CANCEL:
                    self._body = ""
                    self._state = _GROUND
                elif len(self._body) < 16:      # only the `133;A` head matters
                    self._body += char
                continue
            if self._state == _OSC_ESC:
                if char == "\\":
                    prompt = self._end_osc() or prompt
                else:
                    reset = self._after_esc(char) or reset
                continue
            # CSI: a reset is `ESC c`, never a CSI, so only the end matters.
            if char == "\x1b":
                self._state = _ESC
            elif char in _CANCEL:
                self._state = _GROUND
            elif "\x40" <= char <= "\x7e":
                self._state = _GROUND
        return prompt, reset

    def _after_esc(self, char: str) -> bool:
        """One character after ESC in the ground state. True for RIS."""
        if char == "]":
            self._body = ""
            self._state = _OSC
            return False
        if char == "[":
            self._state = _CSI
            return False
        if char == "\x1b":
            self._state = _ESC
            return False
        self._state = _GROUND
        return char == "c"

    def _end_osc(self) -> bool:
        body, self._body = self._body, ""
        self._state = _GROUND
        # `A` is the prompt mark; parameters may follow it (`A;cl=m`).
        return body.startswith("133;A")


class Stream:
    """A byte stream splitter: reports out, everything else passed through.

    The terminal still needs every byte (the sequences are zero-width and xterm
    ignores them), so this never swallows input - it only *also* reports what it
    saw.
    """

    def __init__(self) -> None:
        self._buffer = ""
        self._scanner = _Scanner()

    def feed(self, chunk: bytes) -> Feed:
        """Add bytes; return what they contained."""
        if not chunk:
            return Feed([], False, False, False)
        decoded = chunk.decode("utf-8", "replace")
        # The lifetime events are read off the new bytes only: they are signals a
        # shell and a reset send, and re-reading a buffered sequence would fire
        # the same reset twice.
        prompt, reset = self._scanner.feed(decoded)

        text = self._buffer + decoded
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
            if len(OSC_START) + len(body) + len(ST) > MAX_SEQUENCE:
                continue                  # the whole sequence, OSC through ST
            report = parse_body(body)
            if report is not None:
                reports.append(report)

        if len(self._buffer) > MAX_BUFFER:
            self._buffer = self._buffer[-MAX_BUFFER:]
        return Feed(reports, probe_seen, prompt, reset)


def selftest_line() -> str:
    return "PASS l1: %s" % reason()
