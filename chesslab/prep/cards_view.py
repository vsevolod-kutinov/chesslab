"""Candidate cards at the top of the Prep panel, drawn from `cards.json`.

The numbers come from the files the `card` tool wrote, not from the chat, so a
rating on a card is exactly what FIDE and chess-results said.
"""

from __future__ import annotations

import json
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFrame, QLabel, QScrollArea, QVBoxLayout, QWidget

from .. import theme
from .card import CARDS_FILE

MUTED = theme.TEXT_MUTED.name()
GOOD = theme.SCORE_GOOD.name()
BAD = theme.SCORE_BAD.name()


def _points(game: dict) -> str:
    if game.get("forfeit"):
        return "forfeit"
    points = game.get("points")
    if points is None:
        return "?"
    colour = GOOD if points == 1 else BAD if points == 0 else MUTED
    return f'<span style="color:{colour}">{"½" if points == 0.5 else int(points)}</span>'


def card_html(card: dict) -> str:
    title = f'{card["title"]} ' if card.get("title") else ""
    rating = card.get("std") or card.get("rating") or "—"
    facts = [f"b. {card['birth_year']}" if card.get("birth_year") else "",
             card.get("fed", ""), f"FIDE {card['fide_id']}" if card.get("fide_id") else "no FIDE ID",
             f"board order {card['board_order']}" if card.get("board_order") else ""]
    lines = [
        f'<table width="100%"><tr><td><b style="font-size:14px">{title}{card.get("name", "?")}</b></td>'
        f'<td align="right"><b style="font-size:16px">{rating}</b></td></tr></table>',
        f'<span style="color:{MUTED}">{" · ".join(f for f in facts if f)}</span>',
    ]
    extra = []
    if card.get("rapid") or card.get("blitz"):
        extra.append(f"rapid {card.get('rapid') or '—'} · blitz {card.get('blitz') or '—'}")
    history = [r for _, r in card.get("history") or [] if r]
    if len(history) >= 2:
        change = history[0] - history[-1]
        colour = GOOD if change > 0 else BAD if change < 0 else MUTED
        extra.append(f'{len(history)} mo: {history[-1]} → {history[0]} '
                     f'<span style="color:{colour}">({change:+d})</span>')
    if extra:
        lines.append(f'<span style="color:{MUTED}">{" · ".join(extra)}</span>')
    games = card.get("tournament") or []
    if games:
        lines.append(" · ".join(
            f'R{g["round"]} b{g["board"]} {"○" if g["colour"] == "white" else "●"} '
            f'{g["opponent"].split(",")[0]} {_points(g)}' for g in games))
    return "<br>".join(lines)


class CardsView(QScrollArea):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setMaximumHeight(230)
        self._inner = QWidget()
        # the global QWidget rule paints the page colour; the panel colour must show through
        self._inner.setStyleSheet("background: transparent;")
        self.setStyleSheet("QScrollArea { background: transparent; }")
        self._layout = QVBoxLayout(self._inner)
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._layout.setSpacing(6)
        self.setWidget(self._inner)
        self.setVisible(False)

    def load(self, folder: Path | None) -> None:
        while self._layout.count():
            item = self._layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        try:
            cards = json.loads((folder / CARDS_FILE).read_text(encoding="utf-8")) if folder else []
        except (OSError, ValueError):
            cards = []
        for card in cards:
            label = QLabel(card_html(card))
            label.setWordWrap(True)
            label.setTextFormat(Qt.TextFormat.RichText)
            label.setStyleSheet(
                f"background: {theme.INSET.name()}; border: 1px solid {theme.BORDER.name()};"
                "border-radius: 6px; padding: 8px 10px;"
            )
            self._layout.addWidget(label)
        self.setVisible(bool(cards))
