"""Opening weaknesses: key positions and leaving named lines.

The window sits on top of the calculation in `weakspots.py` — only the view is here.
There are two different questions, hence two tabs:

- "Key positions" — where the player moves from the same position over and
  over and what it costs them. This is about a recurring hole, not about a
  random blunder in a single game.
- "Out of book" — how far a game follows the reference's named lines and
  who deviates first. The ECO reference knows only named
  variations — 3810 lines, not all of theory — so an early exit says
  nothing by itself: look at the evaluation at that moment.

The engine analysis in the `positions` cache is often incomplete, so there is
also an "Analyse with engine" button: it runs `OpeningAnalyzer` over the games
under the current filters and redraws both tabs when done.
"""

from __future__ import annotations

import chess
import chess.pgn
from PySide6.QtCore import QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor, QFont, QPainter, QPen
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import games_db, storage, theme, weakspots
from .board import BoardWidget
from .engine import OpeningAnalyzer
from .explorer import _line_text
from .games import SPEEDS
from .openings import COLORS, ScoreBarDelegate

PLIES = 16   # same number of plies as weakspots and analyse_openings use
DEPTH = 16   # engine depth for the "Analyse with engine" button
THREADS = 8  # analysing an offline sample, so the machine can be loaded harder than interactively

SPOT_HEADERS = ["Cost", "Games", "Score", "Position"]
MOVE_HEADERS = ["Move", "Games", "Score", "Loss"]
# headers are short on purpose: long ones ate the width of the opening name,
# which then got truncated with an ellipsis. What they mean is in the header tooltips
EXIT_HEADERS = ["Opening", "Games", "Left first", "Ply", "Score", "Loss"]

EXIT_TIPS = {
    1: "In how many games this opening was reached",
    2: "In how many of them the player was first to leave the named lines",
    3: "At which ply on average the name ended",
    4: "The player's score share in these games",
    5: "How many chances the player lost with the move that left the line",
}

# same thresholds and colors as the ?? ?! marks in the main window — the same
# words should look the same, rather than introduce their own palette
TAG_COLORS = {
    "blunder": QColor("#d1503f"),
    "mistake": QColor("#d98f3a"),
    "inaccuracy": QColor("#c9b24a"),
}
TAG_LABELS_RU = {"blunder": "blunder", "mistake": "mistake", "inaccuracy": "inaccuracy"}

BOOK_CAPTION = (
    "The ECO reference knows only named lines — 3810 of them, not all of theory: "
    "after 1.g3 it names 9 moves out of 20 legal ones. So an early \"exit from "
    "named lines\" is not a mistake in itself — what matters is the evaluation of "
    "the position at that moment, not the move at which the name ended."
)


def _loss_item(loss: float | None) -> QTableWidgetItem:
    """None and 0.0 are different things: not analysed vs analysed and cost nothing.

    Coloring uses the same thresholds as the rest of the program (tag_for):
    small losses highlight nothing, only blunder/mistake/inaccuracy do.
    """
    if loss is None:
        item = QTableWidgetItem("—")
        item.setForeground(theme.TEXT_MUTED)
        item.setToolTip("the engine has not evaluated this position yet")
    else:
        item = QTableWidgetItem(f"{loss:.1f}")
        tag = weakspots.tag_for(loss)
        if tag:
            item.setForeground(TAG_COLORS[tag])
            item.setToolTip(f"{TAG_LABELS_RU[tag]}: −{loss:.1f} pp of chances")
    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    return item


def _spot_text(spot: weakspots.Spot) -> str:
    """Opening name (or "unnamed") plus the path to the position."""
    if not spot.line:
        return "starting position"
    return f'{spot.name or "unnamed"} · {_line_text(spot.line)}'


def _exit_title(eco_code: str, name: str) -> str:
    if not name:
        return "unnamed"
    return f"{eco_code} {name}".strip()


class ExitHistogram(QWidget):
    """Distribution of games by ply of leaving the book — a simple histogram."""

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMinimumHeight(110)
        self.counts: dict[int, int] = {}

    def set_counts(self, counts: dict[int, int]) -> None:
        self.counts = counts
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        area = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        painter.setPen(QPen(theme.BORDER, 1))
        painter.setBrush(theme.INSET)
        painter.drawRoundedRect(area, 6, 6)

        if not self.counts:
            painter.setPen(QPen(theme.TEXT_MUTED))
            painter.drawText(area, Qt.AlignmentFlag.AlignCenter,
                             "no games that left the book")
            painter.end()
            return

        plot = area.adjusted(10, 10, -10, -20)
        max_ply = max(self.counts)
        max_count = max(self.counts.values())
        n = max_ply + 1
        bar_width = plot.width() / n

        small = QFont(self.font())
        small.setPointSizeF(8.5)
        painter.setFont(theme.tabular(small))

        for ply in range(n):
            count = self.counts.get(ply, 0)
            if count <= 0:
                continue
            height = plot.height() * count / max_count
            x = plot.left() + ply * bar_width
            rect = QRectF(x + 1, plot.bottom() - height,
                          max(1.0, bar_width - 2), height)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(theme.ACCENT)
            painter.drawRoundedRect(rect, 2, 2)

            painter.setPen(QPen(theme.TEXT_FAINT))
            painter.drawText(
                QRectF(x, plot.bottom() + 4, bar_width, 14),
                Qt.AlignmentFlag.AlignCenter, str(ply),
            )

        painter.end()


class WeaknessDialog(QDialog):
    """Opening weaknesses: key positions and leaving the book."""

    line_chosen = Signal(str)  # PGN of the line — to put on the board in the main window

    def __init__(self, engine_path: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Opening weaknesses")
        self.resize(1220, 760)
        self.setStyleSheet(theme.QSS)

        self.engine_path = engine_path
        self.conn = games_db.connect()
        self.accounts = storage.load_accounts()

        self.spot_rows: list[weakspots.Spot] = []
        self.current_line: list[str] = []
        self.exit_rows: list[weakspots.ExitStat] = []
        self.exit_list: list[weakspots.BookExit] = []
        self._analyzer: OpeningAnalyzer | None = None

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
        left.addWidget(self.board, 1)

        self.spot_heading = QLabel("")
        self.spot_heading.setObjectName("heading")
        self.spot_heading.setWordWrap(True)
        left.addWidget(self.spot_heading)

        self.spot_path = QLabel("")
        self.spot_path.setWordWrap(True)
        left.addWidget(self.spot_path)

        row = QHBoxLayout()
        row.setSpacing(theme.gap(2))
        flip_button = QPushButton("Flip")
        flip_button.setToolTip("View from the other side")
        flip_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        flip_button.clicked.connect(self.board.flip)
        row.addWidget(flip_button)
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
        side.setFixedWidth(640)
        right = QVBoxLayout(side)
        right.setContentsMargins(0, 0, 0, 0)
        right.setSpacing(theme.gap(2))

        right.addLayout(self._build_filters())

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_spots_tab(), "Key positions")
        self.tabs.addTab(self._build_exits_tab(), "Out of book")
        right.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        close_button = QPushButton("Close")
        close_button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        right.addLayout(buttons)

        return side

    def _build_filters(self) -> QHBoxLayout:
        """Account/color/time control — shared by both tabs: the same set of games."""
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

    def _build_spots_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, theme.gap(2), 0, 0)
        layout.setSpacing(theme.gap(2))

        controls = QHBoxLayout()
        controls.setSpacing(theme.gap(1))
        controls.addWidget(QLabel("at least"))
        self.min_games_box = QSpinBox()
        self.min_games_box.setRange(1, 999)
        self.min_games_box.setValue(5)
        self.min_games_box.valueChanged.connect(self.reload)
        controls.addWidget(self.min_games_box)
        controls.addStretch(1)

        self.analyze_button = QPushButton("Analyse with engine")
        self.analyze_button.setObjectName("primary")
        self.analyze_button.setToolTip(
            "Runs the engine over the opening part of the games under the current filters.\n"
            "A full analysis of the whole database takes about twenty minutes — "
            "an interrupted analysis is not lost, the cache is written in batches."
        )
        self.analyze_button.clicked.connect(self.toggle_analysis)
        controls.addWidget(self.analyze_button)
        layout.addLayout(controls)

        self.progress_bar = QProgressBar()
        self.progress_bar.setVisible(False)
        layout.addWidget(self.progress_bar)

        self.spots_table = self._table(SPOT_HEADERS)
        self.spots_table.setItemDelegateForColumn(2, ScoreBarDelegate(self.spots_table))
        self.spots_table.clicked.connect(self._on_spot_clicked)
        header = self.spots_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        self.spots_table.setColumnWidth(2, 96)
        layout.addWidget(self.spots_table, 2)

        moves_label = QLabel("THEIR MOVES FROM HERE")
        moves_label.setObjectName("heading")
        layout.addWidget(moves_label)

        self.moves_table = self._table(MOVE_HEADERS)
        self.moves_table.setItemDelegateForColumn(2, ScoreBarDelegate(self.moves_table))
        header = self.moves_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Fixed)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.ResizeToContents)
        self.moves_table.setColumnWidth(2, 96)
        layout.addWidget(self.moves_table, 1)

        self.status_spots = QLabel("")
        self.status_spots.setObjectName("status")
        self.status_spots.setWordWrap(True)
        layout.addWidget(self.status_spots)

        return tab

    def _build_exits_tab(self) -> QWidget:
        tab = QWidget()
        layout = QVBoxLayout(tab)
        layout.setContentsMargins(0, theme.gap(2), 0, 0)
        layout.setSpacing(theme.gap(2))

        caption = QLabel(BOOK_CAPTION)
        caption.setObjectName("status")
        caption.setWordWrap(True)
        layout.addWidget(caption)

        self.histogram = ExitHistogram()
        layout.addWidget(self.histogram)

        self.exits_table = self._table(EXIT_HEADERS)
        self.exits_table.setItemDelegateForColumn(4, ScoreBarDelegate(self.exits_table))
        header = self.exits_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2, 3, 5):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(4, QHeaderView.ResizeMode.Fixed)
        self.exits_table.setColumnWidth(4, 96)
        for column, tip in EXIT_TIPS.items():
            self.exits_table.horizontalHeaderItem(column).setToolTip(tip)
        layout.addWidget(self.exits_table, 1)

        self.status_exits = QLabel("")
        self.status_exits.setObjectName("status")
        self.status_exits.setWordWrap(True)
        layout.addWidget(self.status_exits)

        return tab

    def _table(self, headers: list[str]) -> QTableWidget:
        table = QTableWidget(0, len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.verticalHeader().setVisible(False)
        table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setShowGrid(False)
        table.setWordWrap(False)  # a long opening name gets an ellipsis, not three lines
        for column, header in enumerate(headers):
            if header in ("Position", "Move", "Opening"):
                table.horizontalHeaderItem(column).setTextAlignment(
                    Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
                )
        return table

    # --- data ------------------------------------------------------------

    @Slot()
    def reload(self) -> None:
        """Recalculate both tabs under the current filters."""
        account = self.account_box.currentData()  # None — all accounts
        if not self.accounts:
            self._show_no_account()
            return

        speed = self.speed_box.currentData()
        color = self.color_box.currentData()

        self.spot_rows = weakspots.spots(
            self.conn, account, plies=PLIES, speed=speed, color=color,
            min_games=self.min_games_box.value(),
        )
        self._fill_spots_table()
        self._update_spot_status()
        if self.spot_rows:
            self.spots_table.selectRow(0)
            self._show_spot(self.spot_rows[0])
        else:
            self._clear_spot_view()

        self.exit_list = weakspots.book_exits(
            self.conn, account, plies=PLIES, speed=speed, color=color,
        )
        self.exit_rows = weakspots.exit_stats(
            self.conn, account, plies=PLIES, speed=speed, color=color,
        )
        self.histogram.set_counts(weakspots.exit_depths(self.exit_list))
        self._fill_exits_table()
        self._update_exit_status()

    def _show_no_account(self) -> None:
        self.spot_rows = []
        self.exit_rows = []
        self.exit_list = []
        self._fill_spots_table()
        self._fill_exits_table()
        self._clear_spot_view()
        self.histogram.set_counts({})
        self.status_spots.setText("Add an account: menu “Accounts → Lichess and Chess.com…”")
        self.status_exits.setText("")

    def _clear_spot_view(self) -> None:
        self.current_line = []
        self.moves_table.setRowCount(0)
        self.board.set_position(chess.Board(), None)
        self.spot_heading.setText("NO POSITIONS" if self.accounts else "")
        self.spot_path.setText("")

    def _update_spot_status(self) -> None:
        if not self.spot_rows:
            self.status_spots.setText(
                "No key positions under these filters — "
                "lower \"at least\" games."
            )
            return
        analyzed = sum(1 for s in self.spot_rows if s.loss is not None)
        self.status_spots.setText(
            f"key positions: {len(self.spot_rows)}, analysed — {analyzed}"
        )

    def _update_exit_status(self) -> None:
        if not self.exit_list:
            self.status_exits.setText(
                "No game left the named lines within "
                f"{PLIES} plies."
            )
            return
        self.status_exits.setText(
            f"games that left named lines: {len(self.exit_list)}   ·   "
            f"openings in the table: {len(self.exit_rows)}"
        )

    def _fill_spots_table(self) -> None:
        self.spots_table.setRowCount(len(self.spot_rows))
        for index, spot in enumerate(self.spot_rows):
            cost_item = QTableWidgetItem(f"{spot.cost:.1f}")
            cost_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.spots_table.setItem(index, 0, cost_item)

            games_item = QTableWidgetItem(str(spot.games))
            games_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.spots_table.setItem(index, 1, games_item)

            score_item = QTableWidgetItem()
            score_item.setData(Qt.ItemDataRole.UserRole, spot.points * 100)
            self.spots_table.setItem(index, 2, score_item)

            text = _spot_text(spot)
            pos_item = QTableWidgetItem(text)
            pos_item.setToolTip(text)
            self.spots_table.setItem(index, 3, pos_item)

    def _fill_exits_table(self) -> None:
        self.exits_table.setRowCount(len(self.exit_rows))
        for index, stat in enumerate(self.exit_rows):
            title = _exit_title(stat.eco, stat.name)
            title_item = QTableWidgetItem(title)
            title_item.setToolTip(title)
            self.exits_table.setItem(index, 0, title_item)

            games_item = QTableWidgetItem(str(stat.games))
            games_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.exits_table.setItem(index, 1, games_item)

            mine_item = QTableWidgetItem(str(stat.mine))
            mine_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.exits_table.setItem(index, 2, mine_item)

            ply_item = QTableWidgetItem(f"{stat.avg_ply:.1f}")
            ply_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.exits_table.setItem(index, 3, ply_item)

            score_item = QTableWidgetItem()
            score_item.setData(Qt.ItemDataRole.UserRole, stat.points * 100)
            self.exits_table.setItem(index, 4, score_item)

            self.exits_table.setItem(index, 5, _loss_item(stat.loss))

    def _fill_moves(self, spot: weakspots.Spot) -> None:
        self.moves_table.setRowCount(len(spot.moves))
        for index, move in enumerate(spot.moves):
            title = QTableWidgetItem(f"{move.san} ★" if move.best else move.san)
            if move.best:
                title.setToolTip("engine's best move")
            self.moves_table.setItem(index, 0, title)

            games_item = QTableWidgetItem(str(move.games))
            games_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.moves_table.setItem(index, 1, games_item)

            score_item = QTableWidgetItem()
            score_item.setData(Qt.ItemDataRole.UserRole, move.points * 100)
            self.moves_table.setItem(index, 2, score_item)

            self.moves_table.setItem(index, 3, _loss_item(move.loss))

    # --- position on the board ---------------------------------------------

    @Slot()
    def _on_spot_clicked(self) -> None:
        row = self.spots_table.currentRow()
        if 0 <= row < len(self.spot_rows):
            self._show_spot(self.spot_rows[row])

    def _show_spot(self, spot: weakspots.Spot) -> None:
        self.current_line = spot.line
        self.board.set_position(chess.Board(spot.fen), None)

        if not spot.line:
            self.spot_heading.setText("STARTING POSITION")
            self.spot_path.setText("")
        else:
            self.spot_heading.setText((spot.name or "unnamed").upper())
            self.spot_path.setText(_line_text(spot.line))

        self._fill_moves(spot)

    # --- engine analysis -----------------------------------------------

    @Slot()
    def toggle_analysis(self) -> None:
        if self._analyzer is not None:
            self._analyzer.cancel()
            self.analyze_button.setEnabled(False)
            self.status_spots.setText("stopping analysis…")
            return

        account = self.account_box.currentData()  # None — all accounts
        if not self.accounts:
            return

        games = games_db.search(
            self.conn, account=account, speed=self.speed_box.currentData(),
            color=self.color_box.currentData(), limit=100000,
        )
        if not games:
            self.status_spots.setText("No games under these filters — nothing to analyse.")
            return

        self._analyzer = OpeningAnalyzer(self.engine_path, threads=THREADS)
        self._analyzer.progress.connect(self._on_analysis_progress)
        self._analyzer.finished.connect(self._on_analysis_finished)
        self._analyzer.failed.connect(self._on_analysis_failed)

        self._set_busy(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setVisible(True)
        self.status_spots.setText("evaluating positions…")
        self._analyzer.start(games, plies=PLIES, depth=DEPTH)

    def _set_busy(self, busy: bool) -> None:
        for widget in (self.account_box, self.color_box, self.speed_box,
                      self.min_games_box):
            widget.setEnabled(not busy)
        self.analyze_button.setEnabled(True)
        self.analyze_button.setText("Stop" if busy else "Analyse with engine")

    @Slot(int, int)
    def _on_analysis_progress(self, done: int, total: int) -> None:
        if total:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(done)
            self.status_spots.setText(f"evaluated {done} of {total} positions")
        else:
            self.status_spots.setText("all needed positions are already evaluated")

    @Slot(int)
    def _on_analysis_finished(self, counted: int) -> None:
        self._analyzer = None
        self._set_busy(False)
        self.progress_bar.setVisible(False)
        self.reload()
        base = self.status_spots.text()
        self.status_spots.setText(f"{base}   ·   newly evaluated positions: {counted}")

    @Slot(str)
    def _on_analysis_failed(self, message: str) -> None:
        self._analyzer = None
        self._set_busy(False)
        self.progress_bar.setVisible(False)
        self.status_spots.setText(message)

    # --- handing off -----------------------------------------------------

    @Slot()
    def take_line(self) -> None:
        """Hand the current position's line to the main window — engine and variations are there."""
        game = chess.pgn.Game()
        game.headers["Event"] = "Opening weaknesses"
        game.headers["White"] = "?"
        game.headers["Black"] = "?"
        node = game
        board = chess.Board()
        for san in self.current_line:
            try:
                move = board.parse_san(san)
            except ValueError:
                break
            node = node.add_variation(move)
            board.push(move)
        self.line_chosen.emit(str(game))
        self.accept()

    def closeEvent(self, event) -> None:
        if self._analyzer is not None:
            self._analyzer.cancel()
        self.conn.close()
        super().closeEvent(event)
