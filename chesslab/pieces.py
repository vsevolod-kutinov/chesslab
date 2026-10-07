"""Pieces. Uses the ready-made SVGs (Cburnett set) bundled with python-chess,
so we don't have to ship our own images."""

from __future__ import annotations

import chess
import chess.svg
from PySide6.QtCore import QByteArray
from PySide6.QtSvg import QSvgRenderer

_SVG_DOC = (
    '<svg xmlns="http://www.w3.org/2000/svg" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 45 45">{}</svg>'
)

_cache: dict[str, QSvgRenderer] = {}


def renderer(piece: chess.Piece) -> QSvgRenderer:
    """QSvgRenderer for a piece. Created once per symbol."""
    symbol = piece.symbol()
    r = _cache.get(symbol)
    if r is None:
        doc = _SVG_DOC.format(chess.svg.PIECES[symbol])
        r = QSvgRenderer(QByteArray(doc.encode("utf-8")))
        _cache[symbol] = r
    return r
