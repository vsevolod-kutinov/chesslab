"""Captured pieces and material balance next to the player names.

Each side shows the opponent's pieces it has taken, grouped cheapest first like on
Chess.com, and the side ahead gets the point count: "+2" after winning a rook for
a knight. The points are the real balance, so promotions count too.
"""

from __future__ import annotations

import chess

VALUES = {chess.QUEEN: 9, chess.ROOK: 5, chess.BISHOP: 3, chess.KNIGHT: 3, chess.PAWN: 1}


def imbalance(board: chess.Board, color: bool) -> tuple[list[int], int]:
    """(piece types this side is up, points it is ahead by; 0 when not ahead)."""
    pieces = []
    points = 0
    for kind, value in VALUES.items():
        diff = len(board.pieces(kind, color)) - len(board.pieces(kind, not color))
        points += diff * value
        pieces += [kind] * max(diff, 0)
    return pieces, max(points, 0)


START = {chess.PAWN: 8, chess.KNIGHT: 2, chess.BISHOP: 2, chess.ROOK: 2, chess.QUEEN: 1}
# chess.com groups captured pieces cheapest first
CAPTURED_ORDER = (chess.PAWN, chess.BISHOP, chess.KNIGHT, chess.ROOK, chess.QUEEN)


def captured(board: chess.Board, color: bool) -> list[int]:
    """Opponent's pieces this side has taken (missing from the starting set)."""
    out = []
    for kind in CAPTURED_ORDER:
        out += [kind] * max(START[kind] - len(board.pieces(kind, not color)), 0)
    return out
