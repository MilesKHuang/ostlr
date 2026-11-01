"""The Ostlr window: five regions, as the baseline draws them (3.1).

Top bar (new / refresh / focus all + gauges), session list with two-line rows,
terminal panes, file tree with the launcher's context menu, details, status bar.

What this rewrite adds over the previous shell:

  * the file tree is the launcher's entry point (right-click -> start here);
  * gauges instead of bare numbers, so "who is heavy" is a glance;
  * L1: a pane owns its PTY, so OSC 7501 from the CLI is parsed and stored -
    projV reports `working` / `blocked` by itself;
  * copy / inject from the tree into the focused pane, three-part receipt.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QMainWindow, QMenu,
    QMessageBox, QPushButton, QSplitter, QTabWidget, QVBoxLayout, QWidget,
)

from .. import actions as actions_mod
from .. import config as config_mod
from .. import view_model
from . import (
    discovery, gauges as gauge_mod, launcher as launcher_mod, layout as layout_mod,
    style, terminal, tray as tray_mod, tree as tree_mod,
)

LIST_LIMIT = 200
GLYPH = {"blocked": "\u25cf", "working": "\u25d0", "idle": "\u25cb",
         "done": "\u2713", "error": "\u2715", "unknown": "\u25cc"}


class MainWindow(QMainWindow):
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
        self.resize(1440, 880)
        self.setStyleSheet(style.stylesheet())
        self.setCentralWidget(self._build())

        self._timer = QTimer(self)
        self._timer.timeout.connect(self.refresh_now)
        self._timer.start(max(1, int(self.cfg.tick_s)) * 1000)
        QTimer.singleShot(150, self.refresh_now)

        if not self.tray.start():
            self._set_receipt("no tray (%s)" % self.tray.reason)
        if not self._restore_layout():
            self._set_receipt("ready - right-click a folder in the tree to start")

    # --- layout ---------------------------------------------------------
    def _build(self) -> QWidget:
        central = QWidget(self)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(8, 8, 8, 6)
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
        bar.addStretch(1)
        self.gauges = gauge_mod.GaugeRow(self)
        bar.addWidget(self.gauges)
        outer.addLayout(bar)

        self.banner_label = QLabel("", self)
        self.banner_label.setWordWrap(True)
        outer.addWidget(self.banner_label)

        main = QSplitter(Qt.Horizontal, self)
        outer.addWidget(main, 1)

        left = QWidget(main)
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(0, 0, 0, 0)
        left_layout.addWidget(QLabel("Sessions", left))
        self.list = QListWidget(left)
        self.list.setMinimumWidth(390)
        self.list.itemSelectionChanged.connect(self._on_select)
        self.list.setContextMenuPolicy(Qt.CustomContextMenu)
        self.list.customContextMenuRequested.connect(self._session_menu)
        left_layout.addWidget(self.list, 3)
        left_layout.addWidget(QLabel("Resources", left))
        self.bars = gauge_mod.RowBars(left)
        left_layout.addWidget(self.bars, 1)
        main.addWidget(left)

        right = QWidget(main)
        right_layout = QVBoxLayout(right)
        right_layout.setContentsMargins(0, 0, 0, 0)
        self.tabs = QTabWidget(right)
        self.tabs.setTabsClosable(True)
        self.tabs.tabCloseRequested.connect(self.close_tab)
        right_layout.addWidget(self.tabs, 3)
        # The main area is not left blank: without a pane it says where a session
        # comes from. An empty black rectangle reads as a broken window.
        self.placeholder = QLabel(
            "No terminal yet.\n\n"
            "Right-click a folder in the file tree below ->  \"Start a TUI here...\"\n"
            "or press \"+ New session\" in the top bar.\n\n"
            "A session started here is owned by the panel: its PTY, its window size,\n"
            "its state (OSC 7501 when the CLI reports one, otherwise the panel's own\n"
            "record), and it is marked with * in the list.", self)
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setWordWrap(True)
        self.tabs.addTab(self.placeholder, "welcome")
        bottom = QSplitter(Qt.Horizontal, right)
        self.tree = tree_mod.FileTree(self._initial_root(), bottom)
        self.tree.start_here.connect(self.start_session)
        self.tree.open_terminal.connect(self.open_terminal_here)
        self.tree.inject_path.connect(self.inject_path_into_pane)
        self.tree.copied.connect(lambda path: self._receipt_copy(path, from_tree=True))
        bottom.addWidget(self.tree)
        self.detail = QLabel("", bottom)
        self.detail.setWordWrap(True)
        self.detail.setAlignment(Qt.AlignTop)
        self.detail.setTextInteractionFlags(Qt.TextSelectableByMouse)
        bottom.addWidget(self.detail)
        bottom.setSizes([620, 420])
        right_layout.addWidget(bottom, 2)
        main.addWidget(right)
        main.setSizes([420, 1020])

        self.status_label = QLabel("Ready", self)
        outer.addWidget(self.status_label)
        return central

    def _initial_root(self) -> str:
        for candidate in (getattr(self.cfg, "last_dirs", None) or []):
            if candidate and os.path.isdir(candidate):
                return candidate
        return os.getcwd()

    # --- panes ------------------------------------------------------------
    def _make_pane(self, argv=None, cwd=None, title=None):
        if len(self.panes) >= terminal.MAX_PANES:
            self._set_receipt("pane limit reached (%d)" % terminal.MAX_PANES)
            return None
        pane = terminal.TerminalPane(argv or terminal.shell_for(cwd), cwd=cwd,
                                     title=title or "shell", parent=self)
        pane.exited.connect(lambda reason, p=pane: self._on_pane_exit(p, reason))
        pane.bridge.started.connect(lambda pid, p=pane: self._bind_pane(p, pid))
        pane.l1_report.connect(self._on_l1)
        self.panes.append(pane)
        return pane

    def _bind_pane(self, pane, pid: int) -> None:
        if not pid:
            return
        self.pane_by_pid[int(pid)] = pane
        self._set_receipt("%s -> pane (pid %d)" % (pane.title, pid))
        self.refresh_now()

    def _on_l1(self, pid: int, report: Dict[str, object]) -> None:
        """A CLI told us its own state over OSC 7501: trust it above everything."""
        state = str(report.get("state") or "")
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
        return pane

    def _drop_placeholder(self) -> None:
        """The welcome tab exists only while there is nothing to show."""
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
            pane.bridge.stop()
            pane.deleteLater()

    def _on_pane_exit(self, pane, reason: str) -> None:
        self._set_receipt("%s: %s" % (pane.title, reason))
        self.refresh_now()

    # --- snapshot ---------------------------------------------------------
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
        keep = self.selected_pid

        self.list.blockSignals(True)
        self.list.clear()
        for row in rows:
            owned = int(row.get("pid") or 0) in self.pane_by_pid
            self.list.addItem(self._row_item(row, owned))
        self.list.blockSignals(False)

        if keep is not None:
            for index in range(self.list.count()):
                row = self.list.item(index).data(Qt.UserRole)
                if int(row.get("pid") or 0) == keep:
                    self.list.setCurrentRow(index)
                    break

        self.gauges.update_from(header, rows)
        self.bars.set_entries(rows)
        self._render_banner(rows)
        self._render_detail()
        self.tray.update(int(header.get("waiting") or 0), int(header.get("running") or 0))

    def _row_item(self, row: Dict[str, object], owned: bool) -> QListWidgetItem:
        """Two lines, as baseline 3.1/3.2 draws a row."""
        glyph = GLYPH.get(str(row.get("state") or "unknown"), "\u25cc")
        agent = str(row.get("agent") or "unknown")
        project = str(row.get("project") or "?")
        branch = view_model.branch_text(row.get("branch"))
        star = "*" if owned else " "
        line1 = "%s %s %s   %s" % (glyph, star, agent, project)
        line2 = "      %s   %s   %s" % (
            view_model.state_word(row),
            view_model.format_elapsed(row.get("elapsed_s")),
            view_model.format_mem(row.get("mem_bytes")))
        if branch != "?":
            line1 = "%s   %s" % (line1, branch)
        item = QListWidgetItem("%s\n%s" % (line1, line2))
        item.setData(Qt.UserRole, row)
        role = view_model.role_for(row)
        item.setForeground(style.colour(role if role in style.ROLES else "fg"))
        return item

    def _render_banner(self, rows) -> None:
        stale = str(self.snapshot.get("stale") or "")
        if stale:
            self.banner_label.setText(stale)
            return
        reporting = sum(1 for row in rows
                        if str(row.get("source")) in ("l1", "l2", "ostlr-pane"))
        unknown = sum(1 for row in rows if str(row.get("state")) == "unknown")
        # `unknown == total` is the only case the banner speaks about: it exists
        # for "nothing is reporting", not for a panel that is simply quiet.
        self.banner_label.setText(view_model.unknown_banner(len(rows), unknown, reporting))

    def _render_detail(self) -> None:
        self.detail.setText("\n".join(view_model.detail_lines(self._selected_row())))

    # --- selection / receipts ---------------------------------------------
    def _selected_row(self):
        item = self.list.currentItem()
        return item.data(Qt.UserRole) if item is not None else None

    def _on_select(self) -> None:
        row = self._selected_row()
        self.selected_pid = int(row.get("pid") or 0) if row else None
        self._render_detail()

    def _set_receipt(self, text: str) -> None:
        self.receipt = text
        self.status_label.setText(text)

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
        pane.bridge.write(text)
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
        seen = set()
        for pane in self.panes:
            if not pane.cwd or pane.cwd in seen:
                continue
            seen.add(pane.cwd)
            actions_mod.focus(getattr(pane, "session_pid", 0) or 0)
        self._set_receipt("focused %d pane(s)" % len(seen))

    # --- sessions: start / kill -------------------------------------------
    def start_session(self, directory=None) -> None:
        """Open the selector; it is the only way in (baseline 3.3).

        The dialog's `chosen` is the whole contract: a Cancel leaves it None, so
        this never depends on an enum name on the instance (PySide6 exposes
        `Accepted` on the class, not on the object - the reason for the
        AttributeError this replaces).
        """
        dialog = launcher_mod.Launcher(self.cfg, directory, self)
        dialog.exec()
        if not dialog.chosen:
            return
        self.start_from(dict(dialog.chosen), dialog.directory, dialog.location)

    def start_from(self, entry: Dict[str, object], directory: str,
                   location: str = "panel") -> None:
        command = discovery.resolve(str(entry.get("command")))
        if not command:
            self._set_receipt("not found: %s" % entry.get("command"))
            return
        args = [str(a) for a in (entry.get("args") or [])]
        info = discovery.home_of(command)
        title = "%s @ %s" % (entry.get("id") or os.path.basename(command),
                             os.path.basename(directory or "") or "?")
        if location == "window":
            result = actions_mod.launch({"command": command, "args": args}, directory)
            self._set_receipt("started in its own window: %s"
                              % (result.get("reason") or result.get("detail") or "?"))
            return
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
        cwd = str(row.get("cwd") or "")
        open_pane = menu.addAction("Open a terminal in its directory")
        open_pane.triggered.connect(lambda: self.open_terminal_here(cwd))
        focus = menu.addAction("Focus its window")
        focus.triggered.connect(self.focus_selected)
        copy = menu.addAction("Copy its directory")
        copy.triggered.connect(self.copy_selected_path)
        menu.addSeparator()
        kill = menu.addAction("End this session...")
        kill.triggered.connect(lambda: self.kill_session(row))
        menu.exec(self.list.viewport().mapToGlobal(position))

    def kill_session(self, row: Dict[str, object]) -> None:
        """Baseline 3.6: show what will die before anything runs."""
        pid = int(row.get("pid") or 0)
        mem = view_model.format_mem(row.get("mem_bytes"))
        answer = QMessageBox.question(
            self, "End session",
            "End %s (pid %d)?\n\nIts whole process tree is terminated; the tree holds "
            "about %s right now." % (row.get("agent") or "?", pid, mem))
        from PySide6.QtWidgets import QMessageBox as _Box
        if answer != _Box.StandardButton.Yes:
            return
        result = actions_mod.kill({"pid": pid, "started_at": row.get("started_at"),
                                   "cwd": row.get("cwd")})
        self._set_receipt(str(result.get("receipt") or result.get("reason")))
        self.refresh_now()

    # --- layout / tray -----------------------------------------------------
    def _layout_payload(self) -> List[Dict[str, object]]:
        tabs: List[Dict[str, object]] = []
        for index in range(self.tabs.count()):
            widget = self.tabs.widget(index)
            panes = ([widget] if not isinstance(widget, QSplitter)
                     else [widget.widget(i) for i in range(widget.count())])
            entries = [{"argv": p.argv, "cwd": p.cwd or ""} for p in panes
                       if isinstance(p, terminal.TerminalPane)]
            if entries:
                tabs.append(layout_mod.tab_entry(self.tabs.tabText(index), entries))
        return tabs

    def _restore_layout(self) -> bool:
        if not getattr(self.cfg, "restore_layout", True):
            return False
        data = layout_mod.load()
        restored = 0
        for entry in data.get("tabs") or []:
            first = (entry.get("panes") or [{}])[0]
            if self._make_pane(first.get("argv") or None, first.get("cwd") or None,
                               title=str(entry.get("title") or "shell")) is None:
                break
            pane = self.panes[-1]
            self.tabs.addTab(pane, pane.title)
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
            layout_mod.save(self._layout_payload(), self.tabs.currentIndex())
        except Exception:
            pass
        self.tray.stop()
        for pane in list(self.panes):
            pane.bridge.stop()
        super().closeEvent(event)
