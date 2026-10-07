"""Local game database. SQLite, the file sits next to the rest of the app data."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta

from . import storage

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id         TEXT PRIMARY KEY,
    account    TEXT NOT NULL,
    service    TEXT,
    played_at  INTEGER NOT NULL,
    white      TEXT,
    black      TEXT,
    white_elo  INTEGER,
    black_elo  INTEGER,
    winner     TEXT,
    status     TEXT,
    speed      TEXT,
    rated      INTEGER,
    eco        TEXT,
    opening    TEXT,
    moves      TEXT,
    pgn        TEXT
);
CREATE INDEX IF NOT EXISTS games_by_date ON games(account, played_at DESC);

CREATE TABLE IF NOT EXISTS analysis (
    game_id    TEXT NOT NULL,
    ply        INTEGER NOT NULL,
    cp         INTEGER,
    mate       INTEGER,
    best_uci   TEXT,
    played_uci TEXT,
    loss       REAL,
    tag        TEXT,
    PRIMARY KEY (game_id, ply)
);

CREATE TABLE IF NOT EXISTS tournaments (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    name         TEXT NOT NULL,
    player       TEXT NOT NULL,
    place        TEXT,
    started      TEXT,
    time_control TEXT,
    notes        TEXT,
    official     INTEGER DEFAULT 0,
    fide_event   TEXT,
    country      TEXT,
    finished     TEXT,
    speed        TEXT,
    standing     INTEGER,
    players      INTEGER
);

CREATE TABLE IF NOT EXISTS positions (
    epd      TEXT PRIMARY KEY,
    depth    INTEGER NOT NULL,
    cp       INTEGER,
    mate     INTEGER,
    best_uci TEXT,
    checked_at INTEGER NOT NULL
);
"""

# columns added after the initial schema
_LATE_COLUMNS = {
    "games": {
        "tournament_id": "INTEGER",
        "round": "TEXT",
        "service": "TEXT",  # lichess, chess.com or otb (a game from a scoresheet)
    },
    "tournaments": {
        "official": "INTEGER DEFAULT 0",  # 1 = FIDE-rated tournament
        "fide_event": "TEXT",             # tournament id on ratings.fide.com
        "country": "TEXT",                # three-letter federation code
        "finished": "TEXT",
        "speed": "TEXT",
        "standing": "INTEGER",  # own place in the final standings
        "players": "INTEGER",   # total number of participants
        "cr_link": "TEXT",      # link to the tournament on chess-results
        "cr_event": "TEXT",     # its id there (tnr…)
        "cr_snr": "INTEGER",    # own starting rank number in that listing
    },
}


def _migrate(conn: sqlite3.Connection) -> None:
    """The database may predate tournaments — add the missing columns."""
    added = set()
    for table, columns in _LATE_COLUMNS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, kind in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {kind}")
                added.add((table, column))

    if ("games", "service") in added:
        # everything already in the database before the second service came from Lichess,
        # except games entered by hand from a tournament
        conn.execute(
            "UPDATE games SET service = "
            "CASE WHEN tournament_id IS NOT NULL THEN 'otb' ELSE 'lichess' END"
        )
    conn.commit()


def db_path():
      # computed every time: tests override storage.DATA_DIR
    return storage.DATA_DIR / "games.sqlite3"


def connect() -> sqlite3.Connection:
    storage.DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path())
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    _migrate(conn)
    return conn


def row_from_api(account: str, game: dict) -> dict:
    """Flat record built from what Lichess returns."""
    players = game.get("players") or {}

    def side(color: str, field: str):
        entry = players.get(color) or {}
        if field == "name":
            return (entry.get("user") or {}).get("name") or "Anonymous"
        return entry.get("rating")

    opening = game.get("opening") or {}
    return {
        "id": game["id"],
        "account": account,
        "service": "lichess",
        "played_at": game.get("createdAt") or 0,
        "white": side("white", "name"),
        "black": side("black", "name"),
        "white_elo": side("white", "rating"),
        "black_elo": side("black", "rating"),
        "winner": game.get("winner") or "",  # empty = draw
        "status": game.get("status") or "",
        "speed": game.get("speed") or "",
        "rated": 1 if game.get("rated") else 0,
        "eco": opening.get("eco") or "",
        "opening": opening.get("name") or "",
        "moves": game.get("moves") or "",
        "pgn": game.get("pgn") or "",
        "tournament_id": None,
        "round": "",
    }


COLUMNS = ("id", "account", "service", "played_at", "white", "black", "white_elo", "black_elo",
           "winner", "status", "speed", "rated", "eco", "opening", "moves", "pgn",
           "tournament_id", "round")

_INSERT = (
    f"INSERT OR REPLACE INTO games ({','.join(COLUMNS)}) "
    f"VALUES ({','.join('?' * len(COLUMNS))})"
)


def store(conn: sqlite3.Connection, rows: list[dict]) -> int:
    if not rows:
        return 0
    conn.executemany(_INSERT, [tuple(r[c] for c in COLUMNS) for r in rows])
    conn.commit()
    return len(rows)


_ANALYSIS_COLUMNS = ("game_id", "ply", "cp", "mate", "best_uci", "played_uci",
                     "loss", "tag")


def save_analysis(conn: sqlite3.Connection, game_id: str, rows: list[dict]) -> None:
    conn.execute("DELETE FROM analysis WHERE game_id = ?", (game_id,))
    conn.executemany(
        f"INSERT INTO analysis ({','.join(_ANALYSIS_COLUMNS)})"
        f" VALUES ({','.join('?' * len(_ANALYSIS_COLUMNS))})",
        [tuple(r.get(c) for c in _ANALYSIS_COLUMNS) for r in rows],
    )
    conn.commit()


def load_analysis(conn: sqlite3.Connection, game_id: str) -> dict[int, sqlite3.Row]:
    """Analysis by ply number. Key 0 is the evaluation of the starting position."""
    return {
        row["ply"]: row
        for row in conn.execute(
            "SELECT * FROM analysis WHERE game_id = ? ORDER BY ply", (game_id,)
        )
    }


_SQLITE_CHUNK = 900  # safely under the SQLite limit on query parameters


def position_evals(conn: sqlite3.Connection, epds: list[str],
                   min_depth: int = 0) -> dict[str, sqlite3.Row]:
    """Already analysed positions by EPD, at least min_depth deep.

    Queried in batches rather than one by one: the caller may pass thousands
    of positions at once. Split into chunks to stay under the SQLite parameter limit.
    """
    out: dict[str, sqlite3.Row] = {}
    unique = list(dict.fromkeys(epds))
    for start in range(0, len(unique), _SQLITE_CHUNK):
        chunk = unique[start:start + _SQLITE_CHUNK]
        placeholders = ",".join("?" * len(chunk))
        rows = conn.execute(
            f"SELECT * FROM positions WHERE epd IN ({placeholders}) AND depth >= ?",
            (*chunk, min_depth),
        )
        for row in rows:
            out[row["epd"]] = row
    return out


def save_positions(conn: sqlite3.Connection, rows: list[dict]) -> None:
    """In bulk, in one transaction. Overwrites only with a deeper analysis.

    The position may already have come from another game at a greater depth —
    a shallower analysis must not overwrite it.
    """
    if not rows:
        return
    conn.executemany(
        """
        INSERT INTO positions (epd, depth, cp, mate, best_uci, checked_at)
        VALUES (:epd, :depth, :cp, :mate, :best_uci, :checked_at)
        ON CONFLICT(epd) DO UPDATE SET
            depth = excluded.depth,
            cp = excluded.cp,
            mate = excluded.mate,
            best_uci = excluded.best_uci,
            checked_at = excluded.checked_at
        WHERE excluded.depth > positions.depth
        """,
        rows,
    )
    conn.commit()


def latest_played_at(conn: sqlite3.Connection, account: str) -> int | None:
    row = conn.execute(
        "SELECT MAX(played_at) AS m FROM games WHERE account = ?", (account,)
    ).fetchone()
    return row["m"] if row and row["m"] else None


def count(conn: sqlite3.Connection, account: str | None = None) -> int:
    if account:
        sql, args = "SELECT COUNT(*) AS c FROM games WHERE account = ?", (account,)
    else:
        sql, args = "SELECT COUNT(*) AS c FROM games", ()
    return conn.execute(sql, args).fetchone()["c"]


def find_by_link(conn: sqlite3.Connection, needle: str) -> sqlite3.Row | None:
    """Game by its id: first directly by id, then by the link inside the PGN.

    Lets an own game opened by link pick up the saved analysis
    instead of being recomputed.
    """
    row = conn.execute("SELECT * FROM games WHERE id = ?", (needle,)).fetchone()
    if row is not None:
        return row
    return conn.execute(
        "SELECT * FROM games WHERE pgn LIKE ? ORDER BY played_at DESC LIMIT 1",
        (f"%{needle}%",),
    ).fetchone()


def counts_by_service(conn: sqlite3.Connection,
                      account: str | None = None) -> dict[str, int]:
    """How many games per site. An empty key is legacy data without a label."""
    sql = "SELECT COALESCE(service, '') AS s, COUNT(*) AS c FROM games"
    args: tuple = ()
    if account:
        sql += " WHERE account = ?"
        args = (account,)
    sql += " GROUP BY s ORDER BY c DESC"
    return {row["s"]: row["c"] for row in conn.execute(sql, args)}


def accounts(conn: sqlite3.Connection) -> list[str]:
    return [r["account"] for r in conn.execute(
        "SELECT DISTINCT account FROM games ORDER BY account"
    )]


# what openings are grouped by
GROUP_VARIATION = "variation"
GROUP_FAMILY = "family"
GROUP_ECO = "eco"

# SQLite has no split, so the family is cut at the first colon:
# "Sicilian Defense: Closed" -> "Sicilian Defense"
_FAMILY_SQL = ("CASE WHEN instr(opening, ':') > 0 "
               "THEN substr(opening, 1, instr(opening, ':') - 1) ELSE opening END")

_GROUP_SQL = {
    GROUP_VARIATION: "opening",
    GROUP_FAMILY: _FAMILY_SQL,
    GROUP_ECO: "eco",
}

_OWNER_IS_WHITE = "LOWER(white) = LOWER(account)"


def opening_stats(conn: sqlite3.Connection, account: str | None = None,
                  group: str = GROUP_VARIATION, color: str = "", speed: str = "",
                  min_games: int = 3) -> list[dict]:
    """Opening summary from the account owner's point of view. Without account — all accounts.

    Score is computed as in chess: win 1, draw 0.5. Percentages are of the maximum.
    The own side is determined per row, by the account column, so games
    of different accounts add up without mixing up colors.
    """
    group_sql = _GROUP_SQL.get(group, "opening")

    where = ["opening != ''"]
    args: list = []
    if account:
        where.append("account = ?")
        args.append(account)

    if color == "white":
        where.append(_OWNER_IS_WHITE)
    elif color == "black":
        where.append(f"NOT {_OWNER_IS_WHITE}")
    if speed:
        where.append("speed = ?")
        args.append(speed)

    sql = f"""
        SELECT {group_sql} AS grp,
               MIN(eco) AS eco,
               COUNT(*) AS games,
               SUM(CASE WHEN winner = '' THEN 1 ELSE 0 END) AS draws,
               SUM(CASE WHEN ({_OWNER_IS_WHITE} AND winner = 'white')
                          OR (NOT {_OWNER_IS_WHITE} AND winner = 'black')
                        THEN 1 ELSE 0 END) AS wins
        FROM games
        WHERE {' AND '.join(where)}
        GROUP BY grp
        HAVING games >= ?
    """
    args.append(min_games)

    out = []
    for row in conn.execute(sql, args):
        games, wins, draws = row["games"], row["wins"], row["draws"]
        points = wins + draws / 2
        out.append({
            "key": row["grp"],
            "eco": row["eco"] or "",
            "games": games,
            "wins": wins,
            "draws": draws,
            "losses": games - wins - draws,
            "score": round(points / games * 100, 1) if games else 0.0,
        })
    return out


def rating_series(conn: sqlite3.Connection, account: str,
                  speed: str = "") -> list[tuple[int, int]]:
    """Account owner's rating from games: pairs (when, rating).

    Built from our own database rather than Lichess's rating-history endpoint: it
    returns an empty response for this account, while games with ratings are already stored here.
    """
    where = ["account = ?", "played_at > 0"]
    args: list = [account]
    if speed:
        where.append("speed = ?")
        args.append(speed)

    sql = f"""
        SELECT played_at,
               CASE WHEN {_OWNER_IS_WHITE} THEN white_elo ELSE black_elo END AS rating
        FROM games
        WHERE {' AND '.join(where)}
        ORDER BY played_at
    """
    return [(row["played_at"], row["rating"])
            for row in conn.execute(sql, args) if row["rating"]]


# chunks the history is split into in the rating report
GROUP_DAY = "day"
GROUP_WEEK = "week"
GROUP_MONTH = "month"
GROUP_YEAR = "year"


def period_key(when: datetime, group: str) -> str:
    """Bucket key. A week is labelled by the date of its Monday."""
    if group == GROUP_DAY:
        return when.strftime("%Y-%m-%d")
    if group == GROUP_WEEK:
        monday = when - timedelta(days=when.weekday())
        return "W" + monday.strftime("%Y-%m-%d")
    if group == GROUP_YEAR:
        return when.strftime("%Y")
    return when.strftime("%Y-%m")


def progress(conn: sqlite3.Connection, account: str, speed: str = "",
             group: str = GROUP_MONTH, since: float = 0) -> list[dict]:
    """By day, week, month or year: games, score and rating.

    A bucket's rating is the one after its last game. Grouping is done in
    Python, not SQL: time in the database is UTC milliseconds, while the day and week
    should follow the local time zone.
    """
    where = ["account = ?", "played_at > 0"]
    args: list = [account]
    if speed:
        where.append("speed = ?")
        args.append(speed)
    if since:
        where.append("played_at >= ?")
        args.append(since)

    sql = f"""
        SELECT played_at, winner,
               {_OWNER_IS_WHITE} AS mine_white,
               CASE WHEN {_OWNER_IS_WHITE} THEN white_elo ELSE black_elo END AS rating
        FROM games
        WHERE {' AND '.join(where)}
        ORDER BY played_at
    """

    chunks: dict[str, dict] = {}
    for row in conn.execute(sql, args):
        key = period_key(datetime.fromtimestamp(row["played_at"] / 1000), group)
        month = chunks.setdefault(key, {
            "period": key, "games": 0, "wins": 0, "draws": 0, "losses": 0,
            "rating": None,
        })
        month["games"] += 1
        if not row["winner"]:
            month["draws"] += 1
        elif (row["winner"] == "white") == bool(row["mine_white"]):
            month["wins"] += 1
        else:
            month["losses"] += 1
        if row["rating"]:
            month["rating"] = row["rating"]

    out = sorted(chunks.values(), key=lambda m: m["period"])
    previous = None
    for month in out:
        month["score"] = round(
            (month["wins"] + month["draws"] / 2) / month["games"] * 100, 1
        )
        month["delta"] = (month["rating"] - previous
                          if month["rating"] and previous else None)
        if month["rating"]:
            previous = month["rating"]
    return out


def overall(conn: sqlite3.Connection, account: str | None = None) -> dict:
    """Summary of an account's games, or of all accounts when none is given."""
    where = "WHERE account = ?" if account else ""
    args = (account,) if account else ()
    row = conn.execute(
        f"""SELECT COUNT(*) AS games,
                   SUM(CASE WHEN winner = '' THEN 1 ELSE 0 END) AS draws,
                   SUM(CASE WHEN ({_OWNER_IS_WHITE} AND winner = 'white')
                              OR (NOT {_OWNER_IS_WHITE} AND winner = 'black')
                            THEN 1 ELSE 0 END) AS wins
            FROM games {where}""",
        args,
    ).fetchone()

    games = row["games"] or 0
    wins, draws = row["wins"] or 0, row["draws"] or 0
    return {
        "games": games,
        "wins": wins,
        "draws": draws,
        "losses": games - wins - draws,
        "score": round((wins + draws / 2) / games * 100, 1) if games else 0.0,
    }


def search(conn: sqlite3.Connection, account: str | None = None, text: str = "",
           speed: str = "", color: str = "", result: str = "", service: str = "",
           limit: int = 2000) -> list[sqlite3.Row]:
    """color/result are relative to the account owner.

    Without account — games of all accounts mixed, by time.
    """
    where, args = [], []

    if account:
        where.append("account = ?")
        args.append(account)

    if service:
        where.append("service = ?")
        args.append(service)

    if text:
        where.append("(white LIKE ? OR black LIKE ? OR opening LIKE ? OR eco LIKE ?)")
        args += [f"%{text}%"] * 4

    if speed:
        where.append("speed = ?")
        args.append(speed)

    owner_is_white = _OWNER_IS_WHITE
    if color == "white":
        where.append(owner_is_white)
    elif color == "black":
        where.append(f"NOT {owner_is_white}")

    if result == "win":
        where.append(f"(({owner_is_white} AND winner='white') "
                     f"OR (NOT {owner_is_white} AND winner='black'))")
    elif result == "loss":
        where.append(f"(({owner_is_white} AND winner='black') "
                     f"OR (NOT {owner_is_white} AND winner='white'))")
    elif result == "draw":
        where.append("winner = ''")

    sql = "SELECT * FROM games"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY played_at DESC LIMIT ?"
    args.append(limit)

    return list(conn.execute(sql, args))
