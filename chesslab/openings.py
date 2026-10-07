"""Opening statistics: score percentage per opening and where results sag."""

from __future__ import annotations

from PySide6.QtCore import QRectF, Qt, Signal, Slot
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QStyle,
    QStyledItemDelegate,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import games_db, storage, theme
from .games import SPEEDS

GROUPS = [(games_db.GROUP_VARIATION, "by variation"),
          (games_db.GROUP_FAMILY, "by family"),
          (games_db.GROUP_ECO, "by ECO code")]
COLORS = [("", "both colors"), ("white", "as White"), ("black", "as Black")]
SORTS = [("worst", "worst first"), ("games", "by number of games"),
         ("best", "best first")]
MINIMUMS = [3, 5, 10, 20]

HEADERS = ["Opening", "Games", "Result", "Score"]


def bar_color(score: float) -> QColor:
    """Red — underperforming, green — doing well, gray — roughly even."""
    if score < 45:
        return QColor("#a0553f")
    if score > 55:
        return QColor("#5d8b47")
    return theme.BORDER_HI


class ScoreBarDelegate(QStyledItemDelegate):
    """Draws the score percentage as a bar so weak spots are visible at a glance."""

    def paint(self, painter, option, index) -> None:
        score = index.data(Qt.ItemDataRole.UserRole)
        if score is None:
            super().paint(painter, option, index)
            return

        painter.save()
        if option.state & QStyle.StateFlag.State_Selected:
            painter.fillRect(option.rect, theme.SELECTION)

        track = QRectF(option.rect.adjusted(8, 7, -8, -7))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(theme.INSET)
        painter.drawRoundedRect(track, 4, 4)

        width = track.width() * max(0.0, min(score, 100.0)) / 100
        if width > 1:
            painter.setBrush(bar_color(score))
            painter.drawRoundedRect(
                QRectF(track.x(), track.y(), width, track.height()), 4, 4
            )

        painter.setPen(theme.TEXT)
        painter.drawText(option.rect, Qt.AlignmentFlag.AlignCenter, f"{score:.0f}%")
        painter.restore()


class OpeningsDialog(QDialog):
    opening_chosen = Signal(str)  # text to search for in the games list

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Opening statistics")
        self.resize(860, 640)
        self.setStyleSheet(theme.QSS)

        self.conn = games_db.connect()
        self.accounts = storage.load_accounts()
        self.rows: list[dict] = []

        self._build_ui()
        self.reload()

    # --- UI --------------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.summary = QLabel("")
        self.summary.setObjectName("heading")
        layout.addWidget(self.summary)

        layout.addLayout(self._build_filters())

        self.table = QTableWidget(0, len(HEADERS))
        self.table.setHorizontalHeaderLabels(HEADERS)
        theme.left_header(self.table, 0)
        self.table.verticalHeader().setVisible(False)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setShowGrid(False)
        self.table.setItemDelegateForColumn(3, ScoreBarDelegate(self.table))
        self.table.doubleClicked.connect(self.show_games)

        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in (1, 2):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(3, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(3, 150)
        layout.addWidget(self.table, 1)

        buttons = QHBoxLayout()
        self.games_button = QPushButton("Show games")
        self.games_button.setToolTip("Open the list of games for this opening")
        self.games_button.clicked.connect(self.show_games)
        buttons.addWidget(self.games_button)
        buttons.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _build_filters(self) -> QHBoxLayout:
        row = QHBoxLayout()
        row.setSpacing(6)

        self.account_box = QComboBox()
        storage.fill_account_box(self.account_box, self.accounts)
        self.account_box.currentIndexChanged.connect(self.reload)
        row.addWidget(self.account_box, 1)

        self.group_box = self._combo(GROUPS)
        row.addWidget(self.group_box)
        self.color_box = self._combo(COLORS)
        row.addWidget(self.color_box)
        self.speed_box = self._combo(SPEEDS)
        row.addWidget(self.speed_box)
        self.sort_box = self._combo(SORTS)
        row.addWidget(self.sort_box)

        row.addWidget(QLabel("min"))
        self.min_box = QComboBox()
        for value in MINIMUMS:
            self.min_box.addItem(f"{value} games", value)
        self.min_box.setCurrentIndex(1)  # at least 5 games
        self.min_box.currentIndexChanged.connect(self.reload)
        row.addWidget(self.min_box)

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
        account = self.account_box.currentData()  # None — all accounts
        if not self.accounts:
            self.summary.setText("NO ACCOUNTS")
            self.status.setText("Add an account: menu “Accounts → Lichess and Chess.com…”")
            self.table.setRowCount(0)
            return

        total = games_db.overall(self.conn, account)
        self.summary.setText(
            f"{(account or 'all accounts').upper()}   ·   "
            f"{total['games']} GAMES   ·   "
            f"+{total['wins']} ={total['draws']} −{total['losses']}   ·   "
            f"{total['score']:.1f}% SCORE"
        )

        self.rows = games_db.opening_stats(
            self.conn,
            account,
            group=self.group_box.currentData(),
            color=self.color_box.currentData(),
            speed=self.speed_box.currentData(),
            min_games=self.min_box.currentData(),
        )

        sort = self.sort_box.currentData()
        if sort == "worst":
            self.rows.sort(key=lambda r: (r["score"], -r["games"]))
        elif sort == "best":
            self.rows.sort(key=lambda r: (-r["score"], -r["games"]))
        else:
            self.rows.sort(key=lambda r: -r["games"])

        self._fill_table()

        if not self.rows:
            self.status.setText(
                "No opening has that many games — lower the threshold on the left."
            )
        else:
            self.status.setText(
                f"openings listed: {len(self.rows)}   ·   "
                "double-click to show games for this opening"
            )

    def _fill_table(self) -> None:
        by_eco = self.group_box.currentData() == games_db.GROUP_ECO
        self.table.setRowCount(len(self.rows))

        for index, row in enumerate(self.rows):
            title = row["key"] if by_eco else f'{row["eco"]}  {row["key"]}'
            self.table.setItem(index, 0, QTableWidgetItem(title))

            games_item = QTableWidgetItem(str(row["games"]))
            games_item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.table.setItem(index, 1, games_item)

            outcome = QTableWidgetItem(
                f'+{row["wins"]} ={row["draws"]} −{row["losses"]}'
            )
            outcome.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            outcome.setForeground(theme.TEXT_MUTED)
            self.table.setItem(index, 2, outcome)

            score_item = QTableWidgetItem()
            score_item.setData(Qt.ItemDataRole.UserRole, row["score"])
            self.table.setItem(index, 3, score_item)

    @Slot()
    def show_games(self) -> None:
        row = self.table.currentRow()
        if 0 <= row < len(self.rows):
            self.opening_chosen.emit(self.rows[row]["key"])
            self.accept()

    def closeEvent(self, event) -> None:
        self.conn.close()
        super().closeEvent(event)
