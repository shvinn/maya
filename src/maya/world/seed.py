"""The world seed: what makes one Maya world's luck differ from another's.

Chance events -- a flight's delay or cancellation today -- are drawn from
generators seeded with this number plus the thing they're about (a flight on
a date). Within one world the same question always gets the same answer;
a reset picks a new seed, so the next world's luck is new and nothing can be
learned from a previous run.

Pin it for repeatable runs: ``MAYA_SEED`` in the environment, or a seed
passed to the reset (``world_reset(seed=...)``, ``maya reset --seed``). The
seed is for whoever runs the world, never the agent under test -- with it,
every future delay could be worked out -- so only the ``world`` server
shows it.

One row in the shared maya.db, like the clock. It's written at import (and
by a reset), never on read: domains read it inside their own transactions.
"""

from __future__ import annotations

import os
import random
import sqlite3
import threading

from maya import storage

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


def _fresh() -> int:
    """A new seed: MAYA_SEED if set, otherwise random."""
    pinned = os.environ.get("MAYA_SEED", "").strip()
    return int(pinned) if pinned else random.SystemRandom().randrange(2**31)


def _ensure() -> None:
    """Give a world that has never had a seed one. The first process to get
    here wins; everyone after reads the same row."""
    conn = _connect()
    with conn:
        conn.execute("CREATE TABLE IF NOT EXISTS world_seed (id INTEGER PRIMARY KEY CHECK (id = 1), seed INTEGER NOT NULL)")
        conn.execute("INSERT OR IGNORE INTO world_seed (id, seed) VALUES (1, ?)", (_fresh(),))


def get() -> int:
    return _connect().execute("SELECT seed FROM world_seed WHERE id = 1").fetchone()["seed"]


def _reset(seed: int | None = None) -> int:
    """A new seed for a new world -- ``seed`` if given, else MAYA_SEED, else
    random. Only ``maya.reset.reset_world`` calls this."""
    new = _fresh() if seed is None else seed
    conn = _connect()
    with conn:
        conn.execute("INSERT OR REPLACE INTO world_seed (id, seed) VALUES (1, ?)", (new,))
    return new


_ensure()
