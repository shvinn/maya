"""The one SQLite connection every dining module shares.

Own connection, own tables -- kept separate from the other domains' db.py
on purpose, same reasoning they follow: one domain's schema changes should
never risk breaking another's. All of them write to the same maya.db file;
the table names here (dining_terms, dining_reservations, covers_booked)
don't collide with theirs.

Reference data (each dine-in restaurant's hours, seats and policies) lives
in data/dining.csv -- that is the source of truth, reloaded on every start.
The restaurants themselves and their menus aren't dining's: they live in
world.places. Reservations and the covers they hold per slot are real state
and survive a restart; they are only cleared by an explicit reset_world()
call.
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

#: Column name -> converter, applied when a CSV cell isn't already a string.
_CONVERTERS = {
    "covers_per_slot": int,
    "max_party": int,
    "sitting_minutes": int,
    "popularity": float,
    "deposit_per_guest": int,
    "cancel_cutoff_hours": int,
}

#: Table name -> (CSV file stem, columns).
_REFERENCE_TABLES = {
    "dining_terms": (
        "dining",
        (
            "code", "opens", "closes", "closed_days", "covers_per_slot", "max_party",
            "sitting_minutes", "popularity", "deposit_per_guest", "cancel_cutoff_hours",
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
        CREATE TABLE IF NOT EXISTS dining_terms (
            code TEXT PRIMARY KEY, opens TEXT NOT NULL, closes TEXT NOT NULL,
            closed_days TEXT NOT NULL, covers_per_slot INTEGER NOT NULL,
            max_party INTEGER NOT NULL, sitting_minutes INTEGER NOT NULL,
            popularity REAL NOT NULL, deposit_per_guest INTEGER NOT NULL,
            cancel_cutoff_hours INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS dining_reservations (
            reference TEXT PRIMARY KEY, data TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS covers_booked (
            restaurant TEXT NOT NULL, date TEXT NOT NULL, time TEXT NOT NULL,
            count INTEGER NOT NULL,
            PRIMARY KEY (restaurant, date, time)
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


def reset_world(conn: sqlite3.Connection) -> int:
    """Clear all dynamic state. Reference data is untouched -- it isn't state.

    Runs on ``conn`` and doesn't commit: a world reset clears every domain
    in one transaction (see maya.reset), so no booking can land between one
    domain's reset and the next.

    Returns the number of reservations cleared.
    """
    count = conn.execute("SELECT COUNT(*) AS n FROM dining_reservations").fetchone()["n"]
    conn.execute("DELETE FROM dining_reservations")
    conn.execute("DELETE FROM covers_booked")
    return count
