#!/usr/bin/env python3
"""ChessLab entry point."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
from pathlib import Path

DEFAULT_ENGINE = Path.home() / ".local" / "bin" / "stockfish"


def find_engine(explicit: str | None) -> str:
    if explicit:
        return explicit
    if DEFAULT_ENGINE.is_file() and os.access(DEFAULT_ENGINE, os.X_OK):
        return str(DEFAULT_ENGINE)
    found = shutil.which("stockfish")
    if found:
        return found
    sys.exit(
        f"Stockfish not found. Put it at {DEFAULT_ENGINE}, "
        f"add it to PATH or pass a path: main.py --engine /path/to/engine"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="ChessLab - board with analysis")
    parser.add_argument("--engine", help="path to a UCI engine")
    parser.add_argument("pgn", nargs="?", help="PGN file to open right away")
    args = parser.parse_args()

    engine_path = find_engine(args.engine)

    try:
        from PySide6.QtWidgets import QApplication
    except ImportError:
        sys.exit(
            "PySide6 is missing. Install the system package:\n"
            "    pkexec pacman -S --noconfirm pyside6"
        )

    from chesslab.window import MainWindow

    app = QApplication(sys.argv)
    app.setApplicationName("ChessLab")
    app.setApplicationDisplayName("ChessLab")

    window = MainWindow(engine_path)
    window.show()
    if args.pgn:
        # after show, so the warning about a broken file lands on top of the window
        window.open_pgn_path(args.pgn)
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
