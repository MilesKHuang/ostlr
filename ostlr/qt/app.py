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

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtWidgets import (
    QApplication, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow,
    QMenu, QMessageBox, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
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

        left = QWidget(body)
        left.setMinimumWidth(300)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Sessions (owned by this panel)", left))
        self.list = QListWidget(left)
        self.list.setMinimumWidth(380)
        self.list.setItemDelegate(rowdelegate.RowDelegate(self.list))
        self.list.setSpacing(2)
        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._session_menu)
        left_layout.addWidget(self.list, 1)
        body.addWidget(left)

        self.tabs = QTabWidget(body)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        body.addWidget(self.tabs)
        self.placeholder = QLabel(
            "No terminal yet.\n\n"
            "Right-click a folder in Files -> \"Start a TUI here...\"\n"
            "or press \"+ New session\".\n\n"
            "Only sessions started here are listed: the panel owns their PTY, their\n"
            "size and their state (OSC 7501 when the CLI reports one).", self)
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.tabs.addTab(self.placeholder, "welcome")

        dock = QSplitter(Qt.Vertical, body)
        dock.setMinimumWidth(320)
        self.tree = tree_mod.FileTree(self._initial_root(), dock)
        self.tree.start_here.connect(self.start_session)
        self.tree.open_terminal.connect(self.open_terminal_here)
        self.tree.inject_path.connect(self.inject_path_into_pane)
        self.tree.copied.connect(lambda path: self._receipt_copy(path, from_tree=True))
        dock.addWidget(self.tree)
        body.addWidget(dock)

        body.setStretchFactor(0, 0)   # session list keeps its width
        body.setStretchFactor(1, 1)   # the terminal takes every extra pixel
        body.setStretchFactor(2, 0)   # the dock keeps its width
        self.body = body
        return central

    def _apply_split_sizes(self, width: Optional[int] = None) -> None:
        """Give the terminal the space that is actually there.

        Qt keeps a splitter's absolute sizes, so they have to be recomputed
        whenever the window changes size. Using `self.width()` inside
        `resizeEvent` reads the OLD width, which left the dock squeezed out of the
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
        left = max(300, int(width * 0.20))
        dock = max(260, int(width * 0.15))
        terminal = max(320, width - left - dock)
        self.body.setSizes([left, terminal, dock])

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
        self.panes.append(pane)
        return pane

    def _bind_pane(self, pane, pid: int) -> None:
        if not pid:
            return
        self.pane_by_pid[int(pid)] = pane
        self._set_receipt("%s -> pane (pid %d)" % (pane.title, pid))
        self.refresh_now()

    def _on_l1(self, pid: int, report) -> None:
        """A CLI told us its own state over OSC 7501: trust it above everything."""
        state = str((report or {}).get("state") or "")
        if not pid or not state or state == "clear":
            return
        try:
            from .. import status as status_mod
            pane = self.pane_by_pid.get(pid)
            status_mod.write_status(
                pid, state, kind=str(report.get("kind") or ""),
                msg=str(report.get("msg") or ""),
                cwd=getattr(pane, "cwd", "") or "", source="l1")
        except Exception:
            return
        self._set_receipt("L1 %s: %s" % (state, str(report.get("msg") or "")[:60]))

    def new_terminal(self, argv=None, cwd=None, title=None):
        pane = self._make_pane(argv, cwd, title)
        if pane is None:
            return None
        self._drop_placeholder()
        index = self.tabs.addTab(pane, pane.title)
        self.tabs.setCurrentIndex(index)
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
        index = self.tabs.indexOf(self.placeholder)
        if index >= 0:
            self.tabs.removeTab(index)
        self.placeholder.deleteLater()
        self.placeholder = None

    def open_terminal_here(self, directory: str):
        return self.new_terminal(
            cwd=directory, title="terminal @ %s" % os.path.basename(directory or "?"))

    def close_tab(self, index: int) -> None:
        widget = self.tabs.widget(index)
        if widget is None:
            return
        self.tabs.removeTab(index)
        victims = [widget] if not isinstance(widget, QSplitter) else [
            widget.widget(i) for i in range(widget.count())]
        for pane in victims:
            if not isinstance(pane, terminal.TerminalPane):
                continue
            if pane in self.panes:
                self.panes.remove(pane)
            for pid, bound in list(self.pane_by_pid.items()):
                if bound is pane:
                    del self.pane_by_pid[pid]
            pane.stop()
            pane.deleteLater()
        if not self.panes and getattr(self, "placeholder", None) is None:
            self.placeholder = QLabel("No terminal yet.", self)
            self.placeholder.setAlignment(Qt.AlignCenter)
            self.tabs.addTab(self.placeholder, "welcome")

    def _on_pane_exit(self, pane, reason: str) -> None:
        self._set_receipt("%s: %s" % (pane.title, reason))
        self.refresh_now()

    # --- the list: only what this panel owns ------------------------------
    def pane_rows(self) -> List[Dict[str, object]]:
        """One row per owned pane, with its live state and workload.

        Correction 1: an external process is not a session here. If the panel did
        not start it, the panel does not claim to manage it.

        The state comes from the pane's status record, and it is aged like any
        other: past `l2_stale_s` the record is not believable and the row says
        `unknown` instead of repeating a state nobody re-measured.
        """
        from .. import status as status_mod
        from ..age import record_age
        stale_after = int(getattr(self.cfg, "l2_stale_s", 90) or 90)
        rows: List[Dict[str, object]] = []
        for pane in self.panes:
            pid = int(getattr(pane, "session_pid", 0) or 0)
            if not pid:
                continue
            record = status_mod.read_status(pid) or {}
            age = record_age(record)
            state = str(record.get("state") or "unknown")
            source = str(record.get("source") or "none")
            if not record:
                state, source = "unknown", "none"
            elif age is not None and age > stale_after:
                state, source = "unknown", "none"
            stats = self.stats.get(pid) or {}
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
        rows = self.pane_rows()
        keep = self.selected_pid
        self.list.blockSignals(True)
        self.list.clear()
        for row in rows:
            self.list.addItem(self._row_item(row))
        self.list.blockSignals(False)
        if keep is not None:
            for index in range(self.list.count()):
                item_row = self.list.item(index).data(Qt.UserRole)
                if int(item_row.get("pid") or 0) == keep:
                    self.list.setCurrentRow(index)
                    break

        blocked = sum(1 for row in rows if row["state"] == "blocked")
        working = sum(1 for row in rows if row["state"] == "working")
        self._render_banner(rows)
        self.tray.update(blocked, working)

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
            self._set_receipt("")
            return
        self._set_receipt("%s  %s (%s)  dir %s  cpu %s  mem %s  gpu %s/%s"
                          % (row.get("agent"), row["state"], row.get("source"),
                             row.get("cwd") or "?",
                             procstat.format_cpu(row.get("cpu")),
                             procstat.format_mem(row.get("mem")),
                             procstat.format_gpu(row.get("gpu")),
                             view_model.format_mem(
                                 (row.get("gpu_mib") or 0) * 1024 * 1024)))

    # --- selection / receipts ---------------------------------------------
    def _selected_row(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _on_select(self) -> None:
        row = self._selected_row()
        self.selected_pid = int(row.get("pid") or 0) if row else None
        self._render_detail()

    def _set_receipt(self, text: str) -> None:
        self.receipt_label.setText(text)

    @property
    def receipt(self) -> str:
        return self.receipt_label.text()

    def _focused_pane(self):
        widget = self.tabs.currentWidget()
        if isinstance(widget, terminal.TerminalPane):
            return widget
        if isinstance(widget, QSplitter):
            for index in range(widget.count()):
                child = widget.widget(index)
                if isinstance(child, terminal.TerminalPane) and child.view.hasFocus():
                    return child
            for index in range(widget.count()):
                child = widget.widget(index)
                if isinstance(child, terminal.TerminalPane):
                    return child
        return self.panes[-1] if self.panes else None

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
        menu = QMenu(self)
        menu.setStyleSheet(style.stylesheet())
        menu.addAction("Open a terminal in its directory",
                       lambda: self.open_terminal_here(str(row.get("cwd") or "")))
        menu.addAction("Focus its window", self.focus_selected)
        menu.addAction("Copy its directory", self.copy_selected_path)
        menu.addSeparator()
        menu.addAction("End this session...", lambda: self.kill_session(row))
        menu.exec(self.list.viewport().mapToGlobal(position))

    def kill_session(self, row: Dict[str, object]) -> None:
        """Baseline 3.6: show what will die before anything runs."""
        from PySide6.QtWidgets import QMessageBox as _Box
        pid = int(row.get("pid") or 0)
        answer = QMessageBox.question(
            self, "End session",
            "End %s (pid %d)?\n\nIts whole process tree is terminated; it currently "
            "uses %s of memory." % (row.get("agent") or "?", pid,
                                    view_model.format_mem(row.get("rss"))))
        if answer != _Box.StandardButton.Yes:
            return
        pane = self.pane_by_pid.get(pid)
        if pane is not None:
            pane.stop()
        result = actions_mod.kill({"pid": pid, "started_at": 0, "cwd": row.get("cwd")})
        self._set_receipt(str(result.get("receipt") or result.get("reason")))
        self.refresh_now()

    # --- layout / tray -----------------------------------------------------
    def _layout_payload(self) -> List[Dict[str, object]]:
        tabs: List[Dict[str, object]] = []
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            if not isinstance(widget, terminal.TerminalPane):
                continue
            tabs.append(layout_mod.tab_entry(
                self.tabs.tabText(index),
                [{"argv": widget.argv, "cwd": widget.cwd or ""}]))
        return tabs

    def _restore_layout(self) -> bool:
        if not getattr(self.cfg, "restore_layout", True):
            return False
        data = layout_mod.load()
        restored = 0
        for entry in data.get("tabs") or []:
            first = (entry.get("panes") or [{}])[0]
            if not first.get("argv"):
                continue
            if self.new_terminal(first.get("argv"), first.get("cwd") or None,
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
            layout_mod.save(payload, self.tabs.currentIndex())
            layout_mod.save_window(self.saveGeometry().toHex().decode("ascii"),
                                   self.isMaximized())
        except Exception:
            pass
        self.tray.stop()
        for pane in list(self.panes):
            pane.stop()
        super().closeEvent(event)
