"""Navigation icons drawn with QPainter.

Unicode glyphs like ⏮ / ⏭ fall back to colour emoji on many systems,
so the four move-navigation buttons get their own vector icons.
"""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt
from PySide6.QtGui import QIcon, QPainter, QPainterPath, QPixmap

from . import theme

SIZE = 16


def _triangle(path: QPainterPath, x: float, pointing_left: bool) -> None:
    """Triangle 6 px wide, its flat side at x."""
    tip = x - 6 if pointing_left else x + 6
    path.moveTo(x, 3)
    path.lineTo(tip, SIZE / 2)
    path.lineTo(x, SIZE - 3)
    path.closeSubpath()


def nav_icon(kind: str) -> QIcon:
    """kind: 'start', 'back', 'forward' or 'end'."""
    icon = QIcon()
    for color, mode in ((theme.TEXT, QIcon.Mode.Normal),
                        (theme.TEXT_FAINT, QIcon.Mode.Disabled)):
        for scale in (1, 2):
            pixmap = QPixmap(SIZE * scale, SIZE * scale)
            pixmap.setDevicePixelRatio(scale)
            pixmap.fill(Qt.GlobalColor.transparent)
            painter = QPainter(pixmap)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(color)

            path = QPainterPath()
            if kind == "back":
                _triangle(path, 11, True)
            elif kind == "forward":
                _triangle(path, 5, False)
            elif kind == "start":
                path.addRect(QRectF(3, 3, 2, SIZE - 6))
                _triangle(path, 12, True)
            elif kind == "end":
                path.addRect(QRectF(SIZE - 5, 3, 2, SIZE - 6))
                _triangle(path, 4, False)
            painter.drawPath(path)
            painter.end()
            icon.addPixmap(pixmap, mode)
    return icon



def side_icon(white: bool) -> QIcon:
    """A piece-coloured dot: which side the player had."""
    icon = QIcon()
    for scale in (1, 2):
        pixmap = QPixmap(12 * scale, 12 * scale)
        pixmap.setDevicePixelRatio(scale)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(theme.TEXT_MUTED)
        painter.setBrush(theme.EVAL_WHITE if white else theme.BG)
        painter.drawEllipse(QRectF(1.5, 1.5, 9, 9))
        painter.end()
        icon.addPixmap(pixmap)
    return icon
