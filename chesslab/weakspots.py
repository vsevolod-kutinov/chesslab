"""Key opening positions: where the player errs systematically, not just once.

A single blunder in one game is noise. But if the player keeps choosing the
same mediocre move from the same position, that is a hole in the repertoire
worth closing. So we group not games but the positions themselves by EPD:
a transposition leading to the same place counts as the same hole.

Analysis comes from the `positions` cache (see `engine.OpeningAnalyzer`) in one
batch query. A position may have no analysis at all; it still goes into the
list, just without a loss figure: that is a hint that the engine still has to
evaluate it.
"""

from __future__ import annotations

import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass

import chess

from . import eco, games_db
from .engine import TAG_THRESHOLDS, win_percent
from .explorer import Entry, _clean, _entries


@dataclass
class MoveStat:
    """One of the player's moves from a key position."""

    san: str
    games: int
    points: float       # the player's score in these games, fraction 0..1
    loss: float | None  # average loss of chances in pp, None if not analysed
    best: bool          # matches the engine's best move


@dataclass
class Spot:
    """Key position: where the player moves and what it costs them."""

    epd: str
    fen: str              # position for the board
    white: bool           # the player's color here (= side to move in the position)
    ply: int              # most frequent ply number at which it occurs
    line: list[str]       # most frequent path to the position in SAN
    name: str             # name from the opening reference, or "" if none
    games: int            # in how many of the player's games it occurred
    points: float         # the player's score share from this position, 0..1
    best_san: str | None  # engine's best move in SAN
    loss: float | None    # average loss over the player's moves, pp
    cost: float           # games * loss — total cost of this position
    moves: list[MoveStat]  # what they played, from frequent to rare


def tag_for(loss: float) -> str | None:
    """Move tag using the same thresholds as engine.build_rows."""
    for threshold, name in TAG_THRESHOLDS:
        if loss >= threshold:
            return name
    return None


@dataclass
class _Visit:
    """One visit to a position within a single game, before the analysis is counted."""

    epd_after: str
    san: str
    uci: str
    ply: int
    line: tuple[str, ...]
    points: float


def _visits(entries: list[Entry], plies: int) -> dict[str, list[_Visit]]:
    """Split games into visits to positions where the player is to move — key: EPD "before".

    The same position within one game is counted at most once — otherwise a rare
    repetition inside an opening would inflate the game count, and what we need
    is a count of games, not of visits.
    """
    by_epd: dict[str, list[_Visit]] = defaultdict(list)
    for entry in entries:
        board = chess.Board()
        seen: set[str] = set()
        for ply, san in enumerate(entry.sans[:plies]):
            her_turn = (board.turn == chess.WHITE) == entry.me_white
            epd_before = board.epd()
            try:
                move = board.parse_san(san)
            except ValueError:
                break
            uci = move.uci()
            board.push(move)
            if her_turn and epd_before not in seen:
                seen.add(epd_before)
                by_epd[epd_before].append(_Visit(
                    epd_after=board.epd(), san=san, uci=uci, ply=ply,
                    line=tuple(entry.sans[:ply]), points=entry.points,
                ))
    return by_epd


def _build_spot(epd: str, visits: list[_Visit],
                cached: dict[str, sqlite3.Row], book: eco.Book) -> Spot:
    games = len(visits)
    points = sum(v.points for v in visits) / games

    ply = Counter(v.ply for v in visits).most_common(1)[0][0]
    line = list(Counter(v.line for v in visits).most_common(1)[0][0])

    board = chess.Board()
    for san in line:
        board.push_san(san)
    white = board.turn == chess.WHITE

    opening = book.name_at(board)
    name = opening.title if opening is not None else ""

    before_row = cached.get(epd)
    cp_before = before_row["cp"] if before_row is not None else None
    best_uci = before_row["best_uci"] if before_row is not None else None

    best_san = None
    if best_uci:
        try:
            best_san = _clean(board.san(chess.Move.from_uci(best_uci)))
        except (AssertionError, ValueError):
            best_san = None

    by_san: dict[str, list[_Visit]] = defaultdict(list)
    for visit in visits:
        by_san[visit.san].append(visit)

    moves = []
    weighted_loss, weighted_games = 0.0, 0
    for san, group in by_san.items():
        san_games = len(group)
        san_points = sum(v.points for v in group) / san_games

        after_row = cached.get(group[0].epd_after)
        after_cp = after_row["cp"] if after_row is not None else None
        loss = None
        if cp_before is not None and after_cp is not None:
            before_wp = win_percent(cp_before)
            after_wp = win_percent(after_cp)
            if not white:
                before_wp, after_wp = 100 - before_wp, 100 - after_wp
            loss = max(0.0, before_wp - after_wp)
            weighted_loss += loss * san_games
            weighted_games += san_games

        moves.append(MoveStat(
            san=san, games=san_games, points=san_points, loss=loss,
            best=bool(best_uci) and group[0].uci == best_uci,
        ))
    moves.sort(key=lambda m: (-m.games, m.san))

    spot_loss = weighted_loss / weighted_games if weighted_games else None
    cost = games * spot_loss if spot_loss is not None else 0.0

    return Spot(
        epd=epd, fen=board.fen(), white=white, ply=ply, line=line, name=name,
        games=games, points=points, best_san=best_san, loss=spot_loss,
        cost=cost, moves=moves,
    )


def spots(conn: sqlite3.Connection, account: str | None = None, *, plies: int = 16,
          speed: str = "", color: str = "", min_games: int = 5) -> list[Spot]:
    """Key positions across the player's games, costlier first.

    Positions without analysis in the cache are not dropped but go to the end with
    loss=None: they show what the engine still has to evaluate.
    """
    rows = games_db.search(conn, account=account, speed=speed, color=color,
                           limit=100000)
    entries = _entries(rows, account)
    by_epd = _visits(entries, plies)

    groups = {epd: v for epd, v in by_epd.items() if len(v) >= min_games}
    if not groups:
        return []

    needed: set[str] = set(groups)
    for visits in groups.values():
        needed.update(v.epd_after for v in visits)
    cached = games_db.position_evals(conn, list(needed))

    book = eco.book()
    out = [_build_spot(epd, visits, cached, book) for epd, visits in groups.items()]
    out.sort(key=lambda s: (s.loss is None, -s.cost))
    return out


# --- leaving the book ------------------------------------------------------
#
# A separate question from key positions: not "where the player errs repeatedly" but
# "how far the game follows theory and who deviates first". A move is considered
# theoretical if it is among book.moves_from(board) for the position BEFORE the
# move — compared by UCI, without names: name_at names only the final positions
# of lines, so using it to find the exit from the book would be wrong —
# intermediate moves in the middle of a known line would have no name and
# would look "out of book".


@dataclass
class BookExit:
    """The moment a game first left theory."""

    game_id: str
    ply: int             # ply at which the book ended, zero-based
    by_me: bool          # whether the player or the opponent deviated
    san: str             # the move that left the book
    name: str            # name of the last known opening, "" if none
    eco: str
    cp: int | None       # evaluation AFTER leaving the book, from White's view
    loss: float | None   # loss of chances by this move, from the player's side, pp
    points: float        # the player's result in the game: 1.0 / 0.5 / 0.0
    me_white: bool


@dataclass
class ExitStat:
    """Aggregate for an opening that the player (or the opponent) left."""

    name: str            # the opening that was left
    eco: str
    games: int
    mine: int            # how many times the player deviated first
    avg_ply: float       # average ply of leaving the book
    points: float        # the player's score share in these games, 0..1
    loss: float | None   # average loss at the exit, over the player's exits only


@dataclass
class _Exit:
    """Where the game left the book, before the engine analysis is counted."""

    ply: int
    by_me: bool
    san: str
    name: str
    eco: str
    epd_before: str
    epd_after: str


def _find_exit(entry: Entry, book: eco.Book, plies: int) -> _Exit | None:
    """First move of the game that is not among book.moves_from(board).

    None — the game either did not leave the book within `plies`, or its moves
    failed to parse (bad data) before an exit was found.
    """
    board = chess.Board()
    for ply, san in enumerate(entry.sans[:plies]):
        her_turn = (board.turn == chess.WHITE) == entry.me_white
        book_ucis = {branch.uci for branch in book.moves_from(board)}
        try:
            move = board.parse_san(san)
        except ValueError:
            return None
        if move.uci() not in book_ucis:
            opening = book.name_at(board)
            epd_before = board.epd()
            board.push(move)
            return _Exit(
                ply=ply, by_me=her_turn, san=san,
                name=opening.name if opening is not None else "",
                eco=opening.eco if opening is not None else "",
                epd_before=epd_before, epd_after=board.epd(),
            )
        board.push(move)
    return None


def _build_exit(entry: Entry, found: _Exit,
                cached: dict[str, sqlite3.Row]) -> BookExit:
    before_row = cached.get(found.epd_before)
    after_row = cached.get(found.epd_after)
    cp_before = before_row["cp"] if before_row is not None else None
    cp_after = after_row["cp"] if after_row is not None else None

    loss = None
    if cp_before is not None and cp_after is not None:
        before_wp = win_percent(cp_before)
        after_wp = win_percent(cp_after)
        if not entry.me_white:
            before_wp, after_wp = 100 - before_wp, 100 - after_wp
        loss = max(0.0, before_wp - after_wp)

    return BookExit(
        game_id=entry.row["id"], ply=found.ply, by_me=found.by_me,
        san=found.san, name=found.name, eco=found.eco, cp=cp_after,
        loss=loss, points=entry.points, me_white=entry.me_white,
    )


def book_exits(conn: sqlite3.Connection, account: str | None = None, *, plies: int = 16,
               speed: str = "", color: str = "") -> list[BookExit]:
    """One record per game — where and who first left theory.

    Games that did not leave the book within the first `plies` plies are left out
    entirely (rather than given ply=-1) — there is nothing to show for them.
    """
    rows = games_db.search(conn, account=account, speed=speed, color=color,
                           limit=100000)
    entries = _entries(rows, account)
    book = eco.book()

    found: list[tuple[Entry, _Exit]] = []
    needed: set[str] = set()
    for entry in entries:
        exit_ = _find_exit(entry, book, plies)
        if exit_ is None:
            continue
        found.append((entry, exit_))
        needed.add(exit_.epd_before)
        needed.add(exit_.epd_after)

    cached = games_db.position_evals(conn, list(needed))
    return [_build_exit(entry, exit_, cached) for entry, exit_ in found]


def exit_stats(conn: sqlite3.Connection, account: str | None = None, *, plies: int = 16,
               speed: str = "", color: str = "",
               min_games: int = 3) -> list[ExitStat]:
    """book_exits aggregated by the opening that was left, frequent first."""
    exits = book_exits(conn, account, plies=plies, speed=speed, color=color)

    groups: dict[tuple[str, str], list[BookExit]] = defaultdict(list)
    for exit_ in exits:
        groups[(exit_.eco, exit_.name)].append(exit_)

    out = []
    for (eco_code, name), group in groups.items():
        if len(group) < min_games:
            continue
        mine = sum(1 for e in group if e.by_me)
        avg_ply = sum(e.ply for e in group) / len(group)
        points = sum(e.points for e in group) / len(group)
        my_losses = [e.loss for e in group if e.by_me and e.loss is not None]
        loss = sum(my_losses) / len(my_losses) if my_losses else None
        out.append(ExitStat(name=name, eco=eco_code, games=len(group),
                            mine=mine, avg_ply=avg_ply, points=points,
                            loss=loss))
    out.sort(key=lambda s: -s.games)
    return out


def exit_depths(exits: list[BookExit]) -> dict[int, int]:
    """Distribution "ply of exit -> number of games" — for the histogram."""
    counts: dict[int, int] = defaultdict(int)
    for exit_ in exits:
        counts[exit_.ply] += 1
    return dict(counts)
