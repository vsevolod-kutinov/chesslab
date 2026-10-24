"""The user's own repertoire, for advice that fits what he actually plays.

Taken from his games in the ChessLab database (all accounts plus over-the-board
games) and from the ★ moves of the repertoire table.

    python -m chesslab.prep mine
"""

from __future__ import annotations

import argparse
import collections
import sqlite3

from .. import games_db

PLIES = 6


def _lines(conn: sqlite3.Connection, white: bool, where: str = "") -> list[str]:
    owner = games_db._OWNER_IS_WHITE if white else f"NOT {games_db._OWNER_IS_WHITE}"
    rows = conn.execute(
        f"SELECT moves, winner FROM games WHERE moves != '' AND {owner} {where}"
    ).fetchall()
    stats: dict[str, list[float]] = collections.defaultdict(list)
    own = "white" if white else "black"
    for row in rows:
        line = " ".join((row["moves"] or "").split()[:PLIES])
        stats[line].append(1.0 if row["winner"] == own else 0.5 if not row["winner"] else 0.0)
    top = sorted(stats.items(), key=lambda item: -len(item[1]))[:8]
    return [f"    {line}  x{len(points)}  {round(100 * sum(points) / len(points))}%"
            for line, points in top]


def summary(conn: sqlite3.Connection) -> str:
    total = conn.execute("SELECT COUNT(*) FROM games WHERE moves != ''").fetchone()[0]
    if not total:
        return "My repertoire: no own games in ChessLab yet."
    out = [f"My repertoire ({total} games, first {PLIES} plies, score):"]
    for colour, white in (("White", True), ("Black", False)):
        out.append(f"  As {colour}, all games:")
        out += _lines(conn, white)
        otb = _lines(conn, white, "AND service = 'otb'")
        if otb:
            out.append(f"  As {colour}, over the board:")
            out += otb
    starred = conn.execute(
        "SELECT san, note FROM repertoire WHERE san IS NOT NULL ORDER BY updated_at DESC LIMIT 30"
    ).fetchall()
    if starred:
        out.append("  ★ marked moves: " + ", ".join(
            row["san"] + (f" ({row['note']})" if row["note"] else "") for row in starred))
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(description="Own repertoire from the ChessLab database.").parse_args(argv)
    print(summary(games_db.connect()))
    return 0
