"""Game review: every move gets a class, as in Chess.com's Game Review.

Pure logic, no Qt. Classes come from the loss of winning chances (the same
Lichess formula as accuracy), plus a few patterns on top:

- book: the game is still in the named-opening reference;
- great: the only good move — the engine's best, and its second choice
  is much worse;
- brilliant: a sound sacrifice — the best (or near-best) move leaves a piece
  en prise, and the position stays fine;
- miss: the opponent just made a mistake, and the reply did not punish it.

Chess.com's exact rules are not public, so great and brilliant are an
approximation of the same idea, not a copy.
"""

from __future__ import annotations

from dataclasses import dataclass

import chess

# order = how they are listed in the summary
KINDS = ("brilliant", "great", "best", "excellent", "good", "book",
         "inaccuracy", "mistake", "miss", "blunder")


@dataclass(frozen=True)
class Kind:
    label: str
    symbol: str  # drawn in the badge; plain glyphs, not emoji (those render in colour)
    color: str


STYLE = {
    "brilliant": Kind("Brilliant", "!!", "#26c2a3"),
    "great": Kind("Great", "!", "#5c8bb0"),
    "best": Kind("Best", "★", "#81b64c"),
    "excellent": Kind("Excellent", "✓", "#81b64c"),
    "good": Kind("Good", "✓", "#95b776"),
    "book": Kind("Book", "≡", "#a88865"),
    "inaccuracy": Kind("Inaccuracy", "?!", "#f7c631"),
    "mistake": Kind("Mistake", "?", "#ffa459"),
    "miss": Kind("Miss", "✕", "#ff7769"),
    "blunder": Kind("Blunder", "??", "#fa412d"),
}

# loss of winning chances, percentage points; the bad thresholds are the
# ones the analysis already used for its ?! / ? / ?? marks
EXCELLENT = 2.0
GOOD = 10.0
INACCURACY = 10.0
MISTAKE = 20.0
BLUNDER = 30.0

GREAT_GAP = 20.0     # best line this much better than the second one
BRILLIANT_LOSS = 2.0  # a sacrifice still has to be (almost) the best move

PIECE_VALUES = {chess.PAWN: 1, chess.KNIGHT: 3, chess.BISHOP: 3,
                chess.ROOK: 5, chess.QUEEN: 9, chess.KING: 0}


@dataclass
class Ply:
    """What the classifier needs about one move."""

    board: chess.Board  # position BEFORE the move
    move: chess.Move
    best_uci: str | None
    loss: float  # mover's loss of winning chances
    after: float  # mover's winning chances after the move, 0..100
    gap: float | None  # mover's chances: best line minus second line
    in_book: bool


def classify(ply: Ply, previous_loss: float | None) -> str:
    """previous_loss — the opponent's loss on the move just before."""
    loss = ply.loss
    if ply.in_book:
        return "book"

    if loss >= BLUNDER:
        kind = "blunder"
    elif loss >= MISTAKE:
        kind = "mistake"
    elif loss >= INACCURACY:
        kind = "inaccuracy"
    else:
        kind = ""
    if kind and kind != "blunder" and previous_loss is not None \
            and previous_loss >= MISTAKE:
        return "miss"  # a gift from the opponent went unused
    if kind:
        return kind

    is_best = ply.best_uci == ply.move.uci()
    if _mates(ply.board, ply.move):
        return "best"  # mate on the board is simply the best move
    # a sound sacrifice: the position stays at least balanced after it
    if loss < BRILLIANT_LOSS and ply.after >= 50 and _is_sacrifice(ply.board, ply.move):
        return "brilliant"
    if is_best and ply.gap is not None and ply.gap >= GREAT_GAP and ply.after >= 30 \
            and not _is_recapture(ply.board, ply.move):
        return "great"
    if is_best:
        return "best"
    if loss < EXCELLENT:
        return "excellent"
    return "good"


def _mates(board: chess.Board, move: chess.Move) -> bool:
    """Checkmate is "best": neither a great find nor a sacrifice."""
    after = board.copy(stack=False)
    after.push(move)
    return after.is_checkmate()


def _is_recapture(board: chess.Board, move: chess.Move) -> bool:
    """Taking back on the square where the opponent just captured: too obvious
    to be called great, even if every other move loses."""
    if not board.move_stack or not board.is_capture(move):
        return False
    last = board.peek()
    return last.to_square == move.to_square and board.piece_at(move.to_square) is not None


def _is_sacrifice(board: chess.Board, move: chess.Move) -> bool:
    """The moved piece is left where the opponent wins material by taking it."""
    piece = board.piece_at(move.from_square)
    if piece is None or piece.piece_type in (chess.PAWN, chess.KING) or move.promotion:
        return False
    value = PIECE_VALUES[piece.piece_type]
    taken = board.piece_at(move.to_square)
    if taken is not None and PIECE_VALUES[taken.piece_type] >= value:
        return False  # an even or winning trade is not a sacrifice

    after = board.copy(stack=False)
    after.push(move)
    defended = bool(after.attackers(not after.turn, move.to_square))
    attackers = [sq for sq in after.attackers(after.turn, move.to_square)
                 # a king cannot take a defended piece
                 if not (defended and after.piece_type_at(sq) == chess.KING)]
    if not attackers:
        return False
    cheapest = min(PIECE_VALUES[after.piece_type_at(sq)] for sq in attackers)
    gained = PIECE_VALUES[taken.piece_type] if taken else 0
    # undefended, or the cheapest attacker is worth less than what it takes
    return (not defended or cheapest < value) and value - gained >= 2


def tally(kinds: list[tuple[bool, str]]) -> dict[bool, dict[str, int]]:
    """(white_moved, kind) pairs -> counts per side."""
    out: dict[bool, dict[str, int]] = {chess.WHITE: {}, chess.BLACK: {}}
    for white, kind in kinds:
        if kind:
            out[white][kind] = out[white].get(kind, 0) + 1
    return out
