"""Opponent scouting: a player's games from the chess-results game database.

chess-results keeps the PGN of many club and league games and lets anyone search
them by name (`partiesuche.aspx`). It is an ASP.NET form, so a search is a GET for
the hidden form state followed by a POST with the "Download as PGN-File" button.

`summary()` turns the games into a short text - openings by colour, score,
latest games - meant to be read by a person or by the assistant (`assistant.py`).

Also runs from the command line, which is how the assistant calls it:

    python -m chesslab.prep games "Muller, Tomas:14983571" "Kristof, Simon" --event "2. liga" --out DIR

A FIDE ID after the colon searches by ID: no namesakes, and spellings with and
without diacritics are found together. `--event` keeps only games whose
tournament name contains the text (spaces and case ignored, so "2. liga" also
matches "2.LIGA D 2025/26").
"""

from __future__ import annotations

import argparse
import collections
import html
import http.cookiejar
import io
import logging
import re
import sys
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import chess.pgn

from .. import __version__

SEARCH_URL = "https://s2.chess-results.com/partiesuche.aspx?lan=1"
USER_AGENT = f"ChessLab/{__version__} (personal tool)"
TIMEOUT = 40

_INPUT = re.compile(r"<input[^>]*>", re.I)
_SELECT = re.compile(r'<select[^>]*name="([^"]+)"[^>]*>(.*?)</select>', re.S | re.I)
_ATTR = re.compile(r'(\w+)="([^"]*)"')


class ScoutError(Exception):
    pass


def _plain(text: str) -> str:
    """Müller and Muller are the same person in a search."""
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


def _form_fields(page: str) -> dict[str, str]:
    """Hidden ASP.NET state plus the defaults of every visible field."""
    fields = {}
    for tag in _INPUT.findall(page):
        attrs = {k.lower(): html.unescape(v) for k, v in _ATTR.findall(tag)}
        kind = attrs.get("type", "text").lower()
        if "name" not in attrs or kind in ("submit", "image", "button"):
            continue
        if kind in ("checkbox", "radio") and "checked" not in tag.lower():
            continue
        fields[attrs["name"]] = attrs.get("value", "")
    for name, body in _SELECT.findall(page):
        values = re.findall(r'value="([^"]*)"', body)
        chosen = re.search(r'<option[^>]*selected[^>]*value="([^"]*)"', body)
        # the row limit: take the largest, otherwise only the first page of games comes back
        fields[html.unescape(name)] = (
            values[-1] if "anzahl" in name and values
            else chosen.group(1) if chosen else (values[0] if values else "")
        )
    return fields


def split_name(name: str) -> tuple[str, str]:
    """"Muller, Tomas" or "Muller Tomas" -> ("Muller", "Tomas")."""
    if "," in name:
        last, first = name.split(",", 1)
    else:
        last, _, first = name.strip().partition(" ")
    return last.strip(), first.strip()


def parse_player(text: str) -> tuple[str, str]:
    """"Muller, Tomas:14983571" -> ("Muller, Tomas", "14983571")."""
    name, _, fide_id = text.partition(":")
    return name.strip(), fide_id.strip()


def download(name: str, fide_id: str = "") -> str:
    """All PGN games the database has for this player."""
    last, first = split_name(name)
    if not last and not fide_id:
        raise ScoutError("Empty player name.")
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar())
    )
    opener.addheaders = [("User-Agent", USER_AGENT)]
    try:
        with opener.open(SEARCH_URL, timeout=TIMEOUT) as response:
            page = response.read().decode("utf-8", errors="replace")
        fields = _form_fields(page)
        if fide_id:
            fields["ctl00$P1$Txt_FideID"] = fide_id
        else:
            fields["ctl00$P1$txt_nachname"] = last
            fields["ctl00$P1$txt_vorname"] = first
        fields["ctl00$P1$cb_DownLoadPGN"] = "Download as PGN-File"
        data = urllib.parse.urlencode(fields).encode()
        with opener.open(SEARCH_URL, data=data, timeout=TIMEOUT) as response:
            text = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise ScoutError(f"chess-results responded {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError) as exc:
        raise ScoutError(f"Cannot reach chess-results: {exc}") from exc
    # no games: the server answers with the search page again, not with an empty PGN
    return text if text.lstrip().startswith("[") else ""


def _squeeze(text: str) -> str:
    return _plain(text).replace(" ", "")


def games(pgn: str, name: str, by_id: bool = False,
          event: str = "") -> list[tuple[chess.pgn.Game, bool]]:
    """Games of this player and their colour (True = white), newest first.

    Found by FIDE ID, every game is his already, and the surname only tells the colour;
    found by name, the first name must match too, or namesakes slip in.
    """
    last, first = (_plain(part) for part in split_name(name))
    if by_id:
        first = ""
    event = _squeeze(event)
    found = []
    stream = io.StringIO(pgn)
    while (game := chess.pgn.read_game(stream)) is not None:
        if event and event not in _squeeze(game.headers.get("Event", "")):
            continue
        for white, key in ((True, "White"), (False, "Black")):
            other_last, other_first = (_plain(p) for p in split_name(game.headers.get(key, "")))
            if other_last == last and other_first.startswith(first):
                found.append((game, white))
                break
    found.sort(key=lambda item: item[0].headers.get("Date", ""), reverse=True)
    return found


def _opening(game: chess.pgn.Game, plies: int) -> str:
    board = game.board()
    moves = []
    for move in list(game.mainline_moves())[:plies]:
        moves.append(board.san(move))
        board.push(move)
    return " ".join(moves)


def _points(game: chess.pgn.Game, white: bool) -> float | None:
    result = game.headers.get("Result", "*")
    if result == "1/2-1/2":
        return 0.5
    if result in ("1-0", "0-1"):
        return float((result == "1-0") == white)
    return None


def summary(games: list[tuple[chess.pgn.Game, bool]], name: str, latest: int = 8) -> str:
    """Repertoire and form of one player as plain text."""
    if not games:
        return f"{name}: no games in the chess-results database."
    scored = [p for g, w in games if (p := _points(g, w)) is not None]
    newest, white = games[0]
    rating = newest.headers.get("WhiteElo" if white else "BlackElo", "?")
    lines = [
        f"{name}: {len(games)} games, {games[-1][0].headers.get('Date', '?')} - "
        f"{newest.headers.get('Date', '?')}, latest rating {rating}, "
        f"score {sum(scored):g}/{len(scored)}",
        "  Events: " + "; ".join(f"{e} x{n}" for e, n in collections.Counter(
            g.headers.get("Event", "?") for g, _ in games).most_common(5)),
    ]
    for colour, is_white in (("White", True), ("Black", False)):
        own = [g for g, w in games if w == is_white]
        lines.append(f"  As {colour} ({len(own)} games):")
        # the first four plies: the opponent's reply is part of what the player chose
        lines += [f"    {line}  x{n}" for line, n in
                  collections.Counter(_opening(g, 4) for g in own).most_common(6)]
    lines.append("  Latest games:")
    for game, is_white in games[:latest]:
        h = game.headers
        opponent = h.get("Black" if is_white else "White", "?")
        lines.append(
            f"    {h.get('Date', '?')} {'W' if is_white else 'B'} vs {opponent} "
            f"{h.get('Result', '*')}  {_opening(game, 10)}  [{h.get('Event', '')}]"
        )
    return "\n".join(lines)


def file_name(name: str) -> str:
    last, first = split_name(name)
    return re.sub(r"[^\w-]+", "_", f"{last}_{first}").strip("_") + ".pgn"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scout players in the chess-results game database.")
    parser.add_argument("players", nargs="+", help='"Last, First" or "Last, First:FIDE_ID"')
    parser.add_argument("--event", default="", help="keep only tournaments whose name contains this")
    parser.add_argument("--out", default=".", help="folder for the PGN files")
    args = parser.parse_args(argv)
    # games with a broken move are still counted by their opening; the parser need not complain
    logging.getLogger("chess.pgn").setLevel(logging.CRITICAL)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    for player in args.players:
        name, fide_id = parse_player(player)
        try:
            found = games(download(name, fide_id), name, bool(fide_id), args.event)
        except ScoutError as exc:
            print(f"{name}: {exc}\n")
            continue
        if found:
            # only the games that passed the filters, so the file matches the summary
            path = out / file_name(name)
            path.write_text("\n\n".join(str(g) for g, _ in reversed(found)) + "\n", encoding="utf-8")
            print(f"PGN: {path.resolve()}")
        print(summary(found, name), "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
