"""SQLite access: one connection per request/job, autocommit, explicit transactions with `tx()`."""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path

from . import settings

SCHEMA = Path(__file__).with_name("schema.sql")


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    path = path or settings.DB_PATH
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, isolation_level=None, timeout=10, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def init(con: sqlite3.Connection) -> None:
    con.executescript(SCHEMA.read_text())


@contextmanager
def tx(con: sqlite3.Connection):
    """A write transaction: everything inside commits together or not at all."""
    con.execute("BEGIN IMMEDIATE")
    try:
        yield con
    except BaseException:
        con.execute("ROLLBACK")
        raise
    con.execute("COMMIT")


def log(con: sqlite3.Connection, action: str, item_id: int | None = None, detail: str = "") -> None:
    con.execute("INSERT INTO mod_log (action, item_id, detail) VALUES (?, ?, ?)", (action, item_id, detail))


def save_item(con: sqlite3.Connection, item_id: int | None, d: dict) -> int:
    """Insert or update an item and its cities/universities. `d` comes from forms.clean_item (or the seed).
    Call inside tx()."""
    cols = ("category_id", "name", "url", "description", "kind")
    if item_id is None:
        cur = con.execute(
            "INSERT INTO items (category_id, name, url, description, kind, status, pinned, submitted, note,"
            " approved_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                *(d[c] for c in cols),
                d.get("status", "pending"),
                int(d.get("pinned", 0)),
                int(d.get("submitted", 0)),
                d.get("note"),
                d.get("approved_at"),
            ),
        )
        item_id = cur.lastrowid
    else:
        con.execute(
            "UPDATE items SET category_id=?, name=?, url=?, description=?, kind=? WHERE id=?",
            (*(d[c] for c in cols), item_id),
        )
    con.execute("DELETE FROM item_cities WHERE item_id=?", (item_id,))
    con.executemany("INSERT INTO item_cities VALUES (?, ?)", [(item_id, c) for c in d["city_ids"]])
    con.execute("DELETE FROM item_universities WHERE item_id=?", (item_id,))
    con.executemany("INSERT INTO item_universities VALUES (?, ?)", [(item_id, u) for u in d["universities"] or [None]])
    return item_id
