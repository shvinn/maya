"""The one SQLite connection every delivery module shares.

Own connection, own tables -- kept separate from flights/db.py on purpose,
same reasoning flights already follows for itself: one domain's schema
changes should never risk breaking another's. Both write to the same
maya.db file; SQLite handles independent connections to one file fine, and
the table names here (vendors, menu_items, orders) don't collide with
flights' (airports, airlines, aircraft, schedule, bookings, seats_sold).

Reference data (vendors, menu items) lives in the CSV files under data/ --
that is the source of truth for it, not the database, and it is meant to be
readable and editable directly. Every process start reloads those tables
from CSV.

Orders are different: that is real state, and the whole point of this
module is that it survives a restart. That table is never cleared except by
an explicit reset_world() call. Stock is unlimited in this domain, so there
is no seats_sold-style depletion table to go with it.
"""

from __future__ import annotations

import csv
import sqlite3
import threading
from pathlib import Path


def _repo_root() -> Path:
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").exists():
            return candidate
    raise RuntimeError("Could not find repo root (no pyproject.toml in any parent directory).")


DB_PATH = _repo_root() / "maya.db"
DATA_DIR = Path(__file__).resolve().parent / "data"

#: Column name -> converter, applied when a CSV cell isn't already a string.
_CONVERTERS = {"prep_minutes": int, "delivery_fee": int, "price": int}

_REFERENCE_TABLES = {
    "vendors": ("code", "name", "zone", "cuisine", "style", "prep_minutes", "delivery_fee"),
    "menu_items": ("item_id", "vendor", "name", "category", "price", "diet"),
}


def _load_csv(table: str, columns: tuple[str, ...]) -> list[tuple]:
    with open(DATA_DIR / f"{table}.csv", newline="") as f:
        reader = csv.DictReader(f)
        return [
            tuple(_CONVERTERS.get(c, str)(row[c]) for c in columns)
            for row in reader
        ]


#: The MCP server runs tool calls in a worker-thread pool, and a sqlite3
#: connection can only be used on the thread that created it -- so each
#: thread gets its own connection to the same file rather than sharing one.
_local = threading.local()


def connect() -> sqlite3.Connection:
    """This thread's connection, opening and initialising it on first use."""
    conn = getattr(_local, "connection", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        _init(conn)
        _local.connection = conn
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS vendors (
            code TEXT PRIMARY KEY, name TEXT NOT NULL, zone TEXT NOT NULL,
            cuisine TEXT NOT NULL, style TEXT NOT NULL,
            prep_minutes INTEGER NOT NULL, delivery_fee INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS menu_items (
            item_id TEXT PRIMARY KEY, vendor TEXT NOT NULL, name TEXT NOT NULL,
            category TEXT NOT NULL, price INTEGER NOT NULL, diet TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            order_id TEXT PRIMARY KEY, data TEXT NOT NULL
        );
        """
    )
    for table, columns in _REFERENCE_TABLES.items():
        rows = _load_csv(table, columns)
        conn.execute(f"DELETE FROM {table}")
        if rows:
            placeholders = ", ".join("?" for _ in columns)
            conn.executemany(
                f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                rows,
            )
    conn.commit()


def reset_world() -> int:
    """Clear all dynamic state. Reference data is untouched -- it isn't state.

    Returns the number of orders cleared.
    """
    conn = connect()
    count = conn.execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"]
    conn.execute("DELETE FROM orders")
    conn.commit()
    return count
