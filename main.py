#!/usr/bin/env python3
"""ChessLab entry point."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

ENGINE_NAME = "stockfish.exe" if sys.platform == "win32" else "stockfish"
DEFAULT_ENGINE = Path.home() / ".local" / "bin" / "stockfish"


def _runnable(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def _bundled_engine() -> Path | None:
    """The engine shipped next to a frozen build (PyInstaller)."""
    if not getattr(sys, "frozen", False):
        return None
    roots = [Path(sys.executable).parent, Path(getattr(sys, "_MEIPASS", ""))]
    for root in roots:
        candidate = root / "engine" / ENGINE_NAME
        if _runnable(candidate):
            return candidate
    return None


def find_engine(explicit: str | None) -> str | None:
    """Explicit path, then the one chosen earlier, then bundled, then the usual places."""
    if explicit:
        return explicit

    from chesslab import storage

    saved = storage.load_settings().get("engine")
    if saved and _runnable(Path(saved)):
        return saved

    for candidate in (_bundled_engine(), DEFAULT_ENGINE):
        if candidate is not None and _runnable(candidate):
            return str(candidate)
    return shutil.which("stockfish")


def ask_engine() -> str | None:
    """No engine found: let the user point at one and remember the choice.

    A message box, not sys.exit with text: a windowed build has no console.
    """
    from PySide6.QtWidgets import QFileDialog, QMessageBox

    from chesslab import storage

    QMessageBox.information(
        None,
        "ChessLab",
        "Stockfish was not found.\n\n"
        "Download it from stockfishchess.org/download and choose the "
        "executable in the next window.",
    )
    pattern = "Stockfish (*.exe)" if sys.platform == "win32" else "All files (*)"
    path, _ = QFileDialog.getOpenFileName(None, "Choose the Stockfish executable",
                                          str(Path.home()), pattern)
    if not path:
        return None
    settings = storage.load_settings()
    settings["engine"] = path
    storage.save_settings(settings)
    return path


def main() -> None:
    parser = argparse.ArgumentParser(description="ChessLab: a board with analysis")
    parser.add_argument("--engine", help="path to a UCI engine")
    parser.add_argument("--self-test", action="store_true",
                        help="open the main window, close it after a few seconds")
    parser.add_argument("pgn", nargs="?", help="PGN file to open right away")
    args = parser.parse_args()

    try:
        from PySide6.QtCore import QTimer
        from PySide6.QtGui import QIcon
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.exit(
            "PySide6 is missing. Install it with `pip install PySide6` "
            "or your distribution's package (e.g. `pacman -S pyside6`)."
        )

    from chesslab import theme
    from chesslab.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ChessLab")
    app.setApplicationDisplayName("ChessLab")
    app.setWindowIcon(QIcon(str(theme.ASSETS / "icon.png")))

    engine_path = find_engine(args.engine)
    if engine_path is None:
        if args.self_test:
            sys.exit("self-test: no engine found")
        engine_path = ask_engine()
        if engine_path is None:
            sys.exit(0)

    window = MainWindow(engine_path)
    window.show()
    if args.pgn:
        # after show, so a warning about a broken file lands on top of the window
        window.open_pgn_path(args.pgn)
    if args.self_test:
        # build check: the window opened and the engine did not crash on start
        QTimer.singleShot(4000, app.quit)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
