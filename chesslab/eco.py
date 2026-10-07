"""Opening reference: ECO codes, names and moves.

The table is built from the lichess-org/chess-openings project and ships next
to the code in `data/eco.tsv`, so it works offline. Rebuild: `tools/build_eco.py`.

Positions are compared by EPD, so a transposition into the same position
finds the same opening. The tree is built once per process and reused.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import chess

DATA = Path(__file__).parent / "data" / "eco.tsv"

# position key. python-chess has a ready-made transposition key, about a
# hundred times cheaper than building an EPD string, and the table is run
# through it thirty thousand times. The method is private, so a fallback is kept.
if hasattr(chess.Board, "_transposition_key"):
    def position_key(board: chess.Board):
        return board._transposition_key()
else:  # pragma: no cover — does not happen on our python-chess version
    def position_key(board: chess.Board):
        return board.epd()

# Russian opening names differ from the ECO table: so that search understands
# "сицилианская" and "каро", they are mapped to the English name
SYNONYMS = {
    "сицилианская": "Sicilian",
    "сицилиан": "Sicilian",
    "дракон": "Dragon",
    "найдорф": "Najdorf",
    "челябинский": "Sveshnikov",
    "каро": "Caro-Kann",
    "каро-канн": "Caro-Kann",
    "французская": "French",
    "испанская": "Ruy Lopez",
    "берлин": "Berlin",
    "итальянская": "Italian",
    "шотландская": "Scotch",
    "венская": "Vienna",
    "русская": "Petrov",
    "защита двух коней": "Two Knights",
    "скандинавская": "Scandinavian",
    "алехин": "Alekhine",
    "пирц": "Pirc",
    "уфимцев": "Pirc",
    "современная": "Modern",
    "королевский гамбит": "King's Gambit",
    "ферзевый гамбит": "Queen's Gambit",
    "славянская": "Slav",
    "английское": "English",
    "английская": "English",
    "каталонское": "Catalan",
    "каталонская": "Catalan",
    "староиндийская": "King's Indian",
    "новоиндийская": "Queen's Indian",
    "нимцович": "Nimzo",
    "грюнфельд": "Grunfeld",
    "голландская": "Dutch",
    "лондонская": "London",
    "будапештский": "Budapest",
    "волжский": "Benko",
    "бенони": "Benoni",
    "таймановская": "Taimanov",
    "тайманов": "Taimanov",
    "паульсен": "Paulsen",
    "закрытая": "Closed",
    "открытая": "Open",
    "гамбит": "Gambit",
    "защита": "Defense",
    "атака": "Attack",
}


@dataclass(frozen=True)
class Opening:
    eco: str
    name: str
    ucis: tuple[str, ...]

    @property
    def title(self) -> str:
        return f"{self.eco} {self.name}"

    @property
    def family(self) -> str:
        """Name before the colon: "Sicilian Defense: Closed" -> "Sicilian Defense"."""
        return self.name.split(":")[0].strip()


@dataclass
class Branch:
    """What is known about one move from a position."""

    uci: str
    opening: Opening | None = None  # opening that bears this name after the move
    variants: int = 0               # how many table lines go through this move
    sample: Opening | None = None   # the shortest of them, used for the label

    @property
    def name(self) -> str:
        if self.opening is not None:
            return self.opening.name
        return self.sample.family if self.sample is not None else ""

    @property
    def eco(self) -> str:
        source = self.opening or self.sample
        return source.eco if source is not None else ""


class Book:
    def __init__(self, path: Path = DATA) -> None:
        self.openings: list[Opening] = _read(path)
        self.named: dict[str, Opening] = {}
        self.edges: dict[str, dict[str, Branch]] = {}
        self._build()

    def _build(self) -> None:
        for opening in self.openings:
            board = chess.Board()
            key = None
            for uci in opening.ucis:
                key = position_key(board)  # position before the move
                branch = self.edges.setdefault(key, {}).get(uci)
                if branch is None:
                    branch = Branch(uci)
                    self.edges[key][uci] = branch
                branch.variants += 1
                if branch.sample is None or len(opening.ucis) < len(branch.sample.ucis):
                    branch.sample = opening
                board.push(chess.Move.from_uci(uci))

            final = position_key(board)
            # a position may have several names because of transpositions —
            # keep the one reached by the shortest line
            current = self.named.get(final)
            if current is None or len(opening.ucis) < len(current.ucis):
                self.named[final] = opening
            branch = self.edges[key][opening.ucis[-1]]
            if branch.opening is None or \
                    len(self.named[final].ucis) < len(branch.opening.ucis):
                branch.opening = self.named[final]

    # --- queries ---------------------------------------------------------

    def name_at(self, board: chess.Board) -> Opening | None:
        """Name of a position. If there is no exact name, the last one along the path."""
        probe = board.copy(stack=True)
        while True:
            found = self.named.get(position_key(probe))
            if found is not None:
                return found
            if not probe.move_stack:
                return None
            probe.pop()

    def moves_from(self, board: chess.Board) -> list[Branch]:
        """Moves that lead to known openings, from frequent to rare."""
        branches = list(self.edges.get(position_key(board), {}).values())
        branches.sort(key=lambda b: (-b.variants, b.uci))
        return branches

    def search(self, text: str, limit: int = 40) -> list[Opening]:
        """Search by name or ECO code. Understands Russian names."""
        query = text.strip().lower()
        if not query:
            return []
        for russian, english in SYNONYMS.items():
            if " " in russian:  # multi-word names are replaced whole
                query = query.replace(russian, english.lower())

        words = []
        for word in query.split():
            # the word may be cut short: "сицилианс" is Sicilian
            russian = SYNONYMS.get(word) or _prefix_synonym(word)
            words.append(russian.lower() if russian else word)
        found = [
            opening for opening in self.openings
            if all(w in opening.title.lower() for w in words)
        ]

        def rank(opening: Opening) -> tuple:
            # first those whose family itself matches, then short lines:
            # "Sicilian Defense" must rank above "English … Reversed Sicilian"
            head = f"{opening.eco} {opening.family}".lower()
            return (0 if all(w in head for w in words) else 1,
                    len(opening.ucis), opening.name)

        found.sort(key=rank)

        # the same name occurs several times (different move order) —
        # in the list that looks like a duplicate
        seen, out = set(), []
        for opening in found:
            if opening.name in seen:
                continue
            seen.add(opening.name)
            out.append(opening)
            if len(out) >= limit:
                break
        return out


def _prefix_synonym(word: str) -> str:
    """A partly typed Russian word -> the English name.

    Answer only if the completion is unique: nothing to guess from "к".
    """
    if len(word) < 3 or word.isascii():
        return ""
    hits = {english for russian, english in SYNONYMS.items()
            if russian.startswith(word)}
    return hits.pop().lower() if len(hits) == 1 else ""


def _read(path: Path) -> list[Opening]:
    rows = []
    if not path.exists():
        return rows
    with path.open(encoding="utf-8") as source:
        next(source, None)  # header
        for line in source:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3 or not parts[2]:
                continue
            rows.append(Opening(parts[0], parts[1], tuple(parts[2].split())))
    return rows


_BOOK: Book | None = None


def book() -> Book:
    """One reference per process: parsing the table takes noticeable time."""
    global _BOOK
    if _BOOK is None:
        _BOOK = Book()
    return _BOOK
