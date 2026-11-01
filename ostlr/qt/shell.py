"""The Qt shell: sessions on the left, terminal panes on the right.

What this file is responsible for, in one place:

  * the list answers "who is running and who waits for me" (sessions/ladder/
    view_model do the thinking; this only draws it);
  * the panes are real terminals we own (ConPTY + xterm.js), so a session we
    start can be typed into, split, and reported to the ladder as `ostlr-pane`;
  * the launcher discovers programs instead of demanding a config edit, and it
    says out loud which copy of an exe it will run and whether that copy has an
    API key;
  * layout and tray make it survive a restart without pretending to restore
    processes.
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
from . import discovery, layout as layout_mod, style, terminal, tray as tray_mod

LIST_LIMIT = 200


class LauncherDialog(QWidget):
    """Pick a program, pick a directory, see where that program keeps its data."""

    def __init__(self, cfg, parent=None) -> None:
        super().__init__(parent, Qt.Dialog)
        self.setWindowTitle("Start a session")
        self.resize(760, 380)
        self.cfg = cfg
        self.chosen: Optional[Dict[str, object]] = None
        self.directory = os.getcwd()
        self._live_path = ""

        layout = QVBoxLayout(self)

        row = QHBoxLayout()
        row.addWidget(QLabel("Program"))
        self.combo = QComboBox(self)
        self.combo.setMinimumWidth(420)
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

        self.use_live = QPushButton("", self)
        self.use_live.setVisible(False)
        self.use_live.clicked.connect(self._adopt_live_copy)
        layout.addWidget(self.use_live)

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
            self.use_live.setVisible(False)
            return
        info = discovery.home_of(str(entry.get("command")))
        lines = ["exe:  %s" % info["exe"], "home: %s" % info["dir"]]
        if info["files_dir"]:
            lines.append("projv_files: %s   (api key: %s)"
                         % (info["files_dir"], "yes" if info["has_key"] else "NO"))
        if not info["has_key"]:
            lines.append("")
            lines.append("This copy has no API key. If you already run one that has, "
                         "pick it below instead of letting a fresh copy ask again.")
        self.home_label.setText("\n".join(lines))

        self._live_path = "" if info["has_key"] else discovery.best_keyed_copy(
            str(entry.get("command")))
        if self._live_path:
            self.use_live.setText("Use the running copy: %s" % self._live_path)
            self.use_live.setVisible(True)
        else:
            self.use_live.setVisible(False)

    def _adopt_live_copy(self) -> None:
        path = self._live_path
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
        self._adopt_if_present(path)

    def _adopt_if_present(self, path: str) -> None:
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
    """The panel: session list, terminal panes, tray, layout."""

    def __init__(self, cfg=None, provider=None) -> None:
        super().__init__()
        self.cfg = cfg or config_mod.Config()
        self._provider = provider or (lambda: {"v": 1, "header": {}, "rows": []})
        self.snapshot: Dict[str, object] = {"v": 1, "header": {}, "rows": []}
        self.panes: List[terminal.TerminalPane] = []
        self.pane_by_pid: Dict[int, terminal.TerminalPane] = {}
        self.selected_pid: Optional[int] = None
        self.receipt = ""

        self.tray = tray_mod.Tray(self)
        self.tray.show_requested.connect(self._show_from_tray)
        self.tray.refresh_requested.connect(self.refresh_now)
        self.tray.quit_requested.connect(self.close)

        self.setWindowTitle("Ostlr")
        self.resize(1340, 820)
        self.setStyleSheet(style.stylesheet())
        self.setCentralWidget(self._build())

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_now)
        self._timer.start(max(1, int(self.cfg.tick_s)) * 1000)
        QTimer.singleShot(200, self.refresh_now)

        if self.tray.start():
            self._set_receipt("tray active")
        else:
            self._set_receipt("no tray (%s) - the title carries the count" % self.tray.reason)

        if not self._restore_layout():
            self.new_terminal()

    # --- construction ---------------------------------------------------
    def _build(self) -> QWidget:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 6)
        outer.setSpacing(6)

        bar = QHBoxLayout()
        self.actions: Dict[str, QPushButton] = {}
        for key, label, slot in (
            ("new", "+  New terminal", self.new_terminal),
            ("start", "Start a session", self.start_session),
            ("open", "Open in panel", self.open_selected_session),
            ("refresh", "Refresh", self.refresh_now),
            ("copy", "Copy path", self.copy_selected_path),
            ("focus", "Focus window", self.focus_selected),
            ("split_h", "Split right", lambda: self.split_current(False)),
            ("split_v", "Split down", lambda: self.split_current(True)),
        ):
            button = QPushButton(label, self)
            button.clicked.connect(slot)
            bar.addWidget(button)
            self.actions[key] = button
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
        left_layout.addWidget(QLabel("Sessions  (double-click opens one in a pane)", left))
        self.list = QListWidget(left)
        self.list.setMinimumWidth(360)
        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.itemDoubleClicked.connect(lambda _i: self.open_selected_session())
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
        self.tabs.tabCloseRequested.connect(self.close_tab)
        right_layout.addWidget(self.tabs)
        split.addWidget(right)
        split.setStretchFactor(0, 0)
        split.setStretchFactor(1, 1)
        split.setSizes([400, 940])
        self.split = split

        self.status_label = QLabel("Ready", self)
        outer.addWidget(self.status_label)
        return central

    # --- panes ----------------------------------------------------------
    def _make_pane(self, argv=None, cwd=None, title=None):
        if len(self.panes) >= terminal.MAX_PANES:
            self._set_receipt("pane limit reached (%d)" % terminal.MAX_PANES)
            return None
        pane = terminal.TerminalPane(argv or terminal.shell_for(cwd), cwd=cwd,
                                     title=title or "shell", parent=self)
        pane.exited.connect(lambda reason, p=pane: self._on_pane_exit(p, reason))
        pane.bridge.started.connect(lambda pid, p=pane: self._bind_pane(p, pid))
        self.panes.append(pane)
        return pane

    def _bind_pane(self, pane, pid: int) -> None:
        if pid:
            self.pane_by_pid[int(pid)] = pane
            self._set_receipt("%s is pane-hosted (pid %d)" % (pane.title, pid))

    def new_terminal(self, argv=None, cwd=None, title=None):
        pane = self._make_pane(argv, cwd, title)
        if pane is None:
            return None
        index = self.tabs.addTab(pane, pane.title)
        self.tabs.setCurrentIndex(index)
        pane.focus_terminal()
        return pane

    def split_current(self, vertical: bool = False):
        """Turn the current tab into two panes: the one there, plus a new shell."""
        current = self.tabs.currentWidget()
        if current is None:
            return None
        if isinstance(current, QSplitter):
            self._set_receipt("this tab already has two panes")
            return None
        if len(self.panes) >= terminal.MAX_PANES:
            self._set_receipt("pane limit reached (%d)" % terminal.MAX_PANES)
            return None
        index = self.tabs.currentIndex()
        title = self.tabs.tabText(index)
        cwd = getattr(current, "cwd", None)
        fresh = self._make_pane(cwd=cwd, title="shell")
        if fresh is None:
            return None
        self.tabs.removeTab(index)
        splitter = QSplitter(Qt.Vertical if vertical else Qt.Horizontal, self)
        splitter.addWidget(current)
        splitter.addWidget(fresh)
        splitter.setSizes([500, 500])
        self.tabs.insertTab(index, splitter, title)
        self.tabs.setCurrentIndex(index)
        fresh.focus_terminal()
        return fresh

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
            pane.bridge.stop()
            pane.deleteLater()

    def _on_pane_exit(self, pane, reason: str) -> None:
        self._set_receipt("%s: %s" % (pane.title, reason))
        self.refresh_now()

    # --- session <-> pane ------------------------------------------------
    def open_selected_session(self):
        """Focus the pane that owns this session, or open a shell where it lives.

        We cannot adopt a PTY we do not own, so the honest action for an external
        session is a shell in its directory - never a fake "attach".
        """
        row = self._selected_row()
        if not row:
            self._set_receipt("no row selected")
            return None
        pid = int(row.get("pid") or 0)
        bound = self.pane_by_pid.get(pid)
        if bound is not None:
            self.tabs.setCurrentWidget(bound)
            bound.focus_terminal()
            self._set_receipt("%s is this panel's pane" % str(row.get("agent")))
            return bound
        cwd = str(row.get("cwd") or "")
        if not cwd or cwd == "?":
            self._set_receipt("no directory on this row; use Start a session")
            return None
        return self.new_terminal(cwd=cwd,
                                 title="shell @ %s" % (row.get("project") or "?"))

    # --- layout ----------------------------------------------------------
    def _layout_payload(self) -> List[Dict[str, object]]:
        tabs: List[Dict[str, object]] = []
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            if isinstance(widget, QSplitter):
                panes = [widget.widget(i) for i in range(widget.count())]
                split = "v" if widget.orientation() == Qt.Vertical else "h"
                sizes = list(widget.sizes())
            else:
                panes = [widget]
                split = "h"
                sizes = []
            entries = [{"argv": p.argv, "cwd": p.cwd or ""} for p in panes
                       if isinstance(p, terminal.TerminalPane)]
            if entries:
                tabs.append(layout_mod.tab_entry(self.tabs.tabText(index), entries,
                                                 split, sizes))
        return tabs

    def _save_layout(self) -> None:
        try:
            layout_mod.save(self._layout_payload(), self.tabs.currentIndex())
        except Exception:
            pass

    def _restore_layout(self) -> bool:
        if not getattr(self.cfg, "restore_layout", True):
            return False
        data = layout_mod.load()
        tabs = data.get("tabs") or []
        if not tabs:
            return False
        restored = 0
        for entry in tabs:
            panes = entry.get("panes") or []
            first = panes[0] if panes else {}
            pane = self._make_pane(first.get("argv") or None,
                                   first.get("cwd") or None,
                                   title=str(entry.get("title") or "shell"))
            if pane is None:
                break
            index = self.tabs.addTab(pane, pane.title)
            restored += 1
            if len(panes) > 1:
                second = panes[1]
                fresh = self._make_pane(second.get("argv") or None,
                                        second.get("cwd") or None, title="shell")
                if fresh is not None:
                    self.tabs.removeTab(index)
                    splitter = QSplitter(
                        Qt.Vertical if entry.get("split") == "v" else Qt.Horizontal, self)
                    splitter.addWidget(pane)
                    splitter.addWidget(fresh)
                    sizes = entry.get("sizes") or [500, 500]
                    if len(sizes) == 2:
                        splitter.setSizes([int(sizes[0]), int(sizes[1])])
                    self.tabs.insertTab(index, splitter, pane.title)
        if restored:
            self._set_receipt("layout restored (%d tab(s))" % restored)
        return restored > 0

    # --- tray ------------------------------------------------------------
    def _show_from_tray(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    # --- snapshot --------------------------------------------------------
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
            owned = int(row.get("pid") or 0) in self.pane_by_pid
            item = QListWidgetItem("%s %s %-10s %s" % (
                "*" if owned else " ",
                view_model.state_word(row).ljust(8),
                str(row.get("agent") or "?"),
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
        self.tray.update(int(header.get("waiting") or 0), int(header.get("running") or 0))

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
        producers = sum(1 for row in rows
                        if str(row.get("source")) in ("l2", "ostlr-pane"))
        self.banner_label.setText(view_model.unknown_banner(len(rows), len(rows), producers))

    def _render_detail(self) -> None:
        row = self._selected_row()
        self.detail.setText("\n".join(view_model.detail_lines(row)))
        self.status_label.setText(self.receipt or view_model.READY)

    # --- gestures --------------------------------------------------------
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

    # --- launching -------------------------------------------------------
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
        self._save_layout()
        self.tray.stop()
        for pane in list(self.panes):
            pane.bridge.stop()
        super().closeEvent(event)
