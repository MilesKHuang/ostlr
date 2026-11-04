"""The session row, painted instead of text-dumped.

The user's complaint was exact: "很难一眼看清楚每个 Session 状态". A row of plain
text with one colour cannot answer "who is blocked, who is heavy" in a glance, so
each row is drawn:

    | [glyph] agent   blocked 4m   what it asked ...        [>_] [x] |
    | CPU  |####################--------------------------|   46%     |
    | MEM  |####--------------------------------------------|   313 MB  |
    | GPU  |##----------------------------------------------|   9%      |

Three separate gauge lines, not three bars squeezed onto one: with at most five
panes there is height to spend, and a bar whose length is comparable carries the
number even before the digits are read. GPU is drawn only when something was
measured - an absent line is the honest shape of "no data", not a 0% bar.

The two icons on the right are the row's only actions: open a terminal in its
directory, and close the session. They are painted here and hit-tested by
`action_at()` from the same rects, so a click cannot land on the other icon.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from PySide6.QtCore import QPointF, QRectF, QSize, Qt
from PySide6.QtGui import QColor, QFont, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QStyle, QStyledItemDelegate

from .. import view_model
from . import gauges as gauges_mod
from . import icons as icons_mod
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

# Geometry in one place: the painter, the size hint and the hit test all read
# these, which is the only way a click stays on the icon the eye saw.
PAD_TOP = 6
PAD_X = 14
IDENTITY_H = 22
GAUGE_H = 19
LABEL_W = 38
VALUE_W = 92
BAR_H = 8

# The row's actions, left to right, in the order they are drawn.
ACTIONS = ("terminal", "close")


def _percent(value: Optional[float]) -> str:
    return "-" if value is None else "%d%%" % int(round(float(value)))


def gauge_lines(row: Dict[str, Any]) -> list:
    """(label, value, caption) per line, in the order they are drawn.

    `gpu` missing means the machine or the driver reported nothing: the line is
    not drawn at all, and the row is one line shorter. Never a 0% bar.
    """
    cpu = row.get("cpu")
    gpu = row.get("gpu")
    lines = [("CPU", cpu, _percent(cpu)),
             # Task Manager's process list shows bytes, not a percentage of 64 GB:
             # a 200 MB session would otherwise read "0%" for ever.
             ("MEM", row.get("mem"), gauges_mod.human_mb(row.get("rss")))]
    if gpu is not None:
        lines.append(("GPU", gpu, _percent(gpu)))
    return lines


def height_for(row: Optional[Dict[str, Any]] = None) -> int:
    """How tall this row is: the gauge lines that exist decide it."""
    row = row or {}
    return PAD_TOP * 2 + IDENTITY_H + GAUGE_H * len(gauge_lines(row))


def action_rects(rect: QRectF) -> list:
    """The two action slots inside a row rect, left to right."""
    band = QRectF(rect.left(), rect.top(), rect.width(), IDENTITY_H)
    return icons_mod.right_aligned(band, len(ACTIONS))


def action_at(rect: QRectF, point: QPointF) -> Optional[str]:
    """Which action a click hit ('terminal' / 'close' / None).

    Pure geometry, tested without a painter.
    """
    band = QRectF(rect.left(), rect.top(), rect.width(), IDENTITY_H)
    index = icons_mod.icon_at(band, point, len(ACTIONS))
    return ACTIONS[index] if index is not None else None


def gauge_columns(rect: QRectF) -> Dict[str, QRectF]:
    """The three columns of a gauge line: label, bar, right-aligned value.

    The bar is whatever is LEFT between the label and the value, so it grows with
    the window instead of starting at a guessed offset - the defect that put a
    bar on top of "mem 313 MB" when that offset was 52 px and the caption was
    wider than the guess.
    """
    left = rect.left() + PAD_X
    value_right = rect.right() - PAD_X
    bar_left = left + LABEL_W + 8
    bar_right = max(bar_left, value_right - VALUE_W - 10)
    return {"label": QRectF(left, rect.top(), LABEL_W, GAUGE_H),
            "bar": QRectF(bar_left, rect.top(), bar_right - bar_left, BAR_H),
            "value": QRectF(value_right - VALUE_W, rect.top(), VALUE_W, GAUGE_H)}


def _bar(painter: QPainter, rect: QRectF, fraction: float, colour: QColor) -> None:
    painter.setPen(Qt.NoPen)
    painter.setBrush(style.colour("row"))
    painter.drawRoundedRect(rect, 3, 3)
    width = max(0.0, min(1.0, fraction)) * rect.width()
    if width > 0.5:
        painter.setBrush(colour)
        painter.drawRoundedRect(QRectF(rect.left(), rect.top(), width, rect.height()), 3, 3)


class RowDelegate(QStyledItemDelegate):
    """Four lines at most: identity, then CPU, MEM and (when measured) GPU."""

    def sizeHint(self, option, index) -> QSize:  # noqa: N802
        row: Dict[str, Any] = index.data(Qt.UserRole) or {}
        # Width 0: the row takes the view's width, whatever it is. Answering with
        # the current rect made the list remember the width it had during the
        # first layout and grow a horizontal scrollbar for a row nobody can scroll.
        return QSize(0, height_for(row))

    def paint(self, painter: QPainter, option, index) -> None:  # noqa: N802
        row: Dict[str, Any] = index.data(Qt.UserRole) or {}
        painter.save()
        painter.setRenderHint(QPainter.Antialiasing, True)

        rect = QRectF(option.rect).adjusted(0, 1, 0, -1)
        selected = bool(option.state & QStyle.State_Selected)
        hovered = bool(option.state & QStyle.State_MouseOver)

        painter.setPen(Qt.NoPen)
        painter.setBrush(style.colour("row_selected" if selected else "panel"))
        painter.drawRoundedRect(rect, 5, 5)

        state = str(row.get("state") or "unknown")
        colour = style.colour(STATE_COLOUR.get(state, "fg_dim"))

        # the state bar: the first thing the eye catches
        painter.setBrush(colour)
        painter.drawRoundedRect(QRectF(rect.left() + 2, rect.top() + 3, 4,
                                       rect.height() - 6), 2, 2)

        left = rect.left() + PAD_X
        top = rect.top() + PAD_TOP

        # --- identity: glyph, agent, state and its age, then what it asked ---
        font = QFont(option.font)
        font.setPointSize(max(10, font.pointSize()))
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(colour))
        painter.drawText(QRectF(left, top, 20, IDENTITY_H),
                         Qt.AlignVCenter | Qt.AlignLeft, GLYPH.get(state, "\u25cc"))

        painter.setPen(QPen(style.colour("fg")))
        metrics = QFontMetrics(font)
        agent = str(row.get("agent") or "?")
        agent_width = min(metrics.horizontalAdvance(agent) + 8, rect.width() * 0.42)
        painter.drawText(QRectF(left + 22, top, agent_width, IDENTITY_H),
                         Qt.AlignVCenter | Qt.AlignLeft, agent)

        small = QFont(option.font)
        small.setPointSize(max(9, small.pointSize() - 1))
        small.setBold(False)
        painter.setFont(small)
        small_metrics = QFontMetrics(small)

        # "blocked 4m" - the age is the number a waiting human wants first.
        word = view_model.state_word(row)
        age = view_model.format_elapsed(row.get("age_s"))
        state_text = ("%s %s" % (word, age)) if age else word
        state_width = small_metrics.horizontalAdvance(state_text) + 6
        painter.setPen(QPen(colour))
        state_left = left + 22 + agent_width + 10
        painter.drawText(QRectF(state_left, top, state_width, IDENTITY_H),
                         Qt.AlignVCenter | Qt.AlignLeft, state_text)

        # what it is asking, dimmed, in whatever space is left before the icons
        icons_left = action_rects(rect)[0].left()
        msg_left = state_left + state_width + 10
        msg_room = int(icons_left - msg_left - 10)
        message = str(row.get("msg") or "")
        if message and msg_room > 40:
            painter.setPen(QPen(style.colour("fg_dim")))
            painter.drawText(QRectF(msg_left, top, msg_room, IDENTITY_H),
                             Qt.AlignVCenter | Qt.AlignLeft,
                             small_metrics.elidedText(message, Qt.ElideRight, msg_room))

        # --- the row's two actions ---
        icon_role = "fg" if (hovered or selected) else "fg_dim"
        for slot, name in zip(action_rects(rect), ACTIONS):
            icons_mod.paint(painter, name, slot, icon_role)

        # --- one gauge per line ---
        line_top = top + IDENTITY_H
        columns = gauge_columns(rect)
        for label, value, caption in gauge_lines(row):
            painter.setFont(small)
            painter.setPen(QPen(style.colour("fg_dim")))
            painter.drawText(QRectF(columns["label"].left(), line_top, LABEL_W, GAUGE_H),
                             Qt.AlignVCenter | Qt.AlignLeft, label)
            painter.drawText(QRectF(columns["value"].left(), line_top, VALUE_W, GAUGE_H),
                             Qt.AlignVCenter | Qt.AlignRight, caption)

            bar = columns["bar"]
            if bar.width() >= 24:
                fraction = 0.0 if value is None else max(0.0, min(1.0, float(value) / 100.0))
                tint = style.colour("danger") if (value or 0) > 60 else style.colour("accent")
                _bar(painter, QRectF(bar.left(), line_top + (GAUGE_H - BAR_H) / 2.0,
                                     bar.width(), BAR_H), fraction, tint)
            line_top += GAUGE_H

        painter.restore()
