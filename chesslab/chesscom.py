"""Chess.com client. Public API, no tokens: there are none for reading.

Built like lichess.py — the same signals and names, so the windows work with
both services alike. The main difference: Lichess serves the whole history
as one stream, while Chess.com splits it by month — first we ask for the list of
archives, then download them one by one.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

import chess
import chess.pgn
from PySide6.QtCore import QObject, Signal

from . import __version__, eco

API = "https://api.chess.com/pub"
# ASCII only: HTTP headers are encoded as latin-1
USER_AGENT = f"ChessLab/{__version__} (personal tool)"
# the site's internal endpoint dislikes our agent, so for it — an ordinary
BROWSER_AGENT = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                 "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")
TIMEOUT = 20

# ratings shown in the account list: key in stats -> our mode
PERFS = {
    "chess_bullet": "bullet",
    "chess_blitz": "blitz",
    "chess_rapid": "rapid",
    "chess_daily": "correspondence",
}

# Chess.com has no "classical", but it has correspondence play — name it as on
# Lichess, otherwise the time-control filter in "My Games" would not see it
SPEEDS = {
    "bullet": "bullet",
    "blitz": "blitz",
    "rapid": "rapid",
    "daily": "correspondence",
}

# how a game ended — translated into Lichess terms so the database has one vocabulary
STATUSES = {
    "checkmated": "mate",
    "resigned": "resign",
    "timeout": "outoftime",
    "abandoned": "aborted",
    "agreed": "draw",
    "repetition": "draw",
    "stalemate": "stalemate",
    "insufficient": "draw",
    "50move": "draw",
    "timevsinsufficient": "draw",
}

# how many plies are run through the reference to name the opening
NAME_PLIES = 24


class ChessComError(Exception):
    pass


def _get(path: str) -> dict:
    request = urllib.request.Request(f"{API}/{path}")
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise ChessComError("Chess.com does not know this.") from exc
        if exc.code == 429:
            raise ChessComError("Chess.com asks to wait — too many requests.") from exc
        raise ChessComError(f"Chess.com responded with {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise ChessComError(f"No connection to Chess.com: {exc.reason}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise ChessComError(f"Garbled response from Chess.com: {exc}") from exc


def fetch_user(username: str, token: str | None = None) -> dict:
    """Player profile together with ratings. No token needed; the argument is a pair
    for lichess.fetch_user — the accounts window calls both the same way."""
    username = username.strip().lstrip("@")
    if not username:
        raise ChessComError("Empty username.")

    quoted = urllib.parse.quote(username, safe="")
    try:
        profile = _get(f"player/{quoted}")
    except ChessComError as exc:
        if "does not know" in str(exc):
            raise ChessComError(f"No such player on Chess.com: “{username}”.") from exc
        raise

    if str(profile.get("status") or "").startswith("closed"):
        raise ChessComError(f"Account “{username}” is closed.")

    # ratings are in a separate endpoint; the account works fine without them
    try:
        profile["stats"] = _get(f"player/{quoted}/stats")
    except ChessComError:
        profile["stats"] = {}
    return profile


def ratings(profile: dict) -> dict[str, int]:
    """Current ratings by mode, where any games were played."""
    stats = profile.get("stats") or {}
    out = {}
    for key, name in PERFS.items():
        entry = (stats.get(key) or {}).get("last") or {}
        if entry.get("rating"):
            out[name] = entry["rating"]
    return out


def summary(account: dict) -> str:
    """Line for the account list."""
    name = account.get("username", "?")
    rate = ratings(account.get("profile") or {})
    if not rate:
        return name
    parts = " · ".join(f"{k[:3]} {v}" for k, v in rate.items())
    return f"{name}   {parts}"


# --- games ---------------------------------------------------------------

_HEADER = re.compile(r'\[(\w+)\s+"(.*?)"\]')
_COMMENT = re.compile(r"\{[^}]*\}")
_NUMBER = re.compile(r"\b\d+\.+")
_JUNK = re.compile(r"[?!]+|\$\d+")
_RESULTS = {"1-0", "0-1", "1/2-1/2", "*"}


def split_pgn(pgn: str) -> tuple[dict, list[str]]:
    """Headers and moves. Parsed by hand rather than with python-chess: there are thousands of games,
    and we don't need to walk a tree — just the move string."""
    headers = dict(_HEADER.findall(pgn))
    body = pgn.split("]\n\n", 1)[-1] if "]\n\n" in pgn else pgn
    body = _COMMENT.sub(" ", body)
    body = _NUMBER.sub(" ", body)
    body = _JUNK.sub("", body)
    sans = [token for token in body.split() if token not in _RESULTS]
    return headers, sans


def opening_of(sans: list[str], fallback_eco: str = "") -> tuple[str, str]:
    """Opening code and name from our own ECO reference.

    Run the start of the game over the board and ask the book — this way the names for
    Chess.com games come out the same as for Lichess games, and opening statistics
    are computed over the whole database at once.
    """
    board = chess.Board()
    for san in sans[:NAME_PLIES]:
        try:
            board.push_san(san)
        except ValueError:
            break
    found = eco.book().name_at(board)
    if found is None:
        return fallback_eco, ""
    return found.eco or fallback_eco, found.name


def row_from_api(account: str, game: dict) -> dict | None:
    """Flat record built from what Chess.com returns. None — not our game:
    either not standard chess, or still in progress and the PGN is not ready."""
    if (game.get("rules") or "chess") != "chess":
        return None
    pgn = game.get("pgn") or ""
    if not pgn:
        return None

    white = game.get("white") or {}
    black = game.get("black") or {}
    headers, sans = split_pgn(pgn)

    if white.get("result") == "win":
        winner, status = "white", black.get("result") or ""
    elif black.get("result") == "win":
        winner, status = "black", white.get("result") or ""
    else:
        winner, status = "", white.get("result") or ""

    code, name = opening_of(sans, headers.get("ECO", ""))
    identity = game.get("uuid") or (game.get("url") or "").rsplit("/", 1)[-1]
    return {
        "id": f"cc-{identity}",
        "account": account,
        "service": "chess.com",
        # end_time is in seconds, while the database has milliseconds — as with Lichess
        "played_at": int(game.get("end_time") or 0) * 1000,
        "white": white.get("username") or "Anonymous",
        "black": black.get("username") or "Anonymous",
        "white_elo": white.get("rating"),
        "black_elo": black.get("rating"),
        "winner": winner,
        "status": STATUSES.get(status, status),
        "speed": SPEEDS.get(game.get("time_class") or "", ""),
        "rated": 1 if game.get("rated") else 0,
        "eco": code,
        "opening": name,
        "moves": " ".join(sans),
        "pgn": pgn,
        "tournament_id": None,
        "round": "",
    }


def archives(username: str) -> list[str]:
    """Links to monthly archives, oldest to newest."""
    payload = _get(f"player/{urllib.parse.quote(username, safe='')}/games/archives")
    return list(payload.get("archives") or [])


def _month(url: str) -> tuple[int, int]:
    """(year, month) from the tail of an archive link."""
    parts = url.rstrip("/").split("/")
    try:
        return int(parts[-2]), int(parts[-1])
    except (IndexError, ValueError):
        return 0, 0


def months_since(links: list[str], stamp_ms: int | None) -> list[str]:
    """Archives that may hold games newer than stamp_ms.

    The month of the last saved game is included too: it surely has
    days not yet downloaded.
    """
    if not stamp_ms:
        return links
    from datetime import datetime, timezone

    last = datetime.fromtimestamp(stamp_ms / 1000, timezone.utc)
    return [link for link in links if _month(link) >= (last.year, last.month)]


def fetch_archive(url: str) -> list[dict]:
    if not url.startswith(f"{API}/"):  # links come from Chess.com itself
        raise ChessComError("Foreign archive link.")
    return list(_get(url[len(API) + 1:]).get("games") or [])


# --- a single game by link -----------------------------------------------
#
# The public API serves only monthly archives, with nothing for a link to a
# single game. But the site itself has an endpoint its own board uses:
# /callback/live/game/{id}. It is undocumented and may disappear one day —
# so errors here are soft, not "everything is broken".
#
# Moves arrive not as PGN but in their own encoding: two characters per ply, each is
# a square number from a1 (0) to h8 (63) in this alphabet. The decoding was verified against
# games from the database: 22 games, 1986 plies, everything matched.
CALLBACK = "https://www.chess.com/callback/live/game"
MOVE_CHARS = ("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
              "0123456789!?{~}(^)[_]@#$,./&-*++=")
PROMOTIONS = "qnrbkp"
_LINK = re.compile(r"chess\.com/(?:game|live/game|analysis/game)/(?:live|daily)?/?(\d+)")


def game_id_from_url(text: str) -> str | None:
    """Game id from a link like chess.com/game/live/174073320224?…"""
    text = (text or "").strip()
    if text.isdigit():
        return text
    found = _LINK.search(text)
    return found.group(1) if found else None


def decode_moves(move_list: str) -> list[chess.Move]:
    """Moves from Chess.com's internal encoding."""
    moves = []
    for index in range(0, len(move_list) - 1, 2):
        try:
            start = MOVE_CHARS.index(move_list[index])
            end = MOVE_CHARS.index(move_list[index + 1])
        except ValueError as exc:
            raise ChessComError("Unrecognised move encoding from Chess.com.") from exc
        promotion = None
        if end > 63:
            promotion = chess.Piece.from_symbol(
                PROMOTIONS[(end - 64) // 3].upper()).piece_type
            # the destination square is packed into the remainder: capture left, straight, right
            end = start + (-8 if start < 16 else 8) + ((end - 64) % 3) - 1
        moves.append(chess.Move(start, end, promotion=promotion))
    return moves


def fetch_game(game_id: str) -> str:
    """PGN of a single game by its id.

    The endpoint is internal and sometimes answers 500 out of the blue — for such an
    answer we try a second time before complaining.
    """
    payload = None
    for attempt in (1, 2):
        request = urllib.request.Request(f"{CALLBACK}/{game_id}")
        request.add_header("User-Agent", BROWSER_AGENT)
        # no Accept-Encoding: urllib will not decompress a compressed response itself
        request.add_header("Accept", "application/json")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                payload = json.loads(response.read().decode("utf-8"))
            break
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise ChessComError("Chess.com serves this game only to "
                                    "a logged-in browser.") from exc
            if exc.code == 404:
                raise ChessComError(f"Chess.com does not know game {game_id}.") from exc
            if exc.code >= 500 and attempt == 1:
                continue
            raise ChessComError(f"Chess.com responded with {exc.code}.") from exc
        except urllib.error.URLError as exc:
            raise ChessComError(f"No connection to Chess.com: {exc.reason}") from exc
        except (TimeoutError, json.JSONDecodeError) as exc:
            raise ChessComError(f"Garbled response from Chess.com: {exc}") from exc

    payload = (payload or {}).get("game") or {}
    if not payload.get("moveList"):
        raise ChessComError("The Chess.com response has no moves for this game.")
    return _pgn_from_callback(payload, game_id)


def _pgn_from_callback(payload: dict, game_id: str) -> str:
    game = chess.pgn.Game()
    for key, value in (payload.get("pgnHeaders") or {}).items():
        if key not in ("SetUp", "FEN"):  # game from the starting position
            game.headers[key] = str(value)
    game.headers["Link"] = f"https://www.chess.com/game/live/{game_id}"

    node = game
    board = chess.Board()
    for move in decode_moves(payload["moveList"]):
        if move not in board.legal_moves:
            raise ChessComError("Chess.com moves do not match the board.")
        node = node.add_variation(move)
        board.push(move)
    return str(game)


class GameFetcher(QObject):
    """A single game by link, in the background."""

    found = Signal(str)  # PGN
    failed = Signal(str)

    def start(self, game_id: str) -> None:
        threading.Thread(target=self._run, args=(game_id,),
                         name="chesscom-game", daemon=True).start()

    def _run(self, game_id: str) -> None:
        try:
            self.found.emit(fetch_game(game_id))
        except ChessComError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected error: {exc!r}")


class UserLookup(QObject):
    """One-off account check in the background."""

    found = Signal(dict)
    failed = Signal(str)

    def start(self, username: str, token: str | None = None) -> None:
        threading.Thread(
            target=self._run, args=(username,), name="chesscom", daemon=True
        ).start()

    def _run(self, username: str) -> None:
        try:
            self.found.emit(fetch_user(username))
        except ChessComError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected error: {exc!r}")


class GamesDownloader(QObject):
    """Downloads the account's games into the local database. Runs in its own thread."""

    progress = Signal(int)  # how many games are saved so far
    finished = Signal(int, int)  # added this time, total for the account in the database
    failed = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def start(self, account: dict, full: bool = False) -> None:
        threading.Thread(
            target=self._run, args=(account, full),
            name="chesscom-games", daemon=True,
        ).start()

    def _run(self, account: dict, full: bool) -> None:
        from . import games_db

        username = account["username"]
        try:
            conn = games_db.connect()
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Could not open the game database: {exc}")
            return

        try:
            since = None if full else games_db.latest_played_at(conn, username)
            links = months_since(archives(username), since)

            saved = 0
            for link in links:
                if self._cancel.is_set():
                    break
                rows = []
                for game in fetch_archive(link):
                    row = row_from_api(username, game)
                    if row is None:
                        continue
                    if since and row["played_at"] <= since:
                        continue  # this month is already partly downloaded
                    rows.append(row)
                # an archive is a ready batch, write month by month and report right away
                saved += games_db.store(conn, rows)
                self.progress.emit(saved)

            self.finished.emit(saved, games_db.count(conn, username))
        except ChessComError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected error: {exc!r}")
        finally:
            conn.close()
