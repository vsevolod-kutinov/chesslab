"""Builds the opening table from the TSV files of the lichess-org/chess-openings project.

Run manually when you want to refresh the table:

    python tools/build_eco.py path/to/a.tsv b.tsv ...

Download the sources:
    https://raw.githubusercontent.com/lichess-org/chess-openings/master/a.tsv
    (and likewise b, c, d, e)

Output is `chesslab/data/eco.tsv`: code, name and moves in UCI. UCI rather than
SAN so that no notation parsing is needed on load: `Move.from_uci` is noticeably
cheaper than `parse_san`, and the table is loaded each time the opening window starts.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import chess
import chess.pgn

OUT = Path(__file__).resolve().parent.parent / "chesslab" / "data" / "eco.tsv"


def convert(paths: list[Path]) -> list[tuple[str, str, str]]:
    rows = []
    for path in paths:
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines()):
            if number == 0 or not line.strip():
                continue  # first line is the header
            eco, name, pgn = line.split("\t")[:3]
            game = chess.pgn.read_game(io.StringIO(pgn))
            if game is None:
                print(f"skipping {eco} {name}: cannot parse \"{pgn}\"")
                continue
            board = game.board()
            ucis = []
            for move in game.mainline_moves():
                ucis.append(move.uci())
                board.push(move)
            if ucis:
                rows.append((eco, name, " ".join(ucis)))
    rows.sort(key=lambda r: (len(r[2].split()), r[0], r[1]))
    return rows


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]]
    if not paths:
        print(__doc__)
        raise SystemExit(2)

    rows = convert(paths)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with OUT.open("w", encoding="utf-8") as out:
        out.write("eco\tname\tuci\n")
        for row in rows:
            out.write("\t".join(row) + "\n")
    print(f"{len(rows)} openings written to {OUT}")


if __name__ == "__main__":
    main()
