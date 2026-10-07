"""Main window: board on the left, engine and move list on the right."""

from __future__ import annotations

import io
import sqlite3
import time
from html import escape
from pathlib import Path

import chess
import chess.pgn
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtGui import (
    QAction, QActionGroup, QColor, QGuiApplication, QKeySequence,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import __version__, chesscom, games_db, lichess, report, storage, theme
from .accounts import AccountsDialog
from .board import BoardWidget
from .crossreport import open_report as open_cross_report
from .editor import PositionEditor
from .engine import EngineBridge, GameAnalyzer, Line, move_accuracy
from .explorer import ExplorerDialog
from .evalgraph import EvalGraph
from .evalbar import EvalBar
from .icons import nav_icon
from .games import GamesDialog
from .movelist import MoveList
from .openings import OpeningsDialog
from .rating import RatingDialog
from .sound import Sounds
from .stats import StatsDialog
from .tournaments import TournamentsDialog
from .weaknesses import WeaknessDialog

DEPTH_CHOICES = ((18, "18 — fast"), (22, "22"), (26, "26 — default"),
                 (30, "30 — deep"), (None, "unlimited"))

# depth for analysing a whole game: a hundred positions in a row, so shallower
# than when analysing the single current one
ANALYSIS_DEPTH = 16

# how many seconds a user message is held against the engine line
NOTICE_HOLD = 8.0

TAG_MARKS = {"blunder": "??", "mistake": "?", "inaccuracy": "?!"}
TAG_COLORS = {"blunder": "#d1503f", "mistake": "#d98f3a", "inaccuracy": "#c9b24a"}


class BoardArea(QWidget):
    """Evaluation bar and board as one group centred in the column.

    The board is square while the space for it is usually wider - previously the
    board sat in the centre of its area and the bar stayed at the window edge.
    """

    GAP = 10
    board_moved = Signal(int, int)  # board margins on the left and right

    def __init__(self, bar: QWidget, board: QWidget) -> None:
        super().__init__()
        self._bar, self._board = bar, board
        bar.setParent(self)
        board.setParent(self)
        self.setMinimumSize(bar.width() + self.GAP + board.minimumWidth(),
                            board.minimumHeight())
        self._margins = (-1, -1)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        bar_w = self._bar.width()
        size = max(0, min(self.width() - bar_w - self.GAP, self.height()))
        x = (self.width() - bar_w - self.GAP - size) // 2
        y = (self.height() - size) // 2
        self._bar.setGeometry(x, y, bar_w, size)
        self._board.setGeometry(x + bar_w + self.GAP, y, size, size)
        margins = (x + bar_w + self.GAP, self.width() - x - bar_w - self.GAP - size)
        if margins != self._margins:
            self._margins = margins
            self.board_moved.emit(*margins)


class MainWindow(QMainWindow):
    def __init__(self, engine_path: str) -> None:
        super().__init__()
        self.setWindowTitle("ChessLab")
        self.resize(1180, 800)
        self.setStyleSheet(theme.QSS)

        # a game is a chess.pgn tree: any position can have several
        # continuations, so a move from the middle erases nothing
        self.game = chess.pgn.Game()
        self.node: chess.pgn.GameNode = self.game
        self._lines: list[Line] = []
        # who plays the game: names, ratings and which side is mine
        self.players: dict = {chess.WHITE: {}, chess.BLACK: {}, "me": None}
        # whose eyes the evaluation is shown through: "me" - mine, "white" - the usual convention
        self.score_pov = "me"
        self.game_id: str | None = None
        self.analysis: dict = {}
        self._analyzer: GameAnalyzer | None = None
        self._fetcher = None  # downloader for a single game by link
        self._notice_until = 0.0  # until when a user message is held
        # user arrows and circles, per game node: go forward and come back -
        # the marks are still there, otherwise analysing a position piecemeal is impossible.
        # The key is the node itself, not id(): with id() a deleted node could give up its number
        # to a new one, and the marks would resurface on an unrelated position
        self._marks_by_node: dict[object, dict] = {}
        self._engine_path = engine_path

        self.sounds = Sounds()

        self._build_ui()
        self._build_menu()

        self.engine = EngineBridge(engine_path, multipv=self.multipv_box.currentData())
        self.engine.ready.connect(self._on_engine_ready)
        self.engine.failed.connect(self._on_engine_failed)
        self.engine.updated.connect(self._on_engine_update)
        self.engine.start()

        self._refresh()

    # --- building the UI -------------------------------------------------

    def _build_ui(self) -> None:
        self.board_widget = BoardWidget()
        self.board_widget.move_played.connect(self._on_move_played)
        self.board_widget.marks_changed.connect(self._on_marks_changed)

        self.eval_bar = EvalBar(self.board_widget)

        board_area = BoardArea(self.eval_bar, self.board_widget)
        board_area.board_moved.connect(self._align_to_board)

        # player captions go above and below the board, but not above the eval bar;
        # the exact offset is set by _align_to_board once the board is in place
        indent = self.eval_bar.width() + BoardArea.GAP

        self.top_player = QLabel()
        self.top_player.setObjectName("player")
        self.top_player.setContentsMargins(indent, 0, 0, 0)
        self.top_player.setFont(theme.tabular(self.font(), 15))

        self.bottom_player = QLabel()
        self.bottom_player.setObjectName("player")
        self.bottom_player.setContentsMargins(indent, 0, 0, 0)
        self.bottom_player.setFont(theme.tabular(self.font(), 15))

        board_column = QVBoxLayout()
        board_column.setContentsMargins(theme.gap(4), theme.gap(3), theme.gap(2), theme.gap(3))
        board_column.setSpacing(8)
        self.graph = EvalGraph()
        self.graph.setContentsMargins(indent, 0, 0, 0)
        self.graph.setVisible(False)
        self.graph.ply_clicked.connect(self._goto)

        board_column.addWidget(self.top_player)
        board_column.addWidget(board_area, 1)
        board_column.addWidget(self.bottom_player)
        board_column.addWidget(self.graph)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.addLayout(board_column, 1)
        root.addWidget(self._build_side_panel())
        self.setCentralWidget(central)

    def _align_to_board(self, left: int, right: int) -> None:
        """Captions and graph match the board width exactly, not the whole column."""
        self.top_player.setContentsMargins(left, 0, right, 0)
        self.bottom_player.setContentsMargins(left, 0, right, 0)
        self.graph.setContentsMargins(left, 0, right, 0)

    def _build_menu(self) -> None:
        bar = self.menuBar()

        game = bar.addMenu("&Game")
        _add(self, game, "New", self.new_game, QKeySequence.StandardKey.New)
        _add(self, game, "Undo move", self.undo, QKeySequence.StandardKey.Undo)
        game.addSeparator()
        _add(self, game, "Set up position…", self.edit_position, "Ctrl+E")
        game.addSeparator()
        _add(self, game, "Save variations", self.save_variations, "Ctrl+S")
        game.addSeparator()
        _add(self, game, "Copy FEN", self.copy_fen, QKeySequence.StandardKey.Copy)
        _add(self, game, "Paste FEN", self.paste_fen, QKeySequence.StandardKey.Paste)
        game.addSeparator()
        _add(self, game, "Open game by link…", self.open_by_link, "Ctrl+L")
        _add(self, game, "Paste PGN from clipboard", self.paste_pgn, "Ctrl+Shift+V")
        _add(self, game, "Open PGN file…", self.open_pgn_file,
             QKeySequence.StandardKey.Open)
        game.addSeparator()
        # StandardKey.Quit on this system resolves to the multimedia key
        # "Exit" rather than Ctrl+Q - so the shortcut is set explicitly
        _add(self, game, "Quit", self.close, "Ctrl+Q")

        board = bar.addMenu("&Board")
        _add(self, board, "To start", self.go_start, Qt.Key.Key_Home)
        _add(self, board, "Move back", self.go_back, Qt.Key.Key_Left)
        _add(self, board, "Move forward", self.go_forward, Qt.Key.Key_Right)
        _add(self, board, "To end", self.go_end, Qt.Key.Key_End)
        board.addSeparator()
        _add(self, board, "Flip board", self.flip, Qt.Key.Key_F)

        self.arrow_action = QAction("Best move arrow", self, checkable=True)
        self.arrow_action.setChecked(True)
        self.arrow_action.toggled.connect(self._on_arrow_toggled)
        board.addAction(self.arrow_action)

        self.sound_action = QAction("Move sound", self, checkable=True)
        self.sound_action.setChecked(self.sounds.enabled)
        self.sound_action.setEnabled(self.sounds.available)
        if not self.sounds.available:
            self.sound_action.setToolTip("Sound unavailable: QtMultimedia missing")
        self.sound_action.toggled.connect(self._on_sound_toggled)
        board.addAction(self.sound_action)

        engine = bar.addMenu("&Engine")

        pov_menu = engine.addMenu("Evaluation")
        self._pov_group = QActionGroup(self)
        self._pov_group.setExclusive(True)
        for value, label in (("me", "from my side"), ("white", "from White's side")):
            action = QAction(label, self, checkable=True)
            action.setData(value)
            action.setChecked(value == self.score_pov)
            action.triggered.connect(self._on_pov_action)
            self._pov_group.addAction(action)
            pov_menu.addAction(action)

        self.analyse_action = _add(self, engine, "Analyse game",
                                   self.analyse_game, "Ctrl+R")
        _add(self, engine, "Export written report…",
             self.export_report, "Ctrl+Shift+R")
        engine.addSeparator()

        depth_menu = engine.addMenu("Depth")
        self._depth_group = QActionGroup(self)
        self._depth_group.setExclusive(True)
        for value, label in DEPTH_CHOICES:
            action = QAction(label, self, checkable=True)
            action.setData(value)
            action.setChecked(value == 26)
            action.triggered.connect(self._on_depth_action)
            self._depth_group.addAction(action)
            depth_menu.addAction(action)

        archive = bar.addMenu("&Library")
        _add(self, archive, "My games…", self.open_games, "Ctrl+G")
        _add(self, archive, "Opening explorer…", self.open_explorer, "Ctrl+B")
        _add(self, archive, "Opening statistics…", self.open_openings, "Ctrl+D")
        _add(self, archive, "Opening weaknesses…", self.open_weaknesses, "Ctrl+K")
        archive.addSeparator()
        _add(self, archive, "My tournaments…", self.open_tournaments, "Ctrl+T")
        _add(self, archive, "Tournament report from chess-results…", self.open_cross_report)
        archive.addSeparator()
        _add(self, archive, "Rating progress…", self.open_rating, "Ctrl+Y")

        accounts = bar.addMenu("&Accounts")
        _add(self, accounts, "Lichess and Chess.com…", self.open_accounts)
        _add(self, accounts, "Statistics…", self.open_stats, "Ctrl+I")

        help_menu = bar.addMenu("&Help")
        _add(self, help_menu, "About", self.about)

    def _build_side_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        panel.setFixedWidth(360)

        layout = QVBoxLayout(panel)
        layout.setContentsMargins(theme.gap(4), theme.gap(4), theme.gap(4), theme.gap(4))
        layout.setSpacing(10)

        # Panel header: the big evaluation is the one number you watch
        # constantly, so it should be the most prominent.
        self._engine_name = "ENGINE"
        self.eval_label = QLabel("—")
        self.eval_label.setObjectName("eval")
        self.eval_label.setFont(theme.tabular(self.font(), 26, bold=True))

        self.engine_label = QLabel("engine starting…")
        self.engine_label.setObjectName("heading")
        self.engine_label.setAlignment(
            Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
        )

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, theme.gap(1))
        header.addWidget(self.eval_label)
        header.addStretch(1)
        header.addWidget(self.engine_label)
        layout.addLayout(header)

        self.lines_table = _bare_table(2, "lines")
        self.lines_table.setMinimumHeight(180)
        self.lines_table.setToolTip("Click a line to play its move")
        self.lines_table.setCursor(Qt.CursorShape.PointingHandCursor)
        self.lines_table.cellClicked.connect(self._on_line_clicked)
        header = self.lines_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.lines_table, 2)

        layout.addLayout(self._build_engine_controls())

        moves_label = QLabel("MOVES")
        moves_label.setObjectName("heading")
        layout.addWidget(moves_label)

        self.move_list = MoveList()
        self.move_list.setToolTip(
            "A move from the middle of the game creates a variation.\n"
            "Right-click a move to make it main or delete it."
        )
        self.move_list.node_chosen.connect(self._on_node_chosen)
        self.move_list.node_menu_requested.connect(self._on_move_menu)
        layout.addWidget(self.move_list, 3)

        layout.addLayout(self._build_nav_buttons())
        layout.addLayout(self._build_action_buttons())

        self.status_label = QLabel("")
        self.status_label.setObjectName("status")
        layout.addWidget(self.status_label)

        return panel

    def _build_engine_controls(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(8)

        row.addWidget(QLabel("lines"))
        # a list, not a counter: there are only five values, and QSpinBox arrows
        # look dirty on a dark theme
        self.multipv_box = QComboBox()
        for value in range(1, 6):
            self.multipv_box.addItem(str(value), value)
        self.multipv_box.setCurrentIndex(2)  # three lines
        self.multipv_box.setFixedWidth(58)
        self.multipv_box.currentIndexChanged.connect(self._on_multipv_changed)
        row.addWidget(self.multipv_box)

        row.addStretch(1)

        self.arrow_check = QCheckBox("arrow")
        self.arrow_check.setChecked(True)
        self.arrow_check.toggled.connect(self._on_arrow_toggled)
        row.addWidget(self.arrow_check)

        return row

    def _build_nav_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        for kind, tip, slot in (
            ("start", "To start (Home)", self.go_start),
            ("back", "Back (←)", self.go_back),
            ("forward", "Forward (→)", self.go_forward),
            ("end", "To end (End)", self.go_end),
        ):
            button = QPushButton()
            button.setIcon(nav_icon(kind))
            button.setToolTip(tip)
            button.clicked.connect(slot)
            # without this the arrow keys would move focus between buttons
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.addWidget(button)
        return row

    def _build_action_buttons(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)
        for text, tip, slot in (
            ("New", "Start over (Ctrl+N)", self.new_game),
            ("Undo", "Take back the last move (Ctrl+Z)", self.undo),
            ("Flip", "Switch sides (F)", self.flip),
            ("FEN", "Copy FEN (Ctrl+C)", self.copy_fen),
        ):
            button = QPushButton(text)
            # a long caption got clipped on the 360 px panel: shorter caption and margins
            button.setObjectName("compact")
            button.setToolTip(tip)
            button.clicked.connect(slot)
            # without this the arrow keys would move focus between buttons
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            row.addWidget(button)
        return row

    # Keys are handled here too, not only via menu shortcuts:
    # a shortcut with window context stays silent if the window is not considered active,
    # and stepping through a game with the arrows must always work.
    _NAV_KEYS = {
        Qt.Key.Key_Left: "go_back",
        Qt.Key.Key_Right: "go_forward",
        Qt.Key.Key_Home: "go_start",
        Qt.Key.Key_End: "go_end",
    }

    def keyPressEvent(self, event) -> None:
        handler = self._NAV_KEYS.get(event.key())
        if handler is not None and event.modifiers() == Qt.KeyboardModifier.NoModifier:
            getattr(self, handler)()
            event.accept()
            return
        super().keyPressEvent(event)

    # --- game state ------------------------------------------------------

    @property
    def start_fen(self) -> str:
        return self.game.board().fen()

    @property
    def mainline(self) -> list[chess.Move]:
        return list(self.game.mainline_moves())

    @property
    def current_board(self) -> chess.Board:
        return self.node.board()

    @property
    def ply(self) -> int:
        """Half-move number from the start of the game."""
        return self.node.ply() - self.game.ply()

    def _mainline_ply(self) -> int:
        """Nearest point on the main line - for the graph and analysis."""
        node = self.node
        while node.parent is not None and not node.is_mainline():
            node = node.parent
        return node.ply() - self.game.ply()

    def _reset_game(self, board: chess.Board | None = None) -> None:
        self.game = chess.pgn.Game()
        if board is not None:
            self.game.setup(board)
        self.node = self.game
        self._marks_by_node = {}

    def _refresh(self) -> None:
        board = self.current_board
        last_move = self.node.move if self.node.parent is not None else None
        self.board_widget.set_position(board, last_move)
        self.board_widget.set_marks(self._marks_by_node.get(self.node, {}))
        self.board_widget.set_best_move(None)
        self.eval_bar.set_score(None, None)
        self.eval_label.setText("…")
        self._lines = []
        self.lines_table.setRowCount(0)
        self._update_move_list()
        self._update_player_bars()
        self._update_engine_header()
        self.eval_bar.set_pov(self.score_sign())
        self.graph.set_current(self._mainline_ply())
        self._update_status(board)
        self.engine.analyse(board.fen())

    def _mainline_tags(self) -> dict[int, str]:
        """Analysis marks attached to main-line nodes.

        Analysis is computed on the main line, so variations have no marks.
        """
        tags: dict[int, str] = {}
        if not self.analysis:
            return tags
        node = self.game
        ply = 0
        while node.variations:
            node = node.variations[0]
            ply += 1
            row = self.analysis.get(ply)
            if row is not None and row["tag"]:
                tags[id(node)] = row["tag"]
        return tags

    def _update_move_list(self) -> None:
        self.move_list.set_game(self.game, self.node, self._mainline_tags())

    def _refill_lines(self) -> None:
        """Redraw the engine lines. Separate from data arrival -
        also needed when the side whose eyes we use for the evaluation changes."""
        sign = self.score_sign()
        self.lines_table.setRowCount(len(self._lines))
        for row, line in enumerate(self._lines):
            score_item = QTableWidgetItem(_score_text(line, sign))
            score_item.setForeground(_score_color(line, sign))
            score_item.setFont(theme.tabular(self.font(), bold=True))
            score_item.setTextAlignment(
                Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter
            )
            self.lines_table.setItem(row, 0, score_item)

            move_item = QTableWidgetItem(line.text())
            move_item.setToolTip(" ".join(line.san))
            self.lines_table.setItem(row, 1, move_item)
        self.lines_table.resizeRowsToContents()

    def _update_engine_header(self) -> None:
        """The header shows whose move the engine is computing."""
        board = self.current_board
        me = self.players.get("me")

        if board.is_game_over():
            turn = "game over"
        elif me is None:
            turn = "White to move" if board.turn == chess.WHITE else "Black to move"
        elif board.turn == me:
            turn = "your move"
        else:
            turn = "opponent's move"

        self.engine_label.setText(f"{self._engine_name}   ·   {turn}")

    # --- player captions -------------------------------------------------

    def _known_names(self) -> set[str]:
        """Names I play under: accounts plus participation in my own tournaments."""
        names = {a["username"].lower() for a in storage.load_accounts()}
        try:
            conn = games_db.connect()
            names |= {
                (row["player"] or "").lower()
                for row in conn.execute("SELECT DISTINCT player FROM tournaments")
            }
            conn.close()
        except sqlite3.Error:
            pass
        return names - {""}

    def _set_players(self, white: str, black: str,
                     white_elo: str = "", black_elo: str = "") -> None:
        mine = self._known_names()
        if white.lower() in mine:
            me = chess.WHITE
        elif black.lower() in mine:
            me = chess.BLACK
        else:
            me = None

        self.players = {
            chess.WHITE: {"name": white, "elo": white_elo},
            chess.BLACK: {"name": black, "elo": black_elo},
            "me": me,
        }

        # turn the board with my side at the bottom
        if me is not None:
            self._set_flipped(me == chess.BLACK)

    def _clear_players(self) -> None:
        self.players = {chess.WHITE: {}, chess.BLACK: {}, "me": None}

    def _set_flipped(self, flipped: bool) -> None:
        if self.board_widget.flipped != flipped:
            self.board_widget.flip()
        self.eval_bar.set_flipped(self.board_widget.flipped)

    def _update_player_bars(self) -> None:
        board = self.current_board
        top_color = chess.WHITE if self.board_widget.flipped else chess.BLACK
        self.top_player.setText(self._player_text(top_color, board))
        self.bottom_player.setText(self._player_text(not top_color, board))

    def _player_text(self, color: bool, board: chess.Board) -> str:
        info = self.players.get(color) or {}
        name = info.get("name") or ("White" if color == chess.WHITE else "Black")
        elo = info.get("elo")

        to_move = board.turn == color and not board.is_game_over()
        dot_color = theme.ACCENT.name() if to_move else "#4a4a55"
        muted = theme.TEXT_MUTED.name()

        parts = [
            f"<span style='color:{dot_color}'>●</span>",
            f"<b>{escape(str(name))}</b>",
        ]
        if elo:
            parts.append(f"<span style='color:{muted}'>{escape(str(elo))}</span>")
        if self.players.get("me") == color:
            parts.append(f"<span style='color:{theme.ACCENT.name()}'>· me</span>")
        return "&nbsp;&nbsp;".join(parts)

    def _notice(self, text: str) -> None:
        """User message in the status line.

        Hold it for a few seconds: the engine writes depth there several
        times a second, and without a delay the message cannot be read.
        """
        self.status_label.setText(text)
        self._notice_until = time.monotonic() + NOTICE_HOLD

    def _update_status(self, board: chess.Board) -> None:
        if board.is_checkmate():
            winner = "Black" if board.turn == chess.WHITE else "White"
            self.status_label.setText(f"checkmate — {winner} wins")
        elif board.is_stalemate():
            self.status_label.setText("stalemate")
        elif board.is_insufficient_material():
            self.status_label.setText("draw: insufficient material")
        elif board.can_claim_threefold_repetition():
            self.status_label.setText("draw: threefold repetition")
        elif board.is_check():
            self.status_label.setText("check")
        else:
            self.status_label.setText("White to move" if board.turn else "Black to move")

    # --- actions ---------------------------------------------------------

    @Slot(object)
    def _on_move_played(self, move: chess.Move) -> None:
        self.sounds.play(self._sound_for(move))

        # A move from the middle of the game erases nothing: if such a continuation
        # already exists we just step into it, otherwise we create a new variation.
        existing = self.node.variation(move) if self.node.has_variation(move) else None
        self.node = existing if existing is not None else self.node.add_variation(move)
        self._refresh()

    def _sound_for(self, move: chess.Move) -> str:
        """Which sound suits the move. Call before the move is made."""
        board = self.current_board
        kind = "capture" if board.is_capture(move) else "move"
        probe = board.copy(stack=False)
        probe.push(move)
        return "check" if probe.is_check() else kind

    def _go_to_node(self, node) -> None:
        if node is not self.node:
            self.node = node
            self._refresh()
            self.sounds.play("move")

    @Slot(int)
    def _goto(self, ply: int) -> None:
        """Jump to a half-move number - used by the evaluation graph."""
        node = self.game
        for _ in range(max(0, ply)):
            if not node.variations:
                break
            node = node.variations[0]
        self._go_to_node(node)

    def go_start(self) -> None:
        self._go_to_node(self.game)

    def go_back(self) -> None:
        if self.node.parent is not None:
            self._go_to_node(self.node.parent)

    def go_forward(self) -> None:
        if self.node.variations:
            self._go_to_node(self.node.variations[0])

    def go_end(self) -> None:
        node = self.node
        while node.variations:
            node = node.variations[0]
        self._go_to_node(node)

    @Slot(object)
    def _on_node_chosen(self, node) -> None:
        self._go_to_node(node)

    @Slot(int, int)
    def _on_line_clicked(self, row: int, column: int) -> None:
        """Click on an engine line - play the first move of that line."""
        if not (0 <= row < len(self._lines)):
            return
        uci = self._lines[row].best_move
        if not uci:
            return
        try:
            move = chess.Move.from_uci(uci)
        except ValueError:
            return
        if move in self.current_board.legal_moves:
            self._on_move_played(move)

    def new_game(self) -> None:
        self._reset_game()
        self._clear_players()
        self.game_id = None
        self.analysis = {}
        self.graph.clear()
        self.graph.setVisible(False)
        self.setWindowTitle("ChessLab")
        self._refresh()

    def undo(self) -> None:
        """Remove the current move together with its continuation."""
        if self.node.parent is None:
            return
        parent = self.node.parent
        parent.remove_variation(self.node.move)
        self.node = parent
        self._refresh()

    # --- variations ------------------------------------------------------

    @Slot()
    def save_variations(self) -> None:
        """Rewrite the game's PGN in the database - including the added variations."""
        if not self.game_id:
            self._notice(
                "This game is not from the database, nowhere to save. Copy the PGN manually."
            )
            return
        try:
            conn = games_db.connect()
            conn.execute("UPDATE games SET pgn = ? WHERE id = ?",
                         (str(self.game), self.game_id))
            conn.commit()
            conn.close()
        except sqlite3.Error as exc:
            self._notice(f"not saved: {exc}")
            return
        self._notice("variations saved to the database")

    @Slot(object, object)
    def _on_move_menu(self, node, position) -> None:
        if node.parent is None:
            return
        menu = QMenu(self)
        promote = menu.addAction("Make main line")
        promote.setEnabled(not node.is_mainline())
        up = menu.addAction("Move variation up")
        up.setEnabled(node.parent.variations[0] is not node)
        menu.addSeparator()
        remove = menu.addAction("Delete variation")

        chosen = menu.exec(position)
        if chosen is None:
            return

        parent = node.parent
        if chosen is promote:
            _promote_line(node)
        elif chosen is up:
            parent.promote(node)
        elif chosen is remove:
            # if we are standing inside the branch being deleted, step back to its start
            if _is_descendant(self.node, node):
                self.node = parent
            parent.remove_variation(node.move)
        self._refresh()

    def flip(self) -> None:
        self.board_widget.flip()
        self.eval_bar.set_flipped(self.board_widget.flipped)
        self.graph.set_flipped(self.board_widget.flipped)
        self._update_player_bars()

    def copy_fen(self) -> None:
        QGuiApplication.clipboard().setText(self.current_board.fen())
        self._notice("FEN copied")

    def paste_fen(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        try:
            board = chess.Board(text)
        except ValueError:
            QMessageBox.warning(self, "ChessLab", "The clipboard doesn't look like a FEN.")
            return
        self._reset_game(board)
        self._clear_players()
        self.game_id = None
        self.analysis = {}
        self.graph.clear()
        self.graph.setVisible(False)
        self.setWindowTitle("ChessLab")
        self._refresh()

    @Slot(object)
    def _on_marks_changed(self, marks: dict) -> None:
        """Marks belong to the position, not to the board."""
        if marks:
            self._marks_by_node[self.node] = marks
        else:
            self._marks_by_node.pop(self.node, None)

    @Slot()
    def open_by_link(self) -> None:
        """A game by link from Chess.com or Lichess.

        First look for it locally: an already downloaded game opens instantly and
        with its saved analysis. We go to the network only if it is not in the database.
        """
        clipboard = QGuiApplication.clipboard().text().strip()
        guess = clipboard if ("chess.com" in clipboard or "lichess.org" in clipboard) else ""
        text, chosen = QInputDialog.getText(
            self, "Open game by link",
            "Game link (Chess.com or Lichess):", text=guess,
        )
        if not chosen:
            return

        number = chesscom.game_id_from_url(text)
        code = lichess.game_id_from_url(text)
        if number:
            client, needle = chesscom, f"game/live/{number}"
        elif code:
            client, needle = lichess, code
        else:
            QMessageBox.warning(
                self, "ChessLab",
                "Cannot parse the link. A game link is needed, like\n"
                "chess.com/game/live/… or lichess.org/…",
            )
            return

        try:
            conn = games_db.connect()
            row = games_db.find_by_link(conn, needle)
            conn.close()
        except sqlite3.Error:
            row = None
        if row is not None and row["pgn"]:
            self.load_pgn(row["pgn"], row["id"])
            self._notice("game found in the database — analysis will be kept")
            return

        self._notice("downloading game…")
        self._fetcher = client.GameFetcher()
        self._fetcher.found.connect(self._on_link_game)
        self._fetcher.failed.connect(self._on_link_failed)
        self._fetcher.start(number or code)

    @Slot(str)
    def _on_link_game(self, pgn_text: str) -> None:
        self._fetcher = None
        self._import_pgn(pgn_text, "The game came back empty.")

    @Slot(str)
    def _on_link_failed(self, message: str) -> None:
        self._fetcher = None
        self._notice(message)

    @Slot()
    def paste_pgn(self) -> None:
        """A game from the clipboard - "Share -> PGN" on any site.

        Needed for games not in the username archive: against bots, other people's,
        analysed elsewhere. We don't write these to the database, otherwise they would
        creep into opening statistics alongside the real ones.
        """
        self._import_pgn(QGuiApplication.clipboard().text(), "The clipboard doesn't look like a PGN.")

    @Slot()
    def open_pgn_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Open PGN", str(Path.home()), "Games (*.pgn *.txt);;All files (*)"
        )
        if path:
            self.open_pgn_path(path)

    def open_pgn_path(self, path: str) -> None:
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            QMessageBox.warning(self, "ChessLab", f"Cannot read the file: {exc}")
            return
        self._import_pgn(text, f"No game found in the file: {Path(path).name}")

    def _import_pgn(self, text: str, complaint: str) -> None:
        text = (text or "").strip()
        # read_game returns an empty game on any garbage, not None,
        # so we check for moves specifically, otherwise the board silently resets
        game = chess.pgn.read_game(io.StringIO(text)) if text else None
        if game is None or not any(game.mainline_moves()):
            QMessageBox.warning(self, "ChessLab", complaint)
            return
        # without game_id: analysis of such a game does not go to the database and does
        # not survive closing the window - but it doesn't pollute the database either
        self.load_pgn(text)
        self._notice(
            "game loaded — Ctrl+R analyses it, Ctrl+Shift+R exports the analysis"
        )

    @Slot(int)
    def _on_multipv_changed(self, _index: int) -> None:
        self.engine.set_multipv(self.multipv_box.currentData())
        self.engine.analyse(self.current_board.fen())

    @Slot(bool)
    def _on_arrow_toggled(self, checked: bool) -> None:
        self.board_widget.show_arrow = checked
        self.board_widget.update()
        # the checkbox lives in both the menu and the panel - keep them in sync
        for widget in (self.arrow_check, getattr(self, "arrow_action", None)):
            if widget is not None and widget.isChecked() != checked:
                widget.blockSignals(True)
                widget.setChecked(checked)
                widget.blockSignals(False)

    # --- whole-game analysis ---------------------------------------------

    @Slot()
    def analyse_game(self) -> None:
        if self._analyzer is not None:  # already running - a second press aborts
            self._analyzer.cancel()
            self._notice("aborting analysis…")
            return
        if not self.mainline:
            self._notice("Open a game first.")
            return

        self._analyzer = GameAnalyzer(self._engine_path)
        self._analyzer.progress.connect(self._on_analysis_progress)
        self._analyzer.finished.connect(self._on_analysis_done)
        self._analyzer.failed.connect(self._on_analysis_failed)
        self.analyse_action.setText("Stop analysis")
        self._analyzer.start(self.game_id or "", self.start_fen, self.mainline,
                             depth=ANALYSIS_DEPTH)

    @Slot(int, int)
    def _on_analysis_progress(self, done: int, total: int) -> None:
        self._notice(f"analysis: {done} of {total} positions")

    @Slot(list)
    def _on_analysis_done(self, rows: list[dict]) -> None:
        self._finish_analysis()
        self.analysis = {row["ply"]: row for row in rows}

        if self.game_id:
            try:
                conn = games_db.connect()
                games_db.save_analysis(conn, self.game_id, rows)
                conn.close()
            except sqlite3.Error as exc:
                self._notice(f"analysis computed but not saved: {exc}")

        self._apply_analysis()

    @Slot(str)
    def _on_analysis_failed(self, message: str) -> None:
        self._finish_analysis()
        self._notice(message)

    def _finish_analysis(self) -> None:
        self._analyzer = None
        self.analyse_action.setText("Analyse game")

    def _load_analysis(self) -> None:
        """Load the saved analysis, if there is one."""
        self.analysis = {}
        if self.game_id:
            try:
                conn = games_db.connect()
                self.analysis = games_db.load_analysis(conn, self.game_id)
                conn.close()
            except sqlite3.Error:
                pass

    def _apply_analysis(self) -> None:
        """Show the analysis: graph, marks in the move list, summary."""
        if not self.analysis:
            self.graph.clear()
            self.graph.setVisible(False)  # no point in a graph for an unanalysed game
            self._update_move_list()
            return

        self.graph.setVisible(True)

        points = [
            (self.analysis[ply]["cp"] if ply in self.analysis else 0) or 0
            for ply in range(len(self.mainline) + 1)
        ]
        tags = {
            ply: row["tag"] for ply, row in self.analysis.items() if row["tag"]
        }
        self.graph.set_data(points, tags)
        self.graph.set_flipped(self.board_widget.flipped)
        self.graph.set_current(self._mainline_ply())
        self._update_move_list()
        self._notice(self._analysis_summary())

    def _analysis_summary(self) -> str:
        sides = {chess.WHITE: [], chess.BLACK: []}
        counts = {chess.WHITE: {}, chess.BLACK: {}}
        board = chess.Board(self.start_fen)

        for index, move in enumerate(self.mainline):
            row = self.analysis.get(index + 1)
            if row and row["loss"] is not None:
                sides[board.turn].append(row["loss"])
                if row["tag"]:
                    counts[board.turn][row["tag"]] = \
                        counts[board.turn].get(row["tag"], 0) + 1
            board.push(move)

        me = self.players.get("me")
        color = me if me is not None else chess.WHITE
        losses = sides[color]
        if not losses:
            return "analysis ready"

        accuracy = sum(move_accuracy(loss) for loss in losses) / len(losses)
        tally = counts[color]
        who = "your accuracy" if me is not None else "White's accuracy"
        parts = [f"{who} {accuracy:.0f}%"]
        for tag, label in (("blunder", "blunders"), ("mistake", "mistakes"),
                           ("inaccuracy", "inaccuracies")):
            if tally.get(tag):
                parts.append(f"{label} {tally[tag]}")
        return "   ·   ".join(parts)

    @Slot()
    def export_report(self) -> None:
        """Analysis as markdown: to the clipboard and as a file next to the database.

        The engine explains with numbers; to get an explanation in words, it is
        convenient to hand the whole game to an outside tool - hence this text,
        which can simply be pasted into a chat.
        """
        if not self.mainline:
            self._notice("Open a game first.")
            return
        if not self.analysis:
            self._notice("Analyse the game first: Ctrl+R.")
            return

        text = report.build(self.game, self.start_fen, self.mainline,
                            self.analysis, self.players.get("me"),
                            depth=ANALYSIS_DEPTH)
        QGuiApplication.clipboard().setText(text)
        try:
            path = report.save(self.game, text)
        except OSError as exc:
            self._notice(
                f"analysis is on the clipboard, but the file was not written: {exc}")
            return
        self._notice(f"analysis on the clipboard and in {_short(path)}")

    def score_sign(self) -> int:
        """-1 if the evaluation must be flipped to my side."""
        if self.score_pov == "me" and self.players.get("me") == chess.BLACK:
            return -1
        return 1

    @Slot()
    def _on_pov_action(self) -> None:
        action = self.sender()
        if action is None:
            return
        self.score_pov = action.data()
        self.eval_bar.set_pov(self.score_sign())
        self._refill_lines()
        self._update_engine_header()

    @Slot(bool)
    def _on_sound_toggled(self, checked: bool) -> None:
        self.sounds.enabled = checked

    @Slot()
    def _on_depth_action(self) -> None:
        action = self.sender()
        if action is None:
            return
        depth = action.data()
        self.engine.set_max_depth(depth)
        self.engine.analyse(self.current_board.fen())
        self._notice(
            "unlimited depth" if depth is None else f"depth up to {depth}"
        )

    @Slot()
    def open_accounts(self) -> None:
        AccountsDialog(self).exec()

    @Slot()
    def open_games(self, initial_search: str = "") -> None:
        dialog = GamesDialog(self, initial_search=initial_search)
        dialog.game_chosen.connect(self.load_pgn)
        dialog.exec()

    @Slot()
    def open_tournaments(self) -> None:
        dialog = TournamentsDialog(self)
        dialog.game_chosen.connect(self.load_pgn)
        dialog.exec()

    @Slot()
    def open_cross_report(self) -> None:
        """Crosstable from chess-results: my result against the whole tournament."""
        conn = games_db.connect()
        try:
            row = conn.execute(
                "SELECT player FROM tournaments"
                " WHERE player != '' ORDER BY started DESC, id DESC LIMIT 1"
            ).fetchone()
            open_cross_report(self, conn, player=row["player"] if row else "")
        finally:
            conn.close()

    @Slot()
    def edit_position(self) -> None:
        dialog = PositionEditor(self.current_board.fen(),
                                self.board_widget.flipped, self)
        dialog.position_ready.connect(self.set_position_fen)
        dialog.exec()

    @Slot(str)
    def set_position_fen(self, fen: str) -> None:
        try:
            board = chess.Board(fen)
        except ValueError:
            QMessageBox.warning(self, "ChessLab", "Cannot read the position.")
            return
        self._reset_game(board)
        self._clear_players()
        self.game_id = None
        self.analysis = {}
        self.graph.clear()
        self.graph.setVisible(False)
        self.setWindowTitle("ChessLab — custom position")
        self._refresh()

    @Slot()
    def open_rating(self) -> None:
        RatingDialog(self).exec()

    @Slot()
    def open_stats(self) -> None:
        StatsDialog(self).exec()

    @Slot()
    def open_explorer(self) -> None:
        dialog = ExplorerDialog(self)
        dialog.game_chosen.connect(self.load_pgn)
        dialog.line_chosen.connect(self.load_line)
        dialog.exec()

    @Slot(str)
    def load_line(self, pgn_text: str) -> None:
        """A line from the openings window: stand at its end, not at the start of the game."""
        self.load_pgn(pgn_text)
        self.go_end()

    @Slot()
    def open_weaknesses(self) -> None:
        dialog = WeaknessDialog(self._engine_path, self)
        dialog.line_chosen.connect(self.load_line)
        dialog.exec()

    @Slot()
    def open_openings(self) -> None:
        dialog = OpeningsDialog(self)
        # from the statistics you can drill down into games of the chosen opening
        dialog.opening_chosen.connect(self.open_games)
        dialog.exec()

    @Slot(str)
    @Slot(str, str)
    def load_pgn(self, pgn_text: str, game_id: str = "") -> None:
        game = chess.pgn.read_game(io.StringIO(pgn_text))
        if game is None:
            QMessageBox.warning(self, "ChessLab", "Cannot parse this game's PGN.")
            return

        self.game_id = game_id or None
        self._load_analysis()
        self._marks_by_node = {}

        self.game = game
        self.node = self.game  # easier to review a game from the start

        white = game.headers.get("White", "?")
        black = game.headers.get("Black", "?")
        # before _refresh: it already draws the captions and may flip the board
        self._set_players(
            white, black,
            game.headers.get("WhiteElo", ""), game.headers.get("BlackElo", ""),
        )
        self._refresh()
        self._apply_analysis()
        self.setWindowTitle(f"ChessLab — {white} — {black}")

    @Slot()
    def about(self) -> None:
        QMessageBox.about(
            self,
            "ChessLab",
            f"<b>ChessLab {__version__}</b><br><br>"
            "A custom board with Stockfish analysis.<br>"
            "Qt6 via PySide6, rules and UCI via python-chess.<br><br>"
            f"Data is stored in <code>~/.local/share/chesslab</code>.",
        )

    # --- engine signals --------------------------------------------------

    @Slot(str)
    def _on_engine_ready(self, name: str) -> None:
        self._engine_name = name.upper()
        self._update_engine_header()

    @Slot(str)
    def _on_engine_failed(self, message: str) -> None:
        self.engine_label.setText("engine unavailable")
        self._notice(message)

    @Slot(str, int, int, list)
    def _on_engine_update(self, fen: str, depth: int, nps: int, lines: list[Line]) -> None:
        # while computing the position may have changed - such a reply is no longer needed
        if fen != self.current_board.fen():
            return

        self._lines = lines
        self._refill_lines()

        if lines:
            best = lines[0]
            self.eval_bar.set_score(best.score_cp, best.mate_in)
            self.board_widget.set_best_move(best.best_move)
            sign = self.score_sign()
            self.eval_label.setText(
                f'<span style="color:{_score_color(best, sign).name()}">'
                f'{_score_text(best, sign)}</span>'
            )
        else:
            self.board_widget.set_best_move(None)
            self.eval_label.setText("—")

        if depth and time.monotonic() >= self._notice_until:
            self.status_label.setText(f"depth {depth}   ·   {nps / 1000:.0f}k nodes/s")

    # --- closing ---------------------------------------------------------

    def closeEvent(self, event) -> None:
        self.engine.shutdown()
        super().closeEvent(event)


def _short(path: Path) -> str:
    """Path with ~ instead of the home folder - shorter in the status line."""
    try:
        return "~/" + str(path.relative_to(Path.home()))
    except ValueError:
        return str(path)


def _promote_line(node) -> None:
    """Make the whole branch leading to node the main line, not just the move itself.

    promote_to_main only raises a node among its siblings. To make a variation
    the main line entirely, forks must be fixed from the top down.
    """
    while not node.is_mainline():
        highest = None
        walker = node
        while walker.parent is not None:
            if walker.parent.variations[0] is not walker:
                highest = walker
            walker = walker.parent
        if highest is None:
            return
        highest.parent.promote_to_main(highest)


def _is_descendant(node, ancestor) -> bool:
    """Whether node lies inside the branch of ancestor (including ancestor itself)."""
    while node is not None:
        if node is ancestor:
            return True
        node = node.parent
    return False


def _bare_table(columns: int, name: str) -> QTableWidget:
    """A table without header, grid and cell borders - just even rows."""
    table = QTableWidget(0, columns)
    table.setObjectName(name)
    table.horizontalHeader().setVisible(False)
    table.verticalHeader().setVisible(False)
    table.setShowGrid(False)
    table.setWordWrap(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
    return table


def _add(window, menu, title: str, slot, shortcut=None) -> QAction:
    """The parent of the action must be the WINDOW, not the menu.

    A shortcut works within the window it belongs to.
    If the parent is a QMenu, that is a popup window, and the keys work
    nowhere except in the open menu: that is exactly why the arrows did not move the game.
    """
    action = QAction(title, window)
    if shortcut is not None:
        action.setShortcut(QKeySequence(shortcut))
    action.triggered.connect(slot)
    menu.addAction(action)
        # one parent is not enough: until the action is added to the window it does not appear
        # in window.actions() and the shortcut does not fire
    window.addAction(action)
    return action


def _score_text(line: Line, sign: int = 1) -> str:
    if line.mate_in is not None:
        return f"#{line.mate_in * sign:+d}"
    if line.score_cp is None:
        return "—"
    return f"{line.score_cp * sign / 100:+.2f}"


def _score_color(line: Line, sign: int = 1):
    """Green is good for the side whose eyes we use, red is bad."""
    if line.mate_in is not None:
        return theme.SCORE_GOOD if line.mate_in * sign > 0 else theme.SCORE_BAD
    if line.score_cp is None:
        return theme.TEXT_MUTED
    value = line.score_cp * sign
    if value > 50:
        return theme.SCORE_GOOD
    if value < -50:
        return theme.SCORE_BAD
    return theme.TEXT
