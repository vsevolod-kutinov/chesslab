"""Own tournaments: list, tournament card, manual game entry and report.

A tournament is either official (rated by FIDE) or own. For an official one the card
is filled from the ratings.fide.com page by link - name,
city, country, dates and time control come from there.

Games are entered by hand from a paper scoresheet: moves can be entered on the board
with the mouse or typed as a line.
"""

from __future__ import annotations

from datetime import datetime

import chess
from PySide6.QtCore import Qt, Signal, Slot
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import crossreport, fide, games_db, theme, tournaments_db
from .board import BoardWidget
from .explorer import parse_line

COLORS = [("White", True), ("Black", False)]
OUTCOMES = [("won", 1.0), ("draw", 0.5), ("lost", 0.0)]
SPEEDS = [("classical", "Classical"), ("rapid", "Rapid"), ("blitz", "Blitz")]


def _table(headers: list[str], stretch: int) -> QTableWidget:
    table = QTableWidget(0, len(headers))
    table.setHorizontalHeaderLabels(headers)
    table.verticalHeader().setVisible(False)
    table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    table.setShowGrid(False)
    header = table.horizontalHeader()
    for index in range(len(headers)):
        header.setSectionResizeMode(
            index,
            QHeaderView.ResizeMode.Stretch if index == stretch
            else QHeaderView.ResizeMode.ResizeToContents,
        )
    return table


def _cell(text: str, center: bool = False, muted: bool = False) -> QTableWidgetItem:
    item = QTableWidgetItem(text)
    if center:
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
    if muted:
        item.setForeground(theme.TEXT_MUTED)
    return item


def _combo(pairs: list[tuple], parent: QWidget | None = None) -> QComboBox:
    """Dropdown "label -> value"; the value is kept in userData."""
    combo = QComboBox(parent)
    for label, value in pairs:
        combo.addItem(label, value)
    return combo


def _pick(combo: QComboBox, value) -> None:
    index = combo.findData(value)
    if index >= 0:
        combo.setCurrentIndex(index)


def _line_text(sans: list[str]) -> str:
    parts = []
    for index, san in enumerate(sans):
        if index % 2 == 0:
            parts.append(f"{index // 2 + 1}.")
        parts.append(san)
    return " ".join(parts)


class TournamentEditDialog(QDialog):
    """Create or edit a tournament."""

    def __init__(self, parent: QWidget | None = None, row=None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tournament")
        self.setMinimumWidth(500)
        self.setStyleSheet(theme.QSS)

        self._lookup = fide.TournamentLookup(self)
        self._lookup.found.connect(self._fill_from_fide)
        self._lookup.failed.connect(self._lookup_failed)
        self._event = (row["fide_event"] if row else "") or ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        kind = QLabel("TOURNAMENT TYPE")
        kind.setObjectName("heading")
        layout.addWidget(kind)

        self.official_button = QRadioButton("Official - counts toward FIDE rating")
        self.own_button = QRadioButton("Own: club, school, friendly")
        group = QButtonGroup(self)
        group.addButton(self.official_button)
        group.addButton(self.own_button)
        # official by default: that is what the whole thing was made for
        (self.own_button if row and not row["official"]
         else self.official_button).setChecked(True)
        layout.addWidget(self.official_button)
        layout.addWidget(self.own_button)

        self.fide_box = self._build_fide_box()
        layout.addWidget(self.fide_box)

        form = QFormLayout()
        form.setSpacing(8)

        self.name_edit = QLineEdit(row["name"] if row else "")
        self.name_edit.setPlaceholderText("City Championship, stage 3")
        self.player_edit = QLineEdit(row["player"] if row else "")
        self.player_edit.setPlaceholderText("Surname, First name - as in the protocol")
        self.city_edit = QLineEdit(row["place"] if row else "")
        self.city_edit.setPlaceholderText("Prague")
        self.country_edit = QLineEdit(row["country"] if row else "")
        self.country_edit.setPlaceholderText("SVK")
        self.started_edit = QLineEdit(row["started"] if row else "")
        self.started_edit.setPlaceholderText("2026-09-10")
        self.finished_edit = QLineEdit(row["finished"] if row else "")
        self.finished_edit.setPlaceholderText("2026-09-13")
        self.control_edit = QLineEdit(row["time_control"] if row else "")
        self.control_edit.setPlaceholderText("60+30")
        self.speed_combo = _combo([(label, key) for key, label in SPEEDS])
        _pick(self.speed_combo, (row["speed"] if row else "") or "classical")
        self.standing_spin = QSpinBox()
        self.standing_spin.setRange(0, 999)
        self.standing_spin.setSpecialValueText("—")
        self.standing_spin.setValue((row["standing"] if row else 0) or 0)
        self.players_spin = QSpinBox()
        self.players_spin.setRange(0, 999)
        self.players_spin.setSpecialValueText("—")
        self.players_spin.setValue((row["players"] if row else 0) or 0)
        self.notes_edit = QPlainTextEdit(row["notes"] if row else "")
        self.notes_edit.setFixedHeight(60)

        dates = QHBoxLayout()
        dates.setSpacing(6)
        dates.addWidget(self.started_edit)
        dates.addWidget(QLabel("—"))
        dates.addWidget(self.finished_edit)
        dates_box = QWidget()
        dates_box.setLayout(dates)
        dates.setContentsMargins(0, 0, 0, 0)

        control = QHBoxLayout()
        control.setSpacing(6)
        control.addWidget(self.control_edit, 1)
        control.addWidget(self.speed_combo, 1)
        control_box = QWidget()
        control_box.setLayout(control)
        control.setContentsMargins(0, 0, 0, 0)

        standing = QHBoxLayout()
        standing.setSpacing(6)
        standing.addWidget(self.standing_spin, 1)
        standing.addWidget(QLabel("of"))
        standing.addWidget(self.players_spin, 1)
        standing_box = QWidget()
        standing_box.setLayout(standing)
        standing.setContentsMargins(0, 0, 0, 0)

        form.addRow("Name", self.name_edit)
        form.addRow("My name", self.player_edit)
        form.addRow("City", self.city_edit)
        form.addRow("Country", self.country_edit)
        form.addRow("Dates", dates_box)
        form.addRow("Time control", control_box)
        form.addRow("My place", standing_box)
        form.addRow("Notes", self.notes_edit)
        layout.addLayout(form)

        hint = QLabel(
            "\"My name\" - the tournament games are stored under it; colour and result "
            "are computed from it. Country is the three-letter FIDE code (SVK, RUS, GER). "
            "\"My place\" is the final one, entered after the tournament."
        )
        hint.setObjectName("status")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.official_button.toggled.connect(self._update_kind)
        self._update_kind()

    def _build_fide_box(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.link_edit = QLineEdit(
            fide.URL.format(self._event) if self._event else ""
        )
        self.link_edit.setPlaceholderText(
            "https://ratings.fide.com/tournament_information.phtml?event=495733"
        )
        self.link_edit.returnPressed.connect(self.load_from_fide)
        self.load_button = QPushButton("Fill in")
        self.load_button.clicked.connect(self.load_from_fide)
        row.addWidget(self.link_edit, 1)
        row.addWidget(self.load_button)
        layout.addLayout(row)

        self.fide_status = QLabel(
            "Paste a link to a tournament on ratings.fide.com - name, city, "
            "country, dates and time control will be filled in automatically."
        )
        self.fide_status.setObjectName("status")
        self.fide_status.setWordWrap(True)
        layout.addWidget(self.fide_status)
        return box

    @Slot()
    def _update_kind(self) -> None:
        self.fide_box.setVisible(self.official_button.isChecked())
        self.adjustSize()

    @Slot()
    def load_from_fide(self) -> None:
        link = self.link_edit.text().strip()
        if not link:
            self.fide_status.setText("Paste a tournament link first.")
            return
        self.load_button.setEnabled(False)
        self.fide_status.setText("Asking FIDE...")
        self._lookup.start(link)

    @Slot(dict)
    def _fill_from_fide(self, data: dict) -> None:
        self.load_button.setEnabled(True)
        self._event = data["event"]
        self.name_edit.setText(data["name"])
        self.city_edit.setText(data["city"])
        self.country_edit.setText(data["country"])
        self.started_edit.setText(data["started"])
        self.finished_edit.setText(data["finished"])
        self.control_edit.setText(data["time_control"])
        if data["speed"]:
            _pick(self.speed_combo, data["speed"])
        if data["players"].isdigit():
            self.players_spin.setValue(int(data["players"]))

        where = ", ".join(p for p in (data["city"], data["country_name"]) if p)
        self.fide_status.setText(
            f'{data["name"]} · {where} · {data["raw_control"] or "no time control given"}'
        )

    @Slot(str)
    def _lookup_failed(self, message: str) -> None:
        self.load_button.setEnabled(True)
        self.fide_status.setText(message)

    def _save(self) -> None:
        if not self.name_edit.text().strip():
            QMessageBox.warning(self, "ChessLab", "A tournament needs a name.")
            return
        if not self.player_edit.text().strip():
            QMessageBox.warning(self, "ChessLab", "Enter how you are listed in the protocol.")
            return
        self.accept()

    def values(self) -> dict:
        official = self.official_button.isChecked()
        return {
            "name": self.name_edit.text().strip(),
            "player": self.player_edit.text().strip(),
            "place": self.city_edit.text().strip(),
            "country": self.country_edit.text().strip().upper(),
            "started": self.started_edit.text().strip(),
            "finished": self.finished_edit.text().strip(),
            "time_control": self.control_edit.text().strip(),
            "speed": self.speed_combo.currentData(),
            "standing": self.standing_spin.value() or None,
            "players": self.players_spin.value() or None,
            "official": 1 if official else 0,
            "fide_event": self._event if official else "",
            "notes": self.notes_edit.toPlainText().strip(),
        }


class GameEntryDialog(QDialog):
    """A game from a paper scoresheet: opponent, result and moves."""

    def __init__(self, tournament, parent: QWidget | None = None, row=None) -> None:
        super().__init__(parent)
        self.tournament = tournament
        self.game_id = row["id"] if row else None
        self.sans: list[str] = (row["moves"] or "").split() if row else []
        self.setWindowTitle("Game" if row else "New game")
        self.resize(900, 620)
        self.setStyleSheet(theme.QSS)

        outer = QHBoxLayout(self)
        outer.setContentsMargins(16, 16, 16, 16)
        outer.setSpacing(14)
        outer.addWidget(self._build_board(), 0)
        outer.addWidget(self._build_form(row), 1)

        self._refresh()

    # --- assembly --------------------------------------------------------

    def _build_board(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(8)

        self.board_widget = BoardWidget()
        self.board_widget.setFixedSize(440, 440)
        self.board_widget.move_played.connect(self._on_board_move)
        layout.addWidget(self.board_widget)

        buttons = QHBoxLayout()
        buttons.setSpacing(6)
        for text, slot in (("Take back", self.take_back),
                           ("Clear moves", self.clear_moves),
                           ("Flip", self.flip)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        layout.addLayout(buttons)
        layout.addStretch(1)
        return box

    def _build_form(self, row) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        caption = QLabel("WHO, WITH WHAT COLOUR AND HOW IT ENDED")
        caption.setObjectName("heading")
        layout.addWidget(caption)

        form = QFormLayout()
        form.setSpacing(8)

        self.round_spin = QSpinBox()
        self.round_spin.setRange(0, 30)
        self.round_spin.setSpecialValueText("—")
        self.color_combo = _combo(COLORS)
        self.opponent_edit = QLineEdit()
        self.opponent_edit.setPlaceholderText("Surname, First name")
        self.elo_spin = QSpinBox()
        self.elo_spin.setRange(0, 3500)
        self.elo_spin.setSpecialValueText("unknown")
        self.outcome_combo = _combo(OUTCOMES)
        self.date_edit = QLineEdit()
        self.date_edit.setPlaceholderText(self.tournament["started"] or "2026-09-10")

        form.addRow("Round", self.round_spin)
        form.addRow("I played", self.color_combo)
        form.addRow("Opponent", self.opponent_edit)
        form.addRow("Their rating", self.elo_spin)
        form.addRow("Result", self.outcome_combo)
        form.addRow("Date", self.date_edit)
        layout.addLayout(form)

        moves_label = QLabel("MOVES")
        moves_label.setObjectName("heading")
        layout.addWidget(moves_label)

        self.moves_edit = QPlainTextEdit()
        self.moves_edit.setPlaceholderText("1. e4 e5 2. Nf3 Nc6 3. Bb5")
        self.moves_edit.textChanged.connect(self._on_text_changed)
        layout.addWidget(self.moves_edit, 1)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

        hint = QLabel(
            "Moves can be entered on the board with the mouse or typed: move numbers "
            "are understood too. "
            "A game without moves is saved as well - the result still goes into the table."
        )
        hint.setObjectName("status")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.button(QDialogButtonBox.StandardButton.Save).setText("Save")
        buttons.button(QDialogButtonBox.StandardButton.Cancel).setText("Cancel")
        buttons.accepted.connect(self._save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._fill_from(row)
        self.color_combo.currentIndexChanged.connect(self._face_my_color)
        return box

    def _fill_from(self, row) -> None:
        if row is None:
            return
        me_white = (row["white"] or "").lower() == self.tournament["player"].lower()
        _pick(self.color_combo, me_white)
        self.opponent_edit.setText(row["black"] if me_white else row["white"])
        self.elo_spin.setValue((row["black_elo"] if me_white else row["white_elo"]) or 0)
        if (row["round"] or "").isdigit():
            self.round_spin.setValue(int(row["round"]))

        if not row["winner"]:
            points = 0.5
        elif (row["winner"] == "white") == me_white:
            points = 1.0
        else:
            points = 0.0
        _pick(self.outcome_combo, points)

        stamp = datetime.fromtimestamp(row["played_at"] / 1000)
        self.date_edit.setText(stamp.strftime("%Y-%m-%d"))
        self.moves_edit.setPlainText(_line_text(self.sans))
        self._face_my_color()

    # --- moves -----------------------------------------------------------

    def _position(self) -> tuple[chess.Board, chess.Move | None]:
        board = chess.Board()
        last = None
        for san in self.sans:
            last = board.parse_san(san)
            board.push(last)
        return board, last

    def _refresh(self, message: str = "") -> None:
        board, last = self._position()
        self.board_widget.set_position(board, last)
        if message:
            self.status.setText(message)
        elif self.sans:
            self.status.setText(f"moves recorded: {len(self.sans)}")
        else:
            self.status.setText("no moves yet")

    @Slot()
    def _on_text_changed(self) -> None:
        text = self.moves_edit.toPlainText()
        if not text.strip():
            self.sans = []
            self._refresh()
            return
        line, bad = parse_line(text)
        self.sans = line
        self._refresh(f"Could not parse move '{bad}' - stopped there." if bad else "")

    @Slot(object)
    def _on_board_move(self, move: chess.Move) -> None:
        board, _ = self._position()
        self.sans.append(board.san(move))
        self._write_moves()

    def _write_moves(self) -> None:
        """Rewrite the move line without triggering a re-parse in response."""
        self.moves_edit.blockSignals(True)
        self.moves_edit.setPlainText(_line_text(self.sans))
        self.moves_edit.blockSignals(False)
        cursor = self.moves_edit.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        self.moves_edit.setTextCursor(cursor)
        self._refresh()

    @Slot()
    def take_back(self) -> None:
        if self.sans:
            self.sans.pop()
            self._write_moves()

    @Slot()
    def clear_moves(self) -> None:
        self.sans = []
        self._write_moves()

    @Slot()
    def flip(self) -> None:
        self.board_widget.flip()

    @Slot()
    def _face_my_color(self) -> None:
        """Flip the board to our own colour - easier to copy from the scoresheet."""
        if self.board_widget.flipped == self.color_combo.currentData():
            self.board_widget.flip()

    # --- saving ----------------------------------------------------------

    def _save(self) -> None:
        if not self.opponent_edit.text().strip():
            QMessageBox.warning(self, "ChessLab", "Who was the opponent?")
            return
        self.accept()

    def values(self) -> dict:
        return {
            "round": self.round_spin.value() or "",
            "me_white": self.color_combo.currentData(),
            "opponent": self.opponent_edit.text().strip(),
            "opponent_elo": self.elo_spin.value() or None,
            "points": self.outcome_combo.currentData(),
            "date": self.date_edit.text().strip(),
            "sans": self.sans,
        }


class ReportDialog(QDialog):
    """Tournament report."""

    game_chosen = Signal(str, str)

    def __init__(self, conn, tournament, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.conn = conn
        self.tournament = tournament
        self.setWindowTitle(f"Report - {tournament['name']}")
        self.resize(860, 700)
        self.setStyleSheet(theme.QSS)
        self.summary = tournaments_db.report(conn, tournament["id"])

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        title = QLabel(tournament["name"].upper())
        title.setObjectName("heading")
        layout.addWidget(title)

        where = " · ".join(part for part in (
            tournaments_db.place_text(tournament),
            self._dates(),
            tournament["time_control"],
            self._standing(),
            "FIDE" if tournament["official"] else "own tournament",
        ) if part)
        place = QLabel(where)
        place.setObjectName("status")
        layout.addWidget(place)

        self.totals = QLabel()
        self.totals.setWordWrap(True)
        self.totals.setStyleSheet("padding: 8px 0;")
        layout.addWidget(self.totals)

        games_label = QLabel("GAMES")
        games_label.setObjectName("heading")
        layout.addWidget(games_label)

        self.games_table = _table(
            ["Round", "Colour", "Opponent", "Rating", "Point", "Opening"], stretch=5
        )
        self.games_table.doubleClicked.connect(self._open_selected)
        layout.addWidget(self.games_table, 3)

        openings_label = QLabel("OPENINGS")
        openings_label.setObjectName("heading")
        layout.addWidget(openings_label)

        self.openings_table = _table(["Opening", "Games", "Points"], stretch=0)
        layout.addWidget(self.openings_table, 2)

        buttons = QHBoxLayout()
        for text, slot in (("Open on board", self._open_selected),
                           ("Edit", self.edit_game),
                           ("Delete", self.delete_game)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            buttons.addWidget(button)
        buttons.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        hint = QLabel("double-click a game to open it on the board")
        hint.setObjectName("status")
        layout.addWidget(hint)

        self.reload()

    def _standing(self) -> str:
        text = tournaments_db.standing_text(self.tournament, " of ")
        return f"place {text}" if text else ""

    def _dates(self) -> str:
        started, finished = self.tournament["started"], self.tournament["finished"]
        if started and finished and finished != started:
            return f"{started} — {finished}"
        return started or finished or ""

    def reload(self) -> None:
        self.summary = tournaments_db.report(self.conn, self.tournament["id"])
        self._fill_totals()
        self._fill_games()
        self._fill_openings()

    def _fill_totals(self) -> None:
        summary = self.summary
        if not summary["games"]:
            self.totals.setText("No games yet.")
            return

        points = f'{summary["points"]:g}'
        parts = [
            f'{points} out of {summary["games"]}   ·   {summary["score_pct"]:.1f}%',
            f'+{summary["wins"]} ={summary["draws"]} −{summary["losses"]}',
        ]
        for name, key in (("as White", "white"), ("as Black", "black")):
            side = summary[key]
            if side["games"]:
                got = f'{side["points"]:g}'
                parts.append(f'{name} {got}/{side["games"]}')
        if summary["avg_opponent"]:
            parts.append(f'average opponent {summary["avg_opponent"]}')
        if summary["performance"]:
            parts.append(f'performance {summary["performance"]}')
        self.totals.setText("   ·   ".join(parts))

    def _fill_games(self) -> None:
        player = self.tournament["player"].lower()
        rows = self.summary["rows"]
        self.games_table.setRowCount(len(rows))

        for index, row in enumerate(rows):
            as_white = (row["white"] or "").lower() == player
            opponent = row["black"] if as_white else row["white"]
            elo = row["black_elo"] if as_white else row["white_elo"]

            if not row["winner"]:
                point, mark = "½", "draw"
            elif (row["winner"] == "white") == as_white:
                point, mark = "1", "win"
            else:
                point, mark = "0", "loss"

            opening = f'{row["eco"]} {row["opening"]}'.strip()
            if not opening:
                opening = "—" if row["moves"] else "moves not recorded"

            cells = [
                _cell(row["round"] or str(index + 1), center=True),
                _cell("White" if as_white else "Black", center=True),
                _cell(str(opponent)),
                _cell(str(elo) if elo else "—", center=True),
                _cell(point, center=True),
                _cell(opening, muted=True),
            ]
            cells[4].setToolTip(mark)
            for column, item in enumerate(cells):
                self.games_table.setItem(index, column, item)

    def _fill_openings(self) -> None:
        entries = sorted(
            self.summary["openings"].items(),
            key=lambda pair: (-pair[1]["games"], pair[0]),
        )
        self.openings_table.setRowCount(len(entries))
        for index, (name, data) in enumerate(entries):
            got = f'{data["points"]:g}'
            self.openings_table.setItem(index, 0, _cell(name))
            self.openings_table.setItem(index, 1, _cell(str(data["games"]), center=True))
            self.openings_table.setItem(
                index, 2, _cell(f'{got} out of {data["games"]}', center=True, muted=True)
            )

    def _current(self):
        row = self.games_table.currentRow()
        rows = self.summary["rows"]
        return rows[row] if 0 <= row < len(rows) else None

    @Slot()
    def _open_selected(self) -> None:
        row = self._current()
        if row is not None and row["pgn"]:
            self.game_chosen.emit(row["pgn"], row["id"])
            self.accept()

    @Slot()
    def edit_game(self) -> None:
        row = self._current()
        if row is None:
            return
        dialog = GameEntryDialog(self.tournament, self, row)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            tournaments_db.save_game(self.conn, self.tournament, dialog.values(),
                                     game_id=row["id"])
        except tournaments_db.GameError as exc:
            QMessageBox.warning(self, "ChessLab", str(exc))
            return
        self.reload()

    @Slot()
    def delete_game(self) -> None:
        row = self._current()
        if row is None:
            return
        confirm = QMessageBox.question(
            self, "ChessLab", "Delete this game?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm == QMessageBox.StandardButton.Yes:
            tournaments_db.delete_game(self.conn, row["id"])
            self.reload()


class TournamentsDialog(QDialog):
    """List of tournaments and everything you can do with them."""

    game_chosen = Signal(str, str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("My tournaments")
        self.resize(820, 520)
        self.setStyleSheet(theme.QSS)

        self.conn = games_db.connect()
        self.rows: list = []

        self._build_ui()
        self.reload()

    def _build_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        caption = QLabel("TOURNAMENTS I PLAYED")
        caption.setObjectName("heading")
        layout.addWidget(caption)

        self.table = _table(
            ["Tournament", "Category", "Date", "City", "Games", "Points", "Place"],
            stretch=0,
        )
        self.table.doubleClicked.connect(self.show_report)
        self.table.currentCellChanged.connect(lambda *_: self._update_buttons())
        layout.addWidget(self.table, 1)

        top = QHBoxLayout()
        top.setSpacing(6)
        for text, slot in (("New", self.create_tournament),
                           ("From chess-results...", self.show_cross_report),
                           ("Edit", self.edit_tournament),
                           ("Add game", self.add_game),
                           ("Report", self.show_report),
                           ("Delete", self.delete_tournament)):
            button = QPushButton(text)
            button.clicked.connect(slot)
            top.addWidget(button)
            setattr(self, f"_btn_{slot.__name__}", button)
        top.addStretch(1)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        top.addWidget(close_button)
        layout.addLayout(top)

        self.status = QLabel("")
        self.status.setObjectName("status")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)

    def _update_buttons(self) -> None:
        has = self.table.currentRow() >= 0
        for name in ("edit_tournament", "add_game", "show_report",
                     "delete_tournament"):
            getattr(self, f"_btn_{name}").setEnabled(has)

    @Slot()
    def reload(self) -> None:
        self.rows = tournaments_db.all_tournaments(self.conn)
        self.table.setRowCount(len(self.rows))

        for index, row in enumerate(self.rows):
            summary = tournaments_db.report(self.conn, row["id"])
            points = f'{summary["points"]:g}'
            cells = [
                _cell(row["name"]),
                _cell("FIDE" if row["official"] else "own", center=True),
                _cell(row["started"] or "—", center=True),
                _cell(tournaments_db.place_text(row) or "—"),
                _cell(str(summary["games"]), center=True),
                _cell(f'{points} out of {summary["games"]}' if summary["games"] else "—",
                      center=True),
                _cell(tournaments_db.standing_text(row) or "—", center=True),
            ]
            for column, item in enumerate(cells):
                self.table.setItem(index, column, item)

        self._update_buttons()
        self.status.setText(
            "No tournaments yet - press \"New\"." if not self.rows
            else f"tournaments: {len(self.rows)}   ·   double-click for the report"
        )

    def _current(self):
        row = self.table.currentRow()
        return self.rows[row] if 0 <= row < len(self.rows) else None

    @Slot()
    def create_tournament(self) -> None:
        dialog = TournamentEditDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        tournaments_db.create(self.conn, **dialog.values())
        self.reload()
        self.status.setText("Tournament created - now add the games one by one.")

    @Slot()
    def edit_tournament(self) -> None:
        row = self._current()
        if row is None:
            return
        dialog = TournamentEditDialog(self, row)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        values = dialog.values()
        tournaments_db.update(self.conn, row["id"], **values)
        # the player name is part of the calculations, so already saved games are fixed too
        if values["player"] != row["player"]:
            self.conn.execute(
                "UPDATE games SET account = ? WHERE tournament_id = ?",
                (values["player"], row["id"]),
            )
            self.conn.commit()
        self.reload()

    @Slot()
    def delete_tournament(self) -> None:
        row = self._current()
        if row is None:
            return
        confirm = QMessageBox.question(
            self, "ChessLab",
            f"Delete '{row['name']}' together with all its games?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        tournaments_db.delete(self.conn, row["id"])
        self.reload()
        self.status.setText(f"'{row['name']}' deleted.")

    @Slot()
    def add_game(self) -> None:
        row = self._current()
        if row is None:
            return
        dialog = GameEntryDialog(row, self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        try:
            tournaments_db.save_game(self.conn, row, dialog.values())
        except tournaments_db.GameError as exc:
            QMessageBox.warning(self, "ChessLab", str(exc))
            return
        self.reload()
        self.status.setText("Game saved. Add the next one with \"Add game\" again.")

    @Slot()
    def show_cross_report(self) -> None:
        """Crosstable analysis from chess-results - with own and other players' games."""
        row = self._current()
        crossreport.open_report(
            self, self.conn,
            link=(row["cr_link"] if row else "") or "",
            player=(row["player"] if row else "") or "",
        )
        self.reload()

    @Slot()
    def show_report(self) -> None:
        row = self._current()
        if row is None:
            return
        dialog = ReportDialog(self.conn, row, self)
        dialog.game_chosen.connect(self._forward_game)
        dialog.exec()
        self.reload()

    @Slot(str, str)
    def _forward_game(self, pgn: str, game_id: str) -> None:
        self.game_chosen.emit(pgn, game_id)
        self.accept()

    def closeEvent(self, event) -> None:
        self.conn.close()
        super().closeEvent(event)
