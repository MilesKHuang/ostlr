"""Vector icons: drawn from a rect, never shipped as pixels.

Why not an icon font or a set of PNGs: `tests/unit/test_renderer.py` forbids a
font file in the package (the terminal's font is the OS terminal's, and nothing
else may bring one), and a bitmap needs one file per DPI. A pen and a handful of
coordinates work at every scale and every theme.

    paint(painter, "close", rect, "fg_dim")     # into any painter
    icon_button(name, role)                     # a QPushButton with this face
    right_aligned(rect, 2)                      # the pair at a row's right edge

Colours come from `style.colour()`: an icon has no colour of its own, exactly
like a label has none.
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QFont, QIcon, QPainter, QPen, QPixmap

from . import style

# Every name the panel can draw. A name that is not here draws nothing, which is
# a visible bug the first time it happens rather than a silently blank button.
NAMES = ("terminal", "close", "plus", "copy", "refresh", "send", "send_at")


def _pen(painter: QPainter, role: str) -> None:
    pen = QPen(style.colour(role))
    pen.setWidthF(style.ICON_STROKE)
    pen.setCapStyle(Qt.RoundCap)
    pen.setJoinStyle(Qt.RoundJoin)
    painter.setPen(pen)
    painter.setBrush(Qt.NoBrush)


def _p(rect: QRectF, x: float, y: float) -> QPointF:
    """A point in the icon's own 0..1 space."""
    return QPointF(rect.left() + x * rect.width(), rect.top() + y * rect.height())


def paint(painter: QPainter, name: str, rect: QRectF, role: str = "fg_dim") -> None:
    """Draw one icon inside `rect`. The caller keeps the painter's own state."""
    inner = QRectF(rect).adjusted(1.5, 1.5, -1.5, -1.5)
    if inner.width() <= 2 or inner.height() <= 2:
        return
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    _pen(painter, role)

    if name == "terminal":
        painter.drawRoundedRect(QRectF(inner.left(), inner.top(), inner.width(),
                                       inner.height()), 2.5, 2.5)
        painter.drawLine(_p(inner, 0.24, 0.36), _p(inner, 0.40, 0.50))
        painter.drawLine(_p(inner, 0.40, 0.50), _p(inner, 0.24, 0.64))
        painter.drawLine(_p(inner, 0.52, 0.64), _p(inner, 0.76, 0.64))
    elif name == "close":
        painter.drawLine(_p(inner, 0.22, 0.22), _p(inner, 0.78, 0.78))
        painter.drawLine(_p(inner, 0.78, 0.22), _p(inner, 0.22, 0.78))
    elif name == "plus":
        painter.drawLine(_p(inner, 0.5, 0.16), _p(inner, 0.5, 0.84))
        painter.drawLine(_p(inner, 0.16, 0.5), _p(inner, 0.84, 0.5))
    elif name == "copy":
        painter.drawRoundedRect(QRectF(inner.left() + inner.width() * 0.08,
                                       inner.top() + inner.height() * 0.08,
                                       inner.width() * 0.56,
                                       inner.height() * 0.56), 2, 2)
        painter.drawRoundedRect(QRectF(inner.left() + inner.width() * 0.36,
                                       inner.top() + inner.height() * 0.36,
                                       inner.width() * 0.56,
                                       inner.height() * 0.56), 2, 2)
    elif name == "refresh":
        arc = QRectF(inner.left() + 1, inner.top() + 1,
                     inner.width() - 2, inner.height() - 2)
        painter.drawArc(arc, 40 * 16, 280 * 16)
        painter.drawLine(_p(inner, 0.62, 0.02), _p(inner, 0.86, 0.10))
        painter.drawLine(_p(inner, 0.86, 0.10), _p(inner, 0.96, 0.32))
    elif name in ("send", "send_at"):
        left = 0.40 if name == "send_at" else 0.08
        painter.drawLine(_p(inner, left, 0.5), _p(inner, 0.92, 0.5))
        painter.drawLine(_p(inner, 0.68, 0.26), _p(inner, 0.92, 0.5))
        painter.drawLine(_p(inner, 0.92, 0.5), _p(inner, 0.68, 0.74))
        if name == "send_at":
            font = QFont(painter.font())
            font.setBold(True)
            font.setPixelSize(max(7, int(inner.height() * 0.78)))
            painter.setFont(font)
            painter.drawText(QRectF(inner.left() - 1, inner.top(), inner.width() * 0.38,
                                    inner.height()),
                             Qt.AlignCenter, style.GLYPH_AT)
    painter.restore()


def right_aligned(rect: QRectF, count: int,
                  size: Optional[int] = None) -> List[QRectF]:
    """`count` square slots at the right edge of `rect`, right to left.

    One function for painting and one for hit testing, so a click can never land
    on a different icon than the one the eye saw.
    """
    side = float(size or style.ICON_PX)
    gap = float(style.ICON_GAP)
    pad = float(style.ICON_PAD)
    top = rect.top() + (rect.height() - side) / 2.0
    boxes: List[QRectF] = []
    right = rect.right() - pad
    for _index in range(max(0, int(count))):
        boxes.append(QRectF(right - side, top, side, side))
        right -= side + gap
    boxes.reverse()
    return boxes


def icon_at(rect: QRectF, point: QPointF, count: int,
            size: Optional[int] = None) -> Optional[int]:
    """Which slot of `right_aligned(rect, count)` holds `point`, or None."""
    for index, box in enumerate(right_aligned(rect, count, size)):
        if box.adjusted(-2, -2, 2, 2).contains(point):
            return index
    return None


def pixmap(name: str, role: str = "fg", px: int = 20,
           background: str = "") -> QPixmap:
    """One icon as a pixmap, for a QPushButton or a QIcon."""
    canvas = QPixmap(px, px)
    canvas.fill(style.colour(background) if background else Qt.transparent)
    painter = QPainter(canvas)
    try:
        paint(painter, name, QRectF(0, 0, px, px), role)
    finally:
        painter.end()
    return canvas


def icon(name: str, role: str = "fg", px: int = 20) -> QIcon:
    return QIcon(pixmap(name, role, px))


def button_face(name: str, role: str = "fg",
                px: Optional[int] = None) -> Tuple[QIcon, str]:
    """The icon and an empty label: an icon button keeps its word for the tooltip."""
    return icon(name, role, px or style.ICON_PX + 4), ""
