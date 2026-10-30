"""The Prep tab: a step-by-step chat with Claude about the next opponent.

Saved preps are picked at the top; a new one starts on the first message. Moves the
assistant writes as `line:` links open on the board; candidate cards are drawn from
the prep folder after every answer.
"""

from __future__ import annotations

import chess
import chess.pgn
from PySide6.QtCore import Qt, QUrl, Signal, Slot
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from .. import games_db, theme
from . import store
from .bridge import ClaudeBridge
from .cards_view import CardsView

HINT = ("Paste the tournament link, the team and your board — "
        "or just write who you play.")


def line_pgn(moves: str) -> str:
    """'e4_c5_Nf3' -> PGN of the legal part of the line."""
    game = chess.pgn.Game()
    node = game
    for san in moves.replace("_", " ").split():
        try:
            move = node.board().parse_san(san.rstrip("+#!?"))
        except ValueError:
            break  # the rest cannot be shown
        node = node.add_variation(move)
    return str(game)


class PrepPanel(QFrame):
    line_chosen = Signal(str)  # PGN of a line to show on the board
    pgn_chosen = Signal(str)   # path of a PGN file to open

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("panel")
        self._conn = games_db.connect()
        self._prep_id: int | None = None
        self._messages: list[dict] = []

        self._bridge = ClaudeBridge(self)
        self._bridge.status.connect(self._on_status)
        self._bridge.answered.connect(self._on_answer)
        self._bridge.failed.connect(self._on_failed)

        self._preps = QComboBox()
        self._preps.currentIndexChanged.connect(self._on_prep_picked)
        new_prep = QPushButton("New prep")
        new_prep.clicked.connect(self.new_prep)
        self.eval_label = QLabel("—")
        self.eval_label.setFont(theme.tabular(self.font(), 15, bold=True))
        self.eval_label.setToolTip("Engine evaluation of the board position")

        top = QHBoxLayout()
        top.addWidget(self._preps, 1)
        top.addWidget(new_prep)
        top.addSpacing(theme.gap(2))
        top.addWidget(self.eval_label)

        self._cards = CardsView()

        self._chat = QTextBrowser()
        self._chat.setOpenLinks(False)
        self._chat.anchorClicked.connect(self._on_link)
        self._chat.setStyleSheet("QTextBrowser { padding: 8px 10px; font-size: 13px; }")

        self._input = QLineEdit()
        self._input.setPlaceholderText("Message — e.g. Humenné C, board 7, <link>")
        self._input.returnPressed.connect(self._send)
        self._send_button = QPushButton("Send")
        self._send_button.clicked.connect(self._send)
        row = QHBoxLayout()
        row.addWidget(self._input, 1)
        row.addWidget(self._send_button)

        self._status = QLabel("")
        self._status.setObjectName("status")
        games = QPushButton("Games…")
        games.setToolTip("Open a downloaded PGN of this prep on the board")
        games.clicked.connect(self._open_games)
        bottom = QHBoxLayout()
        bottom.addWidget(self._status, 1)
        bottom.addWidget(games)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(theme.gap(4), theme.gap(4), theme.gap(4), theme.gap(4))
        layout.setSpacing(10)
        layout.addLayout(top)
        layout.addWidget(self._cards)
        layout.addWidget(self._chat, 1)
        layout.addLayout(row)
        layout.addLayout(bottom)

        if not self._bridge.claude:
            self._input.setEnabled(False)
            self._send_button.setEnabled(False)
            self._status.setText("Claude Code (`claude`) is not installed or not on PATH.")

        self._fill_preps()

    # --- preps -----------------------------------------------------------

    def _fill_preps(self, select: int | None = None) -> None:
        self._preps.blockSignals(True)
        self._preps.clear()
        self._preps.addItem("New prep", None)
        for row in store.all_preps(self._conn):
            self._preps.addItem(row["title"], row["id"])
        index = self._preps.findData(select) if select is not None else (1 if self._preps.count() > 1 else 0)
        self._preps.setCurrentIndex(max(index, 0))
        self._preps.blockSignals(False)
        self._on_prep_picked()

    @Slot()
    def new_prep(self) -> None:
        self._preps.setCurrentIndex(0)

    @Slot()
    def _on_prep_picked(self) -> None:
        self._prep_id = self._preps.currentData()
        row = store.get(self._conn, self._prep_id) if self._prep_id else None
        self._messages = store.transcript(row) if row else []
        self._cards.load(store.folder(self._prep_id) if self._prep_id else None)
        self._render()
        self._input.setFocus()

    # --- chat --------------------------------------------------------------

    def _render(self) -> None:
        if not self._messages:
            self._chat.setMarkdown(f"*{HINT}*")
            return
        parts = []
        for message in self._messages:
            if message["role"] == "user":
                parts.append(f"**You:** {message['text']}")
            elif message["role"] == "error":
                parts.append(f"*Error: {message['text']}*")
            else:
                parts.append(message["text"])
        self._chat.setMarkdown("\n\n---\n\n".join(parts))
        # the markdown reader paints links in the application's link colour, not the widget's
        link = QGuiApplication.palette().link().color().name()
        self._chat.setHtml(self._chat.toHtml().replace(f"color:{link}", f"color:{theme.ACCENT.name()}"))
        bar = self._chat.verticalScrollBar()
        bar.setValue(bar.maximum())

    def _save(self) -> None:
        store.save(self._conn, self._prep_id, transcript=self._messages)

    @Slot()
    def _send(self) -> None:
        text = self._input.text().strip()
        if not text or self._bridge.running():
            return
        if self._prep_id is None:
            self._prep_id = store.create(self._conn, text[:40])
            self._fill_preps(select=self._prep_id)
        resume = any(m["role"] == "assistant" for m in self._messages)
        self._messages.append({"role": "user", "text": text})
        self._save()
        self._input.clear()
        self._render()
        self._busy(True)
        row = store.get(self._conn, self._prep_id)
        self._bridge.send(text, row["session"], resume, store.folder(self._prep_id))

    def _busy(self, busy: bool) -> None:
        self._send_button.setEnabled(not busy)
        self._preps.setEnabled(not busy)

    @Slot(str)
    def _on_status(self, text: str) -> None:
        self._status.setText(f"{text}…" if not text.endswith("…") else text)

    @Slot(str)
    def _on_answer(self, text: str) -> None:
        self._messages.append({"role": "assistant", "text": text})
        self._finish()

    @Slot(str)
    def _on_failed(self, text: str) -> None:
        self._messages.append({"role": "error", "text": text})
        self._finish()

    def _finish(self) -> None:
        self._save()
        self._render()
        self._cards.load(store.folder(self._prep_id))
        self._status.setText("")
        self._busy(False)

    # --- links -------------------------------------------------------------

    @Slot(QUrl)
    def _on_link(self, url: QUrl) -> None:
        if url.scheme() == "line":
            self.line_chosen.emit(line_pgn(url.path()))
        elif url.scheme() in ("http", "https"):
            QDesktopServices.openUrl(url)

    @Slot()
    def _open_games(self) -> None:
        folder = store.folder(self._prep_id) if self._prep_id else None
        path, _ = QFileDialog.getOpenFileName(
            self, "Open prep games", str(folder or ""), "Games (*.pgn);;All files (*)"
        )
        if path:
            self.pgn_chosen.emit(path)

    def set_eval(self, html: str) -> None:
        self.eval_label.setText(html)

    def shutdown(self) -> None:
        self._bridge.stop()
