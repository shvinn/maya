"""Maya's world clock: Maya's own time zone, scaled against real time.

Every domain asks this module what time it is -- never ``datetime.now()`` --
so the whole world agrees on "now" whatever machine it runs on, and time
can be sped up, frozen or skipped ahead for scenarios that would otherwise
take hours of real waiting.

Maya keeps Mayan Meridian Time (MYT), a fixed UTC-08:00 with no daylight
saving. ``now()`` returns naive MYT wall-clock time, the same kind of value
the domains always compared against.

The clock is an anchor plus a scale::

    maya_now = anchor_maya + (real_now - anchor_real) * scale

With nothing saved it is simply real time in MYT at 1:1. Scale 60 makes one
real minute a Maya hour; scale 0 freezes the world. Every change re-anchors
at the current Maya moment, so the clock never jumps, and time only moves
forward -- except ``reset()``, which returns to real time and can therefore
step backwards. The state is one row in the shared maya.db, so every
process (the HTTP server, one stdio container per domain) reads the same
clock, and Maya time carries on across restarts.
"""

from __future__ import annotations

import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from maya import storage

MYT = timezone(timedelta(hours=-8), "MYT")
TIMEZONE_LABEL = "MYT (UTC-08:00)"
MAX_SCALE = 10_000


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
    """
    conn.execute(
        "CREATE TABLE IF NOT EXISTS world_clock ("
        " id INTEGER PRIMARY KEY CHECK (id = 1),"
        " anchor_real REAL NOT NULL, anchor_maya TEXT NOT NULL, scale REAL NOT NULL)"
    )


def _real_now() -> float:
    """Real time as a UTC timestamp -- the one place real time is read."""
    return time.time()


def _maya_at(real_ts: float) -> tuple[datetime, float]:
    """(Maya time, scale) at a real instant."""
    try:
        row = _connect().execute("SELECT anchor_real, anchor_maya, scale FROM world_clock WHERE id = 1").fetchone()
    except sqlite3.OperationalError as e:
        if "no such table" not in str(e):
            raise
        row = None  # never changed on this database: real time at 1:1
    if row is None:
        return datetime.fromtimestamp(real_ts, MYT).replace(tzinfo=None), 1.0
    elapsed = (real_ts - row["anchor_real"]) * row["scale"]
    return datetime.fromisoformat(row["anchor_maya"]) + timedelta(seconds=elapsed), row["scale"]


def _save(maya: datetime, scale: float, real_ts: float) -> None:
    conn = _connect()
    _ensure_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO world_clock (id, anchor_real, anchor_maya, scale) VALUES (1, ?, ?, ?)",
        (real_ts, maya.isoformat(), scale),
    )
    conn.commit()


def now() -> datetime:
    """The current Maya time, as naive MYT wall-clock time."""
    return _maya_at(_real_now())[0]


def state() -> dict:
    real_ts = _real_now()
    maya, scale = _maya_at(real_ts)
    return {
        "maya_time": maya.strftime("%Y-%m-%d %H:%M:%S"),
        "weekday": maya.strftime("%A"),
        "timezone": TIMEZONE_LABEL,
        "scale": scale,
        "frozen": scale == 0,
        "real_time_utc": datetime.fromtimestamp(real_ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
    }


def set_scale(scale: float) -> dict:
    """Change how fast Maya time runs against real time, from now on."""
    if not 0 <= scale <= MAX_SCALE:
        raise InvalidScale(f"scale must be between 0 and {MAX_SCALE}; got {scale}.")
    real_ts = _real_now()
    maya, _ = _maya_at(real_ts)
    _save(maya, float(scale), real_ts)
    return state()


def advance(minutes: float = 0, hours: float = 0, days: float = 0) -> dict:
    """Skip Maya time ahead. Never backwards."""
    delta = timedelta(minutes=minutes, hours=hours, days=days)
    if delta <= timedelta(0):
        raise InvalidDuration("Time only moves forward: give a positive number of minutes, hours or days.")
    real_ts = _real_now()
    maya, scale = _maya_at(real_ts)
    _save(maya + delta, scale, real_ts)
    return state()


def reset() -> dict:
    """Back to real time in MYT at 1:1. If time had been skipped ahead,
    this steps backwards -- bookings made in the skipped-ahead time may then
    look like they're in the future."""
    conn = _connect()
    _ensure_table(conn)
    conn.execute("DELETE FROM world_clock")
    conn.commit()
    return state()


def apply_env_scale() -> None:
    """Apply MAYA_TIME_SCALE, if set, when a server process starts."""
    value = os.environ.get("MAYA_TIME_SCALE")
    if value is None or not value.strip():
        return
    try:
        scale = float(value)
    except ValueError:
        raise InvalidScale(f"MAYA_TIME_SCALE must be a number; got {value!r}.") from None
    set_scale(scale)


class ClockError(Exception):
    """Base class for clock requests that are refused."""

    code = "clock_error"
    hint = None


class InvalidScale(ClockError):
    code = "invalid_scale"
    hint = f"Use 1 for real time, a larger number to speed up (up to {MAX_SCALE}), or 0 to freeze."


class InvalidDuration(ClockError):
    code = "invalid_duration"
    hint = "Pass a positive minutes, hours or days. To go back to real time, use world_reset_time."
