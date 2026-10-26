"""The residents MCP tools: what a resident can see about themselves.

Driven through the MCP tool functions, acting as whichever resident's key is
in MAYA_RESIDENT_KEY (see conftest.act_as).
"""

from __future__ import annotations

from datetime import date

from maya.simulations.flights import mcp_tools as flights
from maya.simulations.residents import mcp_tools as t
from maya.simulations.residents import residents

from .conftest import act_as, error_code


def test_the_profile_has_everything_and_the_current_balance(monkeypatch):
    r = residents.create("Seren", "Halloway", age=32, gender="female", opening_balance=5000)
    act_as(monkeypatch, r)
    residents.add_funds(r["resident_id"], 250)

    profile = t.residents_get_profile()
    assert profile == {
        "resident_id": r["resident_id"],
        "first_name": "Seren",
        "last_name": "Halloway",
        "gender": "female",
        "age": 32,
        "email": r["email"],
        "created_at": r["created_at"],
        "balance": {"amount": 5250, "currency": "bucks"},
    }
    assert "key" not in profile and "key_hash" not in profile
    assert profile["email"].startswith("seren.halloway") and profile["email"].endswith("@example.com")


def test_transactions_show_what_a_booking_cost_and_match_the_balance(monkeypatch):
    r = residents.create("Seren", "Halloway", age=32, opening_balance=5000)
    act_as(monkeypatch, r)
    booking = flights.flights_book_flight("SA101", date(2030, 3, 13).isoformat(), ["Seren Halloway"], "s@example.mb")

    history = t.residents_list_transactions()
    assert [x["kind"] for x in history] == ["opening_balance", "charge"]
    assert history[-1]["reference"] == booking["reference"]
    assert sum(x["amount"] for x in history) == t.residents_get_profile()["balance"]["amount"]


def test_each_resident_sees_only_themselves(monkeypatch):
    a = residents.create("Seren", "Halloway", age=32)
    b = residents.create("Tavi", "Okonkwo-Reyes", age=28)
    act_as(monkeypatch, b)
    assert t.residents_get_profile()["resident_id"] == b["resident_id"]
    assert all(x["amount"] for x in t.residents_list_transactions())
    act_as(monkeypatch, a)
    assert t.residents_get_profile()["first_name"] == "Seren"


def test_without_a_key_there_is_no_profile(monkeypatch):
    act_as(monkeypatch, None)
    assert error_code(t.residents_get_profile()) == "resident_required"
    assert error_code(t.residents_list_transactions()) == "resident_required"


def test_there_is_no_tool_to_change_anything():
    names = {name for name in dir(t) if name.startswith("residents_")}
    assert names == {"residents_get_profile", "residents_list_transactions"}
