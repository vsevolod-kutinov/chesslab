"""One Claude Code run per message, with what it is doing shown as it goes.

`claude -p --output-format stream-json` prints one JSON event per line; tool calls
become status updates ("games Muller, Tomas"), the final `result` event is the
answer. The conversation continues through a fixed session id: `--session-id`
creates it on the first message, `--resume` continues it afterwards.

Claude runs without the user's plugins and hooks (`--setting-sources project` in a
folder that has no project settings) and with only Bash, WebFetch and Read; Bash is
further limited to the prep CLI.
"""

from __future__ import annotations

import json
import shlex
import shutil
import urllib.parse
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal, Slot

from .prompt import PREP_CMD, PROMPT

ROOT = Path(__file__).resolve().parents[2]
ALLOWED = [
    f"Bash({PREP_CMD}:*)",
    "WebFetch(domain:chess-results.com)",
    "WebFetch(domain:s2.chess-results.com)",
    "WebFetch(domain:ratings.fide.com)",
    "Read",
]


def _short_command(command: str) -> str:
    """'"/x/python" -m chesslab.prep games "A, B" --out .' -> 'games A, B'."""
    try:
        words = shlex.split(command)
    except ValueError:
        return command[:80]
    if "chesslab.prep" in words:
        words = words[words.index("chesslab.prep") + 1:]
    shown = []
    for word in words:
        if word.startswith("--"):
            break
        if not word.startswith("http"):
            shown.append(word)
    return " ".join(shown)[:80]


def parse_event(line: str) -> tuple[str, str] | None:
    """A stream-json line -> ("status" | "answer" | "error", text), or None."""
    try:
        event = json.loads(line)
    except ValueError:
        return None
    if not isinstance(event, dict):
        return None
    if event.get("type") == "result":
        text = event.get("result") or ""
        return ("error", text or "Claude failed.") if event.get("is_error") else ("answer", text)
    if event.get("type") != "assistant":
        return None
    for item in (event.get("message") or {}).get("content") or []:
        if item.get("type") != "tool_use":
            continue
        args = item.get("input") or {}
        if item.get("name") == "Bash":
            return "status", _short_command(args.get("command", ""))
        if item.get("name") == "WebFetch":
            return "status", f"reading {urllib.parse.urlparse(args.get('url', '')).netloc}"
        return "status", item.get("name", "")
    return None


class ClaudeBridge(QObject):
    status = Signal(str)
    answered = Signal(str)
    failed = Signal(str)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.claude = shutil.which("claude")
        self._process = QProcess(self)
        environment = QProcessEnvironment.systemEnvironment()
        environment.insert("PYTHONPATH", str(ROOT))
        self._process.setProcessEnvironment(environment)
        self._process.readyReadStandardOutput.connect(self._read)
        self._process.finished.connect(self._finished)
        self._process.errorOccurred.connect(self._error)
        self._buffer = b""
        self._answer: tuple[str, str] | None = None
        self._request: tuple[str, str, bool, Path] | None = None
        self._retried = False

    def running(self) -> bool:
        return self._process.state() != QProcess.ProcessState.NotRunning

    def send(self, text: str, session: str, resume: bool, workdir: Path) -> None:
        self._request = (text, session, resume, workdir)
        self._retried = False
        self._start()

    def stop(self) -> None:
        if self.running():
            self._process.kill()

    def _start(self) -> None:
        text, session, resume, workdir = self._request
        self._buffer = b""
        self._answer = None
        self._process.setWorkingDirectory(str(workdir))
        args = [
            "-p", "--model", "sonnet", "--output-format", "stream-json", "--verbose",
            "--setting-sources", "project", "--disable-slash-commands", "--strict-mcp-config",
            "--append-system-prompt", PROMPT,
            "--resume" if resume else "--session-id", session,
            # the message goes through stdin: these lists take any number of values
            # and would swallow a positional prompt
            "--tools", "Bash", "WebFetch", "Read",
            "--allowedTools", *ALLOWED,
        ]
        self._process.start(self.claude, args)
        self._process.write(text.encode("utf-8"))
        self._process.closeWriteChannel()
        self.status.emit("thinking…")

    @Slot()
    def _read(self) -> None:
        self._buffer += bytes(self._process.readAllStandardOutput())
        *lines, self._buffer = self._buffer.split(b"\n")
        for raw in lines:
            event = parse_event(raw.decode("utf-8", errors="replace"))
            if event is None:
                continue
            if event[0] == "status":
                self.status.emit(event[1])
            else:
                self._answer = event

    @Slot(int, QProcess.ExitStatus)
    def _finished(self, code: int, _status) -> None:
        self._read()
        err = bytes(self._process.readAllStandardError()).decode("utf-8", "replace").strip()
        if self._answer and self._answer[0] == "answer":
            self.answered.emit(self._answer[1])
            return
        message = (self._answer[1] if self._answer else "") or err or f"Claude exited with code {code}."
        # the session flag can disagree with Claude's records (a first run that failed
        # half-way, a session deleted by hand): try once with the other flag
        if not self._retried and ("No conversation found" in message or "already in use" in message):
            self._retried = True
            text, session, resume, workdir = self._request
            self._request = (text, session, not resume, workdir)
            self._start()
            return
        self.failed.emit(message[:500])

    @Slot(QProcess.ProcessError)
    def _error(self, error) -> None:
        if error == QProcess.ProcessError.FailedToStart:
            self.failed.emit("Could not start `claude`.")
