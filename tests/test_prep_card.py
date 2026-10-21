import json
import tempfile
import unittest
from pathlib import Path

from chesslab.prep import card

DATA = Path(__file__).parent / "data"


class CardTest(unittest.TestCase):
    def test_parse_profile(self):
        info = card.parse_profile((DATA / "fide_14964619.html").read_text(encoding="utf-8"))
        self.assertEqual(info["name"], "Bilik, Matej")
        self.assertEqual(info["fide_id"], "14964619")
        self.assertEqual(info["birth_year"], 2010)
        self.assertEqual(info["fed"], "Slovakia")
        self.assertEqual(info["title"], "")
        self.assertEqual((info["std"], info["rapid"], info["blitz"]), (1969, 1917, 1822))
        self.assertEqual(info["history"][0], ["2026-Nov", 1969])
        self.assertLessEqual(len(info["history"]), 12)

    def test_player_games_from_his_side(self):
        from chesslab.prep import lineup
        boards = lineup.board_pairings((DATA / "cr_1464725_art3_rd1.html").read_text(encoding="utf-8"))
        games = card.player_games([(1, boards)], "Muller, Tomas")
        self.assertEqual(games, [{"round": 1, "board": 7, "opponent": "Kiraly, Pavol",
                                  "colour": "black", "points": 1.0, "forfeit": False}])

    def test_save_card_replaces_same_id(self):
        with tempfile.TemporaryDirectory() as folder:
            card.save_card(Path(folder), {"fide_id": "1", "name": "A", "std": 1})
            card.save_card(Path(folder), {"fide_id": "1", "name": "A", "std": 2})
            card.save_card(Path(folder), {"fide_id": "", "name": "B"})
            cards = json.loads((Path(folder) / "cards.json").read_text())
        self.assertEqual([(c["name"], c.get("std")) for c in cards], [("A", 2), ("B", None)])


if __name__ == "__main__":
    unittest.main()
