"""The Qt shell: session list on the left, terminal tabs on the right.

This replaces the tkinter panel. The reasons it exists, in one place:

  * the left list answers "who is running and who is waiting for me" - the same
    sessions/ladder/view_model logic the old panel used (never tk-specific);
  * the right side is a real terminal: we own the PTY and xterm.js renders it, so
    a session started here can be typed into without leaving the window;
  * the launcher discovers what is installed instead of demanding a config edit.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMainWindow, QMessageBox, QPushButton, QSplitter,
    QTabWidget, QVBoxLayout, QWidget,
)

from .. import actions as actions_mod
from .. import config as config_mod
from .. import view_model
from . import discovery, style, terminal

LIST_LIMIT = 200


class LauncherDialog(QWidget):
    """Pick a program, pick a directory, see where that program keeps its data."""

    def __init__(self, cfg, parent=None) -> None:
        super().__init__(parent, Qt.Dialog)
        self.setWindowTitle("Start a session")
        self.resize(720, 340)
        self.cfg = cfg
        self.chosen: Optional[Dict[str, object]] = None
        self.directory = os.getcwd()

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Program"))
        self.combo = QComboBox(self)
        self.combo.setMinimumWidth(400)
        self.combo.currentIndexChanged.connect(lambda _i: self._update_home())
        row.addWidget(self.combo, 1)
        browse = QPushButton("Browse...", self)
        browse.clicked.connect(self._browse)
        row.addWidget(browse)
        layout.addLayout(row)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Directory"))
        self.dir_edit = QLineEdit(self.directory, self)
        dir_row.addWidget(self.dir_edit, 1)
        pick = QPushButton("Pick...", self)
        pick.clicked.connect(self._pick_dir)
        dir_row.addWidget(pick)
        layout.addLayout(dir_row)

        self.home_label = QLabel("", self)
        self.home_label.setWordWrap(True)
        layout.addWidget(self.home_label, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject_close)
        buttons.addWidget(cancel)
        start = QPushButton("Start", self)
        start.setDefault(True)
        start.clicked.connect(self._accept)
        buttons.addWidget(start)
        layout.addLayout(buttons)

        self.reload_entries()

    def reload_entries(self) -> None:
        self.combo.clear()
        for entry in discovery.launcher_entries(self.cfg):
            label = str(entry.get("label") or entry.get("id"))
            self.combo.addItem("%s   (%s)" % (label, entry.get("source")), entry)
        if self.combo.count() == 0:
            self.combo.addItem("Nothing found on PATH - use Browse...", None)
        self._update_home()

    def _update_home(self) -> None:
        entry = self.combo.currentData()
        if not entry:
            self.home_label.setText("")
            return
        info = discovery.home_of(str(entry.get("command")))
        lines = ["exe:  %s" % info["exe"], "home: %s" % info["dir"]]
        if info["files_dir"]:
            lines.append("projv_files: %s   (api key: %s)"
                         % (info["files_dir"], "yes" if info["has_key"] else "NO"))
        self.home_label.setText("\n".join(lines))

    def _browse(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a program", self.directory,
                                              "Programs (*.exe);;All files (*)")
        if not path:
            return
        discovery.remember(self.cfg, path)
        try:
            config_mod.save(self.cfg)
        except Exception:
            pass
        self.reload_entries()
        for index in range(self.combo.count()):
            entry = self.combo.itemData(index)
            if entry and os.path.normcase(str(entry.get("command"))) == os.path.normcase(path):
                self.combo.setCurrentIndex(index)
                break
        self._update_home()

    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Start in directory", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)

    def _accept(self) -> None:
        entry = self.combo.currentData()
        if not entry:
            QMessageBox.information(self, "Ostlr", "Choose a program first.")
            return
        self.chosen = dict(entry)
        self.directory = self.dir_edit.text() or os.getcwd()
        self.close()

    def reject_close(self) -> None:
        self.chosen = None
        self.close()


class MainWindow(QMainWindow):
    """Sessions on the left, terminal tabs on the right."""

    def __init__(self, cfg=None, provider=None) -> None:
        super().__init__()
        self.cfg = cfg or config_mod.Config()
        self._provider = provider or (lambda: {"v": 1, "header": {}, "rows": []})
        self.snapshot: Dict[str, object] = {"v": 1, "header": {}, "rows": []}
        self.panes: List[terminal.TerminalPane] = []
        self.selected_pid: Optional[int] = None
        self.receipt = ""

        self.setWindowTitle("Ostlr")
        self.resize(1340, 820)
        self.setStyleSheet(style.stylesheet())

        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 6)
        outer.setSpacing(6)

        bar = QHBoxLayout()
        self.action_new = QPushButton("+  New terminal", self)
        self.action_new.clicked.connect(self.new_terminal)
        self.action_start = QPushButton("Start a session", self)
        self.action_start.clicked.connect(self.start_session)
        self.action_refresh = QPushButton("Refresh", self)
        self.action_refresh.clicked.connect(self.refresh_now)
        self.action_copy = QPushButton("Copy path", self)
        self.action_copy.clicked.connect(self.copy_selected_path)
        self.action_focus = QPushButton("Focus window", self)
        self.action_focus.clicked.connect(self.focus_selected)
        for button in (self.action_new, self.action_start, self.action_refresh,
                       self.action_copy, self.action_focus):
            bar.addWidget(button)
        bar.addStretch(1)
        self.header_label = QLabel("", self)
        bar.addWidget(self.header_label)
        outer.addLayout(bar)

        self.banner_label = QLabel("", self)
        self.banner_label.setWordWrap(True)
        outer.addWidget(self.banner_label)

        split = QSplitter(Qt.Horizontal, self)
        outer.addWidget(split, 1)

        left = QWidget(split)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Sessions", left))
        self.list = QListWidget(left)
        self.list.setMinimumWidth(360)
        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.itemDoubleClicked.connect(lambda _item: self.focus_selected())
        left_layout.addWidget(self.list, 1)
        self.detail = QLabel("", left)
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignTop)
        left_layout.addWidget(self.detail)
        split.addWidget(left)

        right = QWidget(split)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(right)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_pane)
        right_layout.addWidget(self.tabs)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([400, 940])

        self.status_label = QLabel("Ready", self)
        outer.addWidget(self.status_label)
        self.setCentralWidget(central)

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_now)
        self._timer.start(max(1, int(self.cfg.tick_s)) * 1000)
        QTimer.singleShot(200, self.refresh_now)

        self.new_terminal()

    # --- terminals -----------------------------------------------------
    def new_terminal(self, argv=None, cwd=None, title=None):
        if len(self.panes) >= terminal.MAX_PANES:
            self._set_receipt("pane limit reached (%d)" % terminal.MAX_PANES)
            return None
        pane = terminal.TerminalPane(argv or terminal.shell_for(cwd), cwd=cwd,
                                     title=title or "shell", parent=self)
        pane.exited.connect(lambda reason, p=pane: self._on_pane_exit(p, reason))
        self.panes.append(pane)
        index = self.tabs.addTab(pane, pane.title)
        self.tabs.setCurrentIndex(index)
        pane.focus_terminal()
        return pane

    def close_pane(self, index: int) -> None:
        pane = self.tabs.widget(index)
        if pane is None:
            return
        self.tabs.removeTab(index)
        if pane in self.panes:
            self.panes.remove(pane)
        pane.bridge.stop()
        pane.deleteLater()

    def _on_pane_exit(self, pane, reason: str) -> None:
        self._set_receipt("%s: %s" % (pane.title, reason))

    # --- snapshot ------------------------------------------------------
    def refresh_now(self):
        try:
            self.snapshot = self._provider() or {"v": 1, "header": {}, "rows": []}
        except Exception as exc:
            self._set_receipt("refresh failed: %s" % exc)
            return self.snapshot
        self._render()
        return self.snapshot

    def _render(self) -> None:
        header = self.snapshot.get("header") or {}
        rows = list(self.snapshot.get("rows") or [])[:LIST_LIMIT]
        self.header_label.setText(self._header_text(header))

        keep = self.selected_pid
        self.list.blockSignals(True)
        self.list.clear()
        for row in rows:
            item = QListWidgetItem("%s  %s  %s" % (
                view_model.state_word(row).ljust(8),
                str(row.get("agent") or "?").ljust(10),
                str(row.get("project") or "?")))
            item.setData(Qt.UserRole, row)
            role = view_model.role_for(row)
            if role == "danger":
                item.setForeground(style.colour("danger"))
            elif role == "warn":
                item.setForeground(style.colour("warn"))
            self.list.addItem(item)
        self.list.blockSignals(False)

        if keep is not None:
            for index in range(self.list.count()):
                row = self.list.item(index).data(Qt.UserRole)
                if int(row.get("pid") or 0) == keep:
                    self.list.setCurrentRow(index)
                    break
        self._render_banner(rows)
        self._render_detail()

    def _header_text(self, header: Dict[str, object]) -> str:
        parts = []
        for key in ("waiting", "running", "unknown"):
            parts.append("%s %d" % (key, int(header.get(key) or 0)))
        for key in ("error", "done", "idle", "starting"):
            value = int(header.get(key) or 0)
            if value:
                parts.append("%s %d" % (key, value))
        parts.append("sessions %d" % int(header.get("sessions") or 0))
        return "   ".join(parts)

    def _render_banner(self, rows) -> None:
        stale = str(self.snapshot.get("stale") or "")
        if stale:
            self.banner_label.setText(stale)
            return
        producers = sum(1 for row in rows if str(row.get("source")) == "l2")
        self.banner_label.setText(view_model.unknown_banner(len(rows), len(rows), producers))

    def _render_detail(self) -> None:
        row = self._selected_row()
        self.detail.setText("\n".join(view_model.detail_lines(row)))
        self.status_label.setText(self.receipt or view_model.READY)

    # --- gestures ------------------------------------------------------
    def _selected_row(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _on_select(self) -> None:
        row = self._selected_row()
        self.selected_pid = int(row.get("pid") or 0) if row else None
        self._render_detail()

    def focus_selected(self):
        row = self._selected_row()
        if not row:
            return
        result = actions_mod.focus(int(row.get("pid") or 0))
        self._set_receipt(str(result.get("receipt") or result.get("reason") or "focus"))

    def copy_selected_path(self):
        row = self._selected_row()
        path = str((row or {}).get("cwd") or "")
        if not path or path == "?":
            self._set_receipt("no path on this row")
            return
        from PySide6.QtGui import QGuiApplication
        QGuiApplication.clipboard().setText(path)
        self._set_receipt("copied %s" % path)

    def _set_receipt(self, text: str) -> None:
        self.receipt = text
        self.status_label.setText(text)

    # --- launching -----------------------------------------------------
    def start_session(self):
        dialog = LauncherDialog(self.cfg, self)
        dialog.setWindowModality(Qt.ApplicationModal)
        dialog.exec()
        if not dialog.chosen:
            return
        self.start_in_pane(str(dialog.chosen.get("command") or ""),
                           list(dialog.chosen.get("args") or []),
                           dialog.directory)

    def start_in_pane(self, command: str, args=None, cwd: Optional[str] = None):
        resolved = discovery.resolve(command)
        if not resolved:
            self._set_receipt("not found: %s" % command)
            return None
        argv = [resolved] + [str(a) for a in (args or [])]
        info = discovery.home_of(resolved)
        title = os.path.basename(resolved)
        if info["files_dir"] and not info["has_key"]:
            title += "  (no api key)"
        return self.new_terminal(argv, cwd=cwd, title=title)

    def closeEvent(self, event) -> None:  # noqa: N802
        for pane in list(self.panes):
            pane.bridge.stop()
        super().closeEvent(event)
