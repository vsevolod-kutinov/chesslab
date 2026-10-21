"""Player card: who the opponent is, from the FIDE profile and the tournament.

ratings.fide.com/profile/<id> is server-rendered: name, federation, birth year,
title, the three ratings and a monthly history table (the last 12 months of it are
the player's form). The tournament part - team, board order, games in this event -
comes from chess-results through `lineup.py`.

The card is printed for the assistant and also stored in `cards.json` of the current
folder, which the Prep panel renders - numbers on the card come from data, not from
the assistant's retelling.

    python -m chesslab.prep card <FIDE_ID> [--link <tournament>]
    python -m chesslab.prep card --name "Last, First" --link <tournament>
"""

from __future__ import annotations

import argparse
import html
import json
import re
import urllib.error
import urllib.request
from pathlib import Path

from .. import __version__, chessresults
from . import lineup

PROFILE_URL = "https://ratings.fide.com/profile/{}"
USER_AGENT = f"ChessLab/{__version__} (personal tool)"
TIMEOUT = 25
CARDS_FILE = "cards.json"

_NAME = re.compile(r'<h1 class="player-title">([^<]*)</h1>', re.I)
_FIELD = r'class="profile-info-{} ?"[^>]*>(.*?)</(?:p|div)>'
_RATING = r'class="profile-{} profile-game ?">.*?<p>([^<]*)</p>'
_HISTORY_ROW = re.compile(
    r"<tr>\s*<td[^>]*>&nbsp;(\d{4}-\w{3})&nbsp;</td>\s*<td[^>]*>&nbsp;(\d*)&nbsp;</td>", re.I
)


class CardError(Exception):
    pass


def _clean(raw: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())


def _int(text: str) -> int | None:
    text = text.strip()
    return int(text) if text.isdigit() else None


def parse_profile(page: str) -> dict:
    def field(name: str) -> str:
        match = re.search(_FIELD.format(name), page, re.S | re.I)
        return _clean(match.group(1)) if match else ""

    def rating(kind: str) -> int | None:
        match = re.search(_RATING.format(kind), page, re.S | re.I)
        return _int(match.group(1)) if match else None

    name = _NAME.search(page)
    title = field("title")
    return {
        "name": _clean(name.group(1)) if name else "",
        "fide_id": field("id"),
        "title": "" if title == "None" else title,
        "fed": field("country"),
        "birth_year": _int(field("byear")),
        "std": rating("standart"),  # sic: the site's class name
        "rapid": rating("rapid"),
        "blitz": rating("blitz"),
        "history": [[period, _int(std)] for period, std in _HISTORY_ROW.findall(page)][:12],
    }


def fetch_profile(fide_id: str) -> dict:
    request = urllib.request.Request(PROFILE_URL.format(fide_id))
    request.add_header("User-Agent", USER_AGENT)
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            page = response.read().decode("utf-8", errors="replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        raise CardError(f"Cannot reach ratings.fide.com: {exc}") from exc
    info = parse_profile(page)
    if not info["name"]:
        raise CardError(f"No FIDE profile for {fide_id}.")
    return info


def tournament_part(link: str, fide_id: str = "", name: str = "") -> dict:
    """Team, board order and the games of this player in the tournament."""
    host, event = chessresults.parse_link(link)
    roster = lineup.roster(chessresults._fetch_page(host, event, "&art=16&zeilen=99999"))
    me = next((p for p in roster if (fide_id and p["fide_id"] == fide_id)
               or (name and lineup._plain(p["name"]) == lineup._plain(name))), None)
    if me is None:
        return {}
    games = player_games(lineup.played_rounds(host, event), me["name"])
    return {
        "name": me["name"], "fide_id": me["fide_id"], "rating": me["rating"],
        "team": me["team"], "board_order": me["board"], "tournament": games,
    }


def player_games(rounds, name: str) -> list[dict]:
    """This player's boards from (round, boards) pairs, from his side of the table.

    Matched by name: the snr in pairing links is not the starting-list number.
    """
    key = lineup._plain(name)
    games = []
    for round_no, boards in rounds:
        for b in boards:
            home = lineup._plain(b["home_player"]) == key
            if not home and lineup._plain(b["away_player"]) != key:
                continue
            points = chessresults.split_result(b["result"])
            games.append({
                "round": round_no,
                "board": b["board"],
                "opponent": b["away_player" if home else "home_player"] or "no player",
                "colour": "white" if home != b["away_white"] else "black",
                "points": None if points is None else points[0 if home else 1],
                "forfeit": chessresults.is_forfeit(b["result"]),
            })
    return games


def save_card(folder: Path, card: dict) -> None:
    """Add or replace (same FIDE ID, else same name) a card in folder/cards.json."""
    path = folder / CARDS_FILE
    try:
        cards = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        cards = []
    key = (lambda c: ("id", c["fide_id"])) if card.get("fide_id") else (lambda c: ("name", c.get("name")))
    same = key(card)
    cards = [c for c in cards if key(c) != same]
    cards.append(card)
    path.write_text(json.dumps(cards, ensure_ascii=False, indent=1), encoding="utf-8")


def render(card: dict) -> str:
    lines = [f"{card.get('title', '') + ' ' if card.get('title') else ''}{card.get('name', '?')}"
             f"  FIDE {card.get('fide_id') or '-'}"]
    if card.get("birth_year"):
        lines.append(f"  born {card['birth_year']}, {card.get('fed', '')}")
    lines.append(f"  ratings: std {card.get('std') or card.get('rating') or '-'}, "
                 f"rapid {card.get('rapid') or '-'}, blitz {card.get('blitz') or '-'}")
    if card.get("history"):
        lines.append("  std history: " + ", ".join(f"{p} {r}" for p, r in card["history"]))
    if card.get("team"):
        lines.append(f"  team {card['team']}, board order {card.get('board_order')}")
    for g in card.get("tournament", []):
        result = "forfeit" if g["forfeit"] else g["points"]
        lines.append(f"    R{g['round']} board {g['board']} {g['colour']} vs {g['opponent']}: {result}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Player card from FIDE and the tournament.")
    parser.add_argument("fide_id", nargs="?", default="")
    parser.add_argument("--name", default="", help='"Last, First" when there is no FIDE ID')
    parser.add_argument("--link", default="", help="chess-results tournament link")
    args = parser.parse_args(argv)
    if not args.fide_id and not (args.name and args.link):
        parser.error("give a FIDE ID, or --name together with --link")
    card: dict = {}
    try:
        if args.link:
            card.update(tournament_part(args.link, args.fide_id, args.name))
        fide_id = args.fide_id or card.get("fide_id", "")
        if fide_id:
            card.update(fetch_profile(fide_id))
    except (CardError, chessresults.ResultsError) as exc:
        print(f"Error: {exc}")
        if not card:
            return 1
    card.setdefault("name", args.name)
    save_card(Path.cwd(), card)
    print(render(card))
    return 0
