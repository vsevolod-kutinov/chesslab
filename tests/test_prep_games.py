import unittest

from chesslab.prep import games

PGN = """[Event "2.LIGA D 2025/26"]
[Date "2026.03.21"]
[White "Bilík, Matej"]
[Black "Smith, John"]
[Result "1-0"]

1. e4 e5 2. Nf3 1-0

[Event "MSR U12"]
[Date "2026.04.01"]
[White "Smith, John"]
[Black "Bilik, Matej"]
[Result "0-1"]

1. d4 d5 0-1
"""


class GamesTest(unittest.TestCase):
    def test_event_filter_ignores_spaces_and_case(self):
        found = games.games(PGN, "Bilik, Matej", by_id=True, event="2. liga")
        self.assertEqual(len(found), 1)
        self.assertTrue(found[0][1])

    def test_by_id_matches_diacritics(self):
        found = games.games(PGN, "Bilik, Matej", by_id=True)
        self.assertEqual([white for _, white in found], [False, True])


if __name__ == "__main__":
    unittest.main()
