"""My Games window: download from Lichess and Chess.com, search, open a game.

Games from all accounts live in one table: the site is a column, not a
separate list. That way your whole game history reads in one stream, not two halves.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime

from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import games_db, storage, theme
from .icons import side_icon

SPEEDS = [("", "any time control"), ("bullet", "bullet"), ("blitz", "blitz"),
          ("rapid", "rapid"), ("classical", "classical"),
          ("correspondence", "correspondence")]
COLORS = [("", "any color"), ("white", "as White"), ("black", "as Black")]
RESULTS = [("", "any result"), ("win", "wins"), ("loss", "losses"),
           ("draw", "draws")]

# how a game source is labelled; otb = a game from a scoresheet, entered by hand
SERVICE_TEXT = {"lichess": "Lichess", "chess.com": "Chess.com", "otb": "tournament"}
SERVICE_FILTER = [("", "all sites"), ("lichess", "Lichess"),
                  ("chess.com", "Chess.com"), ("otb", "tournaments")]

HEADERS = ["Date", "Result", "Color", "Opponent", "Rating", "Time control", "Opening", "Site"]
OPENING_COLUMN = 6
CENTERED = {1, 5}
RIGHT = {4}
LEFT_HEADERS = {0, 2, 3, 6, 7}
RESULT_COLORS = {"win": theme.SCORE_GOOD, "loss": theme.SCORE_BAD, "draw": theme.TEXT_MUTED}


class GamesDialog(QDialog):
    game_chosen = Signal(str, str)  # PGN of the chosen game and its id

    def __init__(self, parent: QWidget | None = None, initial_search: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("My Games")
        self.resize(1120, 660)
        self.setStyleSheet(theme.QSS)

        self.conn = games_db.connect()
        self.accounts = storage.load_accounts()
        self.rows: list[sqlite3.Row] = []
        self._downloader = None
        self._queue: list[dict] = []   # accounts still to download
        self._full = False
        self._added = 0
        self._troubles: list[str] = []
        self._waited_for_chesscom = False

        self._build_ui()
        if initial_search:
            # setText triggers reload via textChanged
            self.search_edit.setText(initial_search)
        else:
            self.reload()

    # --- UI ---------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        margin = theme.gap(4)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(theme.gap(2))

        layout.addLayout(self._build_top_row())
        layout.addLayout(self._build_filter_row())

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setObjectName("lines")  # row separators instead of a full grid
        self.table.setShowGrid(False)
        self.table.setHorizontalHeaderLabels(HEADERS)
        for column in LEFT_HEADERS:
            self.table.horizontalHeaderItem(column).setTextAlignment(
                Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter
            )
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.doubleClicked.connect(self.open_selected)
        header = self.table.horizontalHeader()
        for index in range(len(HEADERS)):
            # ResizeToContents is not usable here: it recomputes the width on every
            # added cell, and with a couple of thousand games the window freezes.
            # Fit the width once, after the table is filled.
            mode = (QHeaderView.ResizeMode.Stretch if index == OPENING_COLUMN
                    else QHeaderView.ResizeMode.Interactive)
            header.setSectionResizeMode(index, mode)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)

        self.open_button = QPushButton("Open on board")
        self.open_button.clicked.connect(self.open_selected)
        buttons.addWidget(self.open_button)

        buttons.addStretch(1)

        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)

        layout.addLayout(buttons)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _build_top_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        self.account_box = QComboBox()
        if len(self.accounts) > 1:
            # None = all accounts at once; this is the normal mode
            self.account_box.addItem("All accounts", None)
        for account in self.accounts:
            self.account_box.addItem(storage.label(account), account)
        self.account_box.currentIndexChanged.connect(self.reload)
        row.addWidget(self.account_box, 1)

        self.sync_button = QPushButton("Download new")
        self.sync_button.setToolTip("Fetch games that are not in the database yet")
        self.sync_button.clicked.connect(lambda: self.download(full=False))
        row.addWidget(self.sync_button)

        self.full_button = QPushButton("Re-download all")
        self.full_button.setToolTip("Re-download the whole history from scratch")
        self.full_button.clicked.connect(lambda: self.download(full=True))
        row.addWidget(self.full_button)

        self.cancel_button = QPushButton("Stop")
        self.cancel_button.clicked.connect(self.cancel_download)
        self.cancel_button.setVisible(False)
        row.addWidget(self.cancel_button)

        return row

    def _build_filter_row(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("opponent, opening or ECO code")
        self.search_edit.textChanged.connect(self.reload)
        row.addWidget(self.search_edit, 1)

        self.service_box = self._combo(SERVICE_FILTER)
        self.speed_box = self._combo(SPEEDS)
        row.addWidget(self.service_box)
        row.addWidget(self.speed_box)
        self.color_box = self._combo(COLORS)
        row.addWidget(self.color_box)
        self.result_box = self._combo(RESULTS)
        row.addWidget(self.result_box)

        return row

    def _combo(self, options: list[tuple[str, str]]) -> QComboBox:
        box = QComboBox()
        for value, label in options:
            box.addItem(label, value)
        box.currentIndexChanged.connect(self.reload)
        return box

    # --- data -------------------------------------------------------

    def current_account(self) -> dict | None:
        """Selected account. None means "all" or no accounts at all."""
        return self.account_box.currentData()

    def current_username(self) -> str | None:
        account = self.current_account()
        return account["username"] if account else None

    @Slot()
    def reload(self) -> None:
        if not self.accounts:
            self.status.setText(
                "Add an account first: menu “Accounts → Lichess and Chess.com…”"
            )
            self.table.setRowCount(0)
            self.sync_button.setEnabled(False)
            self.full_button.setEnabled(False)
            return

        username = self.current_username()
        self.rows = games_db.search(
            self.conn,
            account=username,
            text=self.search_edit.text().strip(),
            service=self.service_box.currentData(),
            speed=self.speed_box.currentData(),
            color=self.color_box.currentData(),
            result=self.result_box.currentData(),
        )
        self._fill_table()

        total = games_db.count(self.conn, username)
        shown = len(self.rows)
        if total == 0:
            self.status.setText("No games in the database — press “Download new”.")
        else:
            head = (f"games in database: {total}" if shown == total
                    else f"showing {shown} of {total}")
            self.status.setText(f"{head}   ·   {self._breakdown(username)}")

    def _breakdown(self, username: str | None) -> str:
        """Where the games come from — by site, largest first."""
        counts = games_db.counts_by_service(self.conn, username)
        return " · ".join(
            f"{SERVICE_TEXT.get(service, 'unlabelled')} {number}"
            for service, number in counts.items()
        )

    def _fill_table(self) -> None:
        tabular = theme.tabular(self.table.font())
        bold = theme.tabular(self.table.font(), bold=True)
        white_icon, black_icon = side_icon(True), side_icon(False)
        self.table.setUpdatesEnabled(False)
        self.table.setRowCount(len(self.rows))
        for index, game in enumerate(self.rows):
            # each row has its own owner: the table holds all accounts
            owner = (game["account"] or "").lower()
            white_is_owner = (game["white"] or "").lower() == owner
            opponent = game["black"] if white_is_owner else game["white"]
            opponent_elo = game["black_elo"] if white_is_owner else game["white_elo"]
            service = game["service"] or ""

            outcome = outcome_text(game["winner"], white_is_owner)
            cells = [
                format_date(game["played_at"]),
                outcome,
                "White" if white_is_owner else "Black",
                str(opponent),
                str(opponent_elo) if opponent_elo else "",
                dict(SPEEDS).get(game["speed"], game["speed"]),
                f'{game["eco"]} {game["opening"]}'.strip(),
                SERVICE_TEXT.get(service, "—"),
            ]
            for column, text in enumerate(cells):
                item = QTableWidgetItem(text)
                if column in CENTERED:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
                elif column in RIGHT:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight
                                          | Qt.AlignmentFlag.AlignVCenter)
                if column == 2:
                    item.setIcon(white_icon if white_is_owner else black_icon)
                elif column == 1:
                    item.setForeground(RESULT_COLORS[outcome])
                    item.setFont(bold)
                elif column in (0, 4):
                    item.setFont(tabular)
                    item.setForeground(theme.TEXT_MUTED)
                elif column == 7:
                    item.setForeground(theme.TEXT_MUTED)
                self.table.setItem(index, column, item)
        self.table.setUpdatesEnabled(True)
        for column in range(len(HEADERS)):
            if column != OPENING_COLUMN:
                self.table.resizeColumnToContents(column)

    # --- download ---------------------------------------------------

    @Slot()
    def download(self, full: bool = False) -> None:
        if self._downloader is not None or not self.accounts:
            return

        account = self.current_account()
        # "all accounts": download one by one, one thread at a time
        self._queue = list(self.accounts) if account is None else [account]
        self._full = full
        self._added = 0
        self._troubles = []
        self._waited_for_chesscom = any(a.get("service") == "chess.com"
                                        for a in self._queue)

        self._set_busy(True)
        self._start_next()

    def _start_next(self) -> None:
        if not self._queue:
            self._finish_queue()
            return

        account = self._queue.pop(0)
        who = account["username"]
        self.status.setText(
            f"{who}: downloading the whole history — this will take a while…"
            if self._full else f"{who}: checking for new games…"
        )

        # the downloader depends on the service; their signals are identical
        self._downloader = storage.client(account).GamesDownloader()
        self._downloader.progress.connect(self._on_progress)
        self._downloader.finished.connect(self._on_finished)
        self._downloader.failed.connect(self._on_failed)
        self._downloader.start(account, full=self._full)

    def _finish_queue(self) -> None:
        self._downloader = None
        self._set_busy(False)
        self.reload()

        total = games_db.count(self.conn, self.current_username())
        head = (f"added {self._added}, {total} in database in total" if self._added
                else f"no new games, {total} in database")
        if not self._added and self._waited_for_chesscom:
            # common question "I played, why is nothing downloaded": the Chess.com
            # monthly archive is not updated right away, and bot games are not there at all
            head += ("   ·   Chess.com puts fresh games into the archive with a delay "
                     "— if you just played, try again in a few minutes. "
                     "Games against bots are not available there at all: open them "
                     "via “Game → Paste PGN from clipboard”.")
        if self._troubles:
            head += "   ·   " + "; ".join(self._troubles)
        self.status.setText(head)

    @Slot()
    def cancel_download(self) -> None:
        self._queue = []
        if self._downloader is not None:
            self._downloader.cancel()
            self.status.setText("Stopping…")

    def _set_busy(self, busy: bool) -> None:
        self.sync_button.setEnabled(not busy)
        self.full_button.setEnabled(not busy)
        self.account_box.setEnabled(not busy)
        self.cancel_button.setVisible(busy)

    @Slot(int)
    def _on_progress(self, saved: int) -> None:
        self.status.setText(f"games downloaded: {self._added + saved}…")

    @Slot(int, int)
    def _on_finished(self, added: int, total: int) -> None:
        self._added += added
        self._downloader = None
        self._start_next()

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._troubles.append(message)
        self._downloader = None
        self._start_next()

    # --- opening ----------------------------------------------------

    @Slot()
    def open_selected(self) -> None:
        row = self.table.currentRow()
        if not (0 <= row < len(self.rows)):
            return
        pgn = self.rows[row]["pgn"]
        if not pgn:
            self.status.setText("This game has no saved PGN.")
            return
        self.game_chosen.emit(pgn, self.rows[row]["id"])
        self.accept()

    def closeEvent(self, event) -> None:
        self._queue = []
        if self._downloader is not None:
            self._downloader.cancel()
        self.conn.close()
        super().closeEvent(event)


def format_date(ms: int) -> str:
    if not ms:
        return "—"
    return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d %H:%M")


def outcome_text(winner: str, white_is_owner: bool) -> str:
    if not winner:
        return "draw"
    owner_won = (winner == "white") == white_is_owner
    return "win" if owner_won else "loss"
