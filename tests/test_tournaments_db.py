import unittest

from chesslab import tournaments_db


class DateTest(unittest.TestCase):
    def test_formats(self):
        self.assertEqual(tournaments_db.normalize_date("2026-10-17"), "2026-10-17")
        self.assertEqual(tournaments_db.normalize_date("17.10.2026"), "2026-10-17")
        self.assertEqual(tournaments_db.normalize_date(" 2026.10.17 "), "2026-10-17")
        self.assertEqual(tournaments_db.normalize_date(""), "")

    def test_impossible_date(self):
        self.assertIsNone(tournaments_db.normalize_date("2026-17-10"))
        self.assertIsNone(tournaments_db.normalize_date("tomorrow"))


if __name__ == "__main__":
    unittest.main()


class NamesTest(unittest.TestCase):
    def test_known_names(self):
        import sqlite3
        from chesslab import games_db
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(games_db.SCHEMA)
        games_db._migrate(conn)
        conn.execute("INSERT INTO games (id, account, service, played_at, white, black)"
                     " VALUES ('1', 'me', 'otb', 0, 'Kutinov, Vsevolod', 'Martin, Azarii')")
        conn.execute("INSERT INTO games (id, account, service, played_at, white, black)"
                     " VALUES ('2', 'x', 'lichess', 0, 'nick', 'other')")
        conn.execute("INSERT INTO tournaments (name, player) VALUES ('T', 'Kutinov, Vsevolod')")
        self.assertEqual(tournaments_db.known_names(conn), ["Kutinov, Vsevolod", "Martin, Azarii"])


class TrimTest(unittest.TestCase):
    def test_lonely_old_game_is_trimmed(self):
        from chesslab.rating import trim_lonely_start
        year = 365 * 86_400_000
        points = [(0, 1500)] + [(6 * year + i, 1600 + i) for i in range(40)]
        kept, hidden = trim_lonely_start(points)
        self.assertEqual(len(kept), 40)
        self.assertEqual(hidden, [(0, 1500)])

    def test_two_real_periods_are_kept(self):
        from chesslab.rating import trim_lonely_start
        year = 365 * 86_400_000
        points = [(i, 1500) for i in range(30)] + [(3 * year + i, 1600) for i in range(30)]
        self.assertEqual(trim_lonely_start(points), (points, []))


class FreshnessTest(unittest.TestCase):
    def test_old_data_is_flagged(self):
        from datetime import datetime, timedelta, timezone
        from chesslab.stats import freshness
        now = datetime(2026, 11, 2, tzinfo=timezone.utc)
        old = (now - timedelta(days=59)).isoformat()
        self.assertEqual(freshness("Lichess", old, now),
                         ("data from Lichess as of 2026-09-04 — 59 days old, press Refresh", True))
        new = (now - timedelta(days=1)).isoformat()
        self.assertEqual(freshness("Lichess", new, now), ("data from Lichess as of 2026-11-01", False))
        self.assertEqual(freshness("Lichess", None, now), ("", False))
