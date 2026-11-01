"""Resource gauges: numbers are for scripts, gauges are for eyes (baseline 3.1).

A GUI that prints `212MB` makes the user do the comparison in their head. These
widgets answer the question the row is actually asked - "is anything eating this
machine, and who?" - with a bar and a colour, while the exact number stays in the
tooltip for when it matters.
"""

from __future__ import annotations

from typing import List, Sequence

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import style


def human_mb(mem_bytes: float) -> str:
    value = float(mem_bytes or 0)
    if value >= 1024 ** 3:
        return "%.1f GB" % (value / 1024 ** 3)
    if value >= 1024 ** 2:
        return "%.0f MB" % (value / 1024 ** 2)
    return "%d KB" % max(0, int(value / 1024))


class Gauge(QWidget):
    """One horizontal bar: a value against a ceiling, with a caption.

    Deliberately dumb: it paints what it is given. No polling, no thresholds
    hidden inside - the caller decides what "full" means, so the widget cannot
    quietly disagree with the number in the session list.
    """

    def __init__(self, caption: str = "", ceiling: float = 1.0,
                 role: str = "accent", parent=None) -> None:
        super().__init__(parent)
        self.caption = caption
        self.ceiling = max(1e-9, float(ceiling))
        self.value = 0.0
        self.role = role
        self.setMinimumWidth(150)
        self.setFixedHeight(28)
        self.setSizePolicy(QSizePolicy.Preferred, QSizePolicy.Fixed)
        self.setToolTip(caption)

    def set_value(self, value: float, ceiling: float = None, caption: str = None,
                  role: str = None) -> None:
        self.value = max(0.0, float(value or 0))
        if ceiling is not None:
            self.ceiling = max(1e-9, float(ceiling))
        if caption is not None:
            self.caption = caption
            self.setToolTip(caption)
        if role is not None:
            self.role = role
        self.update()

    def fraction(self) -> float:
        return max(0.0, min(1.0, self.value / self.ceiling))

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = self.rect().adjusted(0, 0, -1, -1)

        # Track
        painter.setPen(Qt.NoPen)
        painter.setBrush(style.colour("row"))
        painter.drawRoundedRect(QRectF(rect), 5.0, 5.0)

        # Fill
        width = rect.width() * self.fraction()
        if width > 1:
            painter.setBrush(style.colour(self.role))
            painter.drawRoundedRect(QRectF(rect.left(), rect.top(), width, rect.height()), 5.0, 5.0)

        # Caption: centred, readable on either side of the fill edge
        painter.setPen(QPen(style.colour("fg")))
        font = self.font()
        font.setPointSize(max(8, font.pointSize()))
        painter.setFont(font)
        painter.drawText(rect.adjusted(8, 0, -8, 0), Qt.AlignVCenter | Qt.AlignLeft, self.caption)
        painter.end()


class GaugeRow(QWidget):
    """The three gauges the top bar needs: waiting / running / memory."""

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        from PySide6.QtWidgets import QHBoxLayout

        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self.waiting = Gauge("waiting 0", ceiling=3, role="warn", parent=self)
        self.running = Gauge("running 0", ceiling=6, role="ok", parent=self)
        self.memory = Gauge("memory", ceiling=2 * 1024 ** 3, role="accent", parent=self)
        for gauge in (self.waiting, self.running, self.memory):
            row.addWidget(gauge)

    def update_from(self, header: dict, rows: Sequence[dict]) -> None:
        waiting = int(header.get("waiting") or 0)
        running = int(header.get("running") or 0)
        memory = sum(int(row.get("mem_bytes") or 0) for row in rows)
        # Ceilings scale with what is actually there, so the bar means "share of
        # the sessions I have", not "share of an arbitrary constant".
        self.waiting.set_value(waiting, ceiling=max(3, waiting + 1),
                               caption="waiting %d" % waiting)
        self.running.set_value(running, ceiling=max(6, running + 1),
                               caption="running %d" % running)
        self.memory.set_value(memory, ceiling=max(2 * 1024 ** 3, memory),
                              caption="memory %s" % human_mb(memory))
        self.memory.setToolTip("process trees of %d session(s): %s"
                              % (len(rows), human_mb(memory)))


class RowBars(QWidget):
    """Per-session memory bars, in the order the list shows them.

    A second, smaller view of the same data on purpose: the list row is about
    identity and state, this is about "who is heavy", and mixing the two in one
    row is what made the earlier table unreadable.
    """

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.entries: List[tuple] = []
        self.setMinimumHeight(60)

    def set_entries(self, rows: Sequence[dict]) -> None:
        self.entries = [(str(row.get("agent") or "?"), str(row.get("project") or "?"),
                         int(row.get("mem_bytes") or 0)) for row in rows][:8]
        self.update()

    def paintEvent(self, event) -> None:  # noqa: N802
        if not self.entries:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        peak = max((mem for _a, _p, mem in self.entries), default=1) or 1
        row_height = max(14, int(self.height() / max(1, len(self.entries))))
        metrics = QFontMetrics(self.font())
        for index, (agent, project, mem) in enumerate(self.entries):
            top = index * row_height
            label = "%s / %s" % (agent, project)
            painter.setPen(QPen(style.colour("fg_dim")))
            painter.drawText(0, top, 190, row_height, Qt.AlignVCenter | Qt.AlignLeft,
                             metrics.elidedText(label, Qt.ElideRight, 185))
            bar_left = 196
            width = max(1, self.width() - bar_left - 70)
            painter.setPen(Qt.NoPen)
            painter.setBrush(style.colour("row"))
            painter.drawRoundedRect(QRectF(bar_left, top + 3, width, row_height - 7), 3, 3)
            fraction = mem / peak
            painter.setBrush(style.colour("warn") if fraction > 0.66 else style.colour("accent"))
            painter.drawRoundedRect(QRectF(bar_left, top + 3, width * fraction, row_height - 7), 3, 3)
            painter.setPen(QPen(style.colour("fg_dim")))
            painter.drawText(bar_left + width + 6, top, 64, row_height,
                             Qt.AlignVCenter | Qt.AlignRight, human_mb(mem))
        painter.end()
