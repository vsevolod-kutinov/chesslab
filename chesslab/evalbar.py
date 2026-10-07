"""Evaluation bar next to the board."""

from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QRectF, Qt, QVariantAnimation
from PySide6.QtGui import QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from . import theme

WIDTH = 30
RADIUS = 4


class EvalBar(QWidget):
    def __init__(self, board=None, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setFixedWidth(WIDTH)
        # take height and top of the bar from the board: the board widget can be
        # taller than the board itself, and the bar would stick out past its edges
        self._board = board
        self._cp: int | None = 0
        self._mate: int | None = None
        self.flipped = False
        # +1 - label from White's point of view, -1 - from Black's.
        # Does not affect the bar itself: its direction is set by flipped.
        self.pov_sign = 1

        # the boundary glides to the new evaluation instead of jumping at every depth
        self._shown = 0.5
        self._anim = QVariantAnimation(self)
        self._anim.setDuration(260)
        self._anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._anim.valueChanged.connect(self._on_anim)

    def set_score(self, cp: int | None, mate: int | None) -> None:
        self._cp, self._mate = cp, mate
        target = self._white_fraction()
        self._anim.stop()
        self._anim.setStartValue(self._shown)
        self._anim.setEndValue(target)
        self._anim.start()
        self.update()

    def _on_anim(self, value) -> None:
        self._shown = float(value)
        self.update()

    def set_flipped(self, flipped: bool) -> None:
        self.flipped = flipped
        self.update()

    def _white_fraction(self) -> float:
        """Share of the bar for White, 0..1."""
        if self._mate is not None:
            return 1.0 if self._mate > 0 else 0.0
        if self._cp is None:
            return 0.5
        # logistic curve: +-400 centipawns is already almost the edge
        return 1.0 / (1.0 + math.exp(-self._cp / 320.0))

    def set_pov(self, sign: int) -> None:
        self.pov_sign = 1 if sign >= 0 else -1
        self.update()

    def _label(self) -> str:
        if self._mate is not None:
            return f"M{abs(self._mate)}"
        if self._cp is None:
            return "—"
        value = abs(self._cp) / 100
        # no sign: who leads is visible from the label colour anyway
        return f"{value:.0f}" if value >= 10 else f"{value:.1f}"

    def _bar_rect(self) -> QRectF:
        if self._board is not None:
            r = self._board.board_rect()
            return QRectF(0, r.top(), self.width(), r.height())
        return QRectF(0, 0, self.width(), self.height())

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        bar = self._bar_rect()
        x, top, w, h = bar.x(), bar.y(), bar.width(), bar.height()
        white_h = h * self._shown

        clip = QPainterPath()
        clip.addRoundedRect(bar, RADIUS, RADIUS)
        painter.setClipPath(clip)

        # White at the bottom unless the board is flipped
        if self.flipped:
            white_rect = QRectF(x, top, w, white_h)
            black_rect = QRectF(x, top + white_h, w, h - white_h)
        else:
            black_rect = QRectF(x, top, w, h - white_h)
            white_rect = QRectF(x, top + h - white_h, w, white_h)

        painter.fillRect(black_rect, theme.EVAL_BLACK)
        painter.fillRect(white_rect, theme.EVAL_WHITE)

        # "equal" tick: shows how far the boundary is from the middle
        mid = top + h / 2
        painter.setPen(QPen(theme.EVAL_MID, 2))
        painter.drawLine(int(x), int(mid), int(x + w * 0.3), int(mid))
        painter.drawLine(int(x + w * 0.7), int(mid), int(x + w), int(mid))

        # label at the outer edge of the half that is leading
        white_leads = (self._mate > 0) if self._mate is not None else self._shown >= 0.5
        text_rect = white_rect if white_leads else black_rect
        at_bottom = white_leads != self.flipped
        painter.setPen(QPen(theme.EVAL_BLACK if white_leads else theme.EVAL_WHITE))
        painter.setFont(theme.tabular(self.font(), 9, bold=True))
        label = self._label()
        # does not fit - shrink the font instead of clipping the text
        fm = painter.fontMetrics()
        if fm.horizontalAdvance(label) > w - 4:
            painter.setFont(theme.tabular(self.font(), 7.5, bold=True))
        align = Qt.AlignmentFlag.AlignHCenter | (
            Qt.AlignmentFlag.AlignBottom if at_bottom else Qt.AlignmentFlag.AlignTop
        )
        painter.drawText(text_rect.adjusted(0, 6, 0, -6), align, label)

        painter.setClipping(False)
        painter.setPen(QPen(theme.BOARD_EDGE, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRoundedRect(bar.adjusted(0.5, 0.5, -0.5, -0.5), RADIUS, RADIUS)
        painter.end()
