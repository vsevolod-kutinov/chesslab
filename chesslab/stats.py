"""Lichess statistics: ratings, streaks, best wins, rating chart."""

from __future__ import annotations

from datetime import datetime

from PySide6.QtCore import Qt, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import games_db, lichess, storage, theme
from .rating import RatingChart

# modes shown; Lichess key -> label
PERFS = [("bullet", "bullet"), ("blitz", "blitz"), ("rapid", "rapid"),
         ("classical", "classical"), ("correspondence", "correspondence")]


def _duration(seconds: int) -> str:
    if not seconds:
        return "—"
    hours, rest = divmod(int(seconds), 3600)
    minutes = rest // 60
    if hours:
        return f"{hours} h {minutes:02d} min"
    return f"{minutes} min"


def _when(stamp: str | None) -> str:
    if not stamp:
        return ""
    try:
        return datetime.fromisoformat(stamp.replace("Z", "+00:00")).strftime("%Y-%m-%d")
    except ValueError:
        return ""


class StatsDialog(QDialog):
    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Lichess Statistics")
        self.resize(760, 700)
        self.setStyleSheet(theme.QSS)

        self.conn = games_db.connect()
        # this window is about Lichess: Chess.com has different endpoints and streaks
        self.accounts = [a for a in storage.load_accounts()
                         if a.get("service", "lichess") == "lichess"]
        self._lookup: lichess.PerfLookup | None = None

        self._build_ui()
        self.reload()

    # --- UI ---------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        margin = theme.gap(4)
        layout.setContentsMargins(margin, margin, margin, margin)
        layout.setSpacing(theme.gap(3))

        layout.addLayout(self._build_top_row())

        self.headline = QLabel("—")
        self.headline.setObjectName("eval")
        self.headline.setFont(theme.tabular(self.font(), 26, bold=True))
        layout.addWidget(self.headline)

        self.subline = QLabel("")
        self.subline.setObjectName("status")
        layout.addWidget(self.subline)

        chart_label = QLabel("RATING BY GAMES IN DATABASE")
        chart_label.setObjectName("heading")
        layout.addWidget(chart_label)

        self.chart = RatingChart()
        layout.addWidget(self.chart, 2)

        details_label = QLabel("DETAILS")
        details_label.setObjectName("heading")
        layout.addWidget(details_label)

        self.table = QTableWidget(0, 2)
        self.table.horizontalHeader().setVisible(False)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 3)

        buttons = QHBoxLayout()
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
        row.setSpacing(theme.gap(2))

        self.account_box = QComboBox()
        for account in self.accounts:
            self.account_box.addItem(storage.label(account), account)
        self.account_box.currentIndexChanged.connect(self.reload)
        row.addWidget(self.account_box, 1)

        self.perf_box = QComboBox()
        for value, label in PERFS:
            self.perf_box.addItem(label, value)
        self.perf_box.setCurrentIndex(1)  # blitz
        self.perf_box.currentIndexChanged.connect(self.reload)
        row.addWidget(self.perf_box)

        self.refresh_button = QPushButton("Refresh from Lichess")
        self.refresh_button.setObjectName("primary")
        self.refresh_button.clicked.connect(self.fetch)
        row.addWidget(self.refresh_button)

        return row

    # --- data -------------------------------------------------------

    def current_account(self) -> dict | None:
        """Take it from our own list by index, not via currentData().

        Qt returns a copy of the dict from item data — edits made to it never reached
        self.accounts, and the statistics cache was silently not saved.
        """
        index = self.account_box.currentIndex()
        return self.accounts[index] if 0 <= index < len(self.accounts) else None

    @Slot()
    def reload(self) -> None:
        account = self.current_account()
        if account is None:
            self.headline.setText("—")
            self.subline.setText("Add a Lichess account: menu “Accounts → Lichess and Chess.com…”")
            self.refresh_button.setEnabled(False)
            return

        perf = self.perf_box.currentData()
        self._show_local(account, perf)

        cached = (account.get("perf_stats") or {}).get(perf)
        if cached:
            self._fill_details(cached)
            self.status.setText(
                f'data from Lichess as of {_when(account.get("perf_checked", {}).get(perf))}'
                if account.get("perf_checked", {}).get(perf) else ""
            )
        else:
            self.table.setRowCount(0)
            self.status.setText(
                "Press “Refresh from Lichess” to fetch streaks, "
                "best wins and world rank."
            )

    def _show_local(self, account: dict, perf: str) -> None:
        """What can be shown without network — from the profile and our own database."""
        username = account["username"]
        entry = ((account.get("profile") or {}).get("perfs") or {}).get(perf) or {}
        rating = entry.get("rating")
        games = entry.get("games") or 0

        self.headline.setText(str(rating) if rating else "—")

        played = games_db.search(self.conn, account=username, speed=perf, limit=99999)
        wins = draws = 0
        for row in played:
            as_white = (row["white"] or "").lower() == username.lower()
            if not row["winner"]:
                draws += 1
            elif (row["winner"] == "white") == as_white:
                wins += 1
        total = len(played)
        losses = total - wins - draws
        score = (wins + draws / 2) / total * 100 if total else 0

        parts = [f"{games} games on Lichess"]
        if total:
            parts.append(f"in database {total}: +{wins} ={draws} −{losses}")
            parts.append(f"{score:.1f}% score")
        self.subline.setText("   ·   ".join(parts))

        self.chart.set_points(games_db.rating_series(self.conn, username, perf))

    def _fill_details(self, data: dict) -> None:
        stat = data.get("stat") or {}
        count = stat.get("count") or {}
        streak = stat.get("resultStreak") or {}

        rows: list[tuple[str, str]] = []

        rank, percentile = data.get("rank"), data.get("percentile")
        if rank:
            rows.append(("World rank", f"{rank:,}"))
        if percentile:
            rows.append(("Better than", f"{percentile}% of players"))

        if count:
            rows.append(("Games played", str(count.get("all", 0))))
            rows.append((
                "Wins / draws / losses",
                f'{count.get("win", 0)} / {count.get("draw", 0)} / {count.get("loss", 0)}',
            ))
            if count.get("opAvg"):
                rows.append(("Average opponent", f'{count["opAvg"]:.0f}'))
            rows.append(("Time at the board", _duration(count.get("seconds", 0))))
            if count.get("tour"):
                rows.append(("Of them in tournaments", str(count["tour"])))
            if count.get("disconnects"):
                rows.append(("Disconnections", str(count["disconnects"])))

        for key, label in (("highest", "Highest rating"), ("lowest", "Lowest rating")):
            entry = stat.get(key) or {}
            if entry.get("int"):
                date = _when(entry.get("at"))
                rows.append((label, f'{entry["int"]}' + (f'   ·   {date}' if date else "")))

        for key, label in (("win", "Win streak"), ("loss", "Loss streak")):
            best = ((streak.get(key) or {}).get("max") or {})
            if best.get("v"):
                date = _when((best.get("from") or {}).get("at"))
                rows.append((label, f'{best["v"]} in a row'
                                    + (f'   ·   {date}' if date else "")))

        for win in ((stat.get("bestWins") or {}).get("results") or [])[:5]:
            name = (win.get("opId") or {}).get("name", "?")
            rows.append((
                "Win against",
                f'{name} ({win.get("opRating", "?")})   ·   {_when(win.get("at"))}',
            ))

        self.table.setRowCount(len(rows))
        for index, (label, value) in enumerate(rows):
            key_item = QTableWidgetItem(label)
            key_item.setForeground(theme.TEXT_MUTED)
            self.table.setItem(index, 0, key_item)

            value_item = QTableWidgetItem(value)
            value_item.setFont(theme.tabular(self.font()))
            self.table.setItem(index, 1, value_item)

    # --- network ----------------------------------------------------

    @Slot()
    def fetch(self) -> None:
        account = self.current_account()
        if account is None or self._lookup is not None:
            return
        perf = self.perf_box.currentData()

        self.refresh_button.setEnabled(False)
        self.status.setText(f"Asking Lichess about {self.perf_box.currentText()}…")

        self._lookup = lichess.PerfLookup()
        self._lookup.found.connect(self._on_found)
        self._lookup.failed.connect(self._on_failed)
        self._lookup.start(account["username"], perf, account.get("token"))

    @Slot(dict)
    def _on_found(self, data: dict) -> None:
        account = self.current_account()
        perf = self.perf_box.currentData()

        # store next to the account so it can be shown next time without network
        account.setdefault("perf_stats", {})[perf] = data
        account.setdefault("perf_checked", {})[perf] = datetime.now().isoformat(
            timespec="seconds"
        )
        storage.save_accounts(self.accounts)

        self._fill_details(data)
        glicko = (data.get("perf") or {}).get("glicko") or {}
        if glicko.get("rating"):
            self.headline.setText(f'{glicko["rating"]:.0f}')
        self._finish()
        self.status.setText("updated")

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._finish()
        self.status.setText(message)

    def _finish(self) -> None:
        self._lookup = None
        self.refresh_button.setEnabled(True)

    def closeEvent(self, event) -> None:
        self.conn.close()
        super().closeEvent(event)
