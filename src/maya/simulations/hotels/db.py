"""The one SQLite connection every hotels module shares.

Own connection, own tables -- kept separate from flights/db.py and
delivery/db.py on purpose, same reasoning those follow: one domain's schema
changes should never risk breaking another's. All three write to the same
maya.db file; the table names here (hotels, room_types, hotel_bookings,
rooms_sold) don't collide with theirs. ``hotel_bookings`` rather than plain
``bookings`` precisely because flights already owns that name.

Reference data (hotels, room types) lives in the CSV files under data/ --
that is the source of truth for it, not the database. Every process start
reloads those tables from CSV.

Bookings and rooms sold per night are real state and survive a restart;
they are only cleared by an explicit reset_world() call.
"""

from __future__ import annotations

import csv
import sqlite3
import threading
from pathlib import Path

from maya import storage


#: The shared maya.db -- see maya.storage for where it lives and why.
DB_PATH = storage.db_path()
DATA_DIR = Path(__file__).resolve().parent / "data"


def _optional_int(value: str) -> int | None:
    """An empty cell means "none" -- used for non-refundable hotels'
    cancel_cutoff_hours."""
    return int(value) if value.strip() else None


#: Column name -> converter, applied when a CSV cell isn't already a string.
_CONVERTERS = {
    "stars": int,
    "cancel_cutoff_hours": _optional_int,
    "max_guests": int,
    "rooms_total": int,
    "nightly_rate": int,
}

_REFERENCE_TABLES = {
    "hotels": ("code", "name", "zone", "style", "stars", "cancel_cutoff_hours"),
    "room_types": ("room_type_id", "hotel", "name", "max_guests", "rooms_total", "nightly_rate"),
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

#: Whether this process has reloaded the reference tables from CSV yet.
_reference_loaded = False


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
        CREATE TABLE IF NOT EXISTS hotels (
            code TEXT PRIMARY KEY, name TEXT NOT NULL, zone TEXT NOT NULL,
            style TEXT NOT NULL, stars INTEGER NOT NULL,
            cancel_cutoff_hours INTEGER
        );
        CREATE TABLE IF NOT EXISTS room_types (
            room_type_id TEXT PRIMARY KEY, hotel TEXT NOT NULL, name TEXT NOT NULL,
            max_guests INTEGER NOT NULL, rooms_total INTEGER NOT NULL,
            nightly_rate INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hotel_bookings (
            reference TEXT PRIMARY KEY, data TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS rooms_sold (
            room_type_id TEXT NOT NULL, night TEXT NOT NULL, count INTEGER NOT NULL,
            PRIMARY KEY (room_type_id, night)
        );
        """
    )
    global _reference_loaded
    if not _reference_loaded:
        # Once per process, not on every thread's first connection: the HTTP
        # server opens a connection per worker thread, and rewriting the
        # reference tables each time is needless write-lock traffic.
        for table, columns in _REFERENCE_TABLES.items():
            rows = _load_csv(table, columns)
            conn.execute(f"DELETE FROM {table}")
            if rows:
                placeholders = ", ".join("?" for _ in columns)
                conn.executemany(
                    f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({placeholders})",
                    rows,
                )
        _reference_loaded = True
    conn.commit()


def reset_world() -> int:
    """Clear all dynamic state. Reference data is untouched -- it isn't state.

    Returns the number of bookings cleared.
    """
    conn = connect()
    count = conn.execute("SELECT COUNT(*) AS n FROM hotel_bookings").fetchone()["n"]
    conn.execute("DELETE FROM hotel_bookings")
    conn.execute("DELETE FROM rooms_sold")
    conn.commit()
    return count
