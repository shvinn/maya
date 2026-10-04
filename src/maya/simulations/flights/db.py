"""The one SQLite connection every flights module shares.

Reference data (airports, airlines, aircraft, schedule) lives in the CSV
files under ``data/`` -- that is the source of truth for it, not the
database, and it is meant to be readable and editable directly. Every
process start reloads those tables from CSV, so editing a row in a CSV file
changes the world on next run, same as editing a row of Python used to.

Bookings and seats sold are different: that is real state, and the whole
point of this module is that it survives a restart. Those tables are never
cleared except by an explicit ``reset_world()`` call.
"""

from __future__ import annotations

import csv
import os
import sqlite3
import threading
from pathlib import Path

def _default_db_path() -> Path:
    """maya.db at the repo root when running from a clone, else in the
    working directory.

    The repo root is the nearest ancestor holding both pyproject.toml and
    src/maya -- walking up for markers rather than hardcoding a parent count
    keeps this working if the file moves, and requiring src/maya stops a
    copy installed inside someone else's project from claiming *that*
    project's root. An installed package has no repo root at all, so it
    falls back to wherever the server is started.
    """
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").exists() and (candidate / "src" / "maya").is_dir():
            return candidate / "maya.db"
    return Path.cwd() / "maya.db"


#: MAYA_DB_PATH overrides the location -- the Docker image points it at a volume.
DB_PATH = Path(os.environ["MAYA_DB_PATH"]) if "MAYA_DB_PATH" in os.environ else _default_db_path()
DATA_DIR = Path(__file__).resolve().parent / "data"

#: Column name -> converter, applied when a CSV cell isn't already a string.
_CONVERTERS = {"rate_per_minute": float, "seats": int, "duration_minutes": int}

_REFERENCE_TABLES = {
    "airports": ("code", "city", "name"),
    "airlines": ("code", "name", "style", "rate_per_minute"),
    "aircraft": ("code", "name", "seats"),
    "schedule": (
        "flight_number", "airline", "origin", "destination",
        "departure", "duration_minutes", "aircraft", "days",
    ),
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
        # Multiple threads' connections can contend for a write lock; wait
        # rather than fail immediately.
        conn.execute("PRAGMA busy_timeout = 5000")
        _init(conn)
        _local.connection = conn
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS airports (
            code TEXT PRIMARY KEY, city TEXT NOT NULL, name TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS airlines (
            code TEXT PRIMARY KEY, name TEXT NOT NULL, style TEXT NOT NULL,
            rate_per_minute REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS aircraft (
            code TEXT PRIMARY KEY, name TEXT NOT NULL, seats INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS schedule (
            flight_number TEXT PRIMARY KEY, airline TEXT NOT NULL,
            origin TEXT NOT NULL, destination TEXT NOT NULL,
            departure TEXT NOT NULL, duration_minutes INTEGER NOT NULL,
            aircraft TEXT NOT NULL, days TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS bookings (
            reference TEXT PRIMARY KEY, data TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS seats_sold (
            flight_key TEXT PRIMARY KEY, count INTEGER NOT NULL
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

    Returns the number of bookings cleared.
    """
    conn = connect()
    count = conn.execute("SELECT COUNT(*) AS n FROM bookings").fetchone()["n"]
    conn.execute("DELETE FROM bookings")
    conn.execute("DELETE FROM seats_sold")
    conn.commit()
    return count
