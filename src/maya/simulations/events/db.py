"""The one SQLite connection every events module shares.

Own connection, own tables -- kept separate from the other domains' db.py
on purpose, same reasoning they follow: one domain's schema changes should
never risk breaking another's. All of them write to the same maya.db file;
the table names here (venues, sections, event_series, event_bookings,
tickets_sold) don't collide with theirs.

Reference data (venues, sections, the weekly event series) lives in the CSV
files under data/ -- that is the source of truth, reloaded on every start.
Bookings and tickets sold per performance are real state and survive a
restart; they are only cleared by an explicit reset_world() call.
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
    """An empty cell means "none" -- used for non-refundable events'
    refund_cutoff_hours."""
    return int(value) if value.strip() else None


#: Column name -> converter, applied when a CSV cell isn't already a string.
_CONVERTERS = {
    "capacity": int,
    "price_factor": float,
    "duration_minutes": int,
    "base_price": int,
    "min_age": int,
    "max_tickets_per_order": int,
    "refund_cutoff_hours": _optional_int,
    "popularity": float,
}

#: Table name -> (CSV file stem, columns).
_REFERENCE_TABLES = {
    "venues": ("venues", ("code", "name", "zone", "venue_type")),
    "sections": ("sections", ("section_id", "venue", "name", "capacity", "price_factor")),
    "event_series": (
        "events",
        (
            "event_id", "title", "category", "genre", "venue", "days", "start",
            "duration_minutes", "base_price", "min_age", "max_tickets_per_order",
            "refund_cutoff_hours", "popularity",
        ),
    ),
}


def _load_csv(stem: str, columns: tuple[str, ...]) -> list[tuple]:
    with open(DATA_DIR / f"{stem}.csv", newline="") as f:
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
        CREATE TABLE IF NOT EXISTS venues (
            code TEXT PRIMARY KEY, name TEXT NOT NULL, zone TEXT NOT NULL,
            venue_type TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sections (
            section_id TEXT PRIMARY KEY, venue TEXT NOT NULL, name TEXT NOT NULL,
            capacity INTEGER NOT NULL, price_factor REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS event_series (
            event_id TEXT PRIMARY KEY, title TEXT NOT NULL, category TEXT NOT NULL,
            genre TEXT NOT NULL, venue TEXT NOT NULL, days TEXT NOT NULL,
            start TEXT NOT NULL, duration_minutes INTEGER NOT NULL,
            base_price INTEGER NOT NULL, min_age INTEGER NOT NULL,
            max_tickets_per_order INTEGER NOT NULL, refund_cutoff_hours INTEGER,
            popularity REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS event_bookings (
            reference TEXT PRIMARY KEY, data TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS tickets_sold (
            event_id TEXT NOT NULL, date TEXT NOT NULL, section_id TEXT NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (event_id, date, section_id)
        );
        """
    )
    global _reference_loaded
    if not _reference_loaded:
        # Once per process, not on every thread's first connection: the HTTP
        # server opens a connection per worker thread, and rewriting the
        # reference tables each time is needless write-lock traffic.
        for table, (stem, columns) in _REFERENCE_TABLES.items():
            rows = _load_csv(stem, columns)
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
    count = conn.execute("SELECT COUNT(*) AS n FROM event_bookings").fetchone()["n"]
    conn.execute("DELETE FROM event_bookings")
    conn.execute("DELETE FROM tickets_sold")
    conn.commit()
    return count
