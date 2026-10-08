"""Write build/version_info.txt — Windows file properties for the exe.

Taken from chesslab.__version__ so the properties match the release.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
version = re.search(r'__version__ = "([^"]+)"',
                    (ROOT / "chesslab" / "__init__.py").read_text()).group(1)
parts = [int(p) for p in version.split(".")] + [0] * 4
numbers = ", ".join(str(n) for n in parts[:4])

TEMPLATE = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({numbers}), prodvers=({numbers})),
  kids=[
    StringFileInfo([StringTable('040904B0', [
      StringStruct('CompanyName', 'ChessLab'),
      StringStruct('FileDescription', 'ChessLab - chess board with Stockfish analysis'),
      StringStruct('FileVersion', '{version}'),
      StringStruct('InternalName', 'ChessLab'),
      StringStruct('LegalCopyright', 'GPL-3.0-or-later'),
      StringStruct('OriginalFilename', 'ChessLab.exe'),
      StringStruct('ProductName', 'ChessLab'),
      StringStruct('ProductVersion', '{version}')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])
  ]
)
"""

out = ROOT / "build" / "version_info.txt"
out.parent.mkdir(exist_ok=True)
out.write_text(TEMPLATE, encoding="utf-8")
print("wrote", out, "for", version)
