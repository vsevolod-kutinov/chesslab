"""Evaluation graph over the moves of a game."""

from __future__ import annotations

from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import theme

CP_RANGE = 600  # beyond this the advantage is clamped to the graph edge

TAG_COLORS = {
    "blunder": QColor("#d1503f"),
    "mistake": QColor("#d98f3a"),
    "inaccuracy": QColor("#c9b24a"),
}


class EvalGraph(QWidget):
    ply_clicked = Signal(int)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedHeight(84)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

        self.points: list[int] = []  # evaluation in centipawns, from White's view
        self.tags: dict[int, str] = {}
        self.current = 0
        self.flipped = False  # if Black is at the bottom, flip the graph too

    def set_data(self, points: list[int], tags: dict[int, str]) -> None:
        self.points = points
        self.tags = tags
        self.update()

    def clear(self) -> None:
        self.points = []
        self.tags = {}
        self.update()

    def set_current(self, ply: int) -> None:
        self.current = ply
        self.update()

    def set_flipped(self, flipped: bool) -> None:
        self.flipped = flipped
        self.update()

    # --- geometry --------------------------------------------------------

    def _area(self):
        """Draw within contentsRect, not rect: otherwise margins are ignored
        and the graph sticks out wider than the board."""
        return self.contentsRect()

    def _x(self, ply: int) -> float:
        area = self._area()
        if len(self.points) < 2:
            return float(area.left())
        return area.left() + ply / (len(self.points) - 1) * area.width()

    def _y(self, cp: int) -> float:
        value = max(-CP_RANGE, min(CP_RANGE, cp))
        if self.flipped:
            value = -value
        area = self._area()
        middle = area.top() + area.height() / 2
        return middle - value / CP_RANGE * (area.height() / 2)

    def mousePressEvent(self, event) -> None:
        if len(self.points) < 2:
            return
        area = self._area()
        ratio = max(0.0, min(1.0,
                             (event.position().x() - area.left()) / max(1, area.width())))
        self.ply_clicked.emit(round(ratio * (len(self.points) - 1)))

    # --- painting --------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self._area(), theme.INSET)

        if len(self.points) < 2:
            painter.setPen(QPen(theme.TEXT_MUTED))
            painter.drawText(
                self._area(), Qt.AlignmentFlag.AlignCenter,
                "game not analysed"
            )
            painter.end()
            return

        area = self._area()
        middle = area.top() + area.height() / 2

        # area under the curve: on top - the advantage of the side at the top of the board
        path = QPainterPath()
        path.moveTo(area.left(), middle)
        for ply, cp in enumerate(self.points):
            path.lineTo(self._x(ply), self._y(cp))
        path.lineTo(area.right(), middle)
        path.closeSubpath()

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#41503a"))
        painter.drawPath(path)

        painter.setPen(QPen(theme.BORDER, 1))
        painter.drawLine(QPointF(area.left(), middle), QPointF(area.right(), middle))

        curve = QPainterPath()
        curve.moveTo(area.left(), self._y(self.points[0]))
        for ply, cp in enumerate(self.points):
            curve.lineTo(self._x(ply), self._y(cp))
        painter.setPen(QPen(QColor("#a8bf90"), 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawPath(curve)

        # mistakes as dots
        painter.setPen(Qt.PenStyle.NoPen)
        for ply, tag in self.tags.items():
            color = TAG_COLORS.get(tag)
            if color is None or ply >= len(self.points):
                continue
            painter.setBrush(color)
            radius = 3.5 if tag == "blunder" else 2.5
            painter.drawEllipse(
                QPointF(self._x(ply), self._y(self.points[ply])), radius, radius
            )

        # current position
        x = self._x(self.current)
        painter.setPen(QPen(theme.ACCENT, 1.4))
        painter.drawLine(QPointF(x, area.top()), QPointF(x, area.bottom()))
        painter.end()
