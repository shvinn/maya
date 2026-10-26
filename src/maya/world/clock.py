"""Maya's world clock: Maya's own time zone, set ahead of real time.

Every domain asks this module what time it is -- never ``datetime.now()`` --
so the whole world agrees on "now" whatever machine it runs on, and time
can be moved ahead for scenarios that would otherwise take hours of real
waiting.

Maya keeps Mayan Meridian Time (MYT), a fixed UTC-08:00 with no daylight
saving. ``now()`` returns naive MYT wall-clock time, the same kind of value
the domains always compared against.

Maya time always runs at real speed. The clock is one row: the Maya time
last set and the real time it was set at, both unix milliseconds::

    maya_now = maya_epoch_ms + (real_now - real_epoch_ms)   (shown in MYT)

With no row, Maya time is real time. Moving time ahead -- by a duration or
to a given moment -- rewrites the row and is refused if it would go
backwards; only a full world reset (``maya.reset``), which deletes the row
along with every booking, can step back. The row
lives in the shared maya.db, so every process (the HTTP server, one stdio
container per domain) reads the same clock, and it holds across restarts:
Maya time keeps running while the servers are stopped.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone

from maya import storage

MYT = timezone(timedelta(hours=-8), "MYT")
TIMEZONE_LABEL = "MYT (UTC-08:00)"

#: The shared maya.db -- see maya.storage for where it lives and why.
DB_PATH = storage.db_path()

_local = threading.local()


def _connect() -> sqlite3.Connection:
    """This thread's connection (sqlite3 connections are per-thread)."""
    conn = getattr(_local, "connection", None)
    if conn is None:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 5000")
        _local.connection = conn
    return conn


def _ensure_table(conn: sqlite3.Connection) -> None:
    """Create the clock's table -- only ever from a write that changes the
    clock. Reading the time must never write: domains read it inside their
    booking transactions, and a write on this separate connection would wait
    for that transaction's lock, held by the same thread, until it timed out.

    Maya time starts as real time and only moves ahead, so it is never
    behind real time; the CHECK makes the database refuse a row that is.
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS world_clock ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " maya_epoch_ms INTEGER NOT NULL, real_epoch_ms INTEGER NOT NULL,"
        " CHECK (maya_epoch_ms >= real_epoch_ms))"
    )


def _real_now_ms() -> int:
    """Real time as unix milliseconds -- the one place real time is read."""
    return time.time_ns() // 1_000_000


def _maya_ms(real_ms: int) -> int:
    """Maya time, as unix milliseconds, at a real instant."""
    try:
        row = _connect().execute("SELECT maya_epoch_ms, real_epoch_ms FROM world_clock WHERE id = 1").fetchone()
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        row = None  # never set on this database
    if row is None:
        return real_ms
    return row["maya_epoch_ms"] + (real_ms - row["real_epoch_ms"])


def _to_myt(unix_ms: int) -> datetime:
    return datetime.fromtimestamp(unix_ms / 1000, MYT).replace(tzinfo=None)


def _save(maya_ms: int, real_ms: int) -> None:
    conn = _connect()
    _ensure_table(conn)
    with conn:  # commits, or rolls back a refused write so it can't hold the lock
        conn.execute(
            "INSERT OR REPLACE INTO world_clock (id, maya_epoch_ms, real_epoch_ms) VALUES (1, ?, ?)",
            (maya_ms, real_ms),
        )


def now() -> datetime:
    """The current Maya time, as naive MYT wall-clock time."""
    return _to_myt(_maya_ms(_real_now_ms()))


def state() -> dict:
    real_ms = _real_now_ms()
    maya = _to_myt(_maya_ms(real_ms))
    return {
        "maya_time": maya.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": maya.strftime("%A"),
        "timezone": TIMEZONE_LABEL,
        "real_time_utc": datetime.fromtimestamp(real_ms / 1000, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


def advance(minutes: float = 0, hours: float = 0, days: float = 0) -> dict:
    """Move Maya time ahead by a duration. Never backwards."""
    delta = timedelta(minutes=minutes, hours=hours, days=days)
    if delta <= timedelta(0):
        raise InvalidDuration("Time only moves forward: give a positive number of minutes, hours or days.")
    real_ms = _real_now_ms()
    _save(_maya_ms(real_ms) + delta // timedelta(milliseconds=1), real_ms)
    return state()


def set_time(maya_time: str) -> dict:
    """Move Maya time ahead to a given moment, ``YYYY-MM-DD HH:MM`` in MYT.
    Never backwards."""
    try:
        target = datetime.fromisoformat(maya_time.strip())
    except ValueError:
        raise InvalidTime(f"{maya_time!r} isn't a time; use YYYY-MM-DD HH:MM (Maya time, MYT).") from None
    if target.tzinfo is None:
        target = target.replace(tzinfo=MYT)
    target_ms = round(target.timestamp() * 1000)
    real_ms = _real_now_ms()
    current_ms = _maya_ms(real_ms)
    if target_ms <= current_ms:
        raise InvalidTime(
            f"Time only moves forward: it's already {_to_myt(current_ms):%Y-%m-%d %H:%M:%S} in Maya, "
            f"and {_to_myt(target_ms):%Y-%m-%d %H:%M:%S} isn't after that."
        )
    _save(target_ms, real_ms)
    return state()


def _reset(conn: sqlite3.Connection) -> None:
    """Back to real time in MYT -- the one way time goes backwards. Only
    ``maya.reset.reset_world`` calls this, together with emptying every
    domain's bookings, on its ``conn`` and inside its one transaction (so
    this doesn't commit)."""
    _ensure_table(conn)
    conn.execute("DELETE FROM world_clock")


class ClockError(Exception):
    """Base class for clock requests that are refused."""

    code = "clock_error"
    hint = None


class InvalidTime(ClockError):
    code = "invalid_time"
    hint = "Give a Maya time (MYT) later than now, as YYYY-MM-DD HH:MM."


class InvalidDuration(ClockError):
    code = "invalid_duration"
    hint = "Pass a positive minutes, hours or days."
