"""Who a league team fields on which board, round by round.

Two chess-results pages are enough: the starting list (art=16) gives every
registered player with FIDE ID and the team's board order, and the board pairings
of a round (art=3&rd=N) give who actually sat where. From the rounds played one sees
whether a team rotates a board or always fields the same player.

Board pairings nest a table inside each player cell, so the generic table parser of
`chessresults.py` does not fit them; rows are cut on the outer `<tr class="CRg…">`.

    python -m chesslab.prep lineup <tournament link> "<team>"
"""

from __future__ import annotations

import argparse
import html
import re
import unicodedata

from .. import chessresults

_OUTER_ROW = re.compile(r'<tr class="CRg', re.I)
_HEADER_TEAM = re.compile(r'<th class="CR">(.*?)</th>', re.S | re.I)
_BOARD = re.compile(r'<td class="CRc">\s*\d+\.(\d+)\s*</td>', re.I)
# colour square, then the name cell: a link to the player card or plain "no player"
_PLAYER = re.compile(
    r'Farbe([ws])T"></div></td><td class="CR">(?:<a[^>]*snr=(\d+)[^>]*>)?([^<]*)', re.I
)
_RESULT = re.compile(r'<td class="CRc">([^<]*)</td>\s*<td class="CR">', re.I)


def _plain(text: str) -> str:
    text = unicodedata.normalize("NFKD", text)
    return "".join(c for c in text if not unicodedata.combining(c)).lower().strip()


def same_team(query: str, team: str) -> bool:
    return _plain(query) in _plain(team)


def roster(page: str) -> list[dict]:
    """Starting list: every registered player with FIDE ID, team and board order."""
    out = []
    for table in chessresults._tables(page):
        columns = chessresults._columns(table[0])
        if "fideid" not in columns or "team" not in columns:
            continue
        for row in table[1:]:
            pick = lambda *names: chessresults._pick(row, columns, *names)  # noqa: E731
            out.append({
                "snr": chessresults._number(pick("no")),
                "title": row[1] if len(row) > 1 else "",
                "name": pick("name"),
                "fide_id": pick("fideid"),
                "rating": chessresults._number(pick("rtg")),
                "fed": pick("fed"),
                "team": pick("team"),
                "board": chessresults._number(pick("bo")),
            })
    return out


def board_pairings(page: str) -> list[dict]:
    """Every board of every match of one round, home team first."""
    out = []
    home = away = ""
    for chunk in _OUTER_ROW.split(page)[1:]:
        if "<th" in chunk:
            teams = [chessresults._text(t) for t in _HEADER_TEAM.findall(chunk)]
            if len(teams) >= 2:
                home, away = teams[0], teams[1]
            continue
        board = _BOARD.search(chunk)
        players = _PLAYER.findall(chunk)
        if not board or len(players) < 2:
            continue
        (home_colour, home_snr, home_name), (_, away_snr, away_name) = players[:2]
        result = _RESULT.search(chunk)
        out.append({
            "home": home,
            "away": away,
            "board": int(board.group(1)),
            "home_player": "" if not home_snr else html.unescape(home_name).strip(),
            "away_player": "" if not away_snr else html.unescape(away_name).strip(),
            "home_snr": int(home_snr) if home_snr else None,
            "away_snr": int(away_snr) if away_snr else None,
            "away_white": home_colour.lower() == "s",
            "result": html.unescape(result.group(1)).strip() if result else "",
        })
    return out


def lineup(link: str, team: str) -> dict:
    """The team's roster and who it fielded on each board in the rounds played."""
    host, event = chessresults.parse_link(link)
    players = roster(chessresults._fetch_page(host, event, "&art=16&zeilen=99999"))
    names = sorted({p["team"] for p in players if same_team(team, p["team"])})
    if not names:
        raise chessresults.ResultsError(f"No team matching {team!r} in this tournament.")
    full_name = names[0]
    meta = chessresults.parse_meta(chessresults._fetch_page(host, event, "&turdet=YES"))
    rounds: dict[int, dict[int, str]] = {}
    for round_no in range(1, (meta.get("rounds") or 0) + 1):
        boards = board_pairings(chessresults._fetch_page(host, event, f"&art=3&rd={round_no}"))
        played = [b for b in boards if b["result"] and full_name in (b["home"], b["away"])]
        if not played:
            break  # rounds go in order: the first unplayed one ends the list
        rounds[round_no] = {
            b["board"]: b["home_player"] if b["home"] == full_name else b["away_player"]
            for b in played
        }
    own = sorted((p for p in players if p["team"] == full_name), key=lambda p: p["board"] or 99)
    return {"team": full_name, "roster": own, "rounds": rounds}


def render(data: dict) -> str:
    lines = [f"Team: {data['team']}"]
    rounds = data["rounds"]
    if not rounds:
        lines.append("  no rounds played yet")
    else:
        lines.append("  Board by round (empty = forfeit):")
        boards = sorted({b for r in rounds.values() for b in r})
        for board in boards:
            seen = [rounds[r].get(board, "") or "-" for r in sorted(rounds)]
            lines.append(f"    {board}: " + " | ".join(f"R{r} {name}" for r, name in zip(sorted(rounds), seen)))
    lines.append("  Roster (board order, FIDE ID, rating):")
    for p in data["roster"]:
        title = f"{p['title']} " if p["title"] else ""
        lines.append(f"    {p['board']}. {title}{p['name']}  FIDE {p['fide_id'] or '-'}  "
                     f"{p['rating'] or '-'}  {p['fed']}  snr {p['snr']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Who a team fields on which board.")
    parser.add_argument("link", help="chess-results tournament link or id")
    parser.add_argument("team", help="team name or part of it")
    args = parser.parse_args(argv)
    try:
        print(render(lineup(args.link, args.team)))
    except chessresults.ResultsError as exc:
        print(f"Error: {exc}")
        return 1
    return 0
