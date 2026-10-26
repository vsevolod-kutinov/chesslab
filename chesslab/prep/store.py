"""Saved preps: one row per prep in the ChessLab database, one folder for its files.

The folder (`DATA_DIR/prep/<id>/`) is the assistant's working directory: the PGNs it
downloads and `cards.json` live there, so a prep reopened on match day has everything.
"""

from __future__ import annotations

import json
import sqlite3
import time
import uuid
from pathlib import Path

from .. import storage


def create(conn: sqlite3.Connection, title: str = "New prep") -> int:
    cursor = conn.execute(
        "INSERT INTO preps (title, created_at, session) VALUES (?, ?, ?)",
        (title, int(time.time()), str(uuid.uuid4())),
    )
    conn.commit()
    return cursor.lastrowid


def all_preps(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute("SELECT * FROM preps ORDER BY id DESC").fetchall()


def get(conn: sqlite3.Connection, prep_id: int) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM preps WHERE id = ?", (prep_id,)).fetchone()


def save(conn: sqlite3.Connection, prep_id: int, title: str | None = None,
         transcript: list[dict] | None = None) -> None:
    if title is not None:
        conn.execute("UPDATE preps SET title = ? WHERE id = ?", (title, prep_id))
    if transcript is not None:
        conn.execute("UPDATE preps SET transcript = ? WHERE id = ?",
                     (json.dumps(transcript, ensure_ascii=False), prep_id))
    conn.commit()


def transcript(row: sqlite3.Row) -> list[dict]:
    try:
        return json.loads(row["transcript"] or "[]")
    except ValueError:
        return []


def folder(prep_id: int) -> Path:
    # computed every time: tests override storage.DATA_DIR
    path = storage.DATA_DIR / "prep" / str(prep_id)
    path.mkdir(parents=True, exist_ok=True)
    return path
