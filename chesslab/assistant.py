"""The "Assistant" window: a chat with Claude Code about players and tournaments.

You write in plain words ("scout boards 7-8 of Humenné C"), Claude reads
chess-results pages and runs `scout.py` to pull the players' games, then answers.
Each message is one `claude -p` run; the conversation continues through a fixed
session id, so follow-up questions remember what was already found.

Claude may only fetch chess-results and run the scout module - nothing else on
the machine. The PGN files it downloads land in SCOUT_DIR and open from the
"Open games" button.
"""

from __future__ import annotations

import shutil
import sys
import uuid
from pathlib import Path

from PySide6.QtCore import QProcess, QProcessEnvironment, Signal, Slot
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from . import storage, theme

SCOUT_DIR = storage.DATA_DIR / "scout"
ROOT = Path(__file__).resolve().parent.parent
# shortcut: a frozen build has no Python to run `-m chesslab.scout`, so there Claude
# only reads pages; ship scout as a separate exe if the Windows build needs it
SCOUT_CMD = f'"{sys.executable}" -m chesslab.scout'

PROMPT = f"""You are the scouting assistant inside ChessLab, a chess app.
The user is a club player preparing for over-the-board games (team leagues, opens).
Reply in the user's language, short and practical: who they will likely face,
what those players open with as White and as Black, their form, and what to prepare.

Tools:
- Player games and opening summary from the chess-results game database:
  {SCOUT_CMD} "Last, First:FIDE_ID" "Last, First:FIDE_ID" ... --out . [--event TEXT]
  Always pass the FIDE ID when it is known (no namesakes); take it from the
  FideID column of the tournament's starting list (art=16). `--event` keeps only
  tournaments whose name contains the text, e.g. --event "2. liga" for league games
  only - use it when the user asks for one competition.
  It saves one PGN per player in the current folder and prints a summary.
  Mention the PGN file names at the end, the user can open them in ChessLab.
- chess-results tournament pages via WebFetch (www.chess-results.com redirects
  to s2.chess-results.com - fetch the redirect URL; add &SNode=S0):
  tnr<ID>.aspx?lan=1&art=8 team rosters, art=2 team pairings of all rounds,
  art=3&rd=N board pairings of round N, art=1 rosters with results,
  art=16 starting list with FIDE IDs, art=0 crosstable, art=9&snr=N player card.

Name collisions happen: if a summary mixes ratings or federations that do not fit,
say so. Do not invent games or moves you have not seen."""


class AssistantDialog(QDialog):
    pgn_chosen = Signal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Assistant")
        self.resize(760, 680)
        self.setStyleSheet(theme.QSS)

        self._claude = shutil.which("claude")
        self._session = ""
        self._started = False
        self._transcript: list[str] = []

        self._view = QTextBrowser()
        self._view.setOpenExternalLinks(True)
        self._status = QLabel()
        self._status.setStyleSheet(f"color: {theme.TEXT_MUTED.name()};")

        self._input = QLineEdit()
        self._input.setPlaceholderText(
            "e.g. scout boards 7-8 of the teams we play next: <chess-results link>"
        )
        self._input.returnPressed.connect(self._send)
        self._send_button = QPushButton("Send")
        self._send_button.clicked.connect(self._send)

        new_chat = QPushButton("New chat")
        new_chat.clicked.connect(self._new_chat)
        open_games = QPushButton("Open games…")
        open_games.clicked.connect(self._open_games)

        row = QHBoxLayout()
        row.addWidget(self._input, 1)
        row.addWidget(self._send_button)
        bottom = QHBoxLayout()
        bottom.addWidget(self._status, 1)
        bottom.addWidget(open_games)
        bottom.addWidget(new_chat)

        layout = QVBoxLayout(self)
        layout.addWidget(self._view, 1)
        layout.addLayout(row)
        layout.addLayout(bottom)

        self._process = QProcess(self)
        self._process.setWorkingDirectory(str(SCOUT_DIR))
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONPATH", str(ROOT))
        self._process.setProcessEnvironment(environment)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._failed)

        self._new_chat()
        if not self._claude:
            self._input.setEnabled(False)
            self._send_button.setEnabled(False)
            self._status.setText("Claude Code (`claude`) is not installed or not on PATH.")

    @Slot()
    def _new_chat(self) -> None:
        if self._process.state() != QProcess.ProcessState.NotRunning:
            self._process.kill()
        self._session = str(uuid.uuid4())
        self._started = False
        self._transcript = []
        self._render()
        self._status.setText("")

    def _render(self) -> None:
        self._view.setMarkdown("\n\n---\n\n".join(self._transcript))
        bar = self._view.verticalScrollBar()
        bar.setValue(bar.maximum())

    @Slot()
    def _send(self) -> None:
        text = self._input.text().strip()
        if not text or self._process.state() != QProcess.ProcessState.NotRunning:
            return
        SCOUT_DIR.mkdir(parents=True, exist_ok=True)
        self._input.clear()
        self._transcript.append(f"**You:** {text}")
        self._render()

        session = ["--resume", self._session] if self._started else ["--session-id", self._session]
        args = [
            "-p", "--model", "sonnet", "--output-format", "text",
            "--append-system-prompt", PROMPT,
            *session,
            # the prompt goes through stdin: --allowedTools takes any number of values
            # and would swallow a positional prompt
            "--allowedTools",
            f"Bash({SCOUT_CMD}:*)",
            "WebFetch(domain:chess-results.com)",
            "WebFetch(domain:s2.chess-results.com)",
            "Read",
        ]
        self._process.start(self._claude, args)
        self._process.write(text.encode("utf-8"))
        self._process.closeWriteChannel()
        self._started = True
        self._send_button.setEnabled(False)
        self._status.setText("Thinking… downloading games can take a minute or two.")

    @Slot(int, QProcess.ExitStatus)
    def _finished(self, code: int, _status) -> None:
        out = bytes(self._process.readAllStandardOutput()).decode("utf-8", "replace").strip()
        err = bytes(self._process.readAllStandardError()).decode("utf-8", "replace").strip()
        if code == 0 and out:
            self._transcript.append(out)
            self._status.setText("")
        else:
            self._status.setText(f"Claude exited with code {code}.")
            self._transcript.append(f"*Error:* {err or out or 'no output'}")
        self._render()
        self._send_button.setEnabled(True)

    @Slot(QProcess.ProcessError)
    def _failed(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self._status.setText("Could not start `claude`.")
            self._send_button.setEnabled(True)

    @Slot()
    def _open_games(self) -> None:
        SCOUT_DIR.mkdir(parents=True, exist_ok=True)
        path, _ = QFileDialog.getOpenFileName(
            self, "Open scouted games", str(SCOUT_DIR), "Games (*.pgn);;All files (*)"
        )
        if path:
            self.pgn_chosen.emit(path)
