"""The "Tournament analysis" window: one's own performance against the whole crosstable.

The tournament is downloaded in full from chess-results (`chessresults.py`), computed in
`crosstable.py`, and only displayed here. At the top is a switch for what rating
to use for unrated players: 1000 in the protocol is a placeholder, not playing strength,
and every derived figure shifts with the choice. So it is kept in plain view rather than
in settings: you can see how the picture changes.

The analysis can be imported into "My tournaments" - the games go into the common database
as manually entered ones, without moves; the moves are added later from the scoresheet.
"""

from __future__ import annotations

import re

from PySide6.QtCore import Slot
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import chessresults, crosstable, theme, tournaments_db

_TONES = {"good": theme.SCORE_GOOD, "bad": theme.SCORE_BAD, "plain": theme.TEXT}


def _score_cell(score: float) -> tuple[str, object]:
    if score == 1:
        return "1", theme.SCORE_GOOD
    if score == 0:
        return "0", theme.SCORE_BAD
    return "½", theme.TEXT_MUTED


class LinkDialog(QDialog):
    """Ask for a tournament link and download it."""

    def __init__(self, parent: QWidget | None = None, link: str = "") -> None:
        super().__init__(parent)
        self.setWindowTitle("Tournament from chess-results")
        self.setMinimumWidth(560)
        self.setStyleSheet(theme.QSS)
        self.data: dict | None = None

        self._lookup = chessresults.TournamentLookup(self)
        self._lookup.found.connect(self._done)
        self._lookup.failed.connect(self._failed)
        self._lookup.progress.connect(self._say)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        caption = QLabel("TOURNAMENT LINK")
        caption.setObjectName("heading")
        layout.addWidget(caption)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.link_edit = QLineEdit(link)
        self.link_edit.setPlaceholderText(
            "https://chess-results.com/tnr1493854.aspx?lan=1"
        )
        self.link_edit.returnPressed.connect(self.load)
        self.load_button = QPushButton("Download")
        self.load_button.setObjectName("primary")
        self.load_button.clicked.connect(self.load)
        row.addWidget(self.link_edit, 1)
        row.addWidget(self.load_button)
        layout.addLayout(row)

        self.status = QLabel(
            "All rounds are downloaded in full: not only your own games but everyone else's - "
            "without them neither the tournament upsets nor schedule strength can be computed."
        )
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = QPushButton("Cancel")
        cancel.clicked.connect(self.reject)
        buttons.addWidget(cancel)
        layout.addLayout(buttons)

    @Slot()
    def load(self) -> None:
        link = self.link_edit.text().strip()
        if not link:
            self.status.setText("Paste a link first.")
            return
        self.load_button.setEnabled(False)
        self._lookup.start(link)

    @Slot(str)
    def _say(self, text: str) -> None:
        self.status.setText(text)

    @Slot(dict)
    def _done(self, data: dict) -> None:
        self.data = data
        self.accept()

    @Slot(str)
    def _failed(self, message: str) -> None:
        self.load_button.setEnabled(True)
        self.status.setText(message)


class CrossReportDialog(QDialog):
    """Performance analysis: summary, notes, games, upsets, table."""

    def __init__(self, data: dict, parent: QWidget | None = None,
                 player: str = "", conn=None) -> None:
        super().__init__(parent)
        self.data = data
        self.conn = conn
        self.analysis: dict | None = None
        self.setWindowTitle(f'Analysis - {data["meta"]["name"]}')
        self.resize(940, 760)
        self.setStyleSheet(theme.QSS)

        self._build_ui()
        self._fill_players(player)
        self.recalculate()

    # --- assembly --------------------------------------------------------

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(theme.gap(3))

        meta = self.data["meta"]
        title = QLabel(meta["name"].upper())
        title.setObjectName("heading")
        title.setWordWrap(True)
        layout.addWidget(title)

        where = " · ".join(part for part in (
            meta["location"], meta["date"], meta["time_control"],
            f'{meta["rounds"]} rounds',
            f'{len(self.data["players"])} players',
            f'average rating {meta["field_average"]}' if meta["field_average"] else "",
        ) if part)
        subtitle = QLabel(where)
        subtitle.setObjectName("status")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        layout.addWidget(self._build_controls())

        self.headline = QLabel()
        self.headline.setObjectName("player")
        self.headline.setWordWrap(True)
        layout.addWidget(self.headline)

        self.tabs = QTabWidget()
        self.overview = _Overview()
        self.games_tab = _Games()
        self.upsets_tab = _Upsets()
        self.field_tab = _Field()
        self.tabs.addTab(self.overview, "Overview")
        self.tabs.addTab(self.games_tab, "Games")
        self.tabs.addTab(self.upsets_tab, "Upsets")
        self.tabs.addTab(self.field_tab, "All players")
        layout.addWidget(self.tabs, 1)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        self.import_button = QPushButton("Import into my tournaments")
        self.import_button.clicked.connect(self.import_tournament)
        self.import_button.setEnabled(self.conn is not None)
        copy_button = QPushButton("Copy analysis")
        copy_button.clicked.connect(self.copy_report)
        buttons.addWidget(self.import_button)
        buttons.addWidget(copy_button)
        buttons.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _build_controls(self) -> QWidget:
        box = QFrame()
        box.setObjectName("panel")
        row = QHBoxLayout(box)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(8)

        row.addWidget(QLabel("I am in the tournament as:"))
        self.player_combo = QComboBox()
        self.player_combo.setMinimumWidth(260)
        self.player_combo.currentIndexChanged.connect(lambda _: self.recalculate())
        row.addWidget(self.player_combo)

        row.addSpacing(theme.gap(4))
        row.addWidget(QLabel("Count unrated players as:"))
        self.mode_combo = QComboBox()
        for label, value in crosstable.MODES:
            self.mode_combo.addItem(label, value)
        self.mode_combo.setCurrentIndex(1)  # 1750: 1000 in the protocol is a placeholder
        self.mode_combo.currentIndexChanged.connect(lambda _: self.recalculate())
        row.addWidget(self.mode_combo, 1)
        return box

    def _fill_players(self, player: str) -> None:
        self.player_combo.blockSignals(True)
        wanted = (player or "").strip().lower()
        chosen = 0
        for index, entry in enumerate(sorted(self.data["players"],
                                             key=lambda p: p["name"])):
            rating = entry["rating"] or "—"
            self.player_combo.addItem(
                f'{entry["name"]} ({rating})', entry["snr"])
            if wanted and entry["name"].strip().lower() == wanted:
                chosen = index
        self.player_combo.setCurrentIndex(chosen)
        self.player_combo.blockSignals(False)

    # --- recalculation ---------------------------------------------------

    @Slot()
    def recalculate(self) -> None:
        snr = self.player_combo.currentData()
        if snr is None:
            return
        self.analysis = crosstable.analyse(
            self.data, snr, self.mode_combo.currentData())
        notes = crosstable.insights(self.analysis)

        summary, ranks = self.analysis["summary"], self.analysis["ranks"]
        me = self.analysis["me"]
        points = crosstable.score_text(summary["points"])
        self.headline.setText(
            f'{me["name"]} - {points} out of {summary["games"]}, '
            f'{crosstable.ordinal(me["rank"])} place of {ranks["total"]}, '
            f'performance {summary["performance"]} '
            f'({crosstable.ordinal(ranks["performance"])} in the tournament)'
        )

        self.overview.fill(self.analysis, notes)
        self.games_tab.fill(self.analysis)
        self.upsets_tab.fill(self.analysis)
        self.field_tab.fill(self.analysis)

        mode = self.mode_combo.currentData()
        if mode == crosstable.BASE:
            self.status.setText(
                "Using the protocol as is: 1000 and 0 are placeholders instead of a rating, "
                "so wins over rated players look bigger than they really are."
            )
        elif mode == crosstable.PERF:
            self.status.setText(
                f'Unrated players use their own performance from this tournament '
                f'instead of the placeholder; yours came out at {self.analysis["my_rating"]}.'
            )
        else:
            self.status.setText(
                f'Everyone with {crosstable.UNRATED_MAX} or below in the protocol '
                f'is counted as {mode}.'
            )

    # --- actions ---------------------------------------------------------

    @Slot()
    def copy_report(self) -> None:
        if self.analysis is None:
            return
        QGuiApplication.clipboard().setText(
            markdown(self.analysis, crosstable.insights(self.analysis)))
        self.status.setText("Analysis copied - ready to paste into a chat.")

    @Slot()
    def import_tournament(self) -> None:
        """Create the tournament and own games in the ChessLab database."""
        if self.analysis is None or self.conn is None:
            return
        me = self.analysis["me"]
        meta = self.data["meta"]
        existing = self.conn.execute(
            "SELECT id FROM tournaments WHERE cr_event = ? AND player = ?",
            (self.data["event"], me["name"]),
        ).fetchone()
        if existing:
            QMessageBox.information(
                self, "ChessLab",
                "This tournament is already in the list - not importing the games again.")
            return

        confirm = QMessageBox.question(
            self, "ChessLab",
            f'Import "{meta["name"]}" and {len(self.analysis["rounds"])} '
            f'games without moves? You can add the moves later from the scoresheet.',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return

        country = _federation_code(meta["federation"])
        tournament_id = tournaments_db.create(
            self.conn,
            name=meta["name"],
            player=me["name"],
            place=meta["location"],
            country=country,
            started=meta["date"],
            finished=meta["date"],
            time_control=meta["time_control"],
            speed=meta["speed"],
            standing=me["rank"],
            players=len(self.data["players"]),
            official=1,
            cr_link=f'https://{self.data["host"]}/tnr{self.data["event"]}.aspx?lan=1',
            cr_event=self.data["event"],
            cr_snr=me["snr"],
        )
        row = tournaments_db.get(self.conn, tournament_id)
        for entry in self.analysis["rounds"]:
            tournaments_db.save_game(self.conn, row, {
                "round": entry["round"],
                "me_white": entry["white"],
                "opponent": entry["name"],
                "opponent_elo": entry["rating"] or None,
                "points": entry["score"],
                "date": meta["date"],
                "sans": [],
            })
        self.import_button.setEnabled(False)
        self.status.setText(
            "Tournament and games saved. Add the moves via \"Edit\" in the report.")


def _federation_code(text: str) -> str:
    """«Slovakia ( SVK )» → «SVK»."""
    match = re.search(r"\(\s*([A-Z]{3})\s*\)", text or "")
    return match.group(1) if match else ""


# --- tabs -----------------------------------------------------------------

class _Overview(QScrollArea):
    """Summary and notes - what the whole thing was for."""

    def __init__(self) -> None:
        super().__init__()
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.body = QWidget()
        self.layout_ = QVBoxLayout(self.body)
        self.layout_.setContentsMargins(4, 4, 12, 4)
        self.layout_.setSpacing(theme.gap(2))
        self.setWidget(self.body)

    def fill(self, analysis: dict, notes: list[dict]) -> None:
        while self.layout_.count():
            item = self.layout_.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.layout_.addWidget(_numbers_panel(analysis))
        for note in notes:
            self.layout_.addWidget(_note_card(note))
        self.layout_.addStretch(1)


def _numbers_panel(analysis: dict) -> QWidget:
    summary = analysis["summary"]
    ranks = analysis["ranks"]
    pairs = [
        ("Points", f'{crosstable.score_text(summary["points"])} out of {summary["games"]}'
                 f'   (+{summary["wins"]} ={summary["draws"]} −{summary["losses"]})'),
        ("Place", f'{analysis["me"]["rank"]} out of {ranks["total"]}'),
        ("Performance", f'{summary["performance"]}   ·   {crosstable.ordinal(ranks["performance"])} in the tournament'),
        ("Average opponent", f'{summary["average"]}   ·   '
                             f'{crosstable.ordinal(ranks["average"])} toughest schedule'),
        ("Buchholz", f'{crosstable.decimal(summary["buchholz"])}   ·   '
                     f'{crosstable.ordinal(ranks["buchholz"])}' if summary["buchholz"] else "—"),
        ("Elo expectation", f'{crosstable.decimal(summary["expected"], 2)} points '
                            f'at rating {analysis["my_rating"]}'),
    ]

    box = QFrame()
    box.setObjectName("panel")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(14, 12, 14, 12)
    layout.setSpacing(theme.gap(1))
    for label, value in pairs:
        row = QHBoxLayout()
        row.setSpacing(10)
        name = QLabel(label)
        name.setObjectName("heading")
        name.setMinimumWidth(170)
        text = QLabel(value)
        text.setFont(theme.tabular(text.font()))
        row.addWidget(name)
        row.addWidget(text, 1)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(holder)
    return box


def _note_card(note: dict) -> QWidget:
    box = QFrame()
    box.setObjectName("panel")
    layout = QVBoxLayout(box)
    layout.setContentsMargins(14, 10, 14, 12)
    layout.setSpacing(theme.gap(1))

    title = QLabel(note["title"].upper())
    title.setObjectName("heading")
    colour = _TONES.get(note["tone"], theme.TEXT)
    title.setStyleSheet(f"color: {colour.name()};")
    layout.addWidget(title)

    text = QLabel(note["text"])
    text.setWordWrap(True)
    text.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    layout.addWidget(text)
    return box


class _Table(QWidget):
    """Common base for table tabs: one table filling the whole tab."""

    HEADERS: list[str] = []
    STRETCH = 0

    def __init__(self) -> None:
        super().__init__()
        from .tournaments import _table  # the shared table style lives there

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 8, 0, 0)
        self.table = _table(self.HEADERS, stretch=self.STRETCH)
        layout.addWidget(self.table)
        self.hint = QLabel("")
        self.hint.setObjectName("status")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

    def _cells(self, index: int, values: list) -> None:
        from .tournaments import _cell

        for column, value in enumerate(values):
            if isinstance(value, tuple):
                text, colour, center = (value + (True,))[:3]
                item = _cell(str(text), center=center)
                if colour is not None:
                    item.setForeground(colour)
            else:
                item = _cell(str(value))
            self.table.setItem(index, column, item)

    def _mark(self, index: int) -> None:
        """Own row - bold and accented."""
        for column in range(self.table.columnCount()):
            item = self.table.item(index, column)
            if item is None:
                continue
            font = item.font()
            font.setWeight(font.Weight.DemiBold)
            item.setFont(font)
            item.setForeground(theme.ACCENT)


class _Games(_Table):
    HEADERS = ["Round", "Board", "Color", "Opponent", "Rating", "Counted",
               "Point", "Opponent finish"]
    STRETCH = 3

    def fill(self, analysis: dict) -> None:
        rounds = analysis["rounds"]
        self.table.setRowCount(len(rounds))
        for index, entry in enumerate(rounds):
            text, colour = _score_cell(entry["score"])
            rating = entry["rating"] or "—"
            effective = entry["effective"]
            counted = (str(effective) if effective != entry["rating"]
                       else "—")
            finish = (f'{crosstable.ordinal(entry["opponent_rank"])}, '
                      f'{crosstable.score_text(entry["opponent_points"] or 0)}'
                      if entry["opponent_rank"] else "—")
            self._cells(index, [
                (entry["round"], None),
                (entry["board"] or "—", None),
                ("White" if entry["white"] else "Black", None),
                entry["name"],
                (rating, None),
                (counted, theme.TEXT_MUTED),
                (text, colour),
                (finish, theme.TEXT_MUTED),
            ])
        self.hint.setText(
            "\"Counted\" is the rating the opponent was computed with after the adjustment; "
            "a dash means the rating was taken as is."
        )


class _Upsets(_Table):
    HEADERS = ["#", "Round", "Winner", "Rating", "Loser", "Rating", "Gap"]
    STRETCH = 2

    def fill(self, analysis: dict) -> None:
        upsets = analysis["upsets"]
        mine = set(analysis["my_upsets"])
        self.table.setRowCount(len(upsets))
        for index, upset in enumerate(upsets):
            self._cells(index, [
                (index + 1, None),
                (upset["round"], None),
                upset["winner_name"],
                (upset["winner_rating"], None),
                upset["loser_name"],
                (upset["loser_rating"], None),
                (f'+{upset["gap"]}', None),
            ])
            if index + 1 in mine:
                self._mark(index)
        places = ", ".join(f"#{n}" for n in sorted(mine)) or "none"
        self.hint.setText(
            f'Wins over a higher-rated opponent, biggest to smallest. '
            f'Yours: {places} out of {len(upsets)}.'
        )


class _Field(_Table):
    HEADERS = ["Place", "Player", "Rating", "Points", "Performance",
               "Avg. opponent", "Buchholz"]
    STRETCH = 1

    def fill(self, analysis: dict) -> None:
        rows = analysis["table"]["by_performance"]
        my_snr = analysis["me"]["snr"]
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            raw = row["raw"] or "—"
            shown = (f'{raw} → {row["rating"]}' if row["rating"] != row["raw"]
                     else str(raw))
            self._cells(index, [
                (row["rank"], None),
                row["name"],
                (shown, None),
                (crosstable.score_text(row["points"]), None),
                (row["performance"], None),
                (row["average"], theme.TEXT_MUTED),
                (crosstable.decimal(row["tb1"]) if row["tb1"] else "—",
                 theme.TEXT_MUTED),
            ])
            if row["snr"] == my_snr:
                self._mark(index)
        self.hint.setText(
            "Sorted by performance, not by place: shows who played "
            "above their table result."
        )


# --- text export ----------------------------------------------------------

def markdown(analysis: dict, notes: list[dict]) -> str:
    """The analysis as text - to paste into a chat and discuss in words."""
    meta = analysis["data"]["meta"]
    summary, ranks = analysis["summary"], analysis["ranks"]
    me = analysis["me"]
    mode = analysis["mode"]
    mode_text = {crosstable.BASE: "as in the protocol",
                 crosstable.PERF: "by tournament performance"}.get(
                     mode, f"unrated counted as {mode}")

    lines = [
        f'# {meta["name"]}',
        "",
        f'{meta["location"]} · {meta["date"]} · {meta["time_control"]} · '
        f'{meta["rounds"]} rounds · {len(analysis["data"]["players"])} players',
        "",
        f'## {me["name"]}',
        "",
        f'- Points: {crosstable.score_text(summary["points"])} out of {summary["games"]} '
        f'(+{summary["wins"]} ={summary["draws"]} −{summary["losses"]})',
        f'- Place: {me["rank"]} out of {ranks["total"]}',
        f'- Performance: {summary["performance"]} ({crosstable.ordinal(ranks["performance"])} in the tournament)',
        f'- Average opponent: {summary["average"]} ({crosstable.ordinal(ranks["average"])} toughest schedule)',
        f'- Ratings computed: {mode_text}',
        "",
        "## Games",
        "",
        "| Round | Board | Color | Opponent | Rating | Point | Opponent finish |",
        "|---|---|---|---|---|---|---|",
    ]
    for entry in analysis["rounds"]:
        finish = (f'{crosstable.ordinal(entry["opponent_rank"])} place'
                  if entry["opponent_rank"] else "—")
        lines.append(
            f'| {entry["round"]} | {entry["board"] or "—"} | '
            f'{"White" if entry["white"] else "Black"} | {entry["name"]} | '
            f'{entry["rating"] or "—"} | '
            f'{crosstable.score_text(entry["score"])} | {finish} |'
        )

    lines += ["", "## Worth noting", ""]
    for note in notes:
        lines.append(f'**{note["title"]}.** {note["text"]}')
        lines.append("")
    lines.append("Go through this in words: where I played above my level and where I gave points away.")
    return "\n".join(lines)


def open_report(parent: QWidget, conn, link: str = "", player: str = "",
                data: dict | None = None) -> CrossReportDialog | None:
    """Ask for a link (if needed), download the tournament and show the analysis."""
    if data is None:
        asker = LinkDialog(parent, link)
        if asker.exec() != QDialog.DialogCode.Accepted or asker.data is None:
            return None
        data = asker.data
    dialog = CrossReportDialog(data, parent, player=player, conn=conn)
    dialog.exec()
    return dialog
