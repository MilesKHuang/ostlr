"""Headless self-checks: `py -3 -m ostlr --selftest [--clipboard]`.

One line per check, `PASS` / `FAIL` / `SKIP`, and a non-zero exit code only for
a real failure. A SKIP is not a failure: a windowless session, a missing `pwsh`
or a machine with no clipboard must not read as a broken build.

The clipboard check is the interesting one (plan 8.6): it reads the EXISTING
clipboard first, because a foreign non-empty clipboard is the normal case, then
writes a known path, reads it back through the same ctypes `GetClipboardData`,
and restores the previous content best-effort. It never requires an empty
clipboard and never imports tkinter.
"""

from __future__ import annotations

import sys
from typing import Any, Dict, List, Optional

from . import actions, paths, schema

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

# The path the clipboard check writes. Deliberately not a real file: the check
# is about the clipboard round trip, not about the filesystem.
PROBE_PATH = "D:/ws/ostlr-selftest/clipboard-probe.txt"


def _line(status: str, name: str, detail: str = "") -> str:
    return "%s %s%s" % (status, name, (": " + detail) if detail else "")


def check_paths() -> List[str]:
    resolved = paths.describe()
    return [
        _line(PASS, "paths config", resolved["config"]),
        _line(PASS, "paths status", resolved["status"]),
        _line(PASS, "paths logs", resolved["logs"]),
    ]


def check_schema() -> List[str]:
    header = schema.empty_header()
    missing = [k for k in schema.HEADER_KEYS if k not in header]
    if missing:
        return [_line(FAIL, "schema header", "missing %s" % ", ".join(missing))]
    return [_line(PASS, "schema header", "%d keys" % len(header))]


def check_clipboard(clipboard: Any = None) -> List[str]:
    """The `--clipboard` check: read, write, read back, restore."""
    writer = clipboard if clipboard is not None else actions.default_clipboard()
    if writer is None:
        return [_line(SKIP, "clipboard", "no clipboard on this platform")]

    previous = writer.read()
    if previous.get("ok"):
        before = previous.get("text") or ""
        lines = [_line(PASS, "clipboard read", "%d chars already present" % len(before))]
    else:
        before = ""
        # An empty or non-text clipboard is not a failure: it is the state a
        # fresh session starts in.
        lines = [_line(SKIP, "clipboard read", previous.get("reason") or "empty")]

    written = writer.write(PROBE_PATH)
    if not written.get("ok"):
        lines.append(_line(FAIL, "clipboard write", written.get("reason") or "failed"))
        return lines
    lines.append(_line(PASS, "clipboard write", PROBE_PATH))

    readback = writer.read()
    if not readback.get("ok"):
        lines.append(_line(FAIL, "clipboard readback", readback.get("reason") or "failed"))
    elif readback.get("text") == PROBE_PATH:
        lines.append(_line(PASS, "clipboard readback", "equals the written path"))
    else:
        lines.append(_line(FAIL, "clipboard readback",
                           "got %r" % (readback.get("text"),)))

    # Restore best effort: the user's clipboard is theirs, not ours.
    if before:
        restored = writer.write(before)
        if restored.get("ok"):
            lines.append(_line(PASS, "clipboard restore", "%d chars" % len(before)))
        else:
            lines.append(_line(SKIP, "clipboard restore",
                               restored.get("reason") or "failed"))
    else:
        lines.append(_line(SKIP, "clipboard restore", "nothing to restore"))
    return lines


def check_copy_receipts() -> List[str]:
    """The three receipt variants, proven without touching the clipboard."""
    from . import view_model

    same = view_model.receipt("copied", "D:/ws/projV/main.cpp", "claude", "projV", True)
    cross = view_model.receipt("copied", "D:/ws/projV/main.cpp", "codex", "sop-test", False)
    none = view_model.receipt("copied", "D:/ws/projV/main.cpp", "", "", None)
    ok = (same.endswith("(same project)") and cross.endswith("(cross project)")
          and none.endswith("(no target)") and max(len(same), len(cross), len(none)) <= 120)
    if ok:
        return [_line(PASS, "receipts", "same / cross / no target, <= 120 chars")]
    return [_line(FAIL, "receipts", "a variant is wrong or too long")]


def check_dedupe() -> List[str]:
    """Two copies of one path inside 100 ms produce exactly one write."""
    class _Writer:
        def __init__(self):
            self.calls = []

        def write(self, text):
            self.calls.append(text)
            return {"ok": True, "reason": "copied"}

    clock = [0.0]
    gate = actions.CopyGate(clock=lambda: clock[0])
    writer = _Writer()
    actions.copy_path("D:/ws/x", None, "", clipboard=writer, gate=gate)
    clock[0] = 0.05
    actions.copy_path("D:/ws/x", None, "", clipboard=writer, gate=gate)
    if len(writer.calls) == 1:
        return [_line(PASS, "copy dedupe", "2 copies in 50 ms -> 1 write")]
    return [_line(FAIL, "copy dedupe", "%d writes" % len(writer.calls))]


def check_cim_stall() -> List[str]:
    """The forced stall: a sleepy fake command must give up at the timeout.

    This is the R1 mitigation measured, not asserted: the probe is given a
    command that sleeps far longer than the timeout, and the elapsed wall time
    must stay under 3.2 s. It runs once - a second 3 s sleep would make the
    selftest feel broken.
    """
    import time

    from .probe import cim

    if getattr(sys, "frozen", False):
        # A frozen exe has no interpreter to hand a `-c` script to; the probe's
        # own unit tests cover the timeout path. Say so instead of failing.
        return [_line(SKIP, "cim stall", "frozen build: no interpreter for the sleepy fake")]
    sleepy = [sys.executable, "-c", "import time; time.sleep(30)"]
    probe = cim.CimProbe(command=sleepy, timeout_ms=3000)
    started = time.monotonic()
    sample = probe.sample()
    elapsed = time.monotonic() - started
    if sample.ok:
        return [_line(FAIL, "cim stall", "the sleepy command returned a sample")]
    if elapsed >= 3.2:
        return [_line(FAIL, "cim stall", "%.2fs >= 3.2s" % elapsed)]
    return [_line(PASS, "cim stall", "%.2fs < 3.2s" % elapsed)]


def check_l1() -> List[str]:
    """L1 (OSC 7501) is deferred: a SKIP with the reason, never a failure."""
    from .probe import l1

    if l1.supported():
        return [_line(PASS, "l1", "supported")]
    return [_line(SKIP, "l1", "%s (%s)" % (l1.UNSUPPORTED, l1.REASON))]


def check_imports() -> List[str]:
    """No third-party import anywhere under ostlr/."""
    import ast
    import os

    root = os.path.dirname(os.path.abspath(__file__))
    stdlib = set(sys.stdlib_module_names)
    offenders: List[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            if not name.endswith(".py"):
                continue
            path = os.path.join(dirpath, name)
            try:
                with open(path, "r", encoding="utf-8") as handle:
                    tree = ast.parse(handle.read())
            except (OSError, SyntaxError):
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        top = alias.name.split(".")[0]
                        if top not in stdlib and top != "ostlr":
                            offenders.append("%s: %s" % (name, top))
                elif isinstance(node, ast.ImportFrom):
                    if node.level:  # a relative import is ours
                        continue
                    top = (node.module or "").split(".")[0]
                    if top and top not in stdlib and top != "ostlr":
                        offenders.append("%s: %s" % (name, top))
    if offenders:
        return [_line(FAIL, "imports", ", ".join(sorted(set(offenders))))]
    return [_line(PASS, "imports", "0 non-stdlib imports")]


def run(clipboard: bool = False, clipboard_backend: Any = None) -> int:
    """Run every check and return the exit code (0 unless something FAILed)."""
    lines: List[str] = []
    lines += check_paths()
    lines += check_schema()
    lines += check_imports()
    lines += check_copy_receipts()
    lines += check_dedupe()
    lines += check_cim_stall()
    lines += check_l1()
    if clipboard:
        lines += check_clipboard(clipboard_backend)

    for line in lines:
        print(line)
    failures = [line for line in lines if line.startswith(FAIL)]
    return 1 if failures else 0


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    return run(clipboard="--clipboard" in args)


if __name__ == "__main__":
    raise SystemExit(main())
