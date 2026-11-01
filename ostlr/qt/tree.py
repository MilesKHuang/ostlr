"""The file tree: the launcher's real entry point (baseline 3.1 and 3.3).

The design's L3 flow has three ways into one selector, and two of them are this
widget: right-click a directory -> "Start here...", right-click a file -> "Start
in its folder...". That is why the tree is not decoration - without it the
launcher has no place to be triggered from, which is exactly what went wrong when
it was cut for schedule.

It also owns "copy absolute path" (baseline 3.4): a path copied from here goes to
the clipboard and the receipt names the target pane.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import QDir, QModelIndex, Qt, Signal
from PySide6.QtGui import QAction, QGuiApplication
from PySide6.QtWidgets import (
    QFileSystemModel, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QTreeView, QVBoxLayout, QWidget,
)

from .. import view_model
from . import style


class FileTree(QWidget):
    """Browse a workspace, then start a TUI / copy a path from the context menu."""

    start_here = Signal(str)          # absolute directory
    open_terminal = Signal(str)       # absolute directory
    inject_path = Signal(str, bool)   # absolute path, at_prefix
    copied = Signal(str)              # absolute path

    def __init__(self, root: Optional[str] = None, parent=None) -> None:
        super().__init__(parent)
        self.root = root or os.getcwd()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(4)

        bar = QHBoxLayout()
        bar.addWidget(QLabel("Files", self))
        self.path_edit = QLineEdit(self.root, self)
        self.path_edit.returnPressed.connect(self._apply_path)
        bar.addWidget(self.path_edit, 1)
        up = QPushButton("Up", self)
        up.clicked.connect(self._go_up)
        bar.addWidget(up)
        outer.addLayout(bar)

        self.model = QFileSystemModel(self)
        self.model.setRootPath(QDir.rootPath())
        self.model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.view = QTreeView(self)
        self.view.setModel(self.model)
        self.view.setHeaderHidden(True)
        for column in range(1, 4):
            self.view.hideColumn(column)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.doubleClicked.connect(self._on_double_click)
        outer.addWidget(self.view, 1)

        self.set_root(self.root)

    # --- navigation ---------------------------------------------------
    def set_root(self, path: str) -> None:
        if not path or not os.path.isdir(path):
            return
        self.root = os.path.abspath(path)
        self.path_edit.setText(self.root)
        self.view.setRootIndex(self.model.index(self.root))

    def _apply_path(self) -> None:
        self.set_root(self.path_edit.text().strip().strip('"'))

    def _go_up(self) -> None:
        self.set_root(os.path.dirname(self.root.rstrip("\\/")) or self.root)

    def _on_double_click(self, index: QModelIndex) -> None:
        path = self.model.filePath(index)
        if os.path.isdir(path):
            self.set_root(path)

    # --- the context menu (baseline 3.3 / 3.4) ------------------------
    def selected_path(self) -> str:
        index = self.view.currentIndex()
        if not index.isValid():
            return ""
        return self.model.filePath(index)

    def _menu(self, position) -> None:
        path = self.selected_path() or self.root
        if not path:
            return
        directory = path if os.path.isdir(path) else os.path.dirname(path)
        index = self.view.currentIndex()
        if index.isValid():
            self.view.setCurrentIndex(index)

        menu = QMenu(self)
        menu.setStyleSheet(style.stylesheet())

        start = QAction("Start a TUI here...", menu)
        start.triggered.connect(lambda: self.start_here.emit(directory))
        menu.addAction(start)

        terminal = QAction("Open a terminal here", menu)
        terminal.triggered.connect(lambda: self.open_terminal.emit(directory))
        menu.addAction(terminal)

        menu.addSeparator()

        copy = QAction("Copy absolute path", menu)
        copy.triggered.connect(lambda: self._copy(path))
        menu.addAction(copy)

        inject = QAction("Send path to the focused pane", menu)
        inject.triggered.connect(lambda: self.inject_path.emit(path, False))
        menu.addAction(inject)

        inject_at = QAction("Send @path to the focused pane", menu)
        inject_at.triggered.connect(lambda: self.inject_path.emit(path, True))
        menu.addAction(inject_at)

        menu.exec(self.view.viewport().mapToGlobal(position))

    def _copy(self, path: str) -> None:
        QGuiApplication.clipboard().setText(path)
        self.copied.emit(path)

    # --- the same actions from the keyboard --------------------------
    def copy_selected(self) -> str:
        path = self.selected_path() or self.root
        self._copy(path)
        return path

    def inject_selected(self, at_prefix: bool = False) -> str:
        path = self.selected_path() or self.root
        self.inject_path.emit(path, at_prefix)
        return path
