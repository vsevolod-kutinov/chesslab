"""Captured pieces next to a player's name, drawn with the board's own piece set.

Same-kind pieces overlap in a group, groups stand apart, and the material lead
follows as "+N" - the way Chess.com shows it.
"""

from __future__ import annotations

import chess
from PySide6.QtCore import QRectF, QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from . import pieces, theme

SIZE = 24      # one piece
OVERLAP = 13   # step between pieces of one kind
GROUP_GAP = 3  # extra space between kinds
TEXT_GAP = 6


_CACHE: dict[tuple[chess.Piece, float], QPixmap] = {}


def _outlined(piece: chess.Piece, ratio: float) -> QPixmap:
    """The piece with a thin light rim: black pieces vanish on the dark page without it."""
    key = (piece, ratio)
    if key not in _CACHE:
        side = round(SIZE * ratio)
        art = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        art.fill(Qt.GlobalColor.transparent)
        p = QPainter(art)
        pieces.renderer(piece).render(p, QRectF(0, 0, side, side))
        p.end()
        rim = QImage(art)
        p = QPainter(rim)
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_SourceIn)
        p.fillRect(rim.rect(), QColor(236, 233, 226, 150))
        p.end()
        out = QImage(side, side, QImage.Format.Format_ARGB32_Premultiplied)
        out.fill(Qt.GlobalColor.transparent)
        p = QPainter(out)
        step = max(1, round(ratio))
        for dx, dy in ((-step, 0), (step, 0), (0, -step), (0, step)):
            p.drawImage(dx, dy, rim)
        p.drawImage(0, 0, art)
        p.end()
        pixmap = QPixmap.fromImage(out)
        pixmap.setDevicePixelRatio(ratio)
        _CACHE[key] = pixmap
    return _CACHE[key]


class CapturedPieces(QWidget):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pieces: list[chess.Piece] = []
        self._points = 0
        self.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)

    def set_material(self, kinds: list[int], color: bool, points: int) -> None:
        """kinds - captured piece types; color - colour of those pieces."""
        self._pieces = [chess.Piece(kind, color) for kind in kinds]
        self._points = points
        self.updateGeometry()
        self.update()

    def _text(self) -> str:
        return f"+{self._points}" if self._points else ""

    def _positions(self) -> list[float]:
        """Left edge of every piece: same kinds overlap, a new kind starts clear of the last."""
        out: list[float] = []
        for index, piece in enumerate(self._pieces):
            if not out:
                out.append(0.0)
            elif piece.piece_type == self._pieces[index - 1].piece_type:
                out.append(out[-1] + OVERLAP)
            else:
                out.append(out[-1] + SIZE - 4 + GROUP_GAP)  # svg pieces carry ~2 px padding per side
        return out

    def _pieces_width(self) -> float:
        positions = self._positions()
        return positions[-1] + SIZE if positions else 0.0

    def sizeHint(self) -> QSize:
        width = self._pieces_width()
        if self._text():
            width += (TEXT_GAP if self._pieces else 0) + self.fontMetrics().horizontalAdvance(self._text())
        return QSize(int(width) + 1, SIZE)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        top = (self.height() - SIZE) / 2
        ratio = self.devicePixelRatioF()
        for piece, x in zip(self._pieces, self._positions()):
            image = _outlined(piece, ratio) if piece.color == chess.BLACK else None
            if image is None:
                pieces.renderer(piece).render(painter, QRectF(x, top, SIZE, SIZE))
            else:
                painter.drawPixmap(QRectF(x, top, SIZE, SIZE), image, QRectF(image.rect()))
        if self._text():
            left = self._pieces_width() + (TEXT_GAP if self._pieces else 0)
            painter.setPen(QPen(theme.TEXT_MUTED))
            painter.drawText(QRectF(left, 0, self.width() - left, self.height()),
                             Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._text())
