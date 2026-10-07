"""Move list with variations.

A "White / Black" table works for a single line only. As soon as branches
appear, the only sensible view is text with variations in parentheses, as in
books and in every serious program. Moves are clickable.
"""

from __future__ import annotations

import chess
import chess.pgn
from PySide6.QtCore import QPoint, Qt, Signal
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QTextBrowser, QWidget

MAIN_COLOR = "#ece9e2"
VAR_COLOR = "#9b9689"
PUNCT_COLOR = "#6b675d"
CURRENT_BG = "#3b4a2d"

TAG_MARKS = {"blunder": "??", "mistake": "?", "inaccuracy": "?!"}
TAG_COLORS = {"blunder": "#d1503f", "mistake": "#d98f3a", "inaccuracy": "#c9b24a"}


class MoveList(QTextBrowser):
    node_chosen = Signal(object)  # chess.pgn.GameNode
    node_menu_requested = Signal(object, QPoint)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("moves")
        self.setOpenLinks(False)
        self.setReadOnly(True)
        self.document().setDefaultStyleSheet("a { text-decoration: none; }")

        # otherwise Qt paints links in its own colour over our styles
        palette = self.palette()
        palette.setColor(QPalette.ColorRole.Link, Qt.GlobalColor.white)
        self.setPalette(palette)

        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self.anchorClicked.connect(self._on_anchor)

        self._nodes: dict[str, chess.pgn.GameNode] = {}
        self._current_key: str | None = None

    # --- public ----------------------------------------------------------

    def set_game(self, game: chess.pgn.Game, current: chess.pgn.GameNode,
                 tags: dict[int, str]) -> None:
        """tags - analysis marks keyed by id() of main-line nodes."""
        self._nodes = {}
        self._current_key = None

        parts: list[str] = []
        self._render(game, parts, depth=0, force_number=True,
                     current=current, tags=tags)

        if not parts:
            # an empty board should not look like a broken widget
            self.setHtml(
                f'<div style="color:{PUNCT_COLOR}; padding-top:6px">'
                "No moves yet. Move pieces on the board or open a game "
                "via Library > My games.</div>"
            )
            return

        self.setHtml(
            f'<div style="line-height:170%; color:{MAIN_COLOR}">'
            + " ".join(parts) + "</div>"
        )
        self._scroll_to_current()

    def node_at(self, position: QPoint) -> chess.pgn.GameNode | None:
        return self._nodes.get(self.anchorAt(position) or "")

    # --- painting --------------------------------------------------------

    def _render(self, node: chess.pgn.GameNode, out: list[str], depth: int,
                force_number: bool, current, tags: dict[int, str]) -> None:
        """Continuations of the position node: main line, variations to the side."""
        while node.variations:
            main = node.variations[0]
            out.append(self._move_html(main, depth, force_number, current, tags))
            force_number = False

            for side in node.variations[1:]:
                out.append(f'<span style="color:{PUNCT_COLOR}">(</span>')
                out.append(self._move_html(side, depth + 1, True, current, tags))
                self._render(side, out, depth + 1, False, current, tags)
                out.append(f'<span style="color:{PUNCT_COLOR}">)</span>')
                # after a parenthesis Black's move needs its number again
                force_number = True

            node = main

    def _move_html(self, node: chess.pgn.GameNode, depth: int, force_number: bool,
                   current, tags: dict[int, str]) -> str:
        board = node.parent.board()
        number = board.fullmove_number
        if board.turn == chess.WHITE:
            prefix = f"{number}."
        elif force_number:
            prefix = f"{number}…"
        else:
            prefix = ""

        tag = tags.get(id(node), "")
        is_current = node is current

        # set the colour exactly once: Qt parses two colors in one style
        # unpredictably, and the current move came out unreadable
        if is_current:
            color = "#ffffff"
        else:
            color = TAG_COLORS.get(tag) or (MAIN_COLOR if depth == 0 else VAR_COLOR)

        style = f"color:{color};"
        if depth == 0:
            style += "font-weight:600;"
        if is_current:
            style += f"background-color:{CURRENT_BG};"

        key = f"n{len(self._nodes)}"
        self._nodes[key] = node
        if node is current:
            self._current_key = key

        number_html = (
            f'<span style="color:{PUNCT_COLOR}">{prefix}</span>&nbsp;'
            if prefix else ""
        )
        san = node.san() + TAG_MARKS.get(tag, "")
        return f'{number_html}<a href="{key}" style="{style}">{san}</a>'

    def _scroll_to_current(self) -> None:
        if self._current_key is None:
            self.verticalScrollBar().setValue(0)
            return
        self.scrollToAnchor(self._current_key)

    # --- events ----------------------------------------------------------

    def _on_anchor(self, url) -> None:
        node = self._nodes.get(url.toString())
        if node is not None:
            self.node_chosen.emit(node)

    def _on_context_menu(self, position: QPoint) -> None:
        node = self.node_at(position)
        if node is not None:
            self.node_menu_requested.emit(node, self.mapToGlobal(position))
