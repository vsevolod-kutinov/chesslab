"""Crosstable analysis: one's own result against the whole tournament.

Computes not only points and performance but also the place among the others: how
hard the schedule was, where one's own wins rank in the tournament upset list,
where the personal rating ceiling against opponents lies.

**About the 1000 rating.** Unrated players are listed as 1000 (sometimes 0) in the
protocol, and that is a placeholder, not playing strength. Computing from it is
self-deception: a win over a beginner always looks like an "upset", and the average
opponent rating is understated for anyone who played newcomers. So the placeholder

    BASE      - keep as in the protocol
    1750/1800 - one rough figure for everybody
    PERF      - each gets their own performance, computed from this same tournament

The last method is the fairest: performances of unrated players within one
tournament differ by hundreds of points, and one figure cannot cover them. It is computed
iteratively - performance depends on opponents' ratings, some of whom are also
unrated, so we recompute until the numbers stop moving.

The module is pure: no Qt, only numbers. The window lives in `crossreport.py`.
"""

from __future__ import annotations

from collections import Counter

from .chessresults import is_forfeit, split_result
from .tournaments_db import performance

# rating at or below which we treat it as a placeholder, not playing strength
UNRATED_MAX = 1000

# What Swiss-Manager uses for a completely unrated player (0 in the protocol)
# when it computes performance itself. Checked against the player cards of one
# real tournament: all 42 match with this value, only nine with any other.
UNRATED_DEFAULT = 1400

BASE = "base"      # as in the protocol
PERF = "perf"      # by performance in this same tournament

MODES = [
    ("as in the protocol (1000)", BASE),
    ("count as 1750", 1750),
    ("count as 1800", 1800),
    ("by performance in this tournament", PERF),
]

_SEED = 1750          # starting value for the iterations in PERF mode
_PASSES = 6           # this many recomputations suffice: the numbers settle in 3-4
_FLOOR, _CEIL = 800, 2600


def is_unrated(rating: int | None) -> bool:
    return not rating or rating <= UNRATED_MAX


# --- ratings used for the calculation -----------------------------------

def effective_ratings(players: list[dict], games: list[dict], mode) -> dict[int, int]:
    """Rating of each participant with the chosen adjustment."""
    ratings = {p["snr"]: p["rating"] or 0 for p in players}
    unrated = [snr for snr, rating in ratings.items() if is_unrated(rating)]
    if mode == BASE or not unrated:
        return ratings

    if isinstance(mode, int):
        return {snr: (mode if snr in unrated else rating)
                for snr, rating in ratings.items()}

    # PERF: first set every unrated player to one figure, then refine
    for snr in unrated:
        ratings[snr] = _SEED
    played = _results(games)
    for _ in range(_PASSES):
        updated = dict(ratings)
        for snr in unrated:
            opponents, fraction = counted(played.get(snr, []), ratings)
            if not opponents:
                continue
            value = performance(sum(opponents) / len(opponents), fraction)
            updated[snr] = max(_FLOOR, min(_CEIL, value))
        if updated == ratings:
            break
        ratings = updated
    return ratings


def _results(games: list[dict]) -> dict[int, list[tuple[int, float, bool]]]:
    """Who played whom: {number: [(opponent, point, game played), ...]}."""
    played: dict[int, list[tuple[int, float, bool]]] = {}
    for game in games:
        points = split_result(game["result"])
        if points is None:
            continue
        real = not is_forfeit(game["result"])
        white, black = game["white"], game["black"]
        played.setdefault(white, []).append((black, points[0], real))
        played.setdefault(black, []).append((white, points[1], real))
    return played


def expected_score(rating: int, opponents: list[int]) -> float:
    """How many points Elo predicts against such a field."""
    return sum(1 / (1 + 10 ** ((other - rating) / 400)) for other in opponents)


def counted(pairs: list[tuple[int, float, bool]],
            ratings: dict[int, int]) -> tuple[list[int], float]:
    """What goes into performance: (opponent ratings, fraction of points scored).

    Two rules taken from chess-results itself: a forfeited game does not count
    at all, and an unrated opponent counts as `UNRATED_DEFAULT`. Without them our
    figures differ from the official card by tens and hundreds of points.
    """
    kept = [(ratings.get(other, 0) or UNRATED_DEFAULT, score)
            for other, score, real in pairs if real]
    if not kept:
        return [], 0.0
    return [rating for rating, _ in kept], sum(s for _, s in kept) / len(kept)


# --- main calculation ----------------------------------------------------

def analyse(data: dict, snr: int, mode=BASE) -> dict:
    """Full analysis of player `snr`'s performance in the loaded tournament."""
    players = {p["snr"]: p for p in data["players"]}
    if snr not in players:
        raise ValueError("No such starting number in the tournament")

    ratings = effective_ratings(data["players"], data["games"], mode)
    me = players[snr]
    played = _results(data["games"])

    rounds = []
    for game in sorted(data["games"], key=lambda g: g["round"]):
        if snr not in (game["white"], game["black"]):
            continue
        points = split_result(game["result"])
        if points is None:
            continue
        as_white = game["white"] == snr
        other = game["black"] if as_white else game["white"]
        opponent = players.get(other, {})
        rounds.append({
            "round": game["round"],
            "board": game["board"],
            "white": as_white,
            "snr": other,
            "name": opponent.get("name", "?"),
            "club": opponent.get("club", ""),
            "rating": opponent.get("rating", 0),
            "effective": ratings.get(other, 0),
            "score": points[0] if as_white else points[1],
            "result": game["result"],
            "played": not is_forfeit(game["result"]),
            "opponent_rank": opponent.get("rank"),
            "opponent_points": opponent.get("points"),
        })

    scores = [r["score"] for r in rounds]
    points = sum(scores)
    total = len(rounds)
    # only games against rated opponents count toward performance
    scored, fraction = counted(
        [(r["snr"], r["score"], r["played"]) for r in rounds], ratings)
    raw = [r["rating"] for r in rounds if r["rating"]]

    summary = {
        "points": points,
        "games": total,
        "counted": len(scored),
        "wins": sum(1 for s in scores if s == 1),
        "draws": sum(1 for s in scores if s == 0.5),
        "losses": sum(1 for s in scores if s == 0),
        "average": round(sum(scored) / len(scored)) if scored else None,
        "average_raw": round(sum(raw) / len(raw)) if raw else None,
        "performance": (performance(sum(scored) / len(scored), fraction)
                        if scored else None),
        "expected": expected_score(ratings.get(snr, 0), scored) if scored else 0.0,
        "colors": "".join("W" if r["white"] else "B" for r in rounds),
        "buchholz": me.get("tb1"),
        "best_board": min((r["board"] for r in rounds if r["board"]), default=None),
    }

    table = _whole_tournament(data, ratings, played)
    return {
        "data": data,
        "mode": mode,
        "me": me,
        "my_rating": ratings.get(snr, 0),
        "rounds": rounds,
        "summary": summary,
        "table": table,
        "ranks": _my_ranks(snr, table),
        "upsets": table["upsets"],
        "my_upsets": [i for i, u in enumerate(table["upsets"], 1) if u["winner"] == snr],
    }


def _whole_tournament(data: dict, ratings: dict[int, int],
                      played: dict[int, list[tuple[int, float]]]) -> dict:
    """Performance, schedule strength and upsets - for all participants at once."""
    players = {p["snr"]: p for p in data["players"]}
    rows = []
    for snr, games in played.items():
        if snr not in players:
            continue
        opponents, fraction = counted(games, ratings)
        if not opponents:
            continue
        rows.append({
            "snr": snr,
            "name": players[snr]["name"],
            "rating": ratings.get(snr, 0),
            "raw": players[snr]["rating"],
            "rank": players[snr]["rank"],
            "points": players[snr]["points"],
            "tb1": players[snr]["tb1"],
            "average": round(sum(opponents) / len(opponents)),
            "performance": performance(sum(opponents) / len(opponents), fraction),
        })

    upsets = []
    for game in data["games"]:
        points = split_result(game["result"])
        if points is None or points[0] == points[1]:
            continue  # a draw is never an upset
        if is_forfeit(game["result"]):
            continue  # point without a game
        white, black = game["white"], game["black"]
        winner, loser = (white, black) if points[0] > points[1] else (black, white)
        # rating 0 means "no rating at all", and a gap against it means nothing:
        # otherwise the first newcomer to beat a rated player tops the list
        if not ratings.get(winner) or not ratings.get(loser):
            continue
        gap = ratings[loser] - ratings[winner]
        if gap <= 0:
            continue
        upsets.append({
            "gap": gap,
            "round": game["round"],
            "winner": winner,
            "winner_name": players.get(winner, {}).get("name", "?"),
            "winner_rating": ratings.get(winner, 0),
            "loser_name": players.get(loser, {}).get("name", "?"),
            "loser_rating": ratings.get(loser, 0),
        })
    upsets.sort(key=lambda u: -u["gap"])

    return {
        "rows": rows,
        "by_performance": sorted(rows, key=lambda r: -r["performance"]),
        "by_average": sorted(rows, key=lambda r: -r["average"]),
        "by_buchholz": sorted(rows, key=lambda r: -(r["tb1"] or 0)),
        "upsets": upsets,
        "draws": sum(1 for g in data["games"]
                     if (split_result(g["result"]) or (1, 0))[0] == 0.5),
    }


def _my_ranks(snr: int, table: dict) -> dict:
    def place(rows: list[dict]) -> int | None:
        for index, row in enumerate(rows, 1):
            if row["snr"] == snr:
                return index
        return None

    return {
        "performance": place(table["by_performance"]),
        "average": place(table["by_average"]),
        "buchholz": place(table["by_buchholz"]),
        "total": len(table["rows"]),
    }


# --- notes ----------------------------------------------------------------

def _streak(rounds: list[dict]) -> tuple[int, int, int]:
    """Longest winning streak: (length, first round, last round)."""
    best = (0, 0, 0)
    length = 0
    for entry in rounds:
        if entry["score"] == 1:
            length += 1
            if length > best[0]:
                best = (length, entry["round"] - length + 1, entry["round"])
        else:
            length = 0
    return best


def insights(analysis: dict) -> list[dict]:
    """What is worth noting about this performance. Only what is true."""
    out: list[dict] = []
    rounds, summary = analysis["rounds"], analysis["summary"]
    ranks, me = analysis["ranks"], analysis["me"]
    if not rounds:
        return out

    def add(title: str, text: str, tone: str = "plain") -> None:
        out.append({"title": title, "text": text, "tone": tone})

    total = ranks["total"]

    # performance
    if summary["performance"] and ranks["performance"]:
        place = ranks["performance"]
        tone = "good" if place <= total / 3 else "plain"
        tail = ""
        if is_unrated(me["rating"]) and analysis["mode"] == BASE:
            tail = (f' That is {summary["performance"] - me["rating"]:+d} '
                    f'versus the protocol rating.')
        add("Performance",
            f'{summary["performance"]} - {ordinal(place)} best result of {total} in the tournament.'
            + tail, tone)

    # upsets
    my_upsets = analysis["my_upsets"]
    if my_upsets:
        best = analysis["upsets"][my_upsets[0] - 1]
        where = ", ".join(str(n) for n in my_upsets[:4])
        tone = "good" if my_upsets[0] <= 3 else "plain"
        spot = "place" if len(my_upsets) == 1 else "places"
        add("Tournament upsets",
            f'Your wins rank {where} on the tournament upset list '
            f'out of {len(analysis["upsets"])} ({spot}). Biggest: round {best["round"]}, '
            f'{best["loser_name"]} ({best["loser_rating"]}), a gap '
            f'{best["gap"]} '
            f'{plural(best["gap"], "point", "points")}.', tone)

    # schedule strength
    field = analysis["data"]["meta"].get("field_average")
    if summary["average"] and ranks["average"]:
        text = (f'Average opponent rating {summary["average"]} - '
                f'{ordinal(ranks["average"])} toughest schedule of {total}.')
        if field:
            text += f' Tournament average: {field}.'
        add("Field strength", text, "good" if ranks["average"] <= 5 else "plain")

    # where points were lost
    lost = [r for r in rounds if r["score"] == 0 and r["opponent_rank"]]
    if lost:
        places = sorted(r["opponent_rank"] for r in lost)
        if me["rank"] and all(p < me["rank"] for p in places):
            add("No losses \"down\"",
                "All losses came against players who finished higher: places "
                + ", ".join(str(p) for p in places) + ".", "good")
        else:
            below = [p for p in places if me["rank"] and p > me["rank"]]
            if below:
                add("Losses below",
                    "Points went to players who finished lower: places "
                    + ", ".join(str(p) for p in below)
                    + ". That is where the cheapest improvement lies.", "bad")

    # opponent rating ceiling
    beaten = [r["effective"] for r in rounds if r["score"] == 1]
    fallen = [r["effective"] for r in rounds if r["score"] == 0]
    if beaten and fallen:
        top, floor = max(beaten), min(fallen)
        if top < floor:
            add("Clean ceiling",
                f'Everyone beaten is at most {top}, everyone who beat you is at least '
                f'{floor}. The boundary lies in that gap.')
        else:
            above = [value for value in beaten if value >= floor]
            add("Ceiling",
                f'You usually lose from {floor} up, but {len(above)} '
                f'{plural(len(above), "opponent", "opponents")} '
                f'above that mark '
                f'{plural(len(above), "was", "were")} still beaten '
                f'(up to {max(above)}). The ceiling is not rigid.', "good")

    # board
    if summary["best_board"]:
        best = min((r for r in rounds if r["board"]), key=lambda r: r["board"])
        add("Highest board",
            f'Board {best["board"]} in round {best["round"]} - '
            f'{best["name"]} ({best["rating"]}).')

    # streak
    length, start, end = _streak(rounds)
    if length >= 3:
        add("Best streak",
            f'{length} {plural(length, "win", "wins")} in a row, '
            f'rounds {start}-{end}.', "good")

    # colours
    colors = summary["colors"]
    doubles = [i + 1 for i in range(len(colors) - 1) if colors[i] == colors[i + 1]]
    if doubles:
        pretty = colors
        add("Colors",
            f'{pretty} - {colors.count("W")} White, {colors.count("B")} Black. '
            f'Same colour twice in a row in rounds: '
            + ", ".join(f"{n}–{n + 1}" for n in doubles) + ".")

    # Buchholz
    if summary["buchholz"] and ranks["buchholz"]:
        add("Buchholz",
            f'{decimal(summary["buchholz"])} - {ordinal(ranks["buchholz"])} in the tournament. '
            f'This reflects how strong the opponents were, '
            f'not your own play.')

    # clubs
    clubs = Counter(r["club"] for r in rounds if r["club"])
    if clubs:
        club, count = clubs.most_common(1)[0]
        if count >= 3:
            got = sum(r["score"] for r in rounds if r["club"] == club)
            add("Most frequent opponents",
                f'{club} — {count} '
                f'{plural(count, "opponent", "opponents")} '
                f'out of {len(rounds)}, scoring '
                f'{score_text(got)} out of {count}.')

    # draws
    if not summary["draws"]:
        share = analysis["table"]["draws"] / len(analysis["data"]["games"]) * 100
        add("No draws",
            f'{summary["wins"]} '
            f'{plural(summary["wins"], "win", "wins")} and '
            f'{summary["losses"]} '
            f'{plural(summary["losses"], "loss", "losses")} '
            f'without a single draw. Draws in the tournament: {share:.0f}%.')

    # Elo expectation
    if summary["expected"] and summary["games"]:
        delta = summary["points"] - summary["expected"]
        if abs(delta) >= 1:
            word = "more" if delta > 0 else "fewer"
            add("Against expectation",
                f'Elo at rating {analysis["my_rating"]} predicted '
                f'{decimal(summary["expected"], 2)} points against this field - scored '
                f'{score_text(summary["points"])}, {decimal(abs(delta))} '
                f'{word}.', "good" if delta > 0 else "bad")

    return out


def opponent_gap(rounds: list[dict]) -> tuple[int, int] | None:
    """Widest gap in opponent ratings - if it is noticeable at all."""
    values = sorted({r["effective"] for r in rounds})
    if len(values) < 3:
        return None
    gaps = [(values[i + 1] - values[i], values[i], values[i + 1])
            for i in range(len(values) - 1)]
    widest = max(gaps)
    return (widest[1], widest[2]) if widest[0] >= 300 else None


def plural(count: int, one: str, many: str) -> str:
    """English plural by count: 1 -> one, otherwise many."""
    return one if abs(count) == 1 else many


def ordinal(number: int) -> str:
    """1 -> "1st", 2 -> "2nd", 11 -> "11th"."""
    if 11 <= number % 100 <= 13:
        return f"{number}th"
    return f"{number}{({1: 'st', 2: 'nd', 3: 'rd'}).get(number % 10, 'th')}"


def decimal(value: float, digits: int = 1) -> str:
    """Number without a trailing zero: 45.5 -> "45.5", 3.0 -> "3"."""
    text = f"{value:.{digits}f}".rstrip("0").rstrip(".")
    return text


def score_text(value: float) -> str:
    """0.5 -> "½", 5.0 -> "5", 5.5 -> "5½" - as written in protocols."""
    whole, half = divmod(round(value * 2), 2)
    if not half:
        return str(whole)
    return f"{whole}½" if whole else "½"
