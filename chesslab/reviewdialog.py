"""Game review summary: both players side by side, as on Chess.com.

Accuracy on top, then how many moves of each class each side made.
"Start review" jumps to the first move after the book.
"""

from __future__ import annotations

import chess
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import theme
from .engine import move_accuracy
from .review import KINDS, STYLE


class Badge(QWidget):
    """The same coloured disc the board draws, for the table rows."""

    def __init__(self, kind: str, size: int = 22) -> None:
        super().__init__()
        self.kind = kind
        self.setFixedSize(size, size)

    def paintEvent(self, event) -> None:
        style = STYLE[self.kind]
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(style.color))
        rect = QRectF(1, 1, self.width() - 2, self.height() - 2)
        painter.drawEllipse(rect)
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(int(self.height() * (0.45 if len(style.symbol) > 1 else 0.55)))
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, style.symbol)
        painter.end()


class ReviewDialog(QDialog):
    start_review = Signal(int)  # ply to jump to

    def __init__(self, names: tuple[str, str], kinds: list[tuple[bool, str]],
                 losses: list[tuple[bool, float]], first_ply: int,
                 parent: QWidget | None = None) -> None:
        """kinds / losses: (white_moved, value) for every analysed move."""
        super().__init__(parent)
        self.setWindowTitle("Game review")
        self.setStyleSheet(theme.QSS)
        self._first_ply = first_ply

        layout = QVBoxLayout(self)
        margin = theme.gap(5)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(theme.gap(3))

        title = QLabel("GAME REVIEW")
        title.setObjectName("heading")
        layout.addWidget(title)

        grid = QGridLayout()
        grid.setHorizontalSpacing(theme.gap(4))
        grid.setVerticalSpacing(theme.gap(1))
        grid.setColumnStretch(1, 1)

        # header: names and accuracy
        for column, (white, name) in enumerate(((True, names[0]), (False, names[1])), 2):
            label = QLabel(name)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setObjectName("status")
            grid.addWidget(label, 0, column)

            side = [loss for moved, loss in losses if moved == white]
            accuracy = (sum(move_accuracy(x) for x in side) / len(side)) if side else None
            value = QLabel(f"{accuracy:.1f}" if accuracy is not None else "—")
            value.setAlignment(Qt.AlignmentFlag.AlignCenter)
            value.setFont(theme.tabular(self.font(), 24, bold=True))
            value.setObjectName("accuracy")
            value.setStyleSheet(
                "QLabel#accuracy { background: %s; color: %s; border-radius: 6px;"
                " padding: 6px 14px; }"
                % (("#ece9e2", "#1b1a16") if white else ("#3d3a33", "#ece9e2"))
            )
            grid.addWidget(value, 1, column)
        accuracy_label = QLabel("Accuracy")
        accuracy_label.setObjectName("status")
        grid.addWidget(accuracy_label, 1, 0, 1, 2)

        counts = {chess.WHITE: {}, chess.BLACK: {}}
        for white, kind in kinds:
            if kind:
                counts[white][kind] = counts[white].get(kind, 0) + 1

        bold = theme.tabular(self.font(), 13, bold=True)
        for row, kind in enumerate(KINDS, 2):
            style = STYLE[kind]
            grid.addWidget(Badge(kind), row, 0)
            name = QLabel(style.label)
            grid.addWidget(name, row, 1)
            for column, white in ((2, chess.WHITE), (3, chess.BLACK)):
                number = counts[white].get(kind, 0)
                cell = QLabel(str(number))
                cell.setAlignment(Qt.AlignmentFlag.AlignCenter)
                cell.setFont(bold)
                if number:
                    cell.setStyleSheet(f"color: {style.color};")
                else:
                    cell.setStyleSheet(f"color: {theme.TEXT_FAINT.name()};")
                grid.addWidget(cell, row, column)
        layout.addLayout(grid)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        buttons.addWidget(close_button)
        start = QPushButton("Start review")
        start.setObjectName("primary")
        start.setDefault(True)
        start.clicked.connect(self._start)
        buttons.addWidget(start)
        layout.addLayout(buttons)

    def _start(self) -> None:
        self.start_review.emit(self._first_ply)
        self.accept()
