"""Residents: profiles, keys, wallets and what a world reset does to them.

Driven through the module's functions -- these are admin operations with no
MCP tool, so there is no tool surface to go through.

Residents survive a world reset by design, so unlike bookings they pile up
across tests; nothing here assumes which ids already exist.
"""

from __future__ import annotations

import sqlite3

import pytest

from maya import storage
from maya.reset import reset_world
from maya.simulations.residents import db, residents

from .conftest import NOW


def lena(**overrides) -> dict:
    fields = {"first_name": "Lena", "last_name": "Okafor", "gender": "female", "age": 34}
    return residents.create(**{**fields, **overrides})


# --- create -------------------------------------------------------------------


def test_create_returns_the_profile_the_balance_and_the_key_once():
    r = lena(opening_balance=1200)
    assert r["resident_id"].startswith("R-")
    assert (r["first_name"], r["last_name"], r["gender"], r["age"]) == ("Lena", "Okafor", "female", 34)
    assert r["balance"] == 1200
    assert r["key"].startswith(residents.KEY_PREFIX)
    assert r["created_at"] == NOW.isoformat(timespec="seconds")
    # Never again: reading the resident back has no key and no hash.
    again = residents.get(r["resident_id"])
    assert "key" not in again and "key_hash" not in again


def test_resident_ids_are_consecutive_and_keys_unique():
    a, b = lena(), lena()
    assert int(b["resident_id"][2:]) == int(a["resident_id"][2:]) + 1
    assert a["key"] != b["key"]


def test_the_default_opening_balance_and_gender():
    r = residents.create(first_name="Ari", last_name="Venn", age=30)
    assert r["balance"] == residents.DEFAULT_OPENING_BALANCE
    assert r["gender"] == "unspecified"


def test_only_the_hash_of_the_key_is_stored():
    r = lena()
    conn = sqlite3.connect(db.DB_PATH)
    dump = "\n".join(conn.iterdump())
    assert r["key"] not in dump
    stored = conn.execute(
        "SELECT key_hash FROM residents WHERE resident_id = ?", (r["resident_id"],)
    ).fetchone()[0]
    assert stored == residents._hash_key(r["key"])


def test_names_are_trimmed_and_gender_is_case_insensitive():
    r = lena(first_name="  Lena ", gender="Female")
    assert r["first_name"] == "Lena"
    assert r["gender"] == "female"


@pytest.mark.parametrize(
    "overrides",
    [
        {"first_name": ""},
        {"last_name": "   "},
        {"gender": "robot"},
        {"age": -1},
        {"age": residents.MAX_AGE + 1},
        {"age": 34.5},
        {"age": "34"},
        {"age": True},
    ],
)
def test_create_refuses_a_bad_profile(overrides):
    with pytest.raises(residents.InvalidProfile):
        lena(**overrides)


@pytest.mark.parametrize("opening_balance", [-1, 10.5, "100", True])
def test_create_refuses_a_bad_opening_balance(opening_balance):
    with pytest.raises(residents.InvalidAmount):
        lena(opening_balance=opening_balance)


def test_a_zero_opening_balance_is_allowed():
    assert lena(opening_balance=0)["balance"] == 0


# --- keys ---------------------------------------------------------------------


def test_authenticate_finds_the_resident_a_key_belongs_to():
    r = lena()
    assert residents.authenticate(r["key"])["resident_id"] == r["resident_id"]
    assert residents.authenticate(f"  {r['key']}\n")["resident_id"] == r["resident_id"]


@pytest.mark.parametrize("key", ["", None, "maya_rk_not-a-real-key", "not-even-the-prefix"])
def test_authenticate_refuses_a_bad_key(key):
    lena()
    with pytest.raises(residents.InvalidResidentKey):
        residents.authenticate(key)


def test_rotating_a_key_retires_the_old_one():
    r = lena()
    new_key = residents.rotate_key(r["resident_id"])
    assert new_key != r["key"]
    assert residents.authenticate(new_key)["resident_id"] == r["resident_id"]
    with pytest.raises(residents.InvalidResidentKey):
        residents.authenticate(r["key"])


# --- update -------------------------------------------------------------------


def test_update_changes_only_what_it_is_given():
    r = lena()
    updated = residents.update(r["resident_id"], age=35)
    assert updated["age"] == 35
    assert updated["first_name"] == "Lena"


def test_update_accepts_a_lowercase_id():
    r = lena()
    assert residents.update(r["resident_id"].lower(), last_name="Mensah")["last_name"] == "Mensah"


def test_update_refuses_fields_outside_the_profile():
    r = lena()
    with pytest.raises(residents.InvalidProfile):
        residents.update(r["resident_id"], opening_balance=999_999)
    with pytest.raises(residents.InvalidProfile):
        residents.update(r["resident_id"], key_hash="x")


def test_update_refuses_a_bad_value_and_changes_nothing():
    r = lena()
    with pytest.raises(residents.InvalidProfile):
        residents.update(r["resident_id"], age=-5)
    assert residents.get(r["resident_id"])["age"] == 34


def test_unknown_resident():
    with pytest.raises(residents.NotFound):
        residents.update("R-999999", age=40)
    with pytest.raises(residents.NotFound):
        residents.rotate_key("R-999999")
    with pytest.raises(residents.NotFound):
        residents.add_funds("R-999999", 100)
    with pytest.raises(residents.NotFound):
        residents.list_transactions("R-999999")
    assert residents.get("R-999999") is None


# --- wallet -------------------------------------------------------------------


def test_add_funds_credits_the_wallet():
    r = lena(opening_balance=1000)
    result = residents.add_funds(r["resident_id"], 250)
    assert result["balance"] == 1250
    assert result["transaction"]["kind"] == "top_up"
    assert result["transaction"]["amount"] == 250
    assert residents.balance(r["resident_id"]) == 1250


@pytest.mark.parametrize("amount", [0, -10, 2.5, "100", True])
def test_add_funds_refuses_a_bad_amount(amount):
    r = lena()
    with pytest.raises(residents.InvalidAmount):
        residents.add_funds(r["resident_id"], amount)


def test_the_balance_is_the_sum_of_every_transaction():
    r = lena(opening_balance=1000)
    with storage.transaction(db.connect()):
        residents._record(r["resident_id"], -320, "charge", "HTL001", "hotels")
        residents._record(r["resident_id"], 320, "refund", "HTL001", "hotels")
        residents._record(r["resident_id"], -45, "charge", "ORD002", "delivery")
    assert residents.balance(r["resident_id"]) == 1000 - 320 + 320 - 45
    history = residents.list_transactions(r["resident_id"])
    assert [t["kind"] for t in history] == ["opening_balance", "charge", "refund", "charge"]
    assert [t["amount"] for t in history] == [1000, -320, 320, -45]
    assert history[1]["reference"] == "HTL001" and history[1]["domain"] == "hotels"
    assert all(t["transaction_id"].startswith("T-") for t in history)


def test_wallets_are_separate():
    a, b = lena(opening_balance=100), lena(opening_balance=200)
    residents.add_funds(a["resident_id"], 50)
    assert residents.balance(a["resident_id"]) == 150
    assert residents.balance(b["resident_id"]) == 200


def test_a_transaction_of_an_unknown_kind_is_a_bug():
    r = lena()
    with pytest.raises(ValueError), storage.transaction(db.connect()):
        residents._record(r["resident_id"], 10, "gift")


# --- world reset --------------------------------------------------------------


def test_a_reset_returns_each_wallet_to_its_opening_balance_top_ups_and_all():
    """A reset is the admin starting the world over from the money each
    resident was created with: top-ups go too."""
    r = lena(opening_balance=100)
    residents.add_funds(r["resident_id"], 900)
    reset_world()
    assert residents.balance(r["resident_id"]) == 100


def test_reset_keeps_residents_and_keys_but_refills_wallets():
    r = lena(opening_balance=1000)
    residents.add_funds(r["resident_id"], 500)
    with storage.transaction(db.connect()):
        residents._record(r["resident_id"], -300, "charge", "HTL001", "hotels")

    cleared = reset_world()

    assert cleared["resident_transactions"] >= 2  # the top-up and the charge, at least
    assert residents.get(r["resident_id"])["first_name"] == "Lena"
    assert residents.authenticate(r["key"])["resident_id"] == r["resident_id"]
    assert residents.balance(r["resident_id"]) == 1000
    assert [t["kind"] for t in residents.list_transactions(r["resident_id"])] == ["opening_balance"]


# --- email --------------------------------------------------------------------


def unique_name() -> tuple[str, str]:
    """A name no earlier test used -- residents outlive each test."""
    import uuid

    return "Mira", f"Q{uuid.uuid4().hex[:8]}"


def test_the_default_email_is_first_dot_last_at_example_com():
    first, last = unique_name()
    r = residents.create(first, last, age=30)
    assert r["email"] == f"mira.{last.lower()}@example.com"


def test_a_taken_default_email_is_numbered():
    first, last = unique_name()
    made = [residents.create(first, last, age=30)["email"] for _ in range(3)]
    base = f"mira.{last.lower()}"
    assert made == [f"{base}@example.com", f"{base}1@example.com", f"{base}2@example.com"]


def test_names_are_made_safe_for_an_address():
    _, tag = unique_name()
    r = residents.create("Mary Ann", f"O'Brien-Søndergaard {tag}", age=30)
    assert r["email"] == f"mary-ann.obrien-sondergaard-{tag.lower()}@example.com"


def test_a_given_email_is_kept_lowercased():
    _, tag = unique_name()
    r = residents.create("Lena", "Okafor", age=34, email=f"  Lena.{tag}@Aira.MB ")
    assert r["email"] == f"lena.{tag.lower()}@aira.mb"
    assert residents.get(r["resident_id"])["email"] == r["email"]


def test_a_given_email_that_is_taken_is_refused_not_changed():
    _, tag = unique_name()
    residents.create("Lena", "Okafor", age=34, email=f"{tag}@example.com")
    with pytest.raises(residents.EmailTaken):
        residents.create("Ari", "Venn", age=30, email=f"{tag.upper()}@EXAMPLE.COM")


@pytest.mark.parametrize("email", ["", "lena", "lena@", "@example.com", "lena@example", "le na@example.com"])
def test_a_malformed_email_is_refused(email):
    with pytest.raises(residents.InvalidProfile):
        lena(email=email)


def test_update_changes_the_email_and_keeps_it_unique():
    _, tag = unique_name()
    a, b = lena(), lena()
    email = f"{tag.lower()}@aira.mb"
    assert residents.update(a["resident_id"], email=email)["email"] == email
    with pytest.raises(residents.EmailTaken):
        residents.update(b["resident_id"], email=email)
    # Setting your own email again isn't a conflict with yourself.
    assert residents.update(a["resident_id"], email=email)["email"] == email


def test_changing_a_name_leaves_the_email_alone():
    r = lena()
    assert residents.update(r["resident_id"], last_name="Mensah")["email"] == r["email"]


def test_an_older_database_gets_an_email_for_every_resident(tmp_path):
    """A maya.db from before emails: residents with no email column. Opening
    it adds the column and gives each one their default, oldest first."""
    conn = sqlite3.connect(tmp_path / "old.db")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE residents (
            resident_id TEXT PRIMARY KEY, first_name TEXT NOT NULL, last_name TEXT NOT NULL,
            gender TEXT NOT NULL, age INTEGER NOT NULL, key_hash TEXT NOT NULL UNIQUE,
            created_at TEXT NOT NULL
        );
        INSERT INTO residents VALUES ('R-000001', 'Seren', 'Halloway', 'female', 32, 'h1', '2030-03-01T09:00:00');
        INSERT INTO residents VALUES ('R-000002', 'Seren', 'Halloway', 'female', 40, 'h2', '2030-03-02T09:00:00');
        INSERT INTO residents VALUES ('R-000003', 'Tavi', 'Okonkwo-Reyes', 'male', 28, 'h3', '2030-03-03T09:00:00');
        """
    )
    db._init(conn)
    db._init(conn)  # and opening it again changes nothing
    got = dict(conn.execute("SELECT resident_id, email FROM residents ORDER BY resident_id").fetchall())
    assert got == {
        "R-000001": "seren.halloway@example.com",
        "R-000002": "seren.halloway1@example.com",
        "R-000003": "tavi.okonkwo-reyes@example.com",
    }
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE residents SET email = 'seren.halloway@example.com' WHERE resident_id = 'R-000003'")
