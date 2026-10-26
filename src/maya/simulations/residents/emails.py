"""Residents' email addresses: the default one, and keeping them unique.

Every resident has an email, and no two share one. Given one, it is checked
and kept exactly as given (lowercased) -- or refused if someone already has
it, rather than quietly changed. Not given one, a resident gets
``first.last@example.com``, numbered when that is taken:
``seren.halloway@example.com``, then ``seren.halloway1@example.com``,
``seren.halloway2@example.com``, ...

Separate from residents.py because db.py needs the default too, to give
residents created before emails existed one of their own.
"""

from __future__ import annotations

import re
import sqlite3
import unicodedata

DOMAIN = "example.com"

#: Something@something.something, with no spaces -- enough to catch a typo,
#: not a full RFC 5322 parser.
_VALID = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")

#: Letters that are letters in their own right, not an accent on a plain
#: one, so NFKD can't take them apart -- their usual plain spelling.
_PLAIN = str.maketrans({"ø": "o", "æ": "ae", "œ": "oe", "ß": "ss", "ł": "l", "đ": "d", "þ": "th", "ð": "d"})


def _part(name: str) -> str:
    """A name as it goes into an address: plain lowercase letters and digits,
    any other run of characters a hyphen -- "Okonkwo-Reyes" and
    "Mary Ann" become "okonkwo-reyes" and "mary-ann", "O'Brien" "obrien",
    "Zoë" "zoe" and "Søndergaard" "sondergaard"."""
    plain = re.sub(r"['’]", "", name.lower().translate(_PLAIN))
    ascii_name = unicodedata.normalize("NFKD", plain).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")


def taken(conn: sqlite3.Connection, email: str, except_resident: str | None = None) -> bool:
    """Whether another resident already has ``email``."""
    row = conn.execute(
        "SELECT 1 FROM residents WHERE email = ? AND resident_id IS NOT ?", (email, except_resident)
    ).fetchone()
    return row is not None


def default(conn: sqlite3.Connection, first_name: str, last_name: str) -> str:
    """``first.last@example.com``, or the first numbered one free. Read
    inside the creating transaction, so two residents can't both get it."""
    local = ".".join(p for p in (_part(first_name), _part(last_name)) if p) or "resident"
    email, n = f"{local}@{DOMAIN}", 0
    while taken(conn, email):
        n += 1
        email = f"{local}{n}@{DOMAIN}"
    return email


def normalise(email: str) -> str:
    """``email`` trimmed and lowercased, or ValueError if it isn't one."""
    email = (email or "").strip().lower()
    if not _VALID.fullmatch(email):
        raise ValueError(f"{email!r} is not an email address.")
    return email
