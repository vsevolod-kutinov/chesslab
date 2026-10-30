import io
import unittest

import chess.pgn

from chesslab.prep.panel import line_pgn


class LinePgnTest(unittest.TestCase):
    def moves(self, text):
        return [m.uci() for m in chess.pgn.read_game(io.StringIO(line_pgn(text))).mainline_moves()]

    def test_line(self):
        self.assertEqual(self.moves("e4_c5_Nf3"), ["e2e4", "c7c5", "g1f3"])

    def test_illegal_tail_is_dropped(self):
        self.assertEqual(self.moves("e4_e5_Ke3_Nf3"), ["e2e4", "e7e5"])

    def test_check_marks_and_spaces(self):
        self.assertEqual(self.moves("e4 f6 Qh5+"), ["e2e4", "f7f6", "d1h5"])


if __name__ == "__main__":
    unittest.main()
