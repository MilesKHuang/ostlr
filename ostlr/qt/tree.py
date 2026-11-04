"""The file tree: the launcher's real entry point (baseline 3.1 and 3.3).

The design's L3 flow has three ways into one selector, and two of them are this
widget: a folder -> "Start here...", a file -> "Start in its folder...". That is
why the tree is not decoration - without it the launcher has no place to be
triggered from.

The visible surface is icons, not a menu: `+` starts a session in this folder,
`copy` copies this folder's path, `>_` opens a terminal here, and every row
carries its own pair at the right edge - `>` sends the plain path, `@` sends
`@path`. The context menu stays as the keyboard route, because an icon has no
name until a tooltip is read.

Quoting is not decided here: `ostlr.inject.payload()` owns it, so the text that
lands in the pane and the text the receipt shows can never disagree.

Order: `QFileSystemModel` sorts one flat list by name, which strands folders
between files and puts `b10` before `b2`. Explorer does two things differently -
folders first, numbers compared as numbers - so a `TreeOrder` proxy sits between
the model and the view and applies `ostlr.sortkey.name_key()`. The proxy is the
one place an index can be translated wrongly, so every index that leaves this
module goes through `_source()`, and `tests/unit/test_tree_order.py` clicks the
send icons row by row to prove the path that leaves is the path that was drawn.
"""

from __future__ import annotations

import os
from typing import Optional

from PySide6.QtCore import (
    QDir, QEvent, QModelIndex, QPointF, QRectF, QSortFilterProxyModel, Qt, Signal,
)
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QFileSystemModel, QHBoxLayout, QLabel, QLineEdit, QMenu, QPushButton,
    QStyle, QStyledItemDelegate, QTreeView, QVBoxLayout, QWidget,
)

from .. import sortkey
from . import icons as icons_mod
from . import style

# The two per-row send actions, left to right. `at` sends "@path".
SEND_ACTIONS = ("path", "at")
MIN_ICON_ROW_W = 150


class TreeOrder(QSortFilterProxyModel):
    """Folders first, then natural name order (see `ostlr/sortkey.py`)."""

    def lessThan(self, left: QModelIndex, right: QModelIndex) -> bool:  # noqa: N802
        model = self.sourceModel()
        if model is None:
            return False
        return (sortkey.name_key(model.fileName(left), model.isDir(left))
                < sortkey.name_key(model.fileName(right), model.isDir(right)))


def send_action_at(rect: QRectF, point: QPointF) -> Optional[str]:
    """Which send icon a click hit, or None. Pure geometry, tested headless."""
    index = icons_mod.icon_at(rect, point, len(SEND_ACTIONS))
    return SEND_ACTIONS[index] if index is not None else None


class SendIcons(QStyledItemDelegate):
    """The stock file row plus two send icons at its right edge."""

    def paint(self, painter, option, index) -> None:  # noqa: N802
        super().paint(painter, option, index)
        if not index.isValid() or index.column() != 0:
            return
        rect = QRectF(option.rect)
        if rect.width() < MIN_ICON_ROW_W:
            return
        lit = bool(option.state & (QStyle.State_MouseOver | QStyle.State_Selected))
        for slot, name in zip(icons_mod.right_aligned(rect, len(SEND_ACTIONS)),
                              ("send", "send_at")):
            icons_mod.paint(painter, name, slot, "fg" if lit else "fg_dim")


class FileTree(QWidget):
    """Browse a workspace, then start a session / send a path from one icon."""

    start_here = Signal(str)          # absolute directory
    open_terminal = Signal(str)       # absolute directory
    inject_path = Signal(str, bool)   # absolute path, at_prefix
    copied = Signal(str)              # absolute path
    opened = Signal(str)              # absolute path opened with the OS handler

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
        for key, name, tip, slot in (
            ("start", "plus", "Start a TUI in this folder",
             lambda: self.start_here.emit(self.root)),
            ("copy", "copy", "Copy this folder's path",
             lambda: self._copy(self.root)),
            ("terminal", "terminal", "Open a terminal here",
             lambda: self.open_terminal.emit(self.root)),
        ):
            button = QPushButton(self)
            button.setIcon(icons_mod.icon(name, "fg", style.ICON_PX + 4))
            button.setToolTip(tip)
            button.setAccessibleName(tip)
            button.clicked.connect(slot)
            bar.addWidget(button)
            setattr(self, "button_" + key, button)
        outer.addLayout(bar)

        self.model = QFileSystemModel(self)
        self.model.setRootPath(QDir.rootPath())
        self.model.setFilter(QDir.AllDirs | QDir.Files | QDir.NoDotAndDotDot)
        self.proxy = TreeOrder(self)
        self.proxy.setSourceModel(self.model)
        self.proxy.setDynamicSortFilter(True)
        self.proxy.sort(0, Qt.AscendingOrder)
        self.view = QTreeView(self)
        self.view.setModel(self.proxy)
        self.view.setHeaderHidden(True)
        for column in range(1, 4):
            self.view.hideColumn(column)
        self.view.setItemDelegate(SendIcons(self.view))
        self.view.setMouseTracking(True)
        self.view.setContextMenuPolicy(Qt.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._menu)
        self.view.doubleClicked.connect(self._on_double_click)
        self.view.viewport().installEventFilter(self)
        outer.addWidget(self.view, 1)

        self.set_root(self.root)

    # --- navigation ---------------------------------------------------
    def _source(self, index: QModelIndex) -> QModelIndex:
        """A view index translated for the model.

        The view holds proxy indexes; only the model can answer `filePath` and
        `isDir`. Every index that leaves this class passes through here, because
        a proxy index read as a source index is exactly how a click on one row
        would send the path of another.
        """
        if not index.isValid():
            return QModelIndex()
        return self.proxy.mapToSource(index)

    def set_root(self, path: str) -> None:
        """Point the tree at `path`, unless it is already there.

        The panel calls this on every selection change, and the row list refreshes
        every tick; re-rooting an unchanged path would collapse whatever the user
        had expanded, twice a second.
        """
        if not path or not os.path.isdir(path):
            return
        target = os.path.abspath(path)
        if target == getattr(self, "root", None) and self.view.rootIndex().isValid():
            return
        self.root = target
        self.path_edit.setText(self.root)
        self.view.setRootIndex(self.proxy.mapFromSource(self.model.index(self.root)))

    def _apply_path(self) -> None:
        self.set_root(self.path_edit.text().strip().strip('"'))

    def _go_up(self) -> None:
        self.set_root(os.path.dirname(self.root.rstrip("\\/")) or self.root)

    # --- the two icons on every row -----------------------------------
    def eventFilter(self, watched, event):  # noqa: N802
        if watched is self.view.viewport() \
                and event.type() == QEvent.MouseButtonRelease \
                and event.button() == Qt.LeftButton:
            index = self._source(self.view.indexAt(event.position().toPoint()))
            if index.isValid():
                rect = QRectF(self.view.visualRect(self.proxy.mapFromSource(index)))
                action = send_action_at(rect, QPointF(event.position()))
                if action:
                    self.inject_path.emit(self.model.filePath(index), action == "at")
                    return True
        return super().eventFilter(watched, event)

    def _on_double_click(self, index: QModelIndex) -> None:
        """A directory re-roots the tree; a file opens in whatever the OS uses."""
        path = self.model.filePath(self._source(index))
        if not path:
            return
        if os.path.isdir(path):
            self.set_root(path)
            return
        self._open_file(path)

    def _open_file(self, path: str) -> None:
        try:
            os.startfile(path)          # noqa: S606 - the OS association is the point
        except (AttributeError, OSError) as exc:
            self.opened.emit("%s failed: %s" % (path, exc))
            return
        self.opened.emit(path)

    # --- the context menu: the same actions for the keyboard ------------
    def selected_path(self) -> str:
        index = self._source(self.view.currentIndex())
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
        menu.addAction("Start a TUI here...",
                       lambda: self.start_here.emit(directory))
        menu.addAction("Open a terminal here",
                       lambda: self.open_terminal.emit(directory))
        menu.addSeparator()
        menu.addAction("Copy absolute path", lambda: self._copy(path))
        menu.addAction("Send path to the focused pane",
                       lambda: self.inject_path.emit(path, False))
        menu.addAction("Send @path to the focused pane",
                       lambda: self.inject_path.emit(path, True))
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
