#!/usr/bin/env python3
"""Prints key opening positions — where the same mistakes repeat.

Not about a single game: about a position that occurred many times and
consistently costs the player chances. The analysis comes from the
`positions` cache, filled by `tools/analyse_openings.py`.

Run without the GUI:

    tools/show_weakspots.py --account YOUR_NAME
    tools/show_weakspots.py --account YOUR_NAME --speed blitz --top 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chesslab import games_db, weakspots  # noqa: E402
from chesslab.explorer import _line_text  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--account", required=True, help="account to analyse")
    parser.add_argument("--plies", type=int, default=16,
                        help="number of plies counted as the opening (default 16)")
    parser.add_argument("--min-games", type=int, default=5,
                        help="minimum number of games for a position to count "
                             "as a key position (default 5)")
    parser.add_argument("--top", type=int, default=20,
                        help="number of positions to print (default 20)")
    parser.add_argument("--speed", default="",
                        help="time control: bullet/blitz/rapid/classical/"
                             "correspondence (default any)")
    parser.add_argument("--color", default="",
                        help="only as White or as Black (white/black)")
    return parser.parse_args()


def _fmt_loss(loss: float | None) -> str:
    return f"{loss:5.1f}" if loss is not None else "  ?  "


def main() -> None:
    args = parse_args()

    conn = games_db.connect()
    found = weakspots.spots(
        conn, args.account, plies=args.plies, speed=args.speed,
        color=args.color, min_games=args.min_games,
    )
    conn.close()

    if not found:
        print("No key positions found — too few games or "
              "the --min-games threshold is too high.")
        return

    analysed = sum(1 for spot in found if spot.loss is not None)
    print(f"Key positions: {len(found)}, analysed — {analysed}\n")

    for spot in found[:args.top]:
        color = "White" if spot.white else "Black"
        title = spot.name or "unnamed"

        print(f"cost {spot.cost:6.1f}   {spot.games:3d} games   "
              f"{spot.points * 100:5.1f}% score   {title}")
        print(f"  plays {color}, ply {spot.ply}, "
              f"path: {_line_text(spot.line)}")
        if spot.best_san:
            print(f"  engine best move: {spot.best_san}")

        for move in spot.moves:
            mark = " *" if move.best else "  "
            tag = weakspots.tag_for(move.loss) if move.loss is not None else None
            tag_text = f" [{tag}]" if tag else ""
            print(f"    {move.san:8s}{mark}  {move.games:3d} games   "
                  f"{move.points * 100:5.1f}% score   loss {_fmt_loss(move.loss)}"
                  f"{tag_text}")
        print()


if __name__ == "__main__":
    main()
