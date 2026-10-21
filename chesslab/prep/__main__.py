"""`python -m chesslab.prep <command> …` - the tools the prep assistant may run."""

from __future__ import annotations

import importlib
import sys

# command -> module with main(argv); modules load lazily, a broken one does not block the rest
COMMANDS = {
    "games": "games",
    "lineup": "lineup",
    "card": "card",
}


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        print("usage: python -m chesslab.prep {" + ",".join(COMMANDS) + "} …")
        return 2
    module = importlib.import_module(f"chesslab.prep.{COMMANDS[argv[0]]}")
    return module.main(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
