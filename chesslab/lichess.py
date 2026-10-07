"""Lichess client. So far it can only check an account and fetch ratings.

Network runs in a separate thread: a hung request must not freeze the window.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.parse
import urllib.request

from PySide6.QtCore import QObject, Signal

from . import __version__

API = "https://lichess.org/api"
# ASCII only: HTTP headers are encoded as latin-1, Cyrillic breaks here
USER_AGENT = f"ChessLab/{__version__} (personal tool)"
TIMEOUT = 15

# ratings shown in the account list
PERFS = ("bullet", "blitz", "rapid", "classical")


class LichessError(Exception):
    pass


def fetch_user(username: str, token: str | None = None) -> dict:
    """Public player profile. Token is optional."""
    username = username.strip().lstrip("@")
    if not username:
        raise LichessError("Empty username.")

    # the username goes into the URL path, which allows only ASCII — percent-encode it,
    # otherwise non-Latin names fail with UnicodeEncodeError instead of an honest "no such player"
    request = urllib.request.Request(f"{API}/user/{urllib.parse.quote(username, safe='')}")
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LichessError(f"No such player on Lichess: “{username}”.") from exc
        if exc.code == 401:
            raise LichessError("Token rejected.") from exc
        if exc.code == 429:
            raise LichessError("Lichess asks to wait — too many requests.") from exc
        raise LichessError(f"Lichess responded with {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise LichessError(f"No connection to Lichess: {exc.reason}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise LichessError(f"Garbled response from Lichess: {exc}") from exc

    if payload.get("closed"):
        raise LichessError(f"Account “{username}” is closed.")
    return payload


def ratings(profile: dict) -> dict[str, int]:
    """Only the modes where games were actually played."""
    perfs = profile.get("perfs") or {}
    out = {}
    for name in PERFS:
        entry = perfs.get(name) or {}
        if entry.get("games") and not entry.get("prov"):
            out[name] = entry.get("rating", 0)
    return out


def summary(account: dict) -> str:
    """Line for the account list."""
    name = account.get("username", "?")
    rate = ratings(account.get("profile") or {})
    if not rate:
        return name
    parts = " · ".join(f"{k[:3]} {v}" for k, v in rate.items())
    return f"{name}   {parts}"


def fetch_perf(username: str, perf: str, token: str | None = None) -> dict:
    """Detailed statistics for one mode: rating, streaks, best wins.

    A separate endpoint from the profile — the profile has only current ratings.
    """
    request = urllib.request.Request(
        f"{API}/user/{urllib.parse.quote(username, safe='')}/perf/{perf}"
    )
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LichessError(f"No data for mode “{perf}”.") from exc
        if exc.code == 429:
            raise LichessError("Lichess asks to wait — too many requests.") from exc
        raise LichessError(f"Lichess responded with {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise LichessError(f"No connection to Lichess: {exc.reason}") from exc
    except (TimeoutError, json.JSONDecodeError) as exc:
        raise LichessError(f"Garbled response from Lichess: {exc}") from exc


class PerfLookup(QObject):
    """One-off mode statistics request in the background."""

    found = Signal(dict)
    failed = Signal(str)

    def start(self, username: str, perf: str, token: str | None) -> None:
        threading.Thread(
            target=self._run, args=(username, perf, token),
            name="lichess-perf", daemon=True,
        ).start()

    def _run(self, username: str, perf: str, token: str | None) -> None:
        try:
            self.found.emit(fetch_perf(username, perf, token))
        except LichessError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected error: {exc!r}")


def stream_games(username: str, token: str | None = None, since: int | None = None,
                 max_games: int | None = None):
    """Player's games, newest first. Yields one at a time, accumulating nothing in memory.

    since — a timestamp in milliseconds: only games later than it are taken,
    so a repeated download pulls only the new ones.
    """
    params = {
        "moves": "true",
        "opening": "true",
        "pgnInJson": "true",
        "tags": "true",
        "clocks": "false",
        "evals": "false",
        "sort": "dateDesc",
    }
    if since:
        params["since"] = str(since)
    if max_games:
        params["max"] = str(max_games)

    url = (
        f"{API}/games/user/{urllib.parse.quote(username, safe='')}"
        f"?{urllib.parse.urlencode(params)}"
    )
    request = urllib.request.Request(url)
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/x-ndjson")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    try:
        response = urllib.request.urlopen(request, timeout=TIMEOUT)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LichessError(f"No such player on Lichess: “{username}”.") from exc
        if exc.code == 429:
            raise LichessError(
                "Lichess asks to wait — too many requests. "
                "Try again in a couple of minutes."
            ) from exc
        raise LichessError(f"Lichess responded with {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise LichessError(f"No connection to Lichess: {exc.reason}") from exc

    with response:
        for raw in response:
            raw = raw.strip()
            if not raw:
                continue
            try:
                yield json.loads(raw)
            except json.JSONDecodeError:
                continue  # simply skip a corrupt line


# --- a single game by link -----------------------------------------------

_LINK = re.compile(r"lichess\.org/(?:game/export/)?([A-Za-z0-9]{8})")


def game_id_from_url(text: str) -> str | None:
    """Game id from a link like lichess.org/C3svMLLu (the move suffix is dropped).

    A bare id is understood too: on Lichess it is always eight letters and digits, so
    it cannot be confused with a Chess.com game number.
    """
    text = (text or "").strip()
    if len(text) == 8 and text.isalnum() and not text.isdigit():
        return text
    found = _LINK.search(text)
    return found.group(1) if found else None


def fetch_game(game_id: str) -> str:
    """PGN of a single game. Lichess serves it directly, nothing to parse."""
    request = urllib.request.Request(
        f"https://lichess.org/game/export/{game_id}?clocks=false&evals=false"
    )
    request.add_header("User-Agent", USER_AGENT)
    request.add_header("Accept", "application/x-chess-pgn")
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise LichessError(f"Lichess does not know game {game_id}.") from exc
        raise LichessError(f"Lichess responded with {exc.code}.") from exc
    except urllib.error.URLError as exc:
        raise LichessError(f"No connection to Lichess: {exc.reason}") from exc
    except TimeoutError as exc:
        raise LichessError(f"Lichess did not respond: {exc}") from exc


class GameFetcher(QObject):
    """A single game by link, in the background."""

    found = Signal(str)  # PGN
    failed = Signal(str)

    def start(self, game_id: str) -> None:
        threading.Thread(target=self._run, args=(game_id,),
                         name="lichess-game", daemon=True).start()

    def _run(self, game_id: str) -> None:
        try:
            self.found.emit(fetch_game(game_id))
        except LichessError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected error: {exc!r}")


class UserLookup(QObject):
    """One-off account check in the background."""

    found = Signal(dict)
    failed = Signal(str)

    def start(self, username: str, token: str | None) -> None:
        thread = threading.Thread(
            target=self._run, args=(username, token), name="lichess", daemon=True
        )
        thread.start()

    def _run(self, username: str, token: str | None) -> None:
        try:
            self.found.emit(fetch_user(username, token))
        except LichessError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            # anything unexpected must reach the window: otherwise the dialog
            # stays forever in the "waiting for a response" state with dead buttons
            self.failed.emit(f"Unexpected error: {exc!r}")


class GamesDownloader(QObject):
    """Downloads the account's games into the local database. Runs in its own thread."""

    progress = Signal(int)  # how many games are saved so far
    finished = Signal(int, int)  # added this time, total for the account in the database
    failed = Signal(str)

    BATCH = 100  # write to the database in batches, to avoid hitting the disk on every game

    def __init__(self) -> None:
        super().__init__()
        self._cancel = threading.Event()

    def cancel(self) -> None:
        self._cancel.set()

    def start(self, account: dict, full: bool = False) -> None:
        thread = threading.Thread(
            target=self._run, args=(account, full), name="lichess-games", daemon=True
        )
        thread.start()

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
            # +1 millisecond, otherwise Lichess would return the last game again
            since = since + 1 if since else None

            batch: list[dict] = []
            saved = 0

            for game in stream_games(username, account.get("token"), since):
                if self._cancel.is_set():
                    break
                batch.append(games_db.row_from_api(username, game))
                if len(batch) >= self.BATCH:
                    saved += games_db.store(conn, batch)
                    batch.clear()
                    self.progress.emit(saved)

            saved += games_db.store(conn, batch)
            self.finished.emit(saved, games_db.count(conn, username))
        except LichessError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"Unexpected error: {exc!r}")
        finally:
            conn.close()
