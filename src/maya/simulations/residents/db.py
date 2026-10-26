"""The one SQLite connection every residents module shares.

Own connection, own tables -- kept separate from the other domains' db.py
on purpose, same reasoning they follow: one domain's schema changes should
never risk breaking another's. All of them write to the same maya.db file;
the table names here (residents, transactions) don't collide with theirs.

There is no reference data: residents are created by an admin (see
maya.simulations.residents.residents), never loaded from CSV.

Two tables:

- ``residents`` -- who someone is (their email unique among them), and the
  hash of their key.
- ``transactions`` -- every movement of money in or out of a resident's
  wallet, starting with the ``opening_balance`` written when the resident
  is created. The wallet is not a table of its own: in v1 every resident
  has exactly one, so it is just their transactions. The balance is their
  sum and is never stored, so it can't drift from the history. A hold
  (and its release) is a transaction too, with an ``expires_at``; holds
  don't count towards the balance, only against what is available (see
  residents.held()). The (resident_id, kind, amount) index lets SQLite sum
  one resident's money from the index alone, without reading the table.

Residents survive a world reset, and so does each wallet's opening
balance; every other transaction goes (see residents.reset()).
"""

from __future__ import annotations

import sqlite3
import threading

from maya import storage

from . import emails


#: The shared maya.db -- see maya.storage for where it lives and why.
DB_PATH = storage.db_path()


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
        CREATE TABLE IF NOT EXISTS residents (
            resident_id TEXT PRIMARY KEY,
            first_name TEXT NOT NULL, last_name TEXT NOT NULL,
            gender TEXT NOT NULL, age INTEGER NOT NULL,
            email TEXT,
            key_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS transactions (
            transaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
            resident_id TEXT NOT NULL REFERENCES residents (resident_id),
            amount INTEGER NOT NULL,
            kind TEXT NOT NULL,
            reference TEXT, domain TEXT,
            maya_time TEXT NOT NULL,
            expires_at TEXT
        );
        DROP INDEX IF EXISTS transactions_by_resident;
        CREATE INDEX IF NOT EXISTS transactions_by_resident_kind
            ON transactions (resident_id, kind, amount);
        """
    )
    # Under the write lock: two threads opening their first connection at
    # once must not both find no email column and both try to add it.
    with storage.transaction(conn):
        _add_hold_expiry(conn)
        _add_emails(conn)
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS residents_by_email ON residents (email)")


def _add_hold_expiry(conn: sqlite3.Connection) -> None:
    """Give a database made before holds existed the column a hold keeps
    its expiry in. No row needs a value: there were no holds."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(transactions)")}
    if "expires_at" not in columns:
        conn.execute("ALTER TABLE transactions ADD COLUMN expires_at TEXT")


def _add_emails(conn: sqlite3.Connection) -> None:
    """Give a database made before residents had emails an email column, and
    every resident in it their default address, oldest resident first.

    CREATE TABLE IF NOT EXISTS leaves an existing table exactly as it was,
    so without this a newer Maya would fail on an older maya.db the first
    time it read an email. The column allows NULL only because SQLite can't
    add a NOT NULL column to a table that has rows; every resident gets one
    here, and residents.py never writes one without it.
    """
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(residents)")}
    if "email" not in columns:
        conn.execute("ALTER TABLE residents ADD COLUMN email TEXT")
    missing = conn.execute(
        "SELECT resident_id, first_name, last_name FROM residents WHERE email IS NULL ORDER BY resident_id"
    ).fetchall()
    for row in missing:
        conn.execute(
            "UPDATE residents SET email = ? WHERE resident_id = ?",
            (emails.default(conn, row["first_name"], row["last_name"]), row["resident_id"]),
        )
