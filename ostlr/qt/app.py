"""The Ostlr window: the baseline's regions, after the first hands-on review.

    +--------------------------------------------------------------+
    | + New session  Refresh  Focus all  [receipt]  [gauges]        |  top bar
    +----------------+-----------------------------+---------------+
    | Sessions       |  terminal panes (tabs)      |  Files (dock) |  right dock
    |  pane-owned    |                             |  Details      |
    |  cpu/mem bars  |                             |               |
    +----------------+-----------------------------+---------------+

Corrections this version applies:

  * the list shows ONLY sessions this panel owns - a lifecycle manager for its
    own PTYs, not a process browser;
  * the file tree is docked on the right, where 3.1 draws it;
  * the receipt moved into the top bar: no row of the window is spent on it;
  * per-session CPU% / MEM% (GPU MiB when nvidia-smi exists) as bars, instead of
    raw megabytes nobody can compare;
  * the window opens inside the screen: half off-screen also made the terminal
    compute its grid for a width the user could not see.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from PySide6.QtCore import QEvent, QSize, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSplitter, QStackedWidget, QVBoxLayout,
    QWidget,
)

from .. import actions as actions_mod
from .. import config as config_mod
from .. import view_model
from . import (
    discovery, gauges as gauge_mod, launcher as launcher_mod, layout as layout_mod,
    procstat, rowdelegate, style, terminal, tray as tray_mod, tree as tree_mod,
)

GLYPH = {"blocked": "\u25cf", "working": "\u25d0", "idle": "\u25cb",
         "done": "\u2713", "error": "\u2715", "unknown": "\u25cc"}


class MainWindow(QMainWindow):
    def __init__(self, cfg=None, provider=None) -> None:
        super().__init__()
        self.cfg = cfg or config_mod.Config()
        self._provider = provider
        self.panes: List[terminal.TerminalPane] = []
        self.pane_by_pid: Dict[int, terminal.TerminalPane] = {}
        self.stats: Dict[int, Dict[str, Optional[float]]] = {}
        self.selected_pid: Optional[int] = None
        self._receipt_kind = ""

        self.tray = tray_mod.Tray(self)
        self.tray.show_requested.connect(self._show_from_tray)
        self.tray.refresh_requested.connect(self.refresh_now)
        self.tray.quit_requested.connect(self.close)

        self.setWindowTitle("Ostlr")
        self.setStyleSheet(style.stylesheet())
        self.setCentralWidget(self._build())
        self._place_window()
        self._apply_split_sizes()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_now)
        self._timer.start(max(1, int(self.cfg.tick_s)) * 1000)
        QTimer.singleShot(150, self.refresh_now)

        # Ctrl+W closes the session you are looking at. The tab's close button had
        # no successor, and a pane with no way out is a trap.
        close_shortcut = QShortcut(QKeySequence("Ctrl+W"), self)
        close_shortcut.activated.connect(self.close_current)

        if not self.tray.start():
            self._set_receipt("no tray (%s)" % self.tray.reason)
        # GPU sampling is a background job: the counters need two collections and
        # a PowerShell round trip, neither of which belongs on the UI thread.
        procstat.start_gpu_worker()
        if not self._restore_layout():
            self._set_receipt("ready - right-click a folder in Files to start a session")

    # --- window placement ------------------------------------------------
    def _place_window(self) -> None:
        saved = layout_mod.load().get("window") or {}
        if saved.get("geometry"):
            try:
                self.restoreGeometry(bytes.fromhex(str(saved["geometry"])))
                if saved.get("maximized"):
                    self.showMaximized()
                return
            except Exception:
                pass
        screen = QApplication.primaryScreen()
        available = screen.availableGeometry() if screen is not None else None
        if available is None:
            self.resize(1400, 860)
            return
        # Open big but inside the work area: a window half off the screen also
        # made the terminal compute a grid for a width the user could not see.
        width = min(int(available.width() * 0.92), 2000)
        height = min(int(available.height() * 0.92), 1200)
        self.resize(width, height)
        self.move(available.left() + (available.width() - width) // 2,
                  available.top() + (available.height() - height) // 2)

    # --- layout ----------------------------------------------------------
    def _build(self) -> QWidget:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 8)
        outer.setSpacing(6)

        bar = QHBoxLayout()
        self.actions: Dict[str, QPushButton] = {}
        for key, label, slot in (
            ("new", "+  New session", lambda: self.start_session(None)),
            ("refresh", "Refresh", self.refresh_now),
            ("focus_all", "Focus all", self.focus_all),
        ):
            button = QPushButton(label, self)
            button.clicked.connect(slot)
            bar.addWidget(button)
            self.actions[key] = button
        self.receipt_label = QLabel("", self)
        self.receipt_label.setStyleSheet("color: %s;" % style.ROLES["fg_dim"])
        bar.addSpacing(12)
        bar.addWidget(self.receipt_label, 1)
        outer.addLayout(bar)

        self.banner_label = QLabel("", self)
        self.banner_label.setWordWrap(True)
        outer.addWidget(self.banner_label)

        body = QSplitter(Qt.Horizontal, self)
        outer.addWidget(body, 1)

        # The left column is the switchboard: sessions on top, the file tree under
        # them. The terminal then owns the whole right side - the file tree used to
        # take a third of the window and repeated what the list already said.
        left = QSplitter(Qt.Vertical, body)
        left.setMinimumWidth(340)
        sessions = QWidget(left)
        left_layout = QVBoxLayout(sessions)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Sessions (owned by this panel)", sessions))
        self.list = QListWidget(sessions)
        self.list.setMinimumWidth(320)
        self.list.setItemDelegate(rowdelegate.RowDelegate(self.list))
        self.list.setSpacing(2)
        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._session_menu)
        # Middle click closes a session: it is what a tab's middle click did, and
        # the list is the only place a session is named now.
        self.list.viewport().installEventFilter(self)
        left_layout.addWidget(self.list, 1)
        left.addWidget(sessions)

        self.tree = tree_mod.FileTree(self._initial_root(), left)
        self.tree.start_here.connect(self.start_session)
        self.tree.open_terminal.connect(self.open_terminal_here)
        self.tree.inject_path.connect(self.inject_path_into_pane)
        self.tree.copied.connect(lambda path: self._receipt_copy(path, from_tree=True))
        self.tree.opened.connect(self._receipt_open)
        left.addWidget(self.tree)
        left.setStretchFactor(0, 3)
        left.setStretchFactor(1, 2)
        body.addWidget(left)

        # One page per session, no tab bar: the list above is the switch.
        self.stack = QStackedWidget(body)
        self.placeholder = QLabel(
            "No terminal yet.\n\n"
            "Right-click a folder in Files -> \"Start a TUI here...\"\n"
            "or press \"+ New session\".\n\n"
            "Only sessions started here are listed: the panel owns their PTY, their\n"
            "size and their state (OSC 7501 when the CLI reports one).", self)
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.stack.addWidget(self.placeholder)
        body.addWidget(self.stack)

        body.setStretchFactor(0, 0)   # the switchboard keeps its width
        body.setStretchFactor(1, 1)   # the terminal takes every extra pixel
        self.body = body
        self.left_column = left
        self._items = {}
        return central

    def _apply_split_sizes(self, width: Optional[int] = None) -> None:
        """Give the terminal the space that is actually there.

        Qt keeps a splitter's absolute sizes, so they have to be recomputed
        whenever the window changes size. Using `self.width()` inside
        `resizeEvent` reads the OLD width, which left a column squeezed out of the
        window and the terminal on a grid the user could not see.
        """
        width = int(width or self.width() or 1200)
        # Idempotent: a second call with the same width must not touch the splitter.
        # Otherwise the window's first resize arrives right after a pane spawned,
        # moves the splitter, changes the grid, and the child's already-emitted
        # history is reflowed to the new width - the "layout explodes on session
        # load" symptom. The child must be born into a grid that is already final.
        if getattr(self, "_split_width", None) == width and getattr(self, "body", None):
            return
        self._split_width = width
        left = max(340, int(width * 0.26))
        terminal = max(320, width - left)
        self.body.setSizes([left, terminal])

    def resizeEvent(self, event) -> None:  # noqa: N802
        size = event.size()
        super().resizeEvent(event)
        # Defer: the splitter's own geometry is updated after this returns.
        QTimer.singleShot(0, lambda w=size.width(): self._apply_split_sizes(w))
        pane = self._focused_pane()
        if pane is not None:
            QTimer.singleShot(150, lambda p=pane: self._refit(p))

    def _initial_root(self) -> str:
        for candidate in (getattr(self.cfg, "last_dirs", None) or []):
            if candidate and os.path.isdir(candidate):
                return candidate
        return os.getcwd()

    # --- panes -----------------------------------------------------------
    def _make_pane(self, argv=None, cwd=None, title=None):
        if len(self.panes) >= terminal.MAX_PANES:
            self._set_receipt("pane limit reached (%d)" % terminal.MAX_PANES)
            return None
        pane = terminal.TerminalPane(argv or terminal.shell_for(cwd), cwd=cwd,
                                     title=title or "shell", parent=self, cfg=self.cfg)
        pane.exited.connect(lambda reason, p=pane: self._on_pane_exit(p, reason))
        pane.started.connect(lambda pid, p=pane: self._bind_pane(p, pid))
        pane.l1_report.connect(self._on_l1)
        pane.renderer_changed.connect(self._on_renderer)
        self.panes.append(pane)
        return pane

    def _on_renderer(self, status: str) -> None:
        """`webgl` is the expected path and needs no announcement.

        A fallback is a promise kept: the frame is then drawn by the font, which
        leaves a gap at every row boundary and stripes in a gauge. Say so instead
        of letting the user wonder why the borders look broken.
        """
        if not status.startswith("webgl"):
            self._set_receipt("terminal renderer: %s (the frame is drawn by the "
                              "font, so borders may show seams)" % status)

    def _bind_pane(self, pane, pid: int) -> None:
        if not pid:
            return
        self.pane_by_pid[int(pid)] = pane
        self._set_receipt("%s -> pane (pid %d)" % (pane.title, pid))
        self.refresh_now()
        # A new session becomes the current one: its pane is already visible, and
        # the file tree follows the directory it was started in.
        self._select_pid(int(pid))

    def _on_l1(self, pid: int, report) -> None:
        """A CLI told us its own state over OSC 7501: show it, do not re-write it.

        The pane is the terminal side of the protocol and owns the records
        (`terminal._apply_l1`); the window only reports what arrived.
        """
        state = str((report or {}).get("state") or "")
        if not pid or not state:
            return
        self._set_receipt("L1 %s: %s" % (state, str(report.get("msg") or "")[:60]))

    def new_terminal(self, argv=None, cwd=None, title=None):
        pane = self._make_pane(argv, cwd, title)
        if pane is None:
            return None
        self._drop_placeholder()
        self.stack.addWidget(pane)
        self.stack.setCurrentWidget(pane)
        pane.focus_terminal()
        QTimer.singleShot(250, lambda p=pane: self._refit(p))
        return pane

    def _refit(self, pane) -> None:
        """The pane recomputes its grid; call it after any layout change."""
        try:
            pane._refit()
        except Exception:
            pass

    def _drop_placeholder(self) -> None:
        if getattr(self, "placeholder", None) is None:
            return
        self.stack.removeWidget(self.placeholder)
        self.placeholder.deleteLater()
        self.placeholder = None

    def open_terminal_here(self, directory: str):
        return self.new_terminal(
            cwd=directory, title="terminal @ %s" % os.path.basename(directory or "?"))

    def close_session(self, pane=None) -> None:
        """Close one session: stop its PTY, drop its page, forget its row.

        A tab's close button and middle click both landed here; the list row is
        the only place a session is named now, so the row is where it closes.
        """
        pane = pane if isinstance(pane, terminal.TerminalPane) else self._focused_pane()
        if pane is None:
            self._set_receipt("no session to close")
            return
        title = pane.title
        if pane in self.panes:
            self.panes.remove(pane)
        for pid, bound in list(self.pane_by_pid.items()):
            if bound is pane:
                del self.pane_by_pid[pid]
        self.stack.removeWidget(pane)
        pane.stop()
        pane.deleteLater()
        self.selected_pid = None
        if not self.panes and getattr(self, "placeholder", None) is None:
            self.placeholder = QLabel("No terminal yet.", self)
            self.placeholder.setAlignment(Qt.AlignCenter)
            self.stack.addWidget(self.placeholder)
            self.stack.setCurrentWidget(self.placeholder)
        self.refresh_now()
        self._set_receipt("closed %s" % title)

    def eventFilter(self, watched, event):  # noqa: N802
        """Middle click on a row closes that session - the old tab gesture."""
        if watched is self.list.viewport():
            if event.type() == QEvent.MouseButtonRelease and \
                    event.button() == Qt.MiddleButton:
                item = self.list.itemAt(event.position().toPoint())
                if item is not None:
                    row = item.data(Qt.UserRole) or {}
                    self.close_session(self.pane_by_pid.get(int(row.get("pid") or 0)))
                    return True
        return super().eventFilter(watched, event)

    def close_current(self) -> None:
        self.close_session(self._focused_pane())

    def _on_pane_exit(self, pane, reason: str) -> None:
        self._set_receipt("%s: %s" % (pane.title, reason))
        self.refresh_now()

    # --- the list: only what this panel owns ------------------------------
    def pane_rows(self) -> List[Dict[str, object]]:
        """One row per owned pane, with its live state and workload.

        Correction 1: an external process is not a session here. If the panel did
        not start it, the panel does not claim to manage it.

        The state is what the program reported, and it is HELD: the producer is the
        process in that pane, we can see it is alive, so its last word stands until
        it says something else or the process ends. A state machine reports on
        change, not on a heartbeat - an agent waiting for an approval has to stay
        `blocked` for as long as the human takes. Silence only withdraws the pane's
        own traffic guess (`source=ostlr-pane`), because a guess must not outlive
        its evidence.
        """
        from .. import status as status_mod
        from ..age import record_age
        rows: List[Dict[str, object]] = []
        for pane in self.panes:
            pid = int(getattr(pane, "session_pid", 0) or 0)
            if not pid:
                continue
            record = status_mod.read_status(pid) or {}
            age = record_age(record)
            state = str(record.get("state") or "unknown")
            source = str(record.get("source") or "none")
            quiet = pane.quiet_for() if hasattr(pane, "quiet_for") else 0.0
            if not record:
                # Nothing to report: the pane withdrew its own guess, or never made
                # one. That is `unknown`, never `idle`.
                state, source = "unknown", "none"
            stats = self.stats.get(pid) or {}
            msg = str(record.get("msg") or "")
            if not record:
                msg = "quiet for %ds, nothing reported" % int(quiet)
            rows.append({
                "pid": pid,
                "state": state,
                "source": source,
                "agent": pane.title,
                "project": os.path.basename(pane.cwd or "") or "?",
                "cwd": pane.cwd or "",
                "msg": str(record.get("msg") or ""),
                "kind": str(record.get("kind") or ""),
                "age_s": age,
                "cpu": stats.get("cpu"),
                "mem": stats.get("mem"),
                "gpu": stats.get("gpu"),
                "gpu_mib": stats.get("gpu_mib"),
                "rss": stats.get("rss"),
            })
        return rows

    def refresh_now(self):
        pids = [int(getattr(p, "session_pid", 0) or 0) for p in self.panes]
        pids = [pid for pid in pids if pid]
        if pids and not getattr(self, "_primed", False):
            # cpu_percent has no previous sample on its first call and reports 0:
            # prime once so the first frame is not a lie.
            procstat.prime(pids)
            self._primed = True
        try:
            self.stats = procstat.sample(pids)
        except Exception:
            self.stats = {}
        self._render()
        return {"rows": self.pane_rows()}

    def _render(self) -> None:
        """Refresh the rows IN PLACE.

        The numbers change every tick, so a repaint is needed - but clearing the
        list would destroy the selection, and re-selecting by pid would fire a
        selection change, which now also switches the pane and re-roots the file
        tree. Updating each item's data keeps the row object (and therefore the
        selection) identical from tick to tick.
        """
        rows = self.pane_rows()
        seen = set()
        self.list.blockSignals(True)
        try:
            for row in rows:
                pid = int(row.get("pid") or 0)
                seen.add(pid)
                item = self._items.get(pid)
                if item is None:
                    item = self._row_item(row)
                    self._items[pid] = item
                    self.list.addItem(item)
                else:
                    item.setData(Qt.UserRole, row)
                    item.setText("%s  %s  %s" % (row.get("agent"), row.get("project"),
                                                 row.get("state")))
            for pid in [pid for pid in self._items if pid not in seen]:
                item = self._items.pop(pid)
                self.list.takeItem(self.list.row(item))
        finally:
            self.list.blockSignals(False)

        blocked = sum(1 for row in rows if row["state"] == "blocked")
        working = sum(1 for row in rows if row["state"] == "working")
        self._render_banner(rows)
        self.tray.update(blocked, working)
        # Keep the detail line in step with the row it describes, but never
        # overwrite a message the user just caused.
        if getattr(self, "_receipt_kind", "") in ("", "detail"):
            self._render_detail()

    def _row_item(self, row: Dict[str, object]) -> QListWidgetItem:
        # The delegate paints everything; the text stays for tooltips and for
        # anyone reading the widget tree in a test.
        item = QListWidgetItem("%s  %s  %s" % (row.get("agent"), row.get("project"),
                                               row.get("state")))
        item.setData(Qt.UserRole, row)
        item.setSizeHint(QSize(0, rowdelegate.RowDelegate.ROW_HEIGHT))
        return item

    def _render_banner(self, rows) -> None:
        if not rows:
            self.banner_label.setText("")
            return
        unknown = sum(1 for row in rows if row["state"] == "unknown")
        self.banner_label.setText(
            view_model.unknown_banner(len(rows), unknown, len(rows) - unknown))

    def _render_detail(self) -> None:
        """The selected pane, spelled out. One line in the receipt area.

        The separate detail pane was one of the two "useless panels": it repeated
        what the row already showed and ate a third of the window.
        """
        row = self._selected_row()
        if not row:
            self._set_receipt("", kind="detail")
            return
        self._set_receipt("%s  %s (%s)  dir %s  cpu %s  mem %s  gpu %s/%s"
                          % (row.get("agent"), row["state"], row.get("source"),
                             row.get("cwd") or "?",
                             procstat.format_cpu(row.get("cpu")),
                             procstat.format_mem(row.get("mem")),
                             procstat.format_gpu(row.get("gpu")),
                             view_model.format_mem(
                                 (row.get("gpu_mib") or 0) * 1024 * 1024)),
                          kind="detail")

    # --- selection / receipts ---------------------------------------------
    def _selected_row(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _on_select(self) -> None:
        """Selecting a row is the switch: its pane becomes visible, the file tree
        follows its directory, and the keyboard goes to that terminal."""
        row = self._selected_row()
        self.selected_pid = int(row.get("pid") or 0) if row else None
        pane = self.pane_by_pid.get(self.selected_pid or 0)
        if pane is not None:
            if self.stack.currentWidget() is not pane:
                self.stack.setCurrentWidget(pane)
                QTimer.singleShot(120, lambda p=pane: self._refit(p))
            if pane.cwd:
                self.tree.set_root(pane.cwd)
            pane.focus_terminal()
        self._render_detail()

    def _select_pid(self, pid: int) -> None:
        """Make the row for `pid` current without re-entering the signal."""
        for index in range(self.list.count()):
            item = self.list.item(index)
            row = item.data(Qt.UserRole) or {}
            if int(row.get("pid") or 0) == int(pid or 0):
                self.list.blockSignals(True)
                self.list.setCurrentItem(item)
                self.list.blockSignals(False)
                self._on_select()
                return

    def _set_receipt(self, text: str, kind: str = "message") -> None:
        """One label carries two things: the selected row, and what just happened.

        `kind` is how a transient message ("closed projV", an L1 report) survives
        the next tick: the refresh only rewrites the line when it is the detail
        line, which is derived from the row and would otherwise go stale - the row
        said `working` while the line still said `unknown`.
        """
        self._receipt_kind = kind
        self.receipt_label.setText(text)

    @property
    def receipt(self) -> str:
        return self.receipt_label.text()

    def _focused_pane(self):
        """The pane the user is looking at: the visible page, else the last one."""
        widget = self.stack.currentWidget()
        if isinstance(widget, terminal.TerminalPane):
            return widget
        return self.panes[-1] if self.panes else None

    def _receipt_open(self, path: str) -> None:
        self._set_receipt("opened %s" % path)

    def _receipt_copy(self, path: str, from_tree: bool = False) -> None:
        pane = self._focused_pane()
        where = "Pane\u300c%s\u300d" % pane.title if pane else "clipboard only (no pane)"
        same = bool(pane and getattr(pane, "cwd", None)
                    and os.path.normcase(os.path.dirname(path)) == os.path.normcase(pane.cwd))
        mark = "same project" if same else ("cross project" if pane else "no target")
        self._set_receipt("copied %s -> %s  (%s)" % (path, where, mark))

    def inject_path_into_pane(self, path: str, at_prefix: bool = False) -> None:
        """Baseline 3.4: send the path to the focused pane's PTY, without Enter."""
        pane = self._focused_pane()
        if pane is None:
            from PySide6.QtGui import QGuiApplication
            QGuiApplication.clipboard().setText(path)
            self._set_receipt("no pane to send to - the path is in the clipboard")
            return
        text = ("@" + path) if at_prefix else path
        pane.write(text)
        same = bool(getattr(pane, "cwd", None)) and os.path.normcase(
            os.path.dirname(path)) == os.path.normcase(pane.cwd)
        self._set_receipt("sent %s -> Pane\u300c%s\u300d  (%s)" % (
            text, pane.title, "same project" if same else "cross project"))

    def copy_selected_path(self) -> None:
        row = self._selected_row()
        path = str((row or {}).get("cwd") or "")
        if not path or path == "?":
            self._set_receipt("this row has no directory; use the file tree")
            return
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(path)
        self._receipt_copy(path)

    def focus_selected(self) -> None:
        row = self._selected_row()
        if not row:
            self._set_receipt("no row selected")
            return
        result = actions_mod.focus(int(row.get("pid") or 0))
        self._set_receipt(str(result.get("receipt") or result.get("reason") or "focus"))

    def focus_all(self) -> None:
        focused = 0
        for pane in self.panes:
            if actions_mod.focus(getattr(pane, "session_pid", 0) or 0).get("ok"):
                focused += 1
        self._set_receipt("focused %d pane window(s)" % focused)

    # --- sessions: start / kill -------------------------------------------
    def start_session(self, directory=None) -> None:
        """Open the selector. `chosen` is the whole contract: Cancel leaves it None.

        (PySide6 exposes `Accepted` on QDialog the class, not on an instance -
        reading it from the dialog raised AttributeError on the first click.)
        """
        dialog = launcher_mod.Launcher(self.cfg, directory, self)
        dialog.exec()
        if not dialog.chosen:
            return
        self.start_from(dict(dialog.chosen), dialog.directory, dialog.location)

    def start_from(self, entry: Dict[str, object], directory: str,
                   location: str = "panel"):
        command = discovery.resolve(str(entry.get("command")))
        if not command:
            self._set_receipt("not found: %s" % entry.get("command"))
            return None
        args = [str(a) for a in (entry.get("args") or [])]
        info = discovery.home_of(command)
        title = "%s @ %s" % (entry.get("id") or os.path.basename(command),
                             os.path.basename(directory or "") or "?")
        if location == "window":
            result = actions_mod.launch({"command": command, "args": args}, directory)
            self._set_receipt("started in its own window: %s"
                              % (result.get("reason") or result.get("detail") or "?"))
            return None
        pane = self.new_terminal([command] + args, cwd=directory, title=title)
        if pane is None:
            return None
        note = "" if (info.get("has_key") or not info.get("files_dir")) else "  (no api key)"
        self._set_receipt("started %s in %s%s" % (title, directory, note))
        return pane

    def _session_menu(self, position) -> None:
        row = self._selected_row()
        if not row:
            return
        pid = int(row.get("pid") or 0)
        pane = self.pane_by_pid.get(pid)
        menu = QMenu(self)
        menu.setStyleSheet(style.stylesheet())
        menu.addAction("Open a terminal in its directory",
                       lambda: self.open_terminal_here(str(row.get("cwd") or "")))
        menu.addAction("Focus its window", self.focus_selected)
        menu.addAction("Copy its directory", self.copy_selected_path)
        menu.addSeparator()
        # Two different endings, named for what they do: closing the pane stops the
        # terminal (the child dies with its PTY), ending the tree is the taskkill
        # with a confirmation. The tab's close button used to be the first one.
        close = menu.addAction("Close session", lambda: self.close_session(pane))
        close.setEnabled(pane is not None)
        menu.addAction("End this session...", lambda: self.kill_session(row))
        menu.exec(self.list.viewport().mapToGlobal(position))

    def kill_session(self, row: Dict[str, object]) -> None:
        """Baseline 3.6: show what will die before anything runs.

        The identity is read NOW and handed to `actions.kill` together with a
        probe, because the guard refuses a kill it cannot verify - and passing
        `started_at: 0` (what this did) meant every "End session" was refused
        *after* the pane had already been stopped: the row disappeared and the
        process kept running.
        """
        from PySide6.QtWidgets import QMessageBox as _Box
        pid = int(row.get("pid") or 0)
        if pid <= 0:
            self._set_receipt("no pid on this row")
            return
        started_at = procstat.start_epoch(pid)
        if started_at <= 0:
            self._set_receipt("cannot read this process's creation time - "
                              "refusing to kill a pid that may have been reused")
            return
        answer = QMessageBox.question(
            self, "End session",
            "End %s (pid %d)?\n\nIts whole process tree is terminated; it currently "
            "uses %s of memory." % (row.get("agent") or "?", pid,
                                    view_model.format_mem(row.get("rss"))))
        if answer != _Box.StandardButton.Yes:
            return
        pane = self.pane_by_pid.get(pid)
        result = actions_mod.kill({"pid": pid, "started_at": started_at,
                                   "cwd": row.get("cwd")},
                                  identity_probe=procstat.start_epoch)
        if result.get("ok") and pane is not None:
            # The pane stops AFTER the kill was accepted: stopping it first is what
            # made a refused kill look like a successful one.
            pane.stop()
        self._set_receipt(str(result.get("receipt") or result.get("reason")))
        self.refresh_now()

    # --- layout / tray -----------------------------------------------------
    def _layout_payload(self) -> List[Dict[str, object]]:
        """One entry per session, in list order.

        The storage shape is the one `layout.tab_entry` already writes (a title and
        its panes), so an old file still restores: with no tab bar, a tab is simply
        a session, and each entry carries exactly one pane.
        """
        entries: List[Dict[str, object]] = []
        for pane in self.panes:
            entries.append(layout_mod.tab_entry(
                pane.title, [{"argv": pane.argv, "cwd": pane.cwd or ""}]))
        return entries

    def _active_index(self) -> int:
        pane = self._focused_pane()
        return self.panes.index(pane) if pane in self.panes else 0

    def _restore_layout(self) -> bool:
        if not getattr(self.cfg, "restore_layout", True):
            return False
        data = layout_mod.load()
        restored = 0
        for entry in data.get("tabs") or []:
            panes = [p for p in (entry.get("panes") or [])
                     if isinstance(p, dict) and p.get("argv")]
            if not panes:
                continue
            # A layout written while the split UI existed keeps its first pane and
            # drops the rest: restoring a shape this build cannot draw would be a
            # lie about what the panel does.
            if self.new_terminal(panes[0].get("argv"), panes[0].get("cwd") or None,
                                 title=str(entry.get("title") or "shell")) is None:
                break
            restored += 1
        if restored:
            self._set_receipt("layout restored (%d tab(s))" % restored)
        return restored > 0

    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        try:
            payload = self._layout_payload()
            layout_mod.save(payload, self._active_index())
            layout_mod.save_window(self.saveGeometry().toHex().decode("ascii"),
                                   self.isMaximized())
        except Exception:
            pass
        self.tray.stop()
        for pane in list(self.panes):
            pane.stop()
        super().closeEvent(event)
