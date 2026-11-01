"""Qt look and feel: one stylesheet, colour roles, no hex in the views.

The same discipline the tkinter build had, for the same reason: a colour written
inline is a colour nobody can restyle, and the state colours are the product's
legend.
"""

from __future__ import annotations

from typing import Dict

from PySide6.QtGui import QColor

# Roles, not colours. A view asks for a role.
ROLES: Dict[str, str] = {
    "bg": "#14171c",
    "panel": "#181c22",
    "row": "#1d222a",
    "row_selected": "#26303c",
    "fg": "#e4e6ea",
    "fg_dim": "#98a0ac",
    "border": "#2b323c",
    "accent": "#4f9cf0",
    "ok": "#7ec97e",
    "warn": "#e2c07c",
    "danger": "#f06a6a",
}

STATUS_ROLE = {
    "blocked": "warn",
    "error": "danger",
    "done": "ok",
    "working": "fg",
    "idle": "fg_dim",
    "unknown": "fg_dim",
}


def colour(role: str) -> QColor:
    return QColor(ROLES.get(role, ROLES["fg"]))


def stylesheet() -> str:
    r = ROLES
    template = """
    QMainWindow, QWidget { background: $bg; color: $fg; }
    QLabel { color: $fg; }
    QPushButton {
        background: $row; color: $fg; border: 1px solid $border;
        border-radius: 6px; padding: 6px 12px;
    }
    QPushButton:hover { background: $row_selected; }
    QPushButton:pressed { background: $accent; color: $bg; }
    QListWidget {
        background: $panel; border: 1px solid $border; border-radius: 6px;
        padding: 4px; outline: none;
    }
    QListWidget::item { padding: 6px 8px; border-radius: 4px; }
    QListWidget::item:selected { background: $row_selected; }
    QTabWidget::pane { border: 1px solid $border; border-radius: 6px; }
    QTabBar::tab {
        background: $row; color: $fg_dim; padding: 6px 12px;
        border: 1px solid $border; border-bottom: none;
        border-top-left-radius: 6px; border-top-right-radius: 6px;
    }
    QTabBar::tab:selected { background: $panel; color: $fg; }
    QComboBox, QLineEdit {
        background: $panel; color: $fg; border: 1px solid $border;
        border-radius: 6px; padding: 5px 8px;
    }
    QComboBox QAbstractItemView { background: $panel; color: $fg; }
    QSplitter::handle { background: $border; }
    """
    # `$role` and not `%()` or `{}`: Qt style sheets are full of braces, so a
    # format string fights the syntax. A plain replace cannot.
    out = template
    # Longest key first: "$row" is a prefix of "$row_selected", and a naive
    # replace turns the latter into "#1d222a_selected" - which Qt then rejects as
    # an unknown colour name.
    for role in sorted(r, key=len, reverse=True):
        out = out.replace("$" + role, r[role])
    return out
