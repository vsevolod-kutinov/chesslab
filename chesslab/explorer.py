"""Studying openings from your own games.

The board is on the left; on the right are the moves the player has already
played from this position, how often, and with what result. The tree is built
from the player's own games in the database, not from global statistics: the
point is to see your own repertoire and its holes.
On top of the own games sits the opening reference (`eco.py`): it labels the
position and shows moves that lead to known openings — even those that never
occurred in the player's games. Plus an opening search by name.

Own games are matched by the start of the game, not by position: a
transposition into the same position counts as a different path. That is
enough for reviewing your own repertoire, while matching by position would
require running all games through python-chess every time the window opens.
The reference, by contrast, knows about transpositions: it is built by position.
"""

from __future__ import annotations

import re
import sqlite3
from collections import Counter
from dataclasses import dataclass

import chess
import chess.pgn
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import eco, games_db, storage, theme
from .board import BoardWidget
from .games import SPEEDS, format_date, outcome_text
from .openings import COLORS, ScoreBarDelegate

MOVE_HEADERS = ["Move", "Opening", "Games", "Score"]
GAME_HEADERS = ["Date", "Color", "Opponent", "Result"]

GAMES_SHOWN = 200  # the list at the bottom is for browsing, not for a report


def _clean(san: str) -> str:
    """SAN without check, mate and annotation marks — for comparison."""
    return san.rstrip("+#!?")


# Intentional Russian input: pieces are typed in Russian too: Кр — king, К — knight.
# "Кр" is looked up before "К", otherwise a king would leave a knight with a stray "р".
RU_PIECES = (("КР", "K"), ("Кр", "K"), ("К", "N"), ("С", "B"),
             ("Л", "R"), ("Ф", "Q"))
RU_LETTERS = str.maketrans("авсеох", "abceox")  # look-alike Cyrillic letters in squares
RESULTS = {"1-0", "0-1", "1/2-1/2", "½-½", "*"}


def _tokens(text: str) -> list[tuple[str, str]]:
    """A string of moves -> pairs of "move as typed".

    As typed, so that complaints quote the user's own words, not translated ones.
    """
    text = re.sub(r"\d+\s*[.…]+", " ", text)  # move numbers: "1.", "1…"
    text = text.replace("…", " ")

    out = []
    for typed in text.split():
        token = typed.strip(",;").rstrip("!?")
        if not token or token in RESULTS:
            continue
        token = token.translate(RU_LETTERS)
        for russian, latin in RU_PIECES:
            token = token.replace(russian, latin)
        castle = token.replace("0", "O").upper()
        out.append((castle if castle in ("O-O", "O-O-O") else token, typed))
    return out


def _walk(prefix: list[str], tokens: list[tuple[str, str]]) -> tuple[list[str], str]:
    """Walk the moves from prefix; return the path and the first unrecognised move."""
    board = chess.Board()
    for san in prefix:
        try:
            board.push_san(san)
        except ValueError:
            return [], prefix[0]

    line = list(prefix)
    for token, typed in tokens:
        try:
            move = board.parse_san(token)
        except ValueError:
            return line, typed
        line.append(_clean(board.san(move)))
        board.push(move)
    return line, ""


def parse_line(text: str, current: list[str] | None = None) -> tuple[list[str], str]:
    """Parse a typed line, counting from the start of the game.

    Returns the moves in SAN and the first unrecognised piece (empty — all matched).
    If it does not match from the start, try appending to the current line: then "c5"
    after 1.e4 lands where it should instead of being an error.
    """
    tokens = _tokens(text)
    if not tokens:
        return [], ""

    line, bad = _walk([], tokens)
    if bad and current:
        tail, tail_bad = _walk(current, tokens)
        if not tail_bad:
            return tail, ""
    return line, bad


@dataclass
class Entry:
    """One game, parsed for the needs of the tree."""

    row: sqlite3.Row
    sans: list[str]
    me_white: bool
    points: float  # from the player's side: 1 win, 0.5 draw, 0 loss


def _entries(rows: list[sqlite3.Row], account: str | None = None) -> list[Entry]:
    """account is only a hint: the player's side is taken from the row anyway,
    so the list may mix several accounts."""
    out = []
    for row in rows:
        moves = row["moves"] or ""
        if not moves:
            continue
        owner = account or row["account"] or ""
        me_white = (row["white"] or "").lower() == owner.lower()
        winner = row["winner"] or ""
        if not winner:
            points = 0.5
        else:
            points = 1.0 if (winner == "white") == me_white else 0.0
        out.append(Entry(row, [_clean(s) for s in moves.split()],
                         me_white, points))
    return out


class ExplorerDialog(QDialog):
    """Opening tree built from the player's own games."""

    game_chosen = Signal(str, str)  # PGN and id — open the game on the board
    line_chosen = Signal(str)       # PGN of the current line — put on the board

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Opening explorer")
        self.resize(1080, 720)
        self.setStyleSheet(theme.QSS)

        self.conn = games_db.connect()
        self.accounts = storage.load_accounts()
        self.book = eco.book()

        self.entries: list[Entry] = []
        self.line: list[str] = []          # SAN of the path walked
        self.moves: list[dict] = []        # continuations from the current position
        self.games: list[sqlite3.Row] = []  # games that reached the position

        self._build_ui()
        self.reload()

    # --- UI --------------------------------------------------------------

    def _build_ui(self) -> None:
        root = QHBoxLayout(self)
        margin = theme.gap(4)
        root.setContentsMargins(margin, margin, margin, margin)
        root.setSpacing(theme.gap(4))
        root.addLayout(self._build_left(), 1)
        root.addWidget(self._build_right(), 0)

    def _build_left(self) -> QVBoxLayout:
        left = QVBoxLayout()
        left.setSpacing(theme.gap(2))

        self.board = BoardWidget()
        self.board.show_arrow = False
        self.board.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.board.move_played.connect(self._on_board_move)
        left.addWidget(self.board, 1)

        # this same field shows the path walked — you can type over it
        self.line_edit = QLineEdit()
        self.line_edit.setPlaceholderText("Moves from the start: e4 · 1.e4 c5")
        self.line_edit.setClearButtonEnabled(True)
        self.line_edit.setToolTip(
            "Type moves and press Enter — we jump to that position, "
            "and on the right are your games from here and their results.\n"
            "Understands Russian piece letters (Кf3, Сb5) and castling 0-0."
        )
        self.line_edit.returnPressed.connect(self._on_line_typed)
        left.addWidget(self.line_edit)

        row = QHBoxLayout()
        row.setSpacing(theme.gap(2))
        for text, slot, tip in (
            ("To start", self.go_start, "Back to the starting position (Home)"),
            ("Back", self.go_back, "Undo the last move (←)"),
            ("Flip", self.board.flip, "View from the other side"),
        ):
            button = QPushButton(text)
            button.setToolTip(tip)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            button.clicked.connect(slot)
            row.addWidget(button)
        row.addStretch(1)

        self.line_button = QPushButton("Put on board")
        self.line_button.setObjectName("primary")
        self.line_button.setToolTip("Move this line to the main window, next to the engine")
        self.line_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.line_button.clicked.connect(self.take_line)
        row.addWidget(self.line_button)
        left.addLayout(row)

        return left

    def _build_right(self) -> QWidget:
        side = QWidget()
        side.setFixedWidth(480)
        right = QVBoxLayout(side)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(theme.gap(2))

        self.opening_label = QLabel("")
        self.opening_label.setObjectName("heading")
        self.opening_label.setWordWrap(True)
        right.addWidget(self.opening_label)

        self.summary = QLabel("")
        self.summary.setWordWrap(True)
        right.addWidget(self.summary)

        right.addLayout(self._build_filters())
        right.addWidget(self._build_search())

        self.moves_table = self._table(MOVE_HEADERS)
        self.moves_table.setItemDelegateForColumn(3, ScoreBarDelegate(self.moves_table))
        # a single click is enough: a double click would make two moves at once
        self.moves_table.clicked.connect(self.play_selected)
        header = self.moves_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.moves_table.setColumnWidth(3, 96)
        right.addWidget(self.moves_table, 3)

        games_label = QLabel("GAMES FROM HERE")
        games_label.setObjectName("heading")
        right.addWidget(games_label)

        self.games_table = self._table(GAME_HEADERS)
        self.games_table.doubleClicked.connect(self.open_selected_game)
        games_header = self.games_table.horizontalHeader()
        games_header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        for column in (0, 1, 3):
            games_header.setSectionResizeMode(
                column, QHeaderView.ResizeMode.ResizeToContents
            )
        right.addWidget(self.games_table, 2)

        buttons = QHBoxLayout()
        self.open_button = QPushButton("Open game")
        self.open_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.open_button.clicked.connect(self.open_selected_game)
        buttons.addWidget(self.open_button)
        buttons.addStretch(1)
        close_button = QPushButton("Close")
        close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        right.addLayout(buttons)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        right.addWidget(self.status)

        return side

    def _build_search(self) -> QWidget:
        """Opening search by name: a field and a dropdown list under it."""
        holder = QWidget()
        layout = QVBoxLayout(holder)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(theme.gap(1))

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Find an opening — 'sicilian', 'B23'…")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.textChanged.connect(self._on_search)
        self.search_edit.returnPressed.connect(self._take_first_found)
        layout.addWidget(self.search_edit)

        self.found_list = QListWidget()
        self.found_list.setMaximumHeight(150)
        self.found_list.hide()
        self.found_list.itemClicked.connect(self._on_found_clicked)
        layout.addWidget(self.found_list)
        return holder

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        # headers of text columns used to hang centered over left-aligned text
        for column, header in enumerate(headers):
            if header in ("Move", "Opening", "Date", "Opponent"):
                table.horizontalHeaderItem(column).setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
        return table

    def _build_filters(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(theme.gap(1))

        self.account_box = QComboBox()
        storage.fill_account_box(self.account_box, self.accounts)
        self.account_box.currentIndexChanged.connect(self.reload)
        row.addWidget(self.account_box, 1)

        self.color_box = self._combo(COLORS)
        row.addWidget(self.color_box)
        self.speed_box = self._combo(SPEEDS)
        row.addWidget(self.speed_box)
        return row

    def _combo(self, options: list[tuple[str, str]]) -> QComboBox:
        box = QComboBox()
        for value, label in options:
            box.addItem(label, value)
        box.currentIndexChanged.connect(self.reload)
        return box

    # --- data ------------------------------------------------------------

    @Slot()
    def reload(self) -> None:
        """Re-read the games under the current filters and redraw the position."""
        account = self.account_box.currentData()  # None — all accounts
        if not self.accounts:
            self.entries = []
            self.opening_label.setText("NO ACCOUNTS")
            self.status.setText("Add an account: menu “Accounts → Lichess and Chess.com…”")
            self._refresh()
            return

        rows = games_db.search(
            self.conn,
            account=account,
            speed=self.speed_box.currentData(),
            color=self.color_box.currentData(),
            limit=100000,
        )
        self.entries = _entries(rows, account)
        self._refresh()

    def _matching(self) -> list[Entry]:
        depth = len(self.line)
        if depth == 0:
            return self.entries
        return [e for e in self.entries if e.sans[:depth] == self.line]

    def _continuations(self, reached: list[Entry]) -> list[dict]:
        depth = len(self.line)
        buckets: dict[str, list[Entry]] = {}
        for entry in reached:
            if len(entry.sans) > depth:
                buckets.setdefault(entry.sans[depth], []).append(entry)

        total = sum(len(v) for v in buckets.values())
        out = []
        for san, group in buckets.items():
            points = sum(e.points for e in group)
            wins = sum(1 for e in group if e.points == 1.0)
            draws = sum(1 for e in group if e.points == 0.5)
            out.append({
                "san": san,
                "games": len(group),
                "share": round(len(group) / total * 100, 1) if total else 0.0,
                "wins": wins,
                "draws": draws,
                "losses": len(group) - wins - draws,
                "score": round(points / len(group) * 100, 1),
            })
        out.sort(key=lambda m: (-m["games"], m["san"]))
        return out

    def _merge(self, reached: list[Entry], board: chess.Board) -> list[dict]:
        """Own moves plus moves from the reference — in one table.

        Own moves come first: what the player actually played, then theory
        they have not got around to.
        """
        rows = {row["san"]: row for row in self._continuations(reached)}

        here = self.book.name_at(board)
        family = here.family if here is not None else ""

        for branch in self.book.moves_from(board):
            try:
                san = _clean(board.san(chess.Move.from_uci(branch.uci)))
            except (AssertionError, ValueError):
                continue  # reference move is impossible in this position
            row = rows.get(san)
            if row is None:
                row = {"san": san, "games": 0, "share": 0.0, "score": None,
                       "wins": 0, "draws": 0, "losses": 0}
                rows[san] = row
            row["eco"] = branch.eco
            row["name"] = _short_name(branch.name, family)
            row["full_name"] = branch.name
            row["variants"] = branch.variants

        out = list(rows.values())
        for row in out:
            row.setdefault("eco", "")
            row.setdefault("name", "")
            row.setdefault("full_name", "")
            row.setdefault("variants", 0)
        out.sort(key=lambda r: (0 if r["games"] else 1, -r["games"],
                                -r["variants"], r["san"]))
        return out

    def _refresh(self) -> None:
        board = chess.Board()
        for san in self.line:
            try:
                board.push_san(san)
            except ValueError:
                break
        self.board.set_position(board, board.peek() if board.move_stack else None)

        reached = self._matching()
        self.moves = self._merge(reached, board)
        self.games = [e.row for e in reached][:GAMES_SHOWN]

        self._fill_moves()
        self._fill_games(reached)
        self._update_labels(reached, board)

    def _update_labels(self, reached: list[Entry], board: chess.Board) -> None:
        self.line_edit.setText(_line_text(self.line) if self.line else "")
        self.line_edit.setCursorPosition(0)  # for a long line show the beginning
        self.opening_label.setText(self._position_name(board, reached))

        if not reached:
            self.summary.setText("None of your games reach this position.")
            self.status.setText(
                "No own statistics here, but the moves from the reference are still "
                "in the table — you can keep going along theory."
            )
            return

        wins = sum(1 for e in reached if e.points == 1.0)
        draws = sum(1 for e in reached if e.points == 0.5)
        losses = len(reached) - wins - draws
        score = sum(e.points for e in reached) / len(reached) * 100
        whites = sum(1 for e in reached if e.me_white)
        self.summary.setText(
            f'{len(reached)} games   ·   +{wins} ={draws} −{losses}   ·   '
            f'{score:.0f}% score\n'
            f'as White {whites}, as Black {len(reached) - whites}'
        )
        self.status.setText(
            "Click a move to play it. You can also move on the board with the mouse. "
            "Gray moves are theory you have not played from here."
        )

    def _position_name(self, board: chess.Board, reached: list[Entry]) -> str:
        """Name of the position: the reference first, otherwise own games."""
        known = self.book.name_at(board)
        if known is not None:
            return known.title.upper()

        # the reference has no name — take the most frequent one from own games
        names = Counter(
            f'{e.row["eco"]} {e.row["opening"]}'.strip()
            for e in reached if e.row["opening"]
        ) if self.line else Counter()
        return names.most_common(1)[0][0].upper() if names else "START OF GAME"

    def _fill_moves(self) -> None:
        self.moves_table.setRowCount(len(self.moves))
        number = len(self.line) // 2 + 1
        prefix = f"{number}." if len(self.line) % 2 == 0 else f"{number}…"

        for index, move in enumerate(self.moves):
            played = move["games"] > 0

            title = QTableWidgetItem(f'{prefix} {move["san"]}')
            if not played:
                title.setForeground(theme.TEXT_MUTED)
            self.moves_table.setItem(index, 0, title)

            name = QTableWidgetItem(move["name"])
            name.setForeground(theme.TEXT if played else theme.TEXT_MUTED)
            if move["full_name"]:
                name.setToolTip(
                    f'{move["eco"]} {move["full_name"]}\n'
                    f'variations in the reference: {move["variants"]}'
                )
            self.moves_table.setItem(index, 1, name)

            games_item = QTableWidgetItem(str(move["games"]) if played else "—")
            games_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            if played:
                games_item.setToolTip(
                    f'+{move["wins"]} ={move["draws"]} −{move["losses"]}   ·   '
                    f'{move["share"]:.0f}% of this branch'
                )
            else:
                games_item.setForeground(theme.TEXT_FAINT)
            self.moves_table.setItem(index, 2, games_item)

            score = QTableWidgetItem()
            score.setData(Qt.ItemDataRole.UserRole, move["score"])
            self.moves_table.setItem(index, 3, score)

    def _fill_games(self, reached: list[Entry]) -> None:
        shown = reached[:GAMES_SHOWN]
        self.games_table.setRowCount(len(shown))
        for index, entry in enumerate(shown):
            row = entry.row
            opponent = row["black"] if entry.me_white else row["white"]
            cells = [
                format_date(row["played_at"]),
                "White" if entry.me_white else "Black",
                str(opponent),
                outcome_text(row["winner"] or "", entry.me_white),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column in (1, 3):
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                self.games_table.setItem(index, column, item)

    # --- walking the tree ------------------------------------------------

    @Slot()
    def play_selected(self) -> None:
        row = self.moves_table.currentRow()
        if 0 <= row < len(self.moves):
            self.line.append(self.moves[row]["san"])
            self._refresh()

    @Slot(object)
    def _on_board_move(self, move: chess.Move) -> None:
        """Move with the mouse: leads into any line, even one absent from the games."""
        board = self.board.board
        try:
            san = _clean(board.san(move))
        except (AssertionError, ValueError):
            return
        self.line.append(san)
        self._refresh()

    @Slot()
    def _on_line_typed(self) -> None:
        """Enter in the moves field: jump to the typed line."""
        text = self.line_edit.text()
        if not text.strip():
            self.go_start()
            return

        line, bad = parse_line(text, self.line)
        if line or not bad:  # junk in the field must not reset the position
            self.line = line
        self._refresh()
        if bad:  # _refresh has already rewritten the status, so complain after it
            self.status.setText(f"Could not parse move \"{bad}\" — stopped there.")

    @Slot()
    def go_back(self) -> None:
        if self.line:
            self.line.pop()
            self._refresh()

    @Slot()
    def go_start(self) -> None:
        if self.line:
            self.line.clear()
            self._refresh()

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace):
            self.go_back()
        elif key == Qt.Key.Key_Home:
            self.go_start()
        elif key == Qt.Key.Key_Right and self.moves:
            self.line.append(self.moves[0]["san"])  # the most frequent move
            self._refresh()
        else:
            super().keyPressEvent(event)

    # --- opening search --------------------------------------------------

    @Slot(str)
    def _on_search(self, text: str) -> None:
        found = self.book.search(text, limit=30)
        self.found_list.clear()
        for opening in found:
            item = QListWidgetItem(opening.title)
            item.setData(Qt.ItemDataRole.UserRole, opening)
            item.setToolTip(_line_text(_sans(opening)))
            self.found_list.addItem(item)
        self.found_list.setVisible(bool(found))

        if text.strip() and not found:
            self.status.setText(
                "Nothing found. Try part of the name or an ECO code."
            )

    @Slot()
    def _take_first_found(self) -> None:
        if self.found_list.count():
            self._go_to(self.found_list.item(0).data(Qt.ItemDataRole.UserRole))

    @Slot(QListWidgetItem)
    def _on_found_clicked(self, item: QListWidgetItem) -> None:
        self._go_to(item.data(Qt.ItemDataRole.UserRole))

    def _go_to(self, opening: eco.Opening) -> None:
        """Put the found opening's line on the board."""
        self.line = _sans(opening)
        self.search_edit.clear()  # also hides the list
        self.found_list.hide()
        self._refresh()

    # --- handing off -----------------------------------------------------

    @Slot()
    def take_line(self) -> None:
        """Hand the current line to the main window — engine and variations are there."""
        game = chess.pgn.Game()
        game.headers["Event"] = "Opening explorer"
        game.headers["White"] = "?"
        game.headers["Black"] = "?"
        node = game
        board = chess.Board()
        for san in self.line:
            try:
                move = board.parse_san(san)
            except ValueError:
                break
            node = node.add_variation(move)
            board.push(move)
        self.line_chosen.emit(str(game))
        self.accept()

    @Slot()
    def open_selected_game(self) -> None:
        row = self.games_table.currentRow()
        if not (0 <= row < len(self.games)):
            return
        pgn = self.games[row]["pgn"]
        if not pgn:
            self.status.setText("This game has no saved PGN.")
            return
        self.game_chosen.emit(pgn, self.games[row]["id"])
        self.accept()

    def closeEvent(self, event) -> None:
        self.conn.close()
        super().closeEvent(event)


def _sans(opening: eco.Opening) -> list[str]:
    """Opening moves in the notation in which own games are stored."""
    board = chess.Board()
    line = []
    for uci in opening.ucis:
        move = chess.Move.from_uci(uci)
        line.append(_clean(board.san(move)))
        board.push(move)
    return line


def _short_name(name: str, family: str) -> str:
    """Do not repeat "Sicilian Defense: …" in every row — it is already in the header."""
    if family and name.startswith(family):
        return name[len(family):].lstrip(" :,") or family
    return name


def _line_text(line: list[str]) -> str:
    if not line:
        return "starting position"
    parts = []
    for index, san in enumerate(line):
        if index % 2 == 0:
            parts.append(f"{index // 2 + 1}.")
        parts.append(san)
    return " ".join(parts)
