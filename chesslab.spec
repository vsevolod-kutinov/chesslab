# PyInstaller build: `pyinstaller chesslab.spec` -> dist/ChessLab/
# Put the engine at engine/stockfish(.exe) before building to bundle it.
import sys
from pathlib import Path

engine = Path("engine") / ("stockfish.exe" if sys.platform == "win32" else "stockfish")

a = Analysis(
    ["main.py"],
    datas=[("chesslab/assets", "chesslab/assets"), ("chesslab/data", "chesslab/data")],
    binaries=[(str(engine), "engine")] if engine.is_file() else [],
    # Qt modules the app never touches: keeps the folder smaller
    excludes=["PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
              "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml",
              "PySide6.QtCharts", "PySide6.QtDataVisualization", "tkinter"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="ChessLab",
    console=False,
    icon="chesslab/assets/icon.ico",
    # name, version and description in the file properties (made by tools/version_info.py)
    version="build/version_info.txt" if Path("build/version_info.txt").is_file() else None,
)
coll = COLLECT(exe, a.binaries, a.datas, name="ChessLab")
