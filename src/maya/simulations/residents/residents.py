"""Residents and their wallets, persisted in SQLite.

A resident is a person in Maya: the identity that owns bookings and pays
for them. Its resident profile is who they are -- first and last name,
gender, age, and an email of their own (see emails.py for the default and
how it stays unique). Its wallet is the money they have: every transaction since
the opening balance it was created with, summed on read and never stored
(see db.py).

Everything here that creates or changes a resident is an admin operation.
It is called from the CLI or by an orchestrator importing Maya, never from
an MCP tool: an agent can act *as* a resident, by sending that resident's
key, but it can never create, edit, fund or switch one.

Each resident has one key, ``maya_rk_`` plus 32 random bytes. It is shown
once -- by create() or rotate_key() -- and only its SHA-256 hash is kept.
authenticate() finds a resident by hashing the key it was given and looking
that hash up; a lookup by hash reveals nothing about the key through timing,
so there is no secret comparison to make constant-time.

A world reset (see maya.reset) keeps every resident and their key, so
configured clients keep working, but empties every wallet back to its
opening balance.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3

from maya import storage
from maya.world import clock

from . import db, emails

CURRENCY = "bucks"

KEY_PREFIX = "maya_rk_"
DEFAULT_OPENING_BALANCE = 5000
GENDERS = ("female", "male", "non_binary", "unspecified")
MAX_AGE = 120

#: Why money moved. Credits are positive amounts, debits negative.
KINDS = ("opening_balance", "top_up", "charge", "refund")

_PROFILE_FIELDS = ("first_name", "last_name", "gender", "age")
#: What update() can change: the profile, and the email.
_UPDATABLE = (*_PROFILE_FIELDS, "email")


def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def _new_key() -> tuple[str, str]:
    """A fresh key and the hash that is all Maya keeps of it."""
    key = KEY_PREFIX + secrets.token_urlsafe(32)
    return key, _hash_key(key)


def _now() -> str:
    return clock.now().isoformat(timespec="seconds")


def _normalise_id(resident_id: str) -> str:
    return (resident_id or "").strip().upper()


def _check_profile(first_name: str, last_name: str, gender: str, age: int) -> dict:
    """The profile, tidied, or InvalidProfile saying what's wrong with it."""
    first_name = (first_name or "").strip()
    last_name = (last_name or "").strip()
    if not first_name or not last_name:
        raise InvalidProfile("first_name and last_name are both required.")
    gender = (gender or "").strip().lower()
    if gender not in GENDERS:
        raise InvalidProfile(f"gender must be one of {', '.join(GENDERS)}; got {gender!r}.")
    # bool is an int in Python; True is not an age.
    if not isinstance(age, int) or isinstance(age, bool) or not 0 <= age <= MAX_AGE:
        raise InvalidProfile(f"age must be a whole number from 0 to {MAX_AGE}; got {age!r}.")
    return {"first_name": first_name, "last_name": last_name, "gender": gender, "age": age}


def _check_email(conn: sqlite3.Connection, email: str, resident_id: str | None = None) -> str:
    """``email`` normalised, or InvalidProfile if it isn't one, or EmailTaken
    if another resident has it. A given email is never changed to make it
    fit -- only the default is numbered (see emails.default)."""
    try:
        email = emails.normalise(email)
    except ValueError as e:
        raise InvalidProfile(str(e)) from None
    if emails.taken(conn, email, except_resident=resident_id):
        raise EmailTaken(f"Another resident already has {email}.")
    return email


def _check_amount(amount: int, field: str, allow_zero: bool) -> int:
    if not isinstance(amount, int) or isinstance(amount, bool) or amount < 0 or (amount == 0 and not allow_zero):
        least = 0 if allow_zero else 1
        raise InvalidAmount(f"{field} must be a whole number of {CURRENCY}, {least} or more; got {amount!r}.")
    return amount


def _row(resident_id: str) -> sqlite3.Row | None:
    return db.connect().execute(
        "SELECT * FROM residents WHERE resident_id = ?", (_normalise_id(resident_id),)
    ).fetchone()


def _require(resident_id: str) -> sqlite3.Row:
    row = _row(resident_id)
    if row is None:
        raise NotFound(f"No resident {resident_id!r}.")
    return row


def _public(row: sqlite3.Row) -> dict:
    """The resident profile as anyone outside this module sees it -- never
    the key hash."""
    return {
        "resident_id": row["resident_id"],
        "first_name": row["first_name"],
        "last_name": row["last_name"],
        "gender": row["gender"],
        "age": row["age"],
        "email": row["email"],
        "created_at": row["created_at"],
    }


def _public_transaction(row: sqlite3.Row) -> dict:
    return {
        "transaction_id": f"T-{row['transaction_id']:06d}",
        "amount": row["amount"],
        "currency": CURRENCY,
        "kind": row["kind"],
        "reference": row["reference"],
        "domain": row["domain"],
        "maya_time": row["maya_time"],
    }


def _next_resident_id() -> str:
    """Numbered from the residents already stored, read inside the creating
    transaction -- never from an in-memory counter, which two processes
    sharing the database would both hand out. Residents are never deleted,
    so the count only grows."""
    n = db.connect().execute("SELECT COUNT(*) AS n FROM residents").fetchone()["n"] + 1
    return f"R-{n:06d}"


def _record(
    resident_id: str,
    amount: int,
    kind: str,
    reference: str | None = None,
    domain: str | None = None,
    conn: sqlite3.Connection | None = None,
) -> dict:
    """Write one transaction. Must be called inside storage.transaction(),
    alongside whatever the money is for, so both land or neither does --
    on that transaction's connection, ``conn``, when it isn't this module's
    own (see charge())."""
    if kind not in KINDS:
        raise ValueError(f"Unknown transaction kind {kind!r}.")
    conn = conn or db.connect()
    cursor = conn.execute(
        "INSERT INTO transactions (resident_id, amount, kind, reference, domain, maya_time)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        (resident_id, amount, kind, reference, domain, _now()),
    )
    row = conn.execute(
        "SELECT * FROM transactions WHERE transaction_id = ?", (cursor.lastrowid,)
    ).fetchone()
    return _public_transaction(row)


# --- admin operations ---------------------------------------------------------


def create(
    first_name: str,
    last_name: str,
    age: int,
    gender: str = "unspecified",
    opening_balance: int = DEFAULT_OPENING_BALANCE,
    email: str | None = None,
) -> dict:
    """Create a resident with a wallet holding ``opening_balance``.

    ``email`` defaults to ``first.last@example.com``, numbered if that's
    taken; one that is given must be free (EmailTaken otherwise).

    Returns the resident profile, the balance, and the key under ``key`` --
    the only time the key is ever returned.
    """
    profile = _check_profile(first_name, last_name, gender, age)
    opening_balance = _check_amount(opening_balance, "opening_balance", allow_zero=True)
    key, key_hash = _new_key()
    with storage.transaction(db.connect()) as conn:
        resident_id = _next_resident_id()
        if email is None:
            email = emails.default(conn, profile["first_name"], profile["last_name"])
        else:
            email = _check_email(conn, email)
        conn.execute(
            "INSERT INTO residents (resident_id, first_name, last_name, gender, age,"
            " email, key_hash, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                resident_id, profile["first_name"], profile["last_name"], profile["gender"],
                profile["age"], email, key_hash, _now(),
            ),
        )
        _record(resident_id, opening_balance, "opening_balance")
    return {**get(resident_id), "balance": opening_balance, "currency": CURRENCY, "key": key}


def update(resident_id: str, **changes) -> dict:
    """Change any of first_name, last_name, gender, age and email. Returns
    the updated profile. Changing a name leaves the email as it is."""
    unknown = set(changes) - set(_UPDATABLE)
    if unknown:
        raise InvalidProfile(f"Can't change {', '.join(sorted(unknown))}; only {', '.join(_UPDATABLE)}.")
    with storage.transaction(db.connect()) as conn:
        current = _public(_require(resident_id))
        profile = _check_profile(**{f: changes.get(f, current[f]) for f in _PROFILE_FIELDS})
        email = current["email"]
        if "email" in changes:
            email = _check_email(conn, changes["email"], current["resident_id"])
        conn.execute(
            "UPDATE residents SET first_name = ?, last_name = ?, gender = ?, age = ?, email = ?"
            " WHERE resident_id = ?",
            (profile["first_name"], profile["last_name"], profile["gender"], profile["age"],
             email, current["resident_id"]),
        )
    return get(current["resident_id"])


def rotate_key(resident_id: str) -> str:
    """Give the resident a new key and return it. The old key stops working
    at once."""
    key, key_hash = _new_key()
    with storage.transaction(db.connect()) as conn:
        row = _require(resident_id)
        conn.execute(
            "UPDATE residents SET key_hash = ? WHERE resident_id = ?", (key_hash, row["resident_id"])
        )
    return key


def add_funds(resident_id: str, amount: int) -> dict:
    """Credit ``amount`` to the resident's wallet. Returns the transaction
    and the new balance."""
    amount = _check_amount(amount, "amount", allow_zero=False)
    with storage.transaction(db.connect()):
        row = _require(resident_id)
        transaction = _record(row["resident_id"], amount, "top_up")
    return {"transaction": transaction, "balance": balance(row["resident_id"]), "currency": CURRENCY}


def list_residents() -> list[dict]:
    rows = db.connect().execute("SELECT * FROM residents ORDER BY resident_id").fetchall()
    return [_public(r) for r in rows]


# --- what a resident can see about themselves ---------------------------------


def authenticate(key: str) -> dict:
    """The resident ``key`` belongs to, or InvalidResidentKey."""
    key = (key or "").strip()
    row = None
    if key.startswith(KEY_PREFIX):
        row = db.connect().execute(
            "SELECT * FROM residents WHERE key_hash = ?", (_hash_key(key),)
        ).fetchone()
    if row is None:
        raise InvalidResidentKey("That resident key is not valid.")
    return _public(row)


def get(resident_id: str) -> dict | None:
    row = _row(resident_id)
    return _public(row) if row else None


def balance(resident_id: str, conn: sqlite3.Connection | None = None) -> int:
    """The sum of the resident's transactions. A resident with none (or an
    unknown id) has 0 -- check the resident exists first if that matters.
    ``conn`` as for charge()."""
    return (conn or db.connect()).execute(
        "SELECT COALESCE(SUM(amount), 0) AS total FROM transactions WHERE resident_id = ?",
        (_normalise_id(resident_id),),
    ).fetchone()["total"]


def list_transactions(resident_id: str) -> list[dict]:
    """The resident's transactions, oldest first."""
    row = _require(resident_id)
    rows = db.connect().execute(
        "SELECT * FROM transactions WHERE resident_id = ? ORDER BY transaction_id",
        (row["resident_id"],),
    ).fetchall()
    return [_public_transaction(r) for r in rows]


# --- paying for things ---------------------------------------------------------
#
# Every domain books through its own connection, inside its own
# storage.transaction(). The payment has to land in that same transaction --
# on that same connection -- so a booking is never made without being paid
# for, or paid for without being made. A second connection couldn't even
# try: the booking's transaction holds the database's write lock until it
# commits. So these take the booking's connection, and write this module's
# tables through it (they are in the same maya.db file).
#
# The tables must already exist when the booking's transaction starts:
# creating them would need the write lock too. A domain calls ready() before
# it opens the transaction.


def ready() -> None:
    """Make sure this module's tables exist on this thread. Call before
    opening a transaction that will charge() or refund()."""
    db.connect()


def charge(conn: sqlite3.Connection, resident_id: str, amount: int, reference: str, domain: str) -> dict:
    """Debit ``amount`` for booking ``reference``, inside the booking's own
    transaction on ``conn``. Raises InsufficientFunds -- and then the
    booking's transaction rolls back with it. Returns the transaction and
    the balance after it."""
    amount = _check_amount(amount, "amount", allow_zero=True)
    available = balance(resident_id, conn)
    if available < amount:
        raise InsufficientFunds(
            f"This costs {amount} {CURRENCY}; the resident's wallet has {available} {CURRENCY}."
        )
    transaction = _record(resident_id, -amount, "charge", reference, domain, conn)
    return {"transaction": transaction, "balance": available - amount, "currency": CURRENCY}


def refund(conn: sqlite3.Connection, resident_id: str, amount: int, reference: str, domain: str) -> dict:
    """Credit ``amount`` back for booking ``reference``, inside the
    cancellation's own transaction on ``conn``. Always to the resident who
    paid -- the caller passes the booking's owner, never whoever asked.
    Returns the transaction and the balance after it."""
    amount = _check_amount(amount, "amount", allow_zero=True)
    transaction = _record(resident_id, amount, "refund", reference, domain, conn)
    return {"transaction": transaction, "balance": balance(resident_id, conn), "currency": CURRENCY}


def reset() -> int:
    """Empty every wallet back to its opening balance. Part of a world reset
    (see maya.reset); residents and their keys are kept.

    Every transaction goes except each wallet's ``opening_balance``, which
    is re-stamped with the reset's "now": the world's history starts over,
    and an opening balance dated in a future the clock just left would be
    a transaction from a time that hasn't happened.

    Returns the number of transactions cleared.
    """
    with storage.transaction(db.connect()) as conn:
        cleared = conn.execute("DELETE FROM transactions WHERE kind != 'opening_balance'").rowcount
        conn.execute("UPDATE transactions SET maya_time = ? WHERE kind = 'opening_balance'", (_now(),))
    return cleared


class ResidentError(Exception):
    """Base class for resident and wallet failures."""

    code = "resident_error"
    hint = None


class InvalidProfile(ResidentError):
    code = "invalid_profile"
    hint = f"Give a first and last name, a gender ({', '.join(GENDERS)}) and an age from 0 to {MAX_AGE}."


class EmailTaken(ResidentError):
    code = "email_taken"
    hint = "Choose another email, or leave it out to get first.last@example.com (numbered if taken)."


class InvalidAmount(ResidentError):
    code = "invalid_amount"
    hint = f"Amounts are whole {CURRENCY}, with no subunit."


class NotFound(ResidentError):
    code = "not_found"
    hint = "Check the resident id, or run `maya residents list` to see who exists."


class ResidentRequired(ResidentError):
    code = "resident_required"
    hint = (
        "Ask the admin to create a resident with `maya residents create`, then send its key "
        "as an `Authorization: Bearer <key>` header (or MAYA_RESIDENT_KEY for stdio)."
    )


class InsufficientFunds(ResidentError):
    code = "insufficient_funds"
    hint = "Nothing was booked. Choose something cheaper, or tell the resident their wallet is short."


class InvalidResidentKey(ResidentError):
    code = "invalid_resident_key"
    hint = "Check the key in your MCP client's Authorization header, or ask the admin to rotate it."
