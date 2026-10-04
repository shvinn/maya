"""Where Maya keeps its state, and how it writes it safely.

Every domain (and the world clock) keeps its own tables and its own
connection, but they all share one SQLite file and one rule for finding it
-- that rule lives here, once, instead of being copied into each db.py:

1. ``MAYA_DB_PATH``, if set (the Docker image points it at a volume);
2. the repo root, when running from a clone -- the nearest ancestor holding
   both pyproject.toml and src/maya (requiring src/maya stops a copy
   installed inside someone else's project from claiming *that* project's
   root);
3. otherwise a per-user data folder -- an installed copy (pip, uvx) has no
   repo root, and MCP clients often start servers from a folder that can't
   be written to, such as ``/``.

``transaction()`` is how a booking checks inventory and takes it in one
step. Several threads (the HTTP server runs tool calls in a pool) or
processes (one stdio server per client) can share the file; without it,
two bookings for the last seat could both read "1 left" and both succeed.
"""

from __future__ import annotations

import os
import sqlite3
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path


def user_data_dir() -> Path:
    """The platform's per-user data folder for Maya, created if needed."""
    if sys.platform == "win32":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    folder = base / "maya"
    folder.mkdir(parents=True, exist_ok=True)
    return folder


def db_path() -> Path:
    """The SQLite file every domain and the world clock share."""
    if os.environ.get("MAYA_DB_PATH"):
        return Path(os.environ["MAYA_DB_PATH"])
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").exists() and (candidate / "src" / "maya").is_dir():
            return candidate / "maya.db"
    return user_data_dir() / "maya.db"


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """One atomic write: everything inside commits together or not at all.

    ``BEGIN IMMEDIATE`` takes SQLite's write lock up front, so a check made
    inside (seats left, rooms free, the next reference number) can't be
    invalidated by another writer before the matching update lands. Other
    writers wait (each connection sets a busy timeout) rather than fail.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.rollback()
        raise
    conn.commit()
