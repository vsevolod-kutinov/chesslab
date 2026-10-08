"""Board: rendering and mouse move input."""

from __future__ import annotations

import math

import chess
from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QMenu, QWidget

from . import pieces, theme

_DRAG_THRESHOLD = 5  # pixels after which a press counts as a drag


class BoardWidget(QWidget):
    move_played = Signal(object)  # chess.Move
    position_edited = Signal()    # in setup mode
    # object, not dict: Qt does not convert tuple keys in a QVariantMap
    marks_changed = Signal(object)  # user-drawn arrows and circles on the board

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumSize(360, 360)
        self.setMouseTracking(True)

        self.board = chess.Board()
        self.flipped = False
        self.show_arrow = True

        # setup mode: a click places a piece instead of making a move
        self.edit_mode = False
        self.brush: chess.Piece | None = None  # None = eraser

        self._selected: int | None = None
        self._targets: dict[int, chess.Move] = {}
        self._last_move: chess.Move | None = None
        self._best_uci: str | None = None
        self._badge: tuple[int, str] | None = None  # (square, review class)

        self._press_square: int | None = None
        self._press_pos: QPointF | None = None
        self._drag_pos: QPointF | None = None

        # user marks: (from, to) -> colour; from == to is a circle
        self._marks: dict[tuple[int, int], str] = {}
        self._mark_from: int | None = None   # right button pressed on this square
        self._mark_pos: QPointF | None = None
        self._mark_key = "default"

    # --- state -----------------------------------------------------------

    def set_position(self, board: chess.Board, last_move: chess.Move | None) -> None:
        self.board = board
        self._last_move = last_move
        self._clear_selection()
        self._marks = {}   # marks belong to the position, not to the board
        self._mark_from = None
        self._mark_pos = None
        self.update()

    def set_marks(self, marks: dict[tuple[int, int], str]) -> None:
        """Set ready-made marks - the window remembers them per game move."""
        self._marks = dict(marks)
        self.update()

    def clear_marks(self) -> None:
        if not self._marks:
            return
        self._marks = {}
        self.update()
        self.marks_changed.emit({})

    def set_badge(self, square: int | None, kind: str | None) -> None:
        """Game-review class of the last move, drawn on its destination square."""
        self._badge = (square, kind) if square is not None and kind else None
        self.update()

    def set_best_move(self, uci: str | None) -> None:
        self._best_uci = uci
        self.update()

    def flip(self) -> None:
        self.flipped = not self.flipped
        self.update()

    def _clear_selection(self) -> None:
        self._selected = None
        self._targets = {}
        self._press_square = None
        self._press_pos = None
        self._drag_pos = None

    # --- geometry --------------------------------------------------------

    def _square_size(self) -> float:
        return min(self.width(), self.height()) / 8.0

    def _origin(self) -> QPointF:
        size = self._square_size() * 8
        return QPointF((self.width() - size) / 2, (self.height() - size) / 2)

    def board_rect(self) -> QRectF:
        """Where the board itself is within the widget (without the empty margins)."""
        size = self._square_size() * 8
        return QRectF(self._origin(), QSizeF(size, size))

    def _square_rect(self, square: int) -> QRectF:
        file_, rank = chess.square_file(square), chess.square_rank(square)
        col = 7 - file_ if self.flipped else file_
        row = rank if self.flipped else 7 - rank
        s = self._square_size()
        o = self._origin()
        return QRectF(o.x() + col * s, o.y() + row * s, s, s)

    def _square_at(self, pos: QPointF) -> int | None:
        s = self._square_size()
        o = self._origin()
        col = int((pos.x() - o.x()) // s)
        row = int((pos.y() - o.y()) // s)
        if not (0 <= col < 8 and 0 <= row < 8):
            return None
        file_ = 7 - col if self.flipped else col
        rank = row if self.flipped else 7 - row
        return chess.square(file_, rank)

    # --- input -----------------------------------------------------------

    def mousePressEvent(self, event) -> None:
        square = self._square_at(event.position())
        if square is None:
            return

        if self.edit_mode:
            self._edit_click(square, event.button())
            return

        if event.button() == Qt.MouseButton.RightButton:
            self._mark_from = square
            self._mark_pos = event.position()
            self._mark_key = _mark_key(event.modifiers())
            return

        if event.button() != Qt.MouseButton.LeftButton:
            return

        # like Lichess: a plain click on the board clears everything drawn
        self.clear_marks()
        self._press_pos = event.position()

        if self._selected is not None and square in self._targets:
            self._play(self._targets[square])
            return

        piece = self.board.piece_at(square)
        if piece is not None and piece.color == self.board.turn:
            self._selected = square
            self._press_square = square
            self._targets = {
                m.to_square: m
                for m in self.board.legal_moves
                if m.from_square == square
            }
        else:
            self._clear_selection()
        self.update()

    def _edit_click(self, square: int, button) -> None:
        """Right button always erases, left places the selected piece."""
        if button == Qt.MouseButton.RightButton or self.brush is None:
            changed = self.board.piece_at(square) is not None
            self.board.remove_piece_at(square)
        else:
            changed = self.board.piece_at(square) != self.brush
            self.board.set_piece_at(square, self.brush)

        self.update()
        if changed:
            self.position_edited.emit()

    def mouseMoveEvent(self, event) -> None:
        if self.edit_mode:
            return
        if self._mark_from is not None:
            self._mark_pos = event.position()
            self.update()
            return
        if self._press_square is None or self._press_pos is None:
            return
        if self._drag_pos is None:
            delta = event.position() - self._press_pos
            if math.hypot(delta.x(), delta.y()) < _DRAG_THRESHOLD:
                return
        self._drag_pos = event.position()
        self.update()

    def mouseReleaseEvent(self, event) -> None:
        if self.edit_mode:
            return
        if event.button() == Qt.MouseButton.RightButton:
            self._finish_mark(event.position())
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return
        was_dragging = self._drag_pos is not None
        self._drag_pos = None
        self._press_square = None

        if not was_dragging:
            self.update()
            return

        square = self._square_at(event.position())
        if square is not None and square in self._targets:
            self._play(self._targets[square])
        else:
            self.update()

    def _finish_mark(self, position: QPointF) -> None:
        """Right button released: an arrow if dragged to another square,
        otherwise a circle. Repeating the same mark removes it."""
        start, self._mark_from = self._mark_from, None
        self._mark_pos = None
        if start is None:
            return

        end = self._square_at(position)
        if end is None:
            self.update()
            return

        key = (start, end)
        if self._marks.get(key) == self._mark_key:
            del self._marks[key]          # same mark again - erase it
        else:
            self._marks[key] = self._mark_key
        self.update()
        self.marks_changed.emit(dict(self._marks))

    def _play(self, move: chess.Move) -> None:
        if move.promotion is not None:
            piece = self._ask_promotion()
            if piece is None:
                self._clear_selection()
                self.update()
                return
            move = chess.Move(move.from_square, move.to_square, promotion=piece)

        self._clear_selection()
        self.move_played.emit(move)

    def _ask_promotion(self) -> int | None:
        menu = QMenu(self)
        actions = {}
        for name, piece_type in (
            ("Queen", chess.QUEEN),
            ("Rook", chess.ROOK),
            ("Bishop", chess.BISHOP),
            ("Knight", chess.KNIGHT),
        ):
            actions[menu.addAction(name)] = piece_type
        chosen = menu.exec(self.cursor().pos())
        return actions.get(chosen)

    # --- painting --------------------------------------------------------

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), theme.BG)

        self._paint_edge(painter)
        self._paint_squares(painter)
        if not self.edit_mode:
            self._paint_highlights(painter)
            self._paint_circles(painter)   # circles under the pieces, like a highlight
        self._paint_pieces(painter)
        if self.show_arrow and not self.edit_mode:
            self._paint_arrow(painter)
        if not self.edit_mode:
            self._paint_marks(painter)     # arrows on top - otherwise not visible
            self._paint_badge(painter)
        self._paint_dragged(painter)
        painter.end()

    def _paint_badge(self, painter: QPainter) -> None:
        """A coloured disc with the class symbol on the square's top-right corner,
        half outside it — as on Chess.com, so it does not cover the piece."""
        if self._badge is None:
            return
        from .review import STYLE

        square, kind = self._badge
        style = STYLE.get(kind)
        if style is None:
            return
        rect = self._square_rect(square)
        radius = rect.width() * 0.19
        center = QPointF(rect.right() - radius * 0.35, rect.top() + radius * 0.35)
        # keep the disc inside the widget on the board's top and right edges
        center.setX(min(center.x(), self.width() - radius - 1))
        center.setY(max(center.y(), radius + 1))

        painter.save()
        painter.setPen(QPen(QColor(0, 0, 0, 90), 1.5))
        painter.setBrush(QColor(style.color))
        painter.drawEllipse(center, radius, radius)
        font = QFont(self.font())
        font.setBold(True)
        font.setPixelSize(max(8, int(radius * (1.0 if len(style.symbol) > 1 else 1.25))))
        painter.setFont(font)
        painter.setPen(QColor("#ffffff"))
        painter.drawText(QRectF(center.x() - radius, center.y() - radius,
                                2 * radius, 2 * radius),
                         Qt.AlignmentFlag.AlignCenter, style.symbol)
        painter.restore()

    def _paint_edge(self, painter: QPainter) -> None:
        """Thin frame: without it light squares blend into the window edge."""
        size = self._square_size() * 8
        origin = self._origin()
        painter.setPen(QPen(theme.BOARD_EDGE, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(QRectF(origin.x() - 0.5, origin.y() - 0.5,
                                size + 1, size + 1))

    def _paint_squares(self, painter: QPainter) -> None:
        s = self._square_size()
        font = QFont(self.font())
        font.setPointSizeF(max(7.0, s * 0.16))
        font.setBold(True)
        painter.setFont(font)

        for square in chess.SQUARES:
            rect = self._square_rect(square)
            light = (chess.square_file(square) + chess.square_rank(square)) % 2 == 1
            painter.fillRect(rect, theme.SQ_LIGHT if light else theme.SQ_DARK)

            # coordinates only along the board edge
            label_color = theme.COORD_ON_LIGHT if light else theme.COORD_ON_DARK
            painter.setPen(QPen(label_color))
            file_, rank = chess.square_file(square), chess.square_rank(square)
            bottom_rank = 7 if self.flipped else 0
            left_file = 7 if self.flipped else 0
            pad = s * 0.05
            if rank == bottom_rank:
                painter.drawText(
                    rect.adjusted(0, 0, -pad, -pad),
                    Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignBottom,
                    chess.FILE_NAMES[file_],
                )
            if file_ == left_file:
                painter.drawText(
                    rect.adjusted(pad, pad, 0, 0),
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop,
                    chess.RANK_NAMES[rank],
                )

    def _paint_highlights(self, painter: QPainter) -> None:
        s = self._square_size()

        if self._last_move is not None:
            for sq in (self._last_move.from_square, self._last_move.to_square):
                painter.fillRect(self._square_rect(sq), theme.LAST_MOVE)

        if self.board.is_check():
            king = self.board.king(self.board.turn)
            if king is not None:
                rect = self._square_rect(king)
                grad_color = QColor(theme.CHECK)
                painter.setBrush(grad_color)
                painter.setPen(Qt.PenStyle.NoPen)
                painter.drawEllipse(rect.adjusted(s * 0.06, s * 0.06, -s * 0.06, -s * 0.06))

        if self._selected is not None:
            painter.fillRect(self._square_rect(self._selected), theme.SELECTED)

        painter.setPen(Qt.PenStyle.NoPen)
        for square in self._targets:
            rect = self._square_rect(square)
            if self.board.piece_at(square) is not None:
                # capture - a ring along the square edge
                pen = QPen(theme.LEGAL_RING, s * 0.08)
                painter.setPen(pen)
                painter.setBrush(Qt.BrushStyle.NoBrush)
                inset = s * 0.06
                painter.drawEllipse(rect.adjusted(inset, inset, -inset, -inset))
                painter.setPen(Qt.PenStyle.NoPen)
            else:
                painter.setBrush(theme.LEGAL_DOT)
                r = s * 0.16
                painter.drawEllipse(rect.center(), r, r)
        painter.setBrush(Qt.BrushStyle.NoBrush)

    def _paint_pieces(self, painter: QPainter) -> None:
        dragging_from = self._press_square if self._drag_pos is not None else None
        for square, piece in self.board.piece_map().items():
            if square == dragging_from:
                continue
            pieces.renderer(piece).render(painter, self._square_rect(square))

    def _paint_dragged(self, painter: QPainter) -> None:
        if self._drag_pos is None or self._press_square is None:
            return
        piece = self.board.piece_at(self._press_square)
        if piece is None:
            return
        s = self._square_size()
        rect = QRectF(self._drag_pos.x() - s / 2, self._drag_pos.y() - s / 2, s, s)
        pieces.renderer(piece).render(painter, rect)

    def _paint_circles(self, painter: QPainter) -> None:
        """User circles: right button without dragging."""
        s = self._square_size()
        painter.setBrush(Qt.BrushStyle.NoBrush)
        for (start, end), key in self._marks.items():
            if start != end:
                continue
            painter.setPen(QPen(theme.MARK_COLORS[key], s * 0.07))
            inset = s * 0.08
            painter.drawEllipse(
                self._square_rect(start).adjusted(inset, inset, -inset, -inset)
            )
        painter.setPen(Qt.PenStyle.NoPen)

    def _paint_marks(self, painter: QPainter) -> None:
        """User arrows, including the one being dragged right now."""
        for (start, end), key in self._marks.items():
            if start != end:
                self._arrow_between(painter, start, end, theme.MARK_COLORS[key])

        if self._mark_from is not None and self._mark_pos is not None:
            end = self._square_at(self._mark_pos)
            if end is not None and end != self._mark_from:
                self._arrow_between(painter, self._mark_from, end,
                                    theme.MARK_COLORS[self._mark_key])

    def _paint_arrow(self, painter: QPainter) -> None:
        if not self._best_uci or len(self._best_uci) < 4:
            return
        try:
            move = chess.Move.from_uci(self._best_uci)
        except ValueError:
            return
        self._arrow_between(painter, move.from_square, move.to_square, theme.ARROW)

    def _arrow_between(self, painter: QPainter, start: int, end: int,
                       color: QColor) -> None:
        self._draw_arrow(painter, self._square_rect(start).center(),
                         self._square_rect(end).center(), color,
                         self._elbow(start, end))

    def _elbow(self, start: int, end: int) -> QPointF | None:
        """Bend for a knight move: long leg first, then the short one.

        A straight arrow through a square corner reads as a bishop move, and on
        the board the actual trajectory matters.
        """
        df = chess.square_file(end) - chess.square_file(start)
        dr = chess.square_rank(end) - chess.square_rank(start)
        if sorted((abs(df), abs(dr))) != [1, 2]:
            return None
        corner = (chess.square(chess.square_file(end), chess.square_rank(start))
                  if abs(df) == 2 else
                  chess.square(chess.square_file(start), chess.square_rank(end)))
        return self._square_rect(corner).center()

    def _draw_arrow(self, painter: QPainter, start: QPointF, end: QPointF,
                    color: QColor, elbow: QPointF | None = None) -> None:
        s = self._square_size()

        if elbow is not None:
            # draw the shoulder up to the bend separately, compute the tip from it
            first = elbow - start
            first_length = math.hypot(first.x(), first.y())
            if first_length >= 1:
                fx, fy = first.x() / first_length, first.y() / first_length
                painter.setPen(QPen(color, s * 0.14, Qt.PenStyle.SolidLine,
                                    Qt.PenCapStyle.RoundCap))
                painter.drawLine(start + QPointF(fx * s * 0.18, fy * s * 0.18), elbow)
            start = elbow

        vec = end - start
        length = math.hypot(vec.x(), vec.y())
        if length < 1:
            return
        ux, uy = vec.x() / length, vec.y() / length

        head = s * 0.34
        width = s * 0.14
        tip = end - QPointF(ux * s * 0.14, uy * s * 0.14)
        base = tip - QPointF(ux * head, uy * head)

        painter.setPen(QPen(color, width, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        tail = start if elbow is not None else start + QPointF(ux * s * 0.18, uy * s * 0.18)
        painter.drawLine(tail, base)

        path = QPainterPath()
        path.moveTo(tip)
        path.lineTo(base + QPointF(-uy * head * 0.5, ux * head * 0.5))
        path.lineTo(base + QPointF(uy * head * 0.5, -ux * head * 0.5))
        path.closeSubpath()
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(color)
        painter.drawPath(path)
        painter.setBrush(Qt.BrushStyle.NoBrush)


def _mark_key(modifiers) -> str:
    """Mark colour by held modifier key - like Lichess, but with our own colours."""
    if modifiers & Qt.KeyboardModifier.ShiftModifier:
        return "shift"
    if modifiers & Qt.KeyboardModifier.ControlModifier:
        return "ctrl"
    if modifiers & Qt.KeyboardModifier.AltModifier:
        return "alt"
    return "default"
