"""Own tournaments: storage, manual game entry and report.

Tournament games live in the same `games` table as Lichess games.
The `account` field holds the player name from the tournament, so all the colour and
result logic written for online games works here unchanged.

Games are entered by hand from a paper scoresheet: tournaments have no PGN.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime

import chess
import chess.pgn

from . import eco, games_db

FIELDS = ("name", "player", "place", "started", "time_control", "notes",
          "official", "fide_event", "country", "finished", "speed",
          "standing", "players", "cr_link", "cr_event", "cr_snr")

RESULTS = {1.0: "1-0", 0.5: "1/2-1/2", 0.0: "0-1"}  # from White's point of view

# adjustment to the average opponent rating by score percentage (FIDE table)
_PERFORMANCE_DP = [
    (1.00, 800), (0.99, 677), (0.98, 589), (0.97, 538), (0.96, 501), (0.95, 470),
    (0.94, 444), (0.93, 422), (0.92, 401), (0.91, 383), (0.90, 366), (0.89, 351),
    (0.88, 336), (0.87, 322), (0.86, 309), (0.85, 296), (0.84, 284), (0.83, 273),
    (0.82, 262), (0.81, 251), (0.80, 240), (0.79, 230), (0.78, 220), (0.77, 211),
    (0.76, 202), (0.75, 193), (0.74, 184), (0.73, 175), (0.72, 166), (0.71, 158),
    (0.70, 149), (0.69, 141), (0.68, 133), (0.67, 125), (0.66, 117), (0.65, 110),
    (0.64, 102), (0.63, 95), (0.62, 87), (0.61, 80), (0.60, 72), (0.59, 65),
    (0.58, 57), (0.57, 50), (0.56, 43), (0.55, 36), (0.54, 29), (0.53, 21),
    (0.52, 14), (0.51, 7), (0.50, 0),
]

# same table, but keyed by whole percents: fractions cannot be compared for equality
_DP = {round(threshold * 100): dp for threshold, dp in _PERFORMANCE_DP}


# --- tournaments --------------------------------------------------------

def create(conn: sqlite3.Connection, **fields) -> int:
    pairs = [(k, v) for k, v in fields.items() if k in FIELDS]
    columns = ", ".join(k for k, _ in pairs)
    marks = ", ".join("?" for _ in pairs)
    cursor = conn.execute(
        f"INSERT INTO tournaments ({columns}) VALUES ({marks})",
        [v for _, v in pairs],
    )
    conn.commit()
    return cursor.lastrowid


def update(conn: sqlite3.Connection, tournament_id: int, **fields) -> None:
    pairs = [(k, v) for k, v in fields.items() if k in FIELDS]
    if not pairs:
        return
    assignments = ", ".join(f"{k} = ?" for k, _ in pairs)
    conn.execute(
        f"UPDATE tournaments SET {assignments} WHERE id = ?",
        [v for _, v in pairs] + [tournament_id],
    )
    conn.commit()


def delete(conn: sqlite3.Connection, tournament_id: int) -> None:
    """A tournament together with its games."""
    conn.execute(
        "DELETE FROM analysis WHERE game_id IN"
        " (SELECT id FROM games WHERE tournament_id = ?)", (tournament_id,))
    conn.execute("DELETE FROM games WHERE tournament_id = ?", (tournament_id,))
    conn.execute("DELETE FROM tournaments WHERE id = ?", (tournament_id,))
    conn.commit()


def standing_text(row: sqlite3.Row, separator: str = " / ") -> str:
    """Final placing: "12 / 80". Without the player count, just "#12"."""
    standing, players = row["standing"], row["players"]
    if not standing:
        return ""
        return f"{standing}{separator}{players}" if players else f"#{standing}"


def place_text(row: sqlite3.Row) -> str:
    """City and country on one line: "Prague, Czechia"."""
    from .fide import country_name  # local import: fide pulls in PySide6

    parts = [row["place"] or "", country_name(row["country"] or "")]
    return ", ".join(part for part in parts if part)


def all_tournaments(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return list(conn.execute("SELECT * FROM tournaments ORDER BY started DESC, id DESC"))


def get(conn: sqlite3.Connection, tournament_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM tournaments WHERE id = ?", (tournament_id,)
    ).fetchone()


def games(conn: sqlite3.Connection, tournament_id: int) -> list[sqlite3.Row]:
    return list(conn.execute(
        "SELECT * FROM games WHERE tournament_id = ? ORDER BY played_at, id",
        (tournament_id,),
    ))


# --- games ---------------------------------------------------------------

class GameError(Exception):
    pass


def _stamp(date: str, order: int) -> int:
    """Game date in milliseconds. The round number separates games on the same day."""
    for pattern in ("%Y-%m-%d", "%Y.%m.%d", "%d.%m.%Y"):
        try:
            moment = datetime.strptime(date.strip(), pattern)
            break
        except ValueError:
            continue
    else:
        moment = datetime.now()
    return int(moment.timestamp() * 1000) + order * 3_600_000


def name_opening(conn: sqlite3.Connection, board: chess.Board,
                 moves: str) -> tuple[str, str]:
    """Opening name for a game: the reference first, then our own games."""
    found = eco.book().name_at(board)
    if found is not None:
        return found.eco, found.name
    return guess_opening(conn, moves)


def guess_opening(conn: sqlite3.Connection, moves: str) -> tuple[str, str]:
    """Opening by matching the start of the game against ones already known from Lichess.

    Fallback for when the ECO reference does not know the position: there are hundreds of
    our own labelled games, and the start usually matches one of them.
    """
    tokens = moves.split()
    for length in range(min(14, len(tokens)), 3, -1):
        prefix = " ".join(tokens[:length])
        row = conn.execute(
            "SELECT eco, opening, COUNT(*) AS n FROM games"
            " WHERE opening != '' AND moves LIKE ?"
            " GROUP BY eco, opening ORDER BY n DESC LIMIT 1",
            (prefix + "%",),
        ).fetchone()
        if row:
            return row["eco"] or "", row["opening"] or ""
    return "", ""


def _pgn(tournament: sqlite3.Row, headers: dict, sans: list[str]) -> str:
    """Build a PGN so the game opens on the board like the others."""
    game = chess.pgn.Game()
    game.headers.update({k: v for k, v in headers.items() if v})
    game.headers["Event"] = tournament["name"]
    site = place_text(tournament)
    if site:
        game.headers["Site"] = site
    if tournament["time_control"]:
        game.headers["TimeControl"] = tournament["time_control"]

    node = game
    board = chess.Board()
    for san in sans:
        move = board.parse_san(san)
        node = node.add_variation(move)
        board.push(move)
    return str(game)


def save_game(conn: sqlite3.Connection, tournament: sqlite3.Row, data: dict,
              game_id: str | None = None) -> str:
    """Save a manually entered game. Moves may be omitted.

    `data` is expected to hold: round, me_white, opponent, opponent_elo, points, date, sans.
    Returns the game id: the new one, or the same one if this is an edit.
    """
    opponent = (data.get("opponent") or "").strip() or "opponent"
    me_white = bool(data.get("me_white"))
    points = float(data.get("points", 0.0))
    if points not in RESULTS:
        raise GameError("The result must be 1, ½ or 0.")

    sans = list(data.get("sans") or [])
    board = chess.Board()
    for san in sans:  # validate before saving: a half-valid game is useless
        try:
            board.push_san(san)
        except ValueError as exc:
            raise GameError(f"Move '{san}' is not legal in this position.") from exc

    moves = " ".join(sans)
    code, opening = name_opening(conn, board, moves) if moves else ("", "")
    result = RESULTS[points if me_white else 1.0 - points]
    round_no = str(data.get("round") or "")

    row = {
        "id": game_id or f"t{tournament['id']}-{uuid.uuid4().hex[:10]}",
        "account": tournament["player"],
        "service": "otb",
        "played_at": _stamp(data.get("date") or tournament["started"] or "",
                            int(round_no) if round_no.isdigit() else 0),
        "white": tournament["player"] if me_white else opponent,
        "black": opponent if me_white else tournament["player"],
        "white_elo": None if me_white else data.get("opponent_elo"),
        "black_elo": data.get("opponent_elo") if me_white else None,
        "winner": {"1-0": "white", "0-1": "black", "1/2-1/2": ""}[result],
        "status": "tournament",
        "speed": tournament["speed"] or "classical",
        "rated": 1 if tournament["official"] else 0,
        "eco": code,
        "opening": opening,
        "moves": moves,
        "pgn": "",
        "tournament_id": tournament["id"],
        "round": round_no,
    }
    row["pgn"] = _pgn(tournament, {
        "White": row["white"], "Black": row["black"], "Result": result,
        "Round": round_no, "Date": (data.get("date") or tournament["started"] or
                                    "????.??.??").replace("-", "."),
        "WhiteElo": str(row["white_elo"] or ""),
        "BlackElo": str(row["black_elo"] or ""),
        "ECO": code, "Opening": opening,
    }, sans)

    games_db.store(conn, [row])
    return row["id"]


def delete_game(conn: sqlite3.Connection, game_id: str) -> None:
    conn.execute("DELETE FROM analysis WHERE game_id = ?", (game_id,))
    conn.execute("DELETE FROM games WHERE id = ?", (game_id,))
    conn.commit()


# --- report -------------------------------------------------------------

def performance(average: float, score_fraction: float) -> int:
    """Performance rating from the FIDE table. Public: `crosstable` uses it too.

    Score percentage is ROUNDED to hundredths, not truncated: the FIDE table
    has a 0.01 step, and 5 of 9 is 0.5556, i.e. row 0.56, not 0.55.
    An earlier top-down "first matching row" search lowered the performance
    by about ten points. Checked against chess-results: 5 of 9 with an average
    1785 gives 1828, as on the official player card.
    """
    percent = round(score_fraction * 100)
    if percent >= 50:
        return round(average + _DP[percent])
    return round(average - _DP[100 - percent])


def report(conn: sqlite3.Connection, tournament_id: int) -> dict:
    """Tournament summary: points, split by colour, performance, openings."""
    tournament = get(conn, tournament_id)
    if tournament is None:
        raise ValueError("Tournament not found")

    player = tournament["player"].lower()
    rows = games(conn, tournament_id)

    summary = {
        "tournament": tournament,
        "games": len(rows),
        "wins": 0, "draws": 0, "losses": 0,
        "points": 0.0,
        "white": {"games": 0, "points": 0.0},
        "black": {"games": 0, "points": 0.0},
        "opponents": [],
        "openings": {},
        "rows": rows,
    }

    ratings: list[int] = []
    for row in rows:
        as_white = (row["white"] or "").lower() == player
        side = summary["white"] if as_white else summary["black"]
        side["games"] += 1

        if not row["winner"]:
            points = 0.5
            summary["draws"] += 1
        elif (row["winner"] == "white") == as_white:
            points = 1.0
            summary["wins"] += 1
        else:
            points = 0.0
            summary["losses"] += 1

        summary["points"] += points
        side["points"] += points

        opponent = row["black"] if as_white else row["white"]
        opponent_elo = row["black_elo"] if as_white else row["white_elo"]
        summary["opponents"].append((opponent, opponent_elo, points))
        if opponent_elo:
            ratings.append(opponent_elo)

        key = f'{row["eco"]} {row["opening"]}'.strip() or "no opening"
        entry = summary["openings"].setdefault(key, {"games": 0, "points": 0.0})
        entry["games"] += 1
        entry["points"] += points

    played = summary["games"]
    summary["score_pct"] = round(summary["points"] / played * 100, 1) if played else 0.0
    summary["avg_opponent"] = round(sum(ratings) / len(ratings)) if ratings else None
    summary["performance"] = (
        performance(sum(ratings) / len(ratings), summary["points"] / played)
        if ratings and played else None
    )
    return summary
