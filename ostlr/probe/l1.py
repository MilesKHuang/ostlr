"""L1 - OSC 7501 (Program Status Protocol) capture. A documented stub.

L1 is the *best* state source: the CLI itself reports `working` / `blocked` /
`done` over the terminal, with a reason and a message. It is also the only one
that needs something this machine cannot install.

What L1 would need, concretely:

  1. **Ownership of the session's PTY.** We must be the process that spawned the
     CLI, because OSC 7501 arrives on the child's stdout. That means a PTY layer
     - `pywinpty` (ConPTY) on Windows, `ptyprocess` on Unix. Both are third-party
     and neither installs here (PyPI is effectively unavailable on this box), so
     the panel cannot own a session and cannot see the sequence.
  2. **Answering the probe.** A CLI may send `ESC ] 7501 ; ? ST` and wait for a
     reply. The protocol fails silently: no reply means the CLI stays quiet for
     the whole session. We would have to answer immediately, and answer *before*
     the DA1 reply, because Claude Code uses DA1 as a sentinel and latches
     "unsupported" if DA1 wins the race (claude-code#105534).
  3. **A VT parser that keeps OSC payloads.** ConPTY's VtEngine understands
     CSI/OSC/DCS but not APC, and `PSEUDOCONSOLE_PASSTHROUGH_MODE` is accepted
     and then ignored on some builds. So even with a PTY, the sequence may be
     stripped before we see it - which is why the plan makes M0 a measurement,
     not an assumption.

Until all three hold, L1 reports `unsupported` and the ladder falls back to L2
(status files) and L3 (window titles). That is a stated limit, not a bug: the
panel says `L1: unsupported` in its capability line so a user never mistakes a
grey row for a broken tool.

This module is deliberately import-free of anything but the standard library and
has no side effects, so `--selftest` can call it on any machine.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

# The one word every caller checks. Kept as a constant so a test can assert it
# without duplicating the string.
UNSUPPORTED = "unsupported"

# The reason, in one line, naming the actual blocker. It used to say "pywinpty
# unavailable"; pywinpty is installed now and the pane owns a real ConPTY, so the
# remaining gap is the parser, not the PTY.
REASON = "the pane does not parse OSC 7501 yet: ConPTY is owned, the sequence is not read"


def supported() -> bool:
    """False: the pane owns a PTY now, but it does not read OSC 7501 from it.

    That is the next step rather than a mystery: `terminal.PtyBridge.data` already
    carries every byte the child writes, so a parser that strips `ESC ] 7501 ;`
    records out of that stream and answers `ESC ] 7501 ; ? ST` probes is a
    contained change to one module.
    """
    return False


def probe() -> Dict[str, Any]:
    """What L1 can do right now: nothing, with a reason.

    Shaped like the other probes' results (`ok` + `reason`) so a caller can
    treat L1 uniformly and never special-case it.
    """
    return {"ok": False, "state": UNSUPPORTED, "reason": REASON}


def combine(l2_record: Optional[Dict[str, Any]] = None,
            l3_title: Optional[str] = None,
            alive: bool = True,
            now: int = 0,
            cfg: Any = None) -> Dict[str, Any]:
    """L1's contribution to the ladder: always `unsupported`.

    The ladder does not call this - it is here so the deferral is visible in the
    same shape as the other sources, and so a future implementation has a place
    to land without changing any caller.
    """
    return {"state": UNSUPPORTED, "source": "none", "reason": REASON}


def selftest_line() -> str:
    """The `--selftest` line for L1: a SKIP, never a failure."""
    return "SKIP l1: %s (%s)" % (UNSUPPORTED, REASON)
