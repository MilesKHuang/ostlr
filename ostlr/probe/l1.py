"""L1 - OSC 7501 capture from the PTY we own.

We own the pane's ConPTY, so the child's stdout passes through us: the sequence
the CLI emits is right there in the byte stream. That is the whole reason the
pane exists - a panel that only watches someone else's console can never have
L1.

Two obligations from the baseline (section 4.3):

1. **Answer a probe.** `ESC ] 7501 ; ? ST` means "do you understand this?". The
   protocol fails silently: no reply and the CLI stays quiet for the whole
   session. We reply immediately with the same sequence.
2. **Parse reports.** `ESC ] 7501 ; state=blocked:kind=permission:msg=<b64> ST`.
   The body is `key=value` pairs separated by `:`, `msg` and `title` are standard
   base64, and a sequence may be split across reads - so the parser keeps a
   remainder buffer.

Anything malformed is dropped, never guessed: a wrong state is worse than none.
"""

from __future__ import annotations

import base64
import binascii
from typing import Any, Dict, List, Optional, Tuple

OSC_START = "\x1b]7501;"
ST = "\x1b\\"
BEL = "\x07"

PROBE = OSC_START + "?" + ST
MAX_BUFFER = 64 * 1024      # a runaway stream must not grow without bound
MAX_SEQUENCE = 4096         # the spec's per-sequence cap

STATES = ("idle", "working", "blocked", "done", "error", "clear")
KINDS = ("permission", "question", "auth")

UNSUPPORTED = "unsupported"
REASON = "the pane answers probes and parses reports; L1 is live for pane sessions"


def supported() -> bool:
    """True: a pane owns the PTY, so the sequence is observable.

    Sessions started OUTSIDE the panel still have no L1 - we do not own their
    console - which is why the ladder keeps L2 and L3.
    """
    return True


def probe() -> Dict[str, Any]:
    return {"ok": True, "state": "supported", "reason": REASON}


def _decode(value: str) -> str:
    """Standard base64, tolerant of padding loss. Empty on failure."""
    text = (value or "").strip()
    if not text:
        return ""
    padding = "=" * (-len(text) % 4)
    try:
        return base64.b64decode(text + padding).decode("utf-8", "replace")
    except (binascii.Error, ValueError):
        return ""


def parse_body(body: str) -> Optional[Dict[str, Any]]:
    """One sequence body -> a report dict, or None when it is not a report.

    `body` is what sits between `ESC ] 7501 ;` and `ST`. A body of `?` is a probe,
    which is not a state report; the caller answers it instead.
    """
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
    report: Dict[str, Any] = {
        "v": 1,
        "state": state,
        "kind": fields.get("kind", "").lower(),
        "app": fields.get("app", ""),
        "id": fields.get("id", ""),
        "msg": _decode(fields.get("msg", "")),
        "title": _decode(fields.get("title", "")),
    }
    if report["state"] == "blocked" and report["kind"] not in KINDS:
        # A blocked report without a valid kind cannot tell a human why they are
        # needed; keep the state, drop the claim.
        report["kind"] = "permission"
    progress = fields.get("progress")
    if progress is not None:
        try:
            report["progress"] = max(0, min(100, int(progress)))
        except ValueError:
            pass
    return report


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
                self._buffer = text[-len(OSC_START):] if len(text) > len(OSC_START) else text
                break
            end_st = text.find(ST, start + len(OSC_START))
            end_bel = text.find(BEL, start + len(OSC_START))
            candidates = [pos for pos in (end_st, end_bel) if pos >= 0]
            if not candidates:
                # Incomplete: hold from the start marker, bounded.
                held = text[start:]
                self._buffer = held[:MAX_BUFFER]
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


def combine(l2_record: Optional[Dict[str, Any]] = None,
            l3_title: Optional[str] = None,
            alive: bool = True,
            now: int = 0,
            cfg: Any = None) -> Dict[str, Any]:
    """L1's contribution to the ladder when we parsed a report this tick.

    Kept for symmetry with the other sources: the ladder itself takes the state
    from the store, because an L1 report lands there like any other record.
    """
    if not alive:
        return {"state": "done", "source": "l1", "reason": "the pty ended"}
    return {"state": "unknown", "source": "none", "reason": "no report in this tick"}


def selftest_line() -> str:
    """The `--selftest` line for L1."""
    return "PASS l1: %s" % REASON
