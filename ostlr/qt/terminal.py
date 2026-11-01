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

from PySide6.QtCore import QThread, QUrl, Signal
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
        try:
            from winpty import PtyProcess
        except Exception as exc:  # pragma: no cover - packaging failure
            self.failed.emit("pywinpty is not available: %s" % exc)
            return

        command = " ".join(self._argv)
        previous = None
        try:
            if self._cwd:
                previous = os.getcwd()
                os.chdir(self._cwd)
            self._process = PtyProcess.spawn(command, dimensions=(self._rows, self._cols))
        except Exception as exc:
            self.failed.emit(str(exc))
            return
        finally:
            if previous:
                try:
                    os.chdir(previous)
                except OSError:
                    pass

        # The pane owns this pid from now on: it is the session the list shows,
        # and the status record the ladder reads. This is the whole reason for
        # owning a PTY instead of watching someone else's console.
        self.started.emit(int(getattr(self._process, "pid", 0) or 0))

        last_report = 0.0
        while not self._stop:
            try:
                chunk = self._process.read(8192)
            except EOFError:
                break
            except Exception as exc:  # a dying child is a message, not a crash
                self.failed.emit(str(exc))
                break
            if chunk:
                self.data.emit(chunk if isinstance(chunk, bytes) else chunk.encode("utf-8"))
                now = time.time()
                if now - last_report > 5.0:
                    last_report = now
                    self.heartbeat.emit()
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
        if cols <= 0 or rows <= 0:
            return
        self._cols, self._rows = cols, rows
        if self._process is None:
            return
        try:
            self._process.setwinsize(rows, cols)
        except Exception:
            pass

    def stop(self) -> None:
        self._stop = True
        process, self._process = self._process, None
        if process is not None:
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

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.view = QWebEngineView(self)
        self.view.setPage(_Page(self, self.view))
        layout.addWidget(self.view)
        self.view.loadFinished.connect(self._on_load)
        self.view.load(QUrl.fromLocalFile(_bundled("term.html")))

        self.bridge = PtyBridge(self.argv, cwd=self.cwd)
        self.bridge.data.connect(self._on_data)
        self.bridge.failed.connect(self._on_failed)
        self.bridge.started.connect(self._on_started)
        self.bridge.heartbeat.connect(self.heartbeat)
        self.bridge.start()

    # --- status: we own this PTY, so we can report it ------------------
    def _on_started(self, pid: int) -> None:
        self.session_pid = int(pid or 0)
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
        if not self._ready:
            self._pending.append(chunk)
            return
        self._push(chunk)

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
            self.bridge.write(payload["k"])
        elif "r" in payload:
            cols, rows = payload["r"]
            self.bridge.resize(int(cols), int(rows))

    def focus_terminal(self) -> None:
        self.view.setFocus()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.bridge.stop()
        self.bridge.wait(1500)
        super().closeEvent(event)


import json  # noqa: E402  (kept last so the module reads top-down)


def shell_for(cwd: Optional[str] = None) -> list:
    """The command that opens a plain shell, preferring PowerShell."""
    for name in ("pwsh.exe", "powershell.exe", "cmd.exe"):
        found = shutil.which(name)
        if found:
            return [found]
    return [os.environ.get("COMSPEC", "cmd.exe")]
