"""Quick engine pass over an opponent's games: where and how he goes wrong.

Every position of each game gets a short fixed-time look; a move of the player that
drops his winning chances by LOSS_LIMIT or more (Lichess formula, `engine.win_percent`)
is a mistake. Only the latest games are checked, so a whole player stays within a
couple of minutes.

    python -m chesslab.prep analyse <file.pgn> "<Last, First>" [--games 10]
"""

from __future__ import annotations

import argparse
import collections
import io
import logging
import shutil
from pathlib import Path

import chess
import chess.engine
import chess.pgn

from ..engine import win_percent
from . import games as player_games

LOSS_LIMIT = 15.0
OPENING_PLIES = 30
ENDGAME_MATERIAL = 13  # non-pawn material of both sides together, in pawns
_VALUES = {chess.KNIGHT: 3, chess.BISHOP: 3, chess.ROOK: 5, chess.QUEEN: 9}
DEFAULT_ENGINE = shutil.which("stockfish") or str(Path.home() / ".local" / "bin" / "stockfish")


def _phase(board: chess.Board, ply: int) -> str:
    if ply < OPENING_PLIES:
        return "opening"
    material = sum(len(board.pieces(kind, colour)) * value
                   for kind, value in _VALUES.items() for colour in chess.COLORS)
    return "endgame" if material <= ENDGAME_MATERIAL else "middlegame"


def _white_chances(info: chess.engine.InfoDict) -> float:
    return win_percent(info["score"].white().score(mate_score=100000))


def mistakes(pgn: str, name: str, engine_path: str, seconds: float = 0.08,
             limit: int = 10) -> list[dict]:
    """The player's moves that lost LOSS_LIMIT+ winning chances, newest games first."""
    found = player_games.games(pgn, name, by_id=True)[:limit]
    out = []
    with chess.engine.SimpleEngine.popen_uci(engine_path) as engine:
        for game, is_white in found:
            board = game.board()
            sans: list[str] = []
            before = engine.analyse(board, chess.engine.Limit(time=seconds))
            for ply, move in enumerate(game.mainline_moves()):
                mover_white = board.turn == chess.WHITE
                san = board.san(move)
                phase = _phase(board, ply)
                fen = board.fen()
                line = " ".join(sans)
                best = before.get("pv", [None])[0]
                best_san = board.san(best) if best else ""
                board.push(move)
                sans.append(san)
                if board.is_game_over():
                    after_chances = 100.0 if board.is_checkmate() else 50.0
                    after_chances = after_chances if mover_white else 100 - after_chances
                    after = None
                else:
                    after = engine.analyse(board, chess.engine.Limit(time=seconds))
                    after_chances = _white_chances(after)
                if mover_white == is_white:
                    was = _white_chances(before)
                    loss = (was - after_chances) if mover_white else (after_chances - was)
                    if loss >= LOSS_LIMIT and san != best_san:
                        h = game.headers
                        out.append({
                            "date": h.get("Date", ""),
                            "opponent": h.get("Black" if is_white else "White", "?"),
                            "colour": "white" if is_white else "black",
                            "move_no": ply // 2 + 1,
                            "phase": phase,
                            "fen": fen,
                            "played": san,
                            "best": best_san,
                            "loss": round(loss, 1),
                            "line": line,
                        })
                if after is None:
                    break
                before = after
    return out


def render(found: list[dict], games_checked: int) -> str:
    if not games_checked:
        return "No games to analyse."
    phases = collections.Counter(m["phase"] for m in found)
    lines = [
        f"{games_checked} games checked, {len(found)} mistakes "
        f"({len(found) / games_checked:.1f} per game): "
        + ", ".join(f"{phase} {phases[phase]}" for phase in ("opening", "middlegame", "endgame")),
        "Worst (line: = moves up to the position before the mistake):",
    ]
    for m in sorted(found, key=lambda m: -m["loss"])[:8]:
        lines.append(
            f"  {m['date']} vs {m['opponent']} ({m['colour']}), move {m['move_no']} {m['phase']}: "
            f"played {m['played']}, best {m['best']}, -{m['loss']}%  line:{m['line'].replace(' ', '_')}"
            f"  FEN {m['fen']}"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Engine pass over a player's games.")
    parser.add_argument("pgn", help="PGN file from the games command")
    parser.add_argument("name", help='"Last, First"')
    parser.add_argument("--games", type=int, default=10, help="latest games to check")
    parser.add_argument("--engine", default=DEFAULT_ENGINE)
    args = parser.parse_args(argv)
    logging.getLogger("chess.pgn").setLevel(logging.CRITICAL)
    pgn = Path(args.pgn).read_text(encoding="utf-8", errors="replace")
    checked = min(args.games, len(player_games.games(pgn, args.name, by_id=True)))
    print(render(mistakes(pgn, args.name, args.engine, limit=args.games), checked))
    return 0
