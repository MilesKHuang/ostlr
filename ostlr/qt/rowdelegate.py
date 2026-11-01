"""The session row, painted instead of text-dumped.

The user's complaint was exact: "很难一眼看清楚每个 Session 状态". A row of plain
text with one colour cannot answer "who is blocked, who is heavy" in a glance, so
each row is drawn:

    [colour bar][glyph] agent           state            cpu bar  mem bar
                       project  dir

The bar on the left is the state colour, full row height - the thing the eye
catches first. CPU and memory are small bars with their percentage inside, so the
number and the picture cannot disagree: both come from the same value.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from .. import view_model
from . import gauges as gauges_mod
from . import style

STATE_COLOUR = {
    "blocked": "warn",
    "working": "accent",
    "error": "danger",
    "done": "ok",
    "idle": "fg_dim",
    "unknown": "fg_dim",
}
GLYPH = {"blocked": "\u25cf", "working": "\u25d0", "idle": "\u25cb",
         "done": "\u2713", "error": "\u2715", "unknown": "\u25cc"}


def _bar(painter: QPainter, rect: QRectF, fraction: float, colour: QColor) -> None:
    painter.setPen(Qt.NoPen)
    painter.setBrush(style.colour("row"))
    painter.drawRoundedRect(rect, 3, 3)
    width = max(0.0, min(1.0, fraction)) * rect.width()
    if width > 0.5:
        painter.setBrush(colour)
        painter.drawRoundedRect(QRectF(rect.left(), rect.top(), width, rect.height()), 3, 3)


class RowDelegate(QStyledItemDelegate):
    """Two lines per row: identity on top, workload on the bottom."""

    ROW_HEIGHT = 58

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        return QSize(option.rect.width(), self.ROW_HEIGHT)

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        row: Dict[str, Any] = index.data(Qt.UserRole) or {}
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = option.rect.adjusted(0, 1, 0, -1)
        selected = bool(option.state & QStyle.State_Selected)

        # background
        painter.setPen(Qt.NoPen)
        painter.setBrush(style.colour("row_selected" if selected else "panel"))
        painter.drawRoundedRect(QRectF(rect), 5, 5)

        state = str(row.get("state") or "unknown")
        role = STATE_COLOUR.get(state, "fg_dim")
        colour = style.colour(role)

        # the state bar: the first thing the eye catches
        bar_rect = QRectF(rect.left() + 2, rect.top() + 3, 4, rect.height() - 6)
        painter.setBrush(colour)
        painter.drawRoundedRect(bar_rect, 2, 2)

        left = rect.left() + 16
        top = rect.top() + 6
        line_height = (rect.height() - 12) // 2
        right_pad = 14

        # line 1: glyph + agent (bold) + project (dim, elided in the middle)
        font = QFont(option.font)
        font.setPointSize(max(10, font.pointSize()))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(colour))
        painter.drawText(QRectF(left, top, 20, line_height),
                         Qt.AlignVCenter | Qt.AlignLeft, GLYPH.get(state, "\u25cc"))

        painter.setPen(QPen(style.colour("fg")))
        metrics = QFontMetrics(font)
        agent = str(row.get("agent") or "?")
        agent_width = min(metrics.horizontalAdvance(agent) + 8, rect.width() * 0.5)
        painter.drawText(QRectF(left + 24, top, agent_width, line_height),
                         Qt.AlignVCenter | Qt.AlignLeft, agent)

        small = QFont(option.font)
        small.setPointSize(max(9, small.pointSize() - 1))
        small.setBold(False)
        painter.setFont(small)
        small_metrics = QFontMetrics(small)
        painter.setPen(QPen(style.colour("fg_dim")))
        project = "%s  %s" % (row.get("project") or "?", row.get("cwd") or "")
        project_left = left + 24 + agent_width + 8
        painter.drawText(QRectF(project_left, top, max(20, rect.width() - project_left - right_pad),
                                line_height),
                         Qt.AlignVCenter | Qt.AlignLeft,
                         small_metrics.elidedText(project, Qt.ElideMiddle,
                                                  int(max(20, rect.width() - project_left - right_pad))))

        # line 2: state word, then the two workload bars with room to breathe
        row_top = top + line_height
        painter.setFont(small)
        painter.setPen(QPen(colour))
        painter.drawText(QRectF(left + 24, row_top, 84, line_height),
                         Qt.AlignVCenter | Qt.AlignLeft, view_model.state_word(row))

        bars_left = left + 24 + 92
        bar_width = max(52.0, (rect.width() - bars_left - right_pad - 16) / 3 - 6)
        for index, (value, label) in enumerate(((row.get("cpu"), "cpu"),
                                                (row.get("mem"), "mem"),
                                                (row.get("gpu"), "gpu"))):
            bx = bars_left + index * (bar_width + 12)
            if label == "mem":
                # Task Manager's process list shows bytes, not a percentage of
                # 64 GB: a 200 MB session would otherwise read "0%" for ever.
                caption = gauges_mod.human_mb(row.get("rss"))
            else:
                caption = "-" if value is None else "%d%%" % int(round(value))
            painter.setPen(QPen(style.colour("fg_dim")))
            painter.drawText(QRectF(bx, row_top, bar_width, line_height),
                             Qt.AlignVCenter | Qt.AlignLeft, "%s %s" % (label, caption))
            bar_rect = QRectF(bx + 52, row_top + line_height / 2 - 4,
                              max(20.0, bar_width - 52), 8)
            fraction = 0.0 if value is None else max(0.0, min(1.0, float(value) / 100.0))
            tint = style.colour("danger") if (value or 0) > 60 else style.colour("accent")
            _bar(painter, bar_rect, fraction, tint)

        painter.restore()
