import sqlite3
import tempfile
import unittest
from pathlib import Path

from chesslab import games_db, storage
from chesslab.prep import store


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(games_db.SCHEMA)

    def test_roundtrip(self):
        first = store.create(self.conn)
        second = store.create(self.conn, "Humenné · board 7")
        store.save(self.conn, first, title="Kapušany", transcript=[{"role": "user", "text": "hi"}])
        rows = store.all_preps(self.conn)
        self.assertEqual([r["id"] for r in rows], [second, first])
        self.assertEqual(store.transcript(rows[1]), [{"role": "user", "text": "hi"}])
        self.assertEqual(rows[1]["title"], "Kapušany")
        self.assertTrue(rows[0]["session"])

    def test_folder_is_created(self):
        with tempfile.TemporaryDirectory() as root:
            old, storage.DATA_DIR = storage.DATA_DIR, Path(root)
            try:
                self.assertTrue(store.folder(5).is_dir())
                self.assertEqual(store.folder(5), Path(root) / "prep" / "5")
            finally:
                storage.DATA_DIR = old


if __name__ == "__main__":
    unittest.main()
