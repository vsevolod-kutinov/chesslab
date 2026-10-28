import unittest
from pathlib import Path

from chesslab.prep import lineup

DATA = Path(__file__).parent / "data"


def page(name):
    return (DATA / name).read_text(encoding="utf-8")


class LineupTest(unittest.TestCase):
    def test_roster_has_fide_ids(self):
        players = {p["name"]: p for p in lineup.roster(page("cr_1464725_art16.html"))}
        self.assertEqual(players["Bilik, Matej"]["fide_id"], "14964619")
        self.assertEqual(players["Bilik, Matej"]["team"], "Humenné Reinter C")
        self.assertIn("Muller, Tomas", players)

    def test_board_pairings_round1(self):
        boards = lineup.board_pairings(page("cr_1464725_art3_rd1.html"))
        match = [b for b in boards if b["away"] == "Humenné Reinter C"]
        self.assertEqual(len(match), 8)
        seventh = match[6]
        self.assertEqual(seventh["board"], 7)
        self.assertEqual(seventh["away_player"], "Muller, Tomas")
        self.assertEqual(seventh["home_player"], "Kiraly, Pavol")
        self.assertFalse(seventh["away_white"])  # Moldava had white on board 7

    def test_no_player_is_empty(self):
        boards = lineup.board_pairings(page("cr_1464725_art3_rd1.html"))
        b = [b for b in boards if b["away"] == "TJ Slávia TU Košice" and b["board"] == 7][0]
        self.assertEqual(b["away_player"], "")

    def test_team_lookup_ignores_diacritics(self):
        self.assertTrue(lineup.same_team("humenne", "Humenné Reinter C"))
        self.assertFalse(lineup.same_team("humenne", "Prvý ŠK Prešov"))
        self.assertTrue(lineup.same_team("Humenné C", "Humenné Reinter C"))
        self.assertFalse(lineup.same_team("Humenné B", "Humenné Reinter C"))

    def test_table_without_rounds(self):
        text = lineup.render({"team": "X", "roster": [], "rounds": {}})
        self.assertIn("no rounds played yet", text)


if __name__ == "__main__":
    unittest.main()
