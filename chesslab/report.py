"""Game analysis as text you can hand to a person or a chatbot.

The engine computes in numbers, explanations come in words. Here the numbers
from the analysis table become markdown: a game header, move by move with
evaluation and loss, and a separate list of the moments where the game broke.
Such a file can be pasted into a chat with the request "explain this in plain
words": it has everything needed, and no engine or database is required.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import chess
import chess.pgn

from . import storage
from .engine import move_accuracy

TAG_WORDS = {"blunder": "blunder", "mistake": "mistake", "inaccuracy": "inaccuracy"}
# for the tally: "blunders 2", "mistakes 1"
TAG_COUNTED = {"blunder": "blunders", "mistake": "mistakes", "inaccuracy": "inaccuracies"}
TAG_MARKS = {"blunder": "??", "mistake": "?", "inaccuracy": "?!"}
TAG_ORDER = ("blunder", "mistake", "inaccuracy")

SPEED_WORDS = {"bullet": "bullet", "blitz": "blitz", "rapid": "rapid",
               "classical": "classical", "correspondence": "correspondence"}

# a request to whoever reads this: without it the file looks like a dry table
BRIEF = (
    "Explain this game in plain words: where I went wrong and why, which ideas "
    "I missed, and what the plan was overall. Do not retell the moves one by "
    "one — explain the turning points and what to do about them next.\n\n"
    "The numbers below were computed by Stockfish. The evaluation is in pawns "
    "from White's point of view (plus = better for White). \"Loss\" is how many "
    "percentage points of winning chances the move threw away, according to the engine."
)


def score_text(row) -> str:
    """Evaluation after the move, from White's point of view."""
    if row is None:
        return "—"
    if row["mate"] is not None:
        mate = row["mate"]
        return f"#{mate}" if mate > 0 else f"#−{abs(mate)}"
    cp = row["cp"]
    if cp is None:
        return "—"
    return f"{cp / 100:+.2f}".replace("-", "−")


def move_number(ply: int, start_board: chess.Board) -> str:
    """"12." or "12…" — the usual way to write a ply number."""
    index = start_board.ply() + ply - 1
    number = index // 2 + 1
    return f"{number}." if index % 2 == 0 else f"{number}…"


def accuracy(losses: list[float]) -> float:
    return sum(move_accuracy(loss) for loss in losses) / len(losses) if losses else 0.0


def build(game: chess.pgn.Game, start_fen: str, moves: list[chess.Move],
          analysis: dict, me: bool | None, depth: int | None = None) -> str:
    """Markdown report for an analysed game."""
    headers = game.headers
    start = chess.Board(start_fen)

    lines: list[str] = []
    white = headers.get("White", "?")
    black = headers.get("Black", "?")
    lines.append(f"# Game: {white} — {black}")
    lines.append("")
    lines.append(BRIEF)
    lines.append("")
    lines += _head_block(headers, me, depth)
    lines.append("")

    walk = _walk(start, moves, analysis)
    lines += _totals_block(walk, me)
    lines.append("")
    lines += _moments_block(walk)
    lines.append("")
    lines += _moves_block(walk)
    lines.append("")
    lines.append("## PGN")
    lines.append("")
    lines.append("```")
    lines.append(str(game).strip())
    lines.append("```")
    return "\n".join(lines) + "\n"


def _walk(start: chess.Board, moves: list[chess.Move], analysis: dict) -> list[dict]:
    """Unroll the game into a flat list: move, evaluation, loss, best move.

    Walk the board once: SAN for both the played and the best move has to
    be computed in the position before the move; afterwards it cannot be restored.
    """
    board = start.copy()
    out: list[dict] = []
    for index, move in enumerate(moves):
        ply = index + 1
        row = analysis.get(ply)
        before = analysis.get(ply - 1)
        san = board.san(move)

        # the row's best_uci is the best move INSTEAD of the played one: the engine
        # evaluated it in the same position as our move
        best_san = ""
        best_uci = row["best_uci"] if row is not None else None
        if best_uci:
            try:
                best = chess.Move.from_uci(best_uci)
                if best in board.legal_moves and best != move:
                    best_san = board.san(best)
            except ValueError:
                best_san = ""

        out.append({
            "ply": ply,
            "number": move_number(ply, start),
            "white": board.turn == chess.WHITE,
            "san": san,
            "row": row,
            "before": before,
            "best_san": best_san,
            "fen": board.fen(),
            "loss": (row["loss"] if row is not None else None) or 0.0,
            "tag": (row["tag"] if row is not None else "") or "",
        })
        board.push(move)
    return out


def _head_block(headers, me: bool | None, depth: int | None) -> list[str]:
    site = headers.get("Site", "")
    date = headers.get("Date", "") or headers.get("UTCDate", "")
    speed = SPEED_WORDS.get(headers.get("Event", "").lower(), "")
    control = headers.get("TimeControl", "")
    opening = " ".join(x for x in (headers.get("ECO", ""),
                                   headers.get("Opening", "")) if x).strip()

    out = [
        f"- White: {headers.get('White', '?')} ({headers.get('WhiteElo', '?')})",
        f"- Black: {headers.get('Black', '?')} ({headers.get('BlackElo', '?')})",
        f"- Result: {headers.get('Result', '*')}",
    ]
    if me is not None:
        out.append(f"- I played {'White' if me == chess.WHITE else 'Black'}")
    if opening:
        out.append(f"- Opening: {opening}")
    if date:
        out.append(f"- Date: {date}")
    if control or speed:
        out.append(f"- Time control: {' '.join(x for x in (speed, control) if x)}")
    if site:
        out.append(f"- Source: {site}")
    if depth:
        out.append(f"- Analysis depth: {depth}")
    return out


def _totals_block(walk: list[dict], me: bool | None) -> list[str]:
    out = ["## Summary"]
    for color, title in ((chess.WHITE, "White"), (chess.BLACK, "Black")):
        moves = [item for item in walk if item["white"] == (color == chess.WHITE)
                 and item["row"] is not None]
        if not moves:
            continue
        losses = [item["loss"] for item in moves]
        tally = {tag: sum(1 for item in moves if item["tag"] == tag)
                 for tag in TAG_ORDER}
        mine = " (me)" if me is not None and me == color else ""
        parts = [f"accuracy {accuracy(losses):.0f}%"]
        for tag in TAG_ORDER:
            if tally[tag]:
                parts.append(f"{TAG_COUNTED[tag]} {tally[tag]}")
        out.append(f"- {title}{mine}: " + ", ".join(parts))
    return out


def _moments_block(walk: list[dict]) -> list[str]:
    bad = [item for item in walk if item["tag"]]
    out = ["## Where it went wrong"]
    if not bad:
        out.append("")
        out.append("No blunders or mistakes — the engine is happy with both sides.")
        return out

    for item in bad:
        mark = TAG_MARKS.get(item["tag"], "")
        word = TAG_WORDS.get(item["tag"], item["tag"])
        out.append("")
        out.append(f"### {item['number']} {item['san']}{mark} — {word}")
        out.append(f"- evaluation was {score_text(item['before'])}, "
                   f"became {score_text(item['row'])} "
                   f"(lost {item['loss']:.0f}% of winning chances)")
        if item["best_san"]:
            out.append(f"- the engine wanted {item['number']} {item['best_san']}")
        out.append(f"- position before the move: `{item['fen']}`")
    return out


def _moves_block(walk: list[dict]) -> list[str]:
    out = ["## Moves", "",
           "| move | eval | loss | mark | better was |",
           "| --- | --- | --- | --- | --- |"]
    for item in walk:
        mark = TAG_MARKS.get(item["tag"], "")
        loss = f"{item['loss']:.0f}%" if item["loss"] >= 1 else ""
        out.append(
            f"| {item['number']} {item['san']}{mark} "
            f"| {score_text(item['row'])} | {loss} "
            f"| {TAG_WORDS.get(item['tag'], '')} | {item['best_san']} |"
        )
    return out


_UNSAFE = re.compile(r"[^\w.-]+", re.UNICODE)


def file_name(game: chess.pgn.Game) -> str:
    date = (game.headers.get("Date", "") or "").replace(".", "-")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        date = datetime.now().strftime("%Y-%m-%d")
    who = f"{game.headers.get('White', 'white')}-{game.headers.get('Black', 'black')}"
    return f"{date}-{_UNSAFE.sub('_', who)[:60]}.md"


def reports_dir() -> Path:
    return storage.DATA_DIR / "reports"


def save(game: chess.pgn.Game, text: str) -> Path:
    """Save the report next to the database: the path can simply be named in a chat."""
    folder = reports_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / file_name(game)
    path.write_text(text, encoding="utf-8")
    return path
