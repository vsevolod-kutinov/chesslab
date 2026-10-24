import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

from chesslab.prep import analyse, mine

ENGINE = shutil.which("stockfish") or str(Path.home() / ".local/bin/stockfish")

PGN = """[White "Someone, Else"]
[Black "Muller, Tomas"]
[Result "*"]

1. e4 e5 2. Nf3 Qh4 3. Nxh4 *
"""


@unittest.skipUnless(os.access(ENGINE, os.X_OK), "no stockfish")
class AnalyseTest(unittest.TestCase):
    def test_mistakes_finds_blunder(self):
        found = analyse.mistakes(PGN, "Muller, Tomas", ENGINE, seconds=0.05)
        self.assertEqual([(m["move_no"], m["played"], m["phase"]) for m in found],
                         [(2, "Qh4", "opening")])
        self.assertEqual(found[0]["line"], "e4 e5 Nf3")


class MineTest(unittest.TestCase):
    def test_mine_summary_on_empty_db(self):
        with tempfile.TemporaryDirectory() as folder:
            conn = sqlite3.connect(Path(folder) / "g.sqlite3")
            conn.row_factory = sqlite3.Row
            from chesslab import games_db
            conn.executescript(games_db.SCHEMA)
            games_db._migrate(conn)
            self.assertIn("no own games", mine.summary(conn))

    def test_mine_summary_counts_lines(self):
        from chesslab import games_db
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(games_db.SCHEMA)
        games_db._migrate(conn)
        for i, winner in enumerate(("white", "black", "")):
            conn.execute("INSERT INTO games (id, account, played_at, white, black, winner, speed, moves)"
                         " VALUES (?, 'me', ?, 'me', 'x', ?, 'blitz', 'e4 c5 Nf3 d6 d4 cxd4 Nxd4')",
                         (str(i), i, winner))
        text = mine.summary(conn)
        self.assertIn("e4 c5 Nf3 d6 d4 cxd4  x3  50%", text)


if __name__ == "__main__":
    unittest.main()
