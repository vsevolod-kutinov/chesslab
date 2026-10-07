"""Run the engine over the opening part of the games in the database, caching by position.

The point is not to analyse a single game but to find systematic holes in the
opening across the whole database. Opening positions repeat massively, so each
one is cached in the positions table and evaluated only once — a full game
analysis costs hours, an opening analysis with the cache takes minutes.

Run manually from a terminal, without the GUI:

    tools/analyse_openings.py --plies 16 --depth 16
    tools/analyse_openings.py --limit 20 --plies 12 --depth 12 --account YOUR_NAME

A repeat run with the same --plies/--depth evaluates only positions that are
not in the cache yet — on an already analysed sample it finishes instantly.
"""

from __future__ import annotations

import argparse
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from chesslab import games_db  # noqa: E402
from chesslab.engine import OpeningAnalyzer  # noqa: E402

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--plies", type=int, default=16,
                        help="number of plies counted as the opening (default 16)")
    parser.add_argument("--depth", type=int, default=16,
                        help="engine search depth (default 16)")
    parser.add_argument("--threads", type=int, default=8,
                        help="engine threads (default 8)")
    parser.add_argument("--limit", type=int, default=None,
                        help="number of games to take (default all)")
    parser.add_argument("--account", default=None,
                        help="only games of this account (default all)")
    parser.add_argument("--engine", default=shutil.which("stockfish"),
                        help="path to the Stockfish binary (default: found on PATH)")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    if not args.engine:
        raise SystemExit("Stockfish not found: pass --engine /path/to/stockfish")

    conn = games_db.connect()
    total_games = games_db.count(conn, args.account)
    limit = args.limit if args.limit is not None else total_games
    games = games_db.search(conn, account=args.account, limit=limit)
    conn.close()

    if not games:
        print("No games found.")
        return

    print(f"Games: {len(games)} of {total_games}, "
          f"opening {args.plies} plies, depth {args.depth}, "
          f"threads {args.threads}")

    # OpeningAnalyzer emits signals from its own thread — without a Qt event loop
    # nobody would deliver them, so even without windows a QCoreApplication is needed
    app = QCoreApplication([])
    analyzer = OpeningAnalyzer(args.engine, threads=args.threads)
    started = time.monotonic()

    def on_progress(done: int, total: int) -> None:
        if total:
            print(f"\r{done}/{total} new positions", end="", flush=True)

    def on_finished(counted: int) -> None:
        elapsed = time.monotonic() - started
        print(f"\nDone: new positions evaluated — {counted}, "
              f"time — {elapsed:.1f} s")
        app.quit()

    def on_failed(message: str) -> None:
        print(f"\nError: {message}")
        app.quit()

    analyzer.progress.connect(on_progress)
    analyzer.finished.connect(on_finished)
    analyzer.failed.connect(on_failed)

    analyzer.start(games, plies=args.plies, depth=args.depth)
    app.exec()


if __name__ == "__main__":
    main()
