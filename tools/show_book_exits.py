#!/usr/bin/env python3
"""Prints how far games follow theory and who deviates first.

Not about a single position: about the moment of leaving the book in each game —
whose move it was and what evaluation the player was left with. The analysis
comes from the `positions` cache, filled by `tools/analyse_openings.py`.

Run without the GUI:

    tools/show_book_exits.py --account YOUR_NAME
    tools/show_book_exits.py --account YOUR_NAME --speed blitz --min-games 5
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chesslab import games_db, weakspots  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--account", required=True, help="account to analyse")
    parser.add_argument("--plies", type=int, default=16,
                        help="number of plies to check for being in book "
                             "(default 16)")
    parser.add_argument("--min-games", type=int, default=3,
                        help="minimum number of games for an opening to appear in "
                             "the table (default 3)")
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
    exits = weakspots.book_exits(
        conn, args.account, plies=args.plies, speed=args.speed,
        color=args.color,
    )
    stats = weakspots.exit_stats(
        conn, args.account, plies=args.plies, speed=args.speed,
        color=args.color, min_games=args.min_games,
    )
    conn.close()

    if not exits:
        print("No game left the book within --plies — "
              "either there are too few games or they all stay in book longer than that.")
        return

    print(f"Games that left the book: {len(exits)}\n")

    print("DISTRIBUTION BY PLY OF EXIT")
    depths = weakspots.exit_depths(exits)
    for ply in sorted(depths):
        count = depths[ply]
        move_no = ply // 2 + 1
        side = "White" if ply % 2 == 0 else "Black"
        bar = "#" * count
        print(f"  ply {ply:2d} (move {move_no}, {side}): "
              f"{count:3d}  {bar}")
    print()

    if not stats:
        print("No openings with at least --min-games games.")
        return

    print("EXITS BY OPENING (frequent first)")
    for stat in stats:
        title = f"{stat.eco} {stat.name}".strip() if stat.name else "unnamed"
        print(f"{stat.games:3d} games   average exit at ply "
              f"{stat.avg_ply:4.1f}   player exits {stat.mine:3d}   "
              f"{stat.points * 100:5.1f}% score   "
              f"player loss at exit {_fmt_loss(stat.loss)}   {title}")


if __name__ == "__main__":
    main()
