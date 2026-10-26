"""Flights and money: every booking is paid for from the calling resident's
wallet, refunds go back to it, and a resident only ever sees and cancels
their own bookings.

Driven through the MCP tool functions, so the error codes asserted here are
exactly what an agent sees. Called directly, a tool finds its resident in
MAYA_RESIDENT_KEY (see conftest.act_as); the HTTP header path is tested
against caller.key() with a stand-in for the request context.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date

import pytest

from maya.simulations.flights import mcp_tools as t
from maya.simulations.residents import caller, residents

from .conftest import act_as, error_code

#: Nine days out: every daily route flies, well outside the 24-hour window.
DAY = date(2030, 3, 13).isoformat()


def new_resident(monkeypatch, opening_balance: int) -> dict:
    r = residents.create("Lena", "Okafor", age=34, opening_balance=opening_balance)
    act_as(monkeypatch, r)
    return r


def fare(flight_number: str, passengers: int = 1) -> int:
    options = t.flights_search_flights("AIR", "MEG", DAY, max_stops=0)["options"]
    option = next(o for o in options if o["flight_number"] == flight_number)
    return option["price_per_passenger"]["amount"] * passengers


def book(flight_number: str = "SA101", passengers: int = 1) -> dict:
    return t.flights_book_flight(flight_number, DAY, [f"P{i}" for i in range(passengers)], "pax@example.mb")


# --- paying -------------------------------------------------------------------


def test_booking_charges_the_wallet_the_total_price(monkeypatch):
    r = new_resident(monkeypatch, 5000)
    booking = book(passengers=2)
    total = booking["total_price"]["amount"]

    assert booking["resident_id"] == r["resident_id"]
    assert booking["payment"]["amount"]["amount"] == -total
    assert booking["payment"]["balance"]["amount"] == 5000 - total
    assert residents.balance(r["resident_id"]) == 5000 - total

    charge = residents.list_transactions(r["resident_id"])[-1]
    assert (charge["kind"], charge["amount"]) == ("charge", -total)
    assert (charge["reference"], charge["domain"]) == (booking["reference"], "flights")


def test_the_payment_is_not_stored_on_the_booking(monkeypatch):
    new_resident(monkeypatch, 5000)
    booking = book()
    assert "payment" not in t.flights_get_booking(booking["reference"])


def test_a_wallet_that_is_exactly_enough_pays_down_to_zero(monkeypatch):
    r = new_resident(monkeypatch, fare("SA101"))
    booking = book()
    assert error_code(booking) is None
    assert residents.balance(r["resident_id"]) == 0


def test_insufficient_funds_books_nothing_and_charges_nothing(monkeypatch):
    price = fare("SA101")
    r = new_resident(monkeypatch, price - 1)
    seats_before = next(
        o["seats_available"] for o in t.flights_search_flights("AIR", "MEG", DAY, max_stops=0)["options"]
        if o["flight_number"] == "SA101"
    )

    assert error_code(book()) == "insufficient_funds"

    assert t.flights_list_bookings() == []
    assert residents.balance(r["resident_id"]) == price - 1
    seats_after = next(
        o["seats_available"] for o in t.flights_search_flights("AIR", "MEG", DAY, max_stops=0)["options"]
        if o["flight_number"] == "SA101"
    )
    assert seats_after == seats_before


def test_concurrent_bookings_cannot_spend_the_same_money(monkeypatch):
    opening = fare("SA101") * 3  # a few bookings' worth, not eight
    r = new_resident(monkeypatch, opening)

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: book(), range(8)))

    # Fares rise as seats sell, so how many fit isn't fixed -- but every
    # booking made was paid for exactly once, and the wallet never went
    # below zero to pay for one.
    made = [x for x in results if error_code(x) is None]
    assert made and len(made) < 8
    assert {error_code(x) for x in results} == {None, "insufficient_funds"}
    spent = sum(x["total_price"]["amount"] for x in made)
    assert residents.balance(r["resident_id"]) == opening - spent >= 0
    assert len(t.flights_list_bookings()) == len(made)
    assert residents.balance(r["resident_id"]) < fare("SA101")  # the refusals were right


# --- refunds ------------------------------------------------------------------


def test_a_refundable_cancellation_puts_the_money_back(monkeypatch):
    r = new_resident(monkeypatch, 5000)
    booking = book()
    cancelled = t.flights_cancel_booking(booking["reference"])

    assert cancelled["refund"]["amount"] == booking["total_price"]["amount"]
    assert cancelled["payment"]["balance"]["amount"] == 5000
    assert residents.balance(r["resident_id"]) == 5000
    refund = residents.list_transactions(r["resident_id"])[-1]
    assert (refund["kind"], refund["reference"]) == ("refund", booking["reference"])


def test_a_refused_cancellation_refunds_nothing(monkeypatch):
    r = new_resident(monkeypatch, 5000)
    booking = book("PF301")  # Puffin Air: never refundable
    assert error_code(t.flights_cancel_booking(booking["reference"])) == "not_refundable"
    assert residents.balance(r["resident_id"]) == 5000 - booking["total_price"]["amount"]


def test_a_booking_cannot_be_refunded_twice(monkeypatch):
    r = new_resident(monkeypatch, 5000)
    booking = book()
    t.flights_cancel_booking(booking["reference"])
    assert error_code(t.flights_cancel_booking(booking["reference"])) == "already_cancelled"
    assert residents.balance(r["resident_id"]) == 5000


# --- whose booking it is ------------------------------------------------------


def test_another_resident_cannot_see_or_cancel_a_booking(monkeypatch):
    owner = new_resident(monkeypatch, 5000)
    booking = book()

    new_resident(monkeypatch, 5000)  # someone else, who knows the reference
    assert error_code(t.flights_get_booking(booking["reference"])) == "not_found"
    assert error_code(t.flights_cancel_booking(booking["reference"])) == "not_found"
    assert t.flights_list_bookings() == []

    act_as(monkeypatch, owner)
    assert t.flights_get_booking(booking["reference"])["status"] == "CONFIRMED"
    assert residents.balance(owner["resident_id"]) == 5000 - booking["total_price"]["amount"]


def test_someone_elses_booking_looks_exactly_like_a_missing_one(monkeypatch):
    new_resident(monkeypatch, 5000)
    booking = book()
    new_resident(monkeypatch, 5000)
    theirs = t.flights_get_booking(booking["reference"])
    missing = t.flights_get_booking("ZZZZZZ")
    assert theirs.structured_content["error"]["code"] == missing.structured_content["error"]["code"]
    assert booking["reference"] not in str(theirs.structured_content).replace(f"'{booking['reference']}'", "")


# --- keys ---------------------------------------------------------------------


def test_without_a_key_searching_works_and_everything_personal_is_refused(monkeypatch):
    act_as(monkeypatch, None)
    assert t.flights_search_flights("AIR", "MEG", DAY, max_stops=0)["options"]
    assert "flight_status" in t.flights_get_flight_status("SA101", DAY)

    assert error_code(book()) == "resident_required"
    assert error_code(t.flights_get_booking("ABC234")) == "resident_required"
    assert error_code(t.flights_list_bookings()) == "resident_required"
    assert error_code(t.flights_cancel_booking("ABC234")) == "resident_required"


def test_a_bad_key_is_refused_not_treated_as_no_key(monkeypatch):
    monkeypatch.setenv(caller.ENV_KEY, "maya_rk_made-up")
    assert error_code(book()) == "invalid_resident_key"


class Request:
    """Stands in for the MCP request context: all caller.key() reads."""

    def __init__(self, headers: dict[str, str]) -> None:
        self.headers = headers


def test_over_http_the_key_comes_from_the_authorization_header(monkeypatch):
    r = residents.create("Ari", "Venn", age=30)
    assert caller.current(Request({"authorization": f"Bearer {r['key']}"}))["resident_id"] == r["resident_id"]
    assert caller.current(Request({"Authorization": f"bearer  {r['key']} "}))["resident_id"] == r["resident_id"]


def test_over_http_the_servers_own_environment_is_never_used(monkeypatch, resident):
    # The server process has a key in its environment; an HTTP request
    # without a header must not borrow it.
    monkeypatch.setenv(caller.ENV_KEY, resident["key"])
    assert caller.key(Request({})) is None
    with pytest.raises(residents.ResidentRequired):
        caller.current(Request({}))


@pytest.mark.parametrize("header", ["Basic abc", "Bearer", "Bearer   ", "maya_rk_no-scheme"])
def test_a_malformed_authorization_header_is_refused(header):
    with pytest.raises(residents.InvalidResidentKey):
        caller.current(Request({"authorization": header}))
