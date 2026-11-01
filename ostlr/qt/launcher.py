"""The launcher: one selector, three ways in (baseline 3.3).

    tree: right-click a directory   -> "Start a TUI here..."
    tree: right-click a file        -> "Start in its folder..."
    top bar: + New session          -> last directory / recent

Everything the dialog decides is visible before the click: which programs exist
(greyed with the fix inline when they do not), which copy of the exe will run,
where that copy keeps its data, and whether it has an API key. "Remember" is the
default: the directory, the agent and the location all come back next time,
which is what makes starting a session two interactions instead of ten.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QButtonGroup, QComboBox, QDialog, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QRadioButton, QVBoxLayout, QWidget,
)

from .. import config as config_mod
from . import discovery, style


class Launcher(QDialog):
    """Pick a directory, pick a TUI, pick where it runs."""

    def __init__(self, cfg, directory: Optional[str] = None, parent=None) -> None:
        super().__init__(parent)
        self.cfg = cfg
        self.chosen: Optional[Dict[str, object]] = None
        self.directory = directory or (getattr(cfg, "last_dirs", None) or [os.getcwd()])[0]
        self.location = "panel"

        self.setWindowTitle("New session")
        self.resize(820, 460)
        self.setStyleSheet(style.stylesheet())

        outer = QVBoxLayout(self)

        dir_row = QHBoxLayout()
        dir_row.addWidget(QLabel("Directory", self))
        self.dir_edit = QLineEdit(self.directory, self)
        dir_row.addWidget(self.dir_edit, 1)
        browse = QPushButton("Browse...", self)
        browse.clicked.connect(self._pick_dir)
        dir_row.addWidget(browse)
        outer.addLayout(dir_row)

        outer.addWidget(QLabel("Start", self))
        self.combo = QComboBox(self)
        self.combo.setMinimumWidth(520)
        self.combo.currentIndexChanged.connect(lambda _i: self._describe())
        outer.addWidget(self.combo)
        browse_exe = QPushButton("Browse for another program...", self)
        browse_exe.clicked.connect(self._pick_exe)
        outer.addWidget(browse_exe, 0, Qt.AlignLeft)

        self.describe = QLabel("", self)
        self.describe.setWordWrap(True)
        outer.addWidget(self.describe, 1)

        loc_row = QHBoxLayout()
        loc_row.addWidget(QLabel("Where", self))
        self.group = QButtonGroup(self)
        panel_radio = QRadioButton("Inside Ostlr (a tab)", self)
        panel_radio.setChecked(True)
        external_radio = QRadioButton("New window (external terminal)", self)
        self.group.addButton(panel_radio, 0)
        self.group.addButton(external_radio, 1)
        loc_row.addWidget(panel_radio)
        loc_row.addWidget(external_radio)
        loc_row.addStretch(1)
        outer.addLayout(loc_row)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel", self)
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        start = QPushButton("Start", self)
        start.setDefault(True)
        start.clicked.connect(self._accept)
        buttons.addWidget(start)
        outer.addLayout(buttons)

        self.reload_entries()

    # --- entries ------------------------------------------------------
    def reload_entries(self) -> None:
        self.combo.clear()
        last = (getattr(self.cfg, "last_agents", None) or [""])[0]
        selected = 0
        for index, entry in enumerate(discovery.launcher_entries(self.cfg)):
            label = str(entry.get("label") or entry.get("id"))
            available = bool(discovery.resolve(str(entry.get("command"))))
            suffix = "" if available else "   -- not found"
            self.combo.addItem("%s%s" % (label, suffix), entry)
            if not available:
                self.combo.model().item(index).setEnabled(False)
            if last and str(entry.get("id")) == last:
                selected = index
        if self.combo.count() == 0:
            self.combo.addItem("Nothing found - use Browse...", None)
        self.combo.setCurrentIndex(selected)
        self._describe()

    def _describe(self) -> None:
        entry = self.combo.currentData()
        if not entry:
            self.describe.setText("")
            return
        command = str(entry.get("command"))
        resolved = discovery.resolve(command)
        info = discovery.home_of(resolved or command)
        lines = ["exe:  %s" % (resolved or command + "  (not found - install it or Browse)"),
                 "home: %s" % info["dir"]]
        if info["files_dir"]:
            lines.append("data: %s   (api key: %s)"
                         % (info["files_dir"], "yes" if info["has_key"] else "NO"))
        else:
            lines.append("data: no projv_files next to this exe")
        self.describe.setText("\n".join(lines))

    # --- files --------------------------------------------------------
    def _pick_dir(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Start in directory", self.dir_edit.text())
        if path:
            self.dir_edit.setText(path)

    def _pick_exe(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Choose a program", self.dir_edit.text(),
                                              "Programs (*.exe);;All files (*)")
        if not path:
            return
        discovery.remember(self.cfg, path)
        self._save()
        self.reload_entries()
        # Select the program that was just chosen. Without this, reload_entries
        # restored the *previous* last-agent, so the dialog described a different
        # exe than the one the user picked - and the api-key state it showed
        # belonged to that other copy (the report that made this a bug).
        for index in range(self.combo.count()):
            entry = self.combo.itemData(index)
            if entry and os.path.normcase(str(entry.get("command"))) == os.path.normcase(path):
                self.combo.setCurrentIndex(index)
                break
        self._describe()

    def _save(self) -> None:
        try:
            config_mod.save(self.cfg)
        except Exception:
            pass

    # --- result -------------------------------------------------------
    def _accept(self) -> None:
        entry = self.combo.currentData()
        if not entry:
            return
        self.chosen = dict(entry)
        self.directory = self.dir_edit.text().strip() or os.getcwd()
        self.location = "panel" if self.group.checkedId() == 0 else "window"

        # Remember all three (baseline 3.3 rule 2): directory, agent, location.
        try:
            dirs = [self.directory] + [d for d in (self.cfg.last_dirs or [])
                                       if os.path.normcase(d) != os.path.normcase(self.directory)]
            self.cfg.last_dirs = dirs[:3]
            agent = str(entry.get("id"))
            self.cfg.last_agents = ([agent] + [a for a in (self.cfg.last_agents or [])
                                               if a != agent])[:3]
            self.cfg.last_location = self.location
            self._save()
        except Exception:
            pass
        self.accept()
