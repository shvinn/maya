"""The one SQLite connection every cars module shares.

Own connection, own tables -- kept separate from the other domains' db.py
on purpose, same reasoning they follow: one domain's schema changes should
never risk breaking another's. All of them write to the same maya.db file;
the table names here (cars, car_rentals, cars_out) don't collide with
theirs.

Reference data (the fleet) lives in data/cars.csv -- that is the source of
truth, reloaded on every start. Rentals and cars out per day are real state
and survive a restart; they are only cleared by an explicit reset_world().
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
_CONVERTERS = {
    "model_age_years": int,
    "seats": int,
    "fleet_size": int,
    "daily_rate": int,
}

_REFERENCE_TABLES = {
    "cars": (
        "car_id", "make", "model", "model_age_years", "car_class",
        "seats", "transmission", "fleet_size", "daily_rate",
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
        conn.execute("PRAGMA busy_timeout = 5000")
        _init(conn)
        _local.connection = conn
    return conn


def _init(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS cars (
            car_id TEXT PRIMARY KEY, make TEXT NOT NULL, model TEXT NOT NULL,
            model_age_years INTEGER NOT NULL, car_class TEXT NOT NULL,
            seats INTEGER NOT NULL, transmission TEXT NOT NULL,
            fleet_size INTEGER NOT NULL, daily_rate INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS car_rentals (
            reference TEXT PRIMARY KEY, data TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS cars_out (
            car_id TEXT NOT NULL, day TEXT NOT NULL, count INTEGER NOT NULL,
            PRIMARY KEY (car_id, day)
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

    Returns the number of rentals cleared.
    """
    conn = connect()
    count = conn.execute("SELECT COUNT(*) AS n FROM car_rentals").fetchone()["n"]
    conn.execute("DELETE FROM car_rentals")
    conn.execute("DELETE FROM cars_out")
    conn.commit()
    return count
