"""The terminal pane: a real ConPTY bridged into xterm.js inside QtWebEngine.

Why this shape (design baseline 4.1, now that the mirror provides the wheels):

  * we own the PTY -> the panel sees the same bytes the terminal does, so state,
    scrollback and keystrokes are ours to read (that is what L1 needs);
  * xterm.js does the VT parsing and rendering -> no hand-written terminal, no
    glyph atlas, and IME works because Chromium handles composition;
  * QtWebEngine hosts it -> a native window, DPI aware, no browser chrome.

The bridge is deliberately dumb: PTY bytes are base64-encoded into
`runJavaScript`, and keystrokes come back through the JavaScript console. A
webchannel would be tidier, but `qwebchannel.js` is not shipped with PySide6 and
fetching it is one more moving part; a log line per keystroke costs nothing.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import sys
import time
from typing import Optional

from PySide6.QtCore import QThread, QTimer, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QVBoxLayout, QWidget

HERE = os.path.dirname(os.path.abspath(__file__))
TERM_HTML = os.path.join(HERE, "term.html")


def _bundled(name: str) -> str:
    """A data file next to this module, also inside a PyInstaller bundle.

    A frozen build sets `__file__` to a path inside the unpacked archive; the
    layout is preserved, so the join normally works. The _MEIPASS branch covers a
    build that puts the data at the archive root instead.
    """
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), name)
    if os.path.exists(here):
        return here
    base = getattr(sys, "_MEIPASS", "")
    if base:
        for candidate in (os.path.join(base, "ostlr", "qt", name), os.path.join(base, name)):
            if os.path.exists(candidate):
                return candidate
    return here

# One QWebEngineView is a Chromium renderer (~100 MB). The design caps panes at
# four; beyond that the panel would cost more than the agents it watches.
MAX_PANES = 4


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class PtyBridge(QThread):
    """Owns the pseudo console and pumps both directions."""

    data = Signal(bytes)
    failed = Signal(str)
    started = Signal(int)
    heartbeat = Signal()
    resized = Signal(int, int)

    def __init__(self, argv, cwd: Optional[str] = None, cols: int = 100,
                 rows: int = 30, parent=None) -> None:
        super().__init__(parent)
        self._argv = list(argv) if not isinstance(argv, str) else [argv]
        self._cwd = cwd
        self._cols = cols
        self._rows = rows
        self._process = None
        self._stop = False
    # --- lifecycle ---------------------------------------------------
    def run(self) -> None:  # noqa: D401 - QThread entry point
        """Read the PTY and emit what arrives.

        This uses `winpty.PTY`, the C++ object, not `PtyProcess`: the wrapper puts
        a socket and a pump thread (which sleeps 1 ms per chunk and reassembles
        UTF-8 one byte at a time) between the pseudo console and us, and that hop
        is what capped throughput. PTY gives the same console with nothing in
        between, and `set_size` is the resize the child actually observes.
        """
        try:
            from winpty import PTY
        except Exception as exc:  # pragma: no cover - packaging failure
            self.failed.emit("winpty is not available: %s" % exc)
            return

        try:
            pty = PTY(self._cols, self._rows)
            pty.spawn(self._argv[0],
                      cmdline=" ".join(self._argv) if len(self._argv) > 1 else None,
                      cwd=self._cwd)
        except Exception as exc:
            self.failed.emit(str(exc))
            return

        self._process = pty
        self.started.emit(int(pty.pid))
        last_report = 0.0
        while not self._stop:
            try:
                chunk = pty.read(blocking=True)
            except Exception:
                break
            if not chunk:
                if pty.iseof() or not pty.isalive():
                    break
                continue
            if isinstance(chunk, str):
                chunk = chunk.encode("utf-8", "replace")
            self.data.emit(chunk)
            now = time.time()
            if now - last_report > 5.0:
                last_report = now
                self.heartbeat.emit()
        try:
            self.exit_code = pty.get_exitstatus()
        except Exception:
            self.exit_code = None
        self.failed.emit("process exited")


    # --- input -------------------------------------------------------
    def write(self, text: str) -> None:
        if self._process is None or not text:
            return
        try:
            self._process.write(text)
        except Exception:
            pass

    def resize(self, cols: int, rows: int) -> None:
        """ResizePseudoConsole under the hood: the child sees this size."""
        if cols <= 0 or rows <= 0:
            return
        self._cols, self._rows = cols, rows
        if self._process is None:
            return
        try:
            self._process.set_size(int(cols), int(rows))
        except Exception:
            pass

    def stop(self) -> None:
        self._stop = True
        process, self._process = self._process, None
        if process is not None:
            try:
                process.cancel_io()   # unblocks the reader thread
            except Exception:
                pass
            try:
                process.terminate(force=True)
            except Exception:
                pass


class _Page(QWebEnginePage):
    """A page that forwards `OSTLR:{...}` console lines to the pane."""

    def __init__(self, owner, parent=None) -> None:
        super().__init__(parent)
        self._owner = owner

    def javaScriptConsoleMessage(self, level, message, line, source):  # noqa: N802
        # OSTLR_DEBUG_JS=1 surfaces the page's own console: in a packaged build
        # there is no devtools, and a silent blank pane is the worst symptom to
        # chase. Every other message is ignored unless that flag is set.
        if os.environ.get("OSTLR_DEBUG_JS") == "1" and not message.startswith("OSTLR:"):
            print("[js] %s (%s:%s)" % (message, source, line), flush=True)
        self._owner.on_console(message)


class TerminalPane(QWidget):
    """One tab: a PTY plus the xterm.js view that renders it."""

    titleChanged = Signal(str)
    exited = Signal(str)
    # (pid, report): an OSC 7501 report from the CLI running in this pane.
    l1_report = Signal(int, dict)
    # The PTY started late (once the page knew its grid), so callers listen here
    # instead of reaching into the bridge that may not exist yet.
    started = Signal(int)

    def stop(self) -> None:
        if self.bridge is not None:
            self.bridge.stop()
        if self._flush_timer is not None:
            self._flush_timer.stop()

    def write(self, text: str) -> None:
        if self.bridge is not None:
            self.bridge.write(text)

    def __init__(self, argv, cwd: Optional[str] = None, title: str = "",
                 parent=None) -> None:
        super().__init__(parent)
        self.argv = list(argv) if not isinstance(argv, str) else [argv]
        self.cwd = cwd
        self.title = title or " ".join(self.argv)[:40]
        self._ready = False
        self._pending: list = []
        # Filled in once the PTY reports its pid: this is the session identity.
        self.session_pid = 0
        self.exited_code = None
        self._l1 = None
        try:
            from ..probe import l1 as l1_mod
            self._l1 = l1_mod.Stream()
        except Exception:
            self._l1 = None
        # The PTY is NOT started here: it waits for the page's first grid report,
        # so the child is born with the right size. Spawning at 100x30 and
        # resizing later left the TUI with a half-repainted frame - the stray
        # box-drawing characters the user saw.
        self.bridge = None
        self._buffer = bytearray()
        self._started = False
        self._flush_timer = None
        self._last_grid = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        self.view.setPage(_Page(self, self.view))
        layout.addWidget(self.view)
        self.view.loadFinished.connect(self._on_load)
        self.view.load(QUrl.fromLocalFile(_bundled("term.html")))

        # Fallback: if the page never reports a grid, start anyway after a second
        # so a broken web view cannot leave the pane permanently empty.
        QTimer.singleShot(1000, lambda: self._start_pty(100, 30))

    # --- pty lifecycle: born at the right size ---------------------------
    def _start_pty(self, cols: int, rows: int) -> None:
        """Spawn the child at the grid the pane actually has.

        The page can report a placeholder grid before the layout settles (20x5 was
        observed), and spawning there leaves a TUI drawing for a size it never
        sees again - the stray borders. So a grid must be plausible AND seen twice
        before it is trusted; the 1 s fallback spawns with the last seen size so a
        broken web view still gets a terminal.
        """
        if self._started or not self._ready:
            return
        cols, rows = int(cols), int(rows)
        if not self._plausible(cols, rows):
            self._last_grid = (cols, rows)
            return
        if self._last_grid != (cols, rows) and self._last_grid is not None:
            # Changed since the last report: wait for the next one to settle.
            self._last_grid = (cols, rows)
            QTimer.singleShot(120, lambda: self._start_pty(*self._last_grid))
            return
        self._last_grid = (cols, rows)
        self._started = True
        self.bridge = PtyBridge(self.argv, cwd=self.cwd, cols=cols, rows=rows)
        self.bridge.data.connect(self._on_data)
        self.bridge.failed.connect(self._on_failed)
        self.bridge.started.connect(self._on_started)
        self.bridge.heartbeat.connect(self.heartbeat)
        self.bridge.start()
        self._flush_timer = QTimer(self)
        self._flush_timer.timeout.connect(self._flush)
        self._flush_timer.start(16)   # 60 Hz: batching, not one IPC per chunk

    @staticmethod
    def _plausible(cols: int, rows: int) -> bool:
        """A real pane is never 20x5: that is the fit floor, not a window."""
        return cols >= 40 and rows >= 10

    def _flush(self) -> None:
        """Push whatever accumulated in the last 16 ms as one JS call.

        One `runJavaScript` per PTY read made a busy TUI crawl: the round trip
        dominated the throughput. Coalescing to a frame is what a terminal is
        supposed to do.
        """
        if not self._buffer:
            return
        chunk = bytes(self._buffer)
        self._buffer.clear()
        self._push(chunk)

    # --- status: we own this PTY, so we can report it ------------------
    def _on_started(self, pid: int) -> None:
        self.session_pid = int(pid or 0)
        self.started.emit(self.session_pid)
        self._report("working", "running in the panel")

    def heartbeat(self) -> None:
        """Keep the record fresh while the child is alive and quiet."""
        if self.session_pid:
            self._report("working", "running in the panel")

    def _report(self, state: str, msg: str, exit_code=None) -> None:
        if not self.session_pid:
            return
        try:
            from .. import status as status_mod
            status_mod.write_status(self.session_pid, state, msg=msg,
                                    cwd=self.cwd or os.getcwd(), exit_code=exit_code,
                                    source="ostlr-pane")
        except Exception:
            pass

    # --- pty -> view -------------------------------------------------
    def _on_load(self, ok: bool) -> None:
        self._ready = bool(ok)
        if not ok:
            self.exited.emit("the terminal page failed to load")
            return
        for chunk in self._pending:
            self._push(chunk)
        self._pending.clear()

    def _on_data(self, chunk: bytes) -> None:
        # L1 lives here: the CLI writes OSC 7501 into the same stream we render,
        # so this is the only place that can see it. The bytes still go to xterm
        # (the sequence is zero-width and ignored there).
        try:
            from ..probe import l1 as l1_mod
            reports, probe = self._l1.feed(chunk)
            if probe and self.bridge is not None:
                self.bridge.write(l1_mod.PROBE)   # answer or the CLI stays silent
            for report in reports:
                self.l1_report.emit(self.session_pid, report)
        except Exception:
            pass
        if not self._ready:
            self._pending.append(chunk)
            return
        # Coalesce: the 16 ms flush timer pushes one JS call per frame.
        self._buffer.extend(chunk)

    def _push(self, chunk: bytes) -> None:
        self.view.page().runJavaScript("window.writeB64 && window.writeB64('%s')" % _b64(chunk))

    def _on_failed(self, reason: str) -> None:
        if self._ready:
            self.view.page().runJavaScript(
                "window.setStatus && window.setStatus('%s')" % reason.replace("'", " "))
        # The pane owned this session; report the ending so the list says `done`
        # instead of leaving a row that looks alive after its terminal is gone.
        if self.session_pid:
            self._report("done", reason, exit_code=self._exit_code())
        self.exited.emit(reason)

    def _exit_code(self):
        process = getattr(self.bridge, "_process", None)
        code = getattr(process, "exitstatus", None)
        return code if isinstance(code, int) else None

    # --- view -> pty -------------------------------------------------
    def on_console(self, message: str) -> None:
        if not message.startswith("OSTLR:"):
            return
        try:
            payload = json.loads(base64.b64decode(message[6:]).decode("utf-8"))
        except Exception:
            return
        if "k" in payload:
            if self.bridge is not None:
                self.bridge.write(payload["k"])
        elif "r" in payload:
            cols, rows = payload["r"]
            cols, rows = int(cols), int(rows)
            if not self._started:
                # First grid report: the child is born with the size the user sees.
                self._start_pty(cols, rows)
                return
            # The placeholder grid the page reports before the layout settles
            # (20x5) must never reach the child: resizing to it would shrink a
            # running TUI to nothing.
            if self._plausible(cols, rows) and self.bridge is not None:
                self.bridge.resize(cols, rows)

    def _refit(self) -> None:
        """Ask the page to recompute its grid after the layout settled."""
        try:
            if self._ready and getattr(self, "view", None) is not None:
                self.view.page().runJavaScript("window.fitNow && window.fitNow()")
                self.view.page().runJavaScript("window.report && window.report()")
        except Exception:
            pass

    def focus_terminal(self) -> None:
        self.view.setFocus()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.bridge is not None:
            self.bridge.stop()
            self.bridge.wait(1500)
        if self._flush_timer is not None:
            self._flush_timer.stop()
        super().closeEvent(event)


import json  # noqa: E402  (kept last so the module reads top-down)


def shell_for(cwd: Optional[str] = None) -> list:
    """The command that opens a plain shell, preferring PowerShell."""
    for name in ("pwsh.exe", "powershell.exe", "cmd.exe"):
        found = shutil.which(name)
        if found:
            return [found]
    return [os.environ.get("COMSPEC", "cmd.exe")]
