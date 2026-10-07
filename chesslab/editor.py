"""Manual position setup."""

from __future__ import annotations

import chess
from PySide6.QtCore import QSize, Qt, Signal, Slot
from PySide6.QtGui import QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import pieces, theme
from .board import BoardWidget

ICON = 34  # side of a palette button

# what to say about each problem reported by board.status()
PROBLEMS = [
    (chess.STATUS_NO_WHITE_KING, "no white king"),
    (chess.STATUS_NO_BLACK_KING, "no black king"),
    (chess.STATUS_TOO_MANY_KINGS, "more than one king"),
    (chess.STATUS_PAWNS_ON_BACKRANK, "pawns on the first or last rank"),
    (chess.STATUS_TOO_MANY_WHITE_PAWNS, "too many white pawns"),
    (chess.STATUS_TOO_MANY_BLACK_PAWNS, "too many black pawns"),
    (chess.STATUS_TOO_MANY_WHITE_PIECES, "too many white pieces"),
    (chess.STATUS_TOO_MANY_BLACK_PIECES, "too many black pieces"),
    (chess.STATUS_OPPOSITE_CHECK, "the side not on move is in check"),
    (chess.STATUS_INVALID_EP_SQUARE, "invalid en passant square"),
    (chess.STATUS_IMPOSSIBLE_CHECK, "impossible check"),
]


def piece_icon(piece: chess.Piece | None) -> QIcon:
    """Piece icon for the palette. None = eraser, draw a cross."""
    pixmap = QPixmap(ICON, ICON)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)

    if piece is None:
        pen = painter.pen()
        pen.setColor(theme.TEXT_MUTED)
        pen.setWidth(2)
        painter.setPen(pen)
        pad = ICON * 0.28
        painter.drawLine(pad, pad, ICON - pad, ICON - pad)
        painter.drawLine(ICON - pad, pad, pad, ICON - pad)
    else:
        pieces.renderer(piece).render(painter)

    painter.end()
    return QIcon(pixmap)


class PositionEditor(QDialog):
    position_ready = Signal(str)  # FEN

    def __init__(self, start_fen: str, flipped: bool = False,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Set up position")
        self.resize(880, 640)
        self.setStyleSheet(theme.QSS)

        self._updating = False

        self.board = BoardWidget()
        self.board.edit_mode = True
        self.board.flipped = flipped
        self.board.brush = chess.Piece(chess.PAWN, chess.WHITE)
        self.board.position_edited.connect(self._on_board_edited)

        try:
            self.board.board = chess.Board(start_fen)
        except ValueError:
            self.board.board = chess.Board()

        self._build_ui()
        self._sync_from_board()

    # --- interface -------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        margin = theme.gap(4)
        root.setContentsMargins(margin, margin, margin, margin)
        root.setSpacing(theme.gap(4))
        root.addWidget(self.board, 1)
        root.addLayout(self._build_side(), 0)

    def _build_side(self) -> QVBoxLayout:
        side = QVBoxLayout()
        side.setSpacing(theme.gap(3))

        palette_label = QLabel("PIECE")
        palette_label.setObjectName("heading")
        side.addWidget(palette_label)
        side.addLayout(self._build_palette())

        hint = QLabel(
            "Left button places the selected piece, right button erases."
        )
        hint.setObjectName("status")
        hint.setWordWrap(True)
        side.addWidget(hint)

        side.addWidget(self._separator())

        turn_row = QHBoxLayout()
        turn_row.addWidget(QLabel("Side to move"))
        self.turn_box = QComboBox()
        self.turn_box.addItem("White", chess.WHITE)
        self.turn_box.addItem("Black", chess.BLACK)
        self.turn_box.currentIndexChanged.connect(self._on_controls_changed)
        turn_row.addWidget(self.turn_box, 1)
        side.addLayout(turn_row)

        castling_label = QLabel("CASTLING")
        castling_label.setObjectName("heading")
        side.addWidget(castling_label)

        self.castling: dict[str, QCheckBox] = {}
        for row_keys, caption in ((("K", "Q"), "White"), (("k", "q"), "Black")):
            row = QHBoxLayout()
            row.addWidget(QLabel(caption))
            for key in row_keys:
                box = QCheckBox("0-0" if key.upper() == "K" else "0-0-0")
                box.toggled.connect(self._on_controls_changed)
                self.castling[key] = box
                row.addWidget(box)
            row.addStretch(1)
            side.addLayout(row)

        side.addWidget(self._separator())

        fen_label = QLabel("FEN")
        fen_label.setObjectName("heading")
        side.addWidget(fen_label)

        self.fen_edit = QLineEdit()
        self.fen_edit.setToolTip("You can paste your own FEN - the board will update")
        self.fen_edit.editingFinished.connect(self._on_fen_typed)
        side.addWidget(self.fen_edit)

        self.problem_label = QLabel("")
        self.problem_label.setObjectName("status")
        self.problem_label.setWordWrap(True)
        side.addWidget(self.problem_label)

        side.addStretch(1)

        presets = QHBoxLayout()
        for text, slot in (("Starting position", self.reset_start), ("Clear", self.clear)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            presets.addWidget(button)
        side.addLayout(presets)

        actions = QHBoxLayout()
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        actions.addWidget(cancel)
        self.apply_button = QPushButton("Apply to board")
        self.apply_button.setObjectName("primary")
        self.apply_button.clicked.connect(self.apply)
        actions.addWidget(self.apply_button)
        side.addLayout(actions)

        return side

    def _separator(self) -> QWidget:
        line = QWidget()
        line.setFixedHeight(1)
        line.setStyleSheet(f"background: {theme.BORDER.name()};")
        return line

    def _build_palette(self) -> QVBoxLayout:
        layout = QVBoxLayout()
        layout.setSpacing(theme.gap(1))
        self._palette_group = QButtonGroup(self)
        self._palette_group.setExclusive(True)

        order = [chess.KING, chess.QUEEN, chess.ROOK,
                 chess.BISHOP, chess.KNIGHT, chess.PAWN]

        for color in (chess.WHITE, chess.BLACK):
            row = QHBoxLayout()
            row.setSpacing(theme.gap(1))
            for piece_type in order:
                piece = chess.Piece(piece_type, color)
                row.addWidget(self._palette_button(piece))
            layout.addLayout(row)

        eraser_row = QHBoxLayout()
        eraser_row.setSpacing(theme.gap(1))
        eraser_row.addWidget(self._palette_button(None))
        eraser_row.addStretch(1)
        layout.addLayout(eraser_row)
        return layout

    def _palette_button(self, piece: chess.Piece | None) -> QPushButton:
        button = QPushButton()
        button.setCheckable(True)
        button.setIcon(piece_icon(piece))
        button.setIconSize(QSize(ICON, ICON))
        button.setFixedSize(ICON + 14, ICON + 10)
        button.setToolTip("Eraser" if piece is None else piece.symbol())
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        if piece is not None and piece.piece_type == chess.PAWN \
                and piece.color == chess.WHITE:
            button.setChecked(True)
        button.clicked.connect(lambda _, p=piece: self._set_brush(p))
        self._palette_group.addButton(button)
        return button

    # --- state -----------------------------------------------------------

    def _set_brush(self, piece: chess.Piece | None) -> None:
        self.board.brush = piece

    @Slot()
    def _on_board_edited(self) -> None:
        self._sync_from_board()

    @Slot()
    def _on_controls_changed(self) -> None:
        if self._updating:
            return
        self.board.board.turn = self.turn_box.currentData()

        rights = "".join(key for key, box in self.castling.items() if box.isChecked())
        try:
            self.board.board.set_castling_fen(rights or "-")
        except ValueError:
            pass  # rights impossible for this setup are simply ignored
        self._sync_from_board()

    @Slot()
    def _on_fen_typed(self) -> None:
        if self._updating:
            return
        text = self.fen_edit.text().strip()
        try:
            self.board.board = chess.Board(text)
        except ValueError:
            self.problem_label.setText("That doesn't look like a FEN.")
            return
        self.board.update()
        self._sync_from_board()

    def _sync_from_board(self) -> None:
        """Sync the fields with the current setup and validate it."""
        board = self.board.board
        self._updating = True

        self.turn_box.setCurrentIndex(0 if board.turn == chess.WHITE else 1)
        rights = board.castling_xfen()
        for key, box in self.castling.items():
            box.setChecked(key in rights)
        self.fen_edit.setText(board.fen())
        self.fen_edit.setCursorPosition(0)  # otherwise only the end of the string is visible

        self._updating = False
        self._check(board)

    def _check(self, board: chess.Board) -> None:
        status = board.status()
        troubles = [text for flag, text in PROBLEMS if status & flag]

        if troubles:
            self.problem_label.setText("Not allowed: " + ", ".join(troubles) + ".")
            self.problem_label.setStyleSheet(f"color: {theme.SCORE_BAD.name()};")
            self.apply_button.setEnabled(False)
        else:
            self.problem_label.setText("Position is valid.")
            self.problem_label.setStyleSheet(f"color: {theme.ACCENT.name()};")
            self.apply_button.setEnabled(True)

    # --- buttons ---------------------------------------------------------

    @Slot()
    def reset_start(self) -> None:
        self.board.board = chess.Board()
        self.board.update()
        self._sync_from_board()

    @Slot()
    def clear(self) -> None:
        self.board.board = chess.Board(None)  # empty board
        self.board.update()
        self._sync_from_board()

    @Slot()
    def apply(self) -> None:
        self.position_ready.emit(self.board.board.fen())
        self.accept()
