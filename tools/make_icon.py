"""Draw the app icon: a white Cburnett knight on a board-green rounded square.

Writes chesslab/assets/icon.png (window icon) and icon.ico (Windows build).
Run by hand after changing the design.
"""

from __future__ import annotations

import sys
from pathlib import Path

import chess
import chess.svg
from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QPainter
from PySide6.QtSvg import QSvgRenderer

ASSETS = Path(__file__).resolve().parent.parent / "chesslab" / "assets"


def draw(size: int) -> QImage:
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#769656"))
    radius = size * 0.2
    painter.drawRoundedRect(QRectF(0, 0, size, size), radius, radius)

    piece = chess.svg.piece(chess.Piece(chess.KNIGHT, chess.WHITE))
    renderer = QSvgRenderer(QByteArray(piece.encode()))
    margin = size * 0.1
    renderer.render(painter, QRectF(margin, margin, size - 2 * margin, size - 2 * margin))
    painter.end()
    return image


def main() -> None:
    QGuiApplication(sys.argv)
    draw(256).save(str(ASSETS / "icon.png"))
    # Qt's ico writer stores one size per file, so 256 px: Windows scales it down
    if not draw(256).save(str(ASSETS / "icon.ico")):
        sys.exit("could not write icon.ico — Qt image formats plugin missing?")
    print("icon.png and icon.ico written to", ASSETS)


if __name__ == "__main__":
    main()
