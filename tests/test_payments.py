"""Money in every domain but flights (see test_flight_payments.py): each
booking is paid for from the calling resident's wallet, refunded to it on
the domain's own terms, declined with insufficient_funds when the wallet
can't cover it -- and only ever visible to the resident who made it.
Dining is the exception: a table costs nothing, or *holds* a fine-dining
deposit until the sitting is over.

Driven through the MCP tool functions, acting as whichever resident's key
is in MAYA_RESIDENT_KEY (see conftest.act_as).
"""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from maya.mcp_tools import SERVERS
from maya.simulations.cars import mcp_tools as cars
from maya.simulations.delivery import mcp_tools as delivery
from maya.simulations.dining import mcp_tools as dining
from maya.simulations.events import mcp_tools as events
from maya.simulations.hotels import mcp_tools as hotels
from maya.simulations.residents import mcp_tools as resident_tools
from maya.simulations.residents import residents

from .conftest import act_as, error_code

WED_NEXT = "2030-03-13"


def new_resident(monkeypatch, opening_balance: int = 100_000) -> dict:
    r = residents.create("Lena", "Okafor", age=34, opening_balance=opening_balance)
    act_as(monkeypatch, r)
    return r


#: Per domain: make one booking, then get, list and cancel by reference.
DOMAINS = {
    "hotels": (
        lambda: hotels.hotels_book_room("HLH-STD", "2030-03-14", "2030-03-16", 2, "Lena Okafor", "l@example.mb"),
        hotels.hotels_get_booking, hotels.hotels_list_bookings, hotels.hotels_cancel_booking,
    ),
    "cars": (
        lambda: cars.cars_book_car("KSW", "2030-03-09 10:00", "2030-03-11 10:00", "Lena Okafor", 34, "l@example.mb"),
        cars.cars_get_rental, cars.cars_list_rentals, cars.cars_cancel_rental,
    ),
    "delivery": (
        lambda: delivery.delivery_place_order("OQN", [{"item_id": "OQN-01", "quantity": 2}], "AIR-OLD", "+1"),
        delivery.delivery_get_order, delivery.delivery_list_orders, delivery.delivery_cancel_order,
    ),
    "events": (
        lambda: events.events_book_tickets("SYM", "2030-03-09", "ACH-STL", 2, "Lena Okafor", "l@example.mb"),
        events.events_get_booking, events.events_list_bookings, events.events_cancel_booking,
    ),
}
PAID = pytest.mark.parametrize("domain", DOMAINS)


# --- paying -------------------------------------------------------------------


@PAID
def test_booking_charges_the_total_to_the_wallet(monkeypatch, domain):
    r = new_resident(monkeypatch)
    made = DOMAINS[domain][0]()
    total = made["total_price"]["amount"]
    assert total > 0

    assert made["resident_id"] == r["resident_id"]
    assert made["payment"]["kind"] == "charge"
    assert made["payment"]["amount"]["amount"] == -total
    assert made["payment"]["available"]["amount"] == 100_000 - total
    charge = residents.list_transactions(r["resident_id"])[-1]
    assert (charge["kind"], charge["amount"], charge["domain"]) == ("charge", -total, domain)
    assert charge["reference"] == made["reference"]


@PAID
def test_without_the_money_nothing_is_booked(monkeypatch, domain):
    r = new_resident(monkeypatch, opening_balance=1)
    made = DOMAINS[domain][0]()
    assert error_code(made) == "insufficient_funds"
    assert DOMAINS[domain][2]() == []
    assert residents.balance(r["resident_id"]) == 1
    assert [t["kind"] for t in residents.list_transactions(r["resident_id"])] == ["opening_balance"]


@PAID
def test_cancelling_refunds_on_the_domains_terms(monkeypatch, domain):
    r = new_resident(monkeypatch)
    made = DOMAINS[domain][0]()
    cancelled = DOMAINS[domain][3](made["reference"])

    kept = cancelled.get("fee_not_refunded", {"amount": 0})["amount"]  # events keep the service fee
    assert cancelled["refund"]["amount"] == made["total_price"]["amount"] - kept
    assert cancelled["payment"]["kind"] == "refund"
    assert residents.balance(r["resident_id"]) == 100_000 - kept
    assert error_code(DOMAINS[domain][3](made["reference"])) == "already_cancelled"
    assert residents.balance(r["resident_id"]) == 100_000 - kept  # not refunded twice


def test_a_delivery_order_is_refunded_in_full_including_the_fee(monkeypatch):
    r = new_resident(monkeypatch)
    made = DOMAINS["delivery"][0]()
    assert made["delivery_fee"]["amount"] > 0
    delivery.delivery_cancel_order(made["reference"])
    assert residents.balance(r["resident_id"]) == 100_000


# --- whose booking it is ------------------------------------------------------


@PAID
def test_another_resident_cannot_see_or_cancel_it(monkeypatch, domain):
    book, get, listed, cancel = DOMAINS[domain]
    owner = new_resident(monkeypatch)
    made = book()

    new_resident(monkeypatch)
    assert error_code(get(made["reference"])) == "not_found"
    assert error_code(cancel(made["reference"])) == "not_found"
    assert listed() == []

    act_as(monkeypatch, owner)
    assert get(made["reference"])["reference"] == made["reference"]
    assert residents.balance(owner["resident_id"]) == 100_000 - made["total_price"]["amount"]


@PAID
def test_without_a_key_nothing_personal_works(monkeypatch, domain):
    book, get, listed, cancel = DOMAINS[domain]
    act_as(monkeypatch, None)
    assert error_code(book()) == "resident_required"
    assert error_code(get("ABC234")) == "resident_required"
    assert error_code(listed()) == "resident_required"
    assert error_code(cancel("ABC234")) == "resident_required"


def test_browsing_needs_no_key(monkeypatch):
    act_as(monkeypatch, None)
    assert hotels.hotels_list_hotels()
    assert cars.cars_list_cars()
    assert delivery.delivery_list_vendors()
    assert events.events_list_venues()
    assert dining.dining_check_availability("OQN", "2030-03-06")["slots"]


# --- dining: free tables, and held deposits -----------------------------------


def lpk(party: int = 2) -> dict:
    """A table for ``party`` at Lamplight Kitchen, fine dining: 25 a guest held."""
    return dining.dining_book_table("LPK", WED_NEXT, "19:00", party, "Lena Okafor", "+1")


def test_an_ordinary_table_costs_nothing(monkeypatch):
    r = new_resident(monkeypatch, opening_balance=0)
    made = dining.dining_book_table("OQN", "2030-03-06", "13:00", 2, "Lena Okafor", "+1")
    assert error_code(made) is None
    assert made["payment"]["kind"] is None and made["payment"]["amount"]["amount"] == 0
    assert [t["kind"] for t in residents.list_transactions(r["resident_id"])] == ["opening_balance"]


def test_fine_dining_holds_the_deposit_without_spending_it(monkeypatch):
    r = new_resident(monkeypatch, opening_balance=1000)
    made = lpk()
    assert made["deposit"]["amount"] == 50
    assert made["payment"]["kind"] == "hold"

    profile = resident_tools.residents_get_profile()
    assert profile["balance"]["amount"] == 1000  # still theirs
    assert profile["held"]["amount"] == 50
    assert profile["available"]["amount"] == 950  # but not spendable
    hold = resident_tools.residents_list_transactions()[-1]
    assert (hold["kind"], hold["amount"], hold["reference"]) == ("hold", -50, made["reference"])
    assert hold["expires_at"] > f"{WED_NEXT}T19:00:00"  # the end of the sitting


def test_held_money_cannot_be_spent_elsewhere(monkeypatch):
    order = DOMAINS["delivery"][0]
    new_resident(monkeypatch)
    price = order()["total_price"]["amount"]  # what the same order costs

    r = new_resident(monkeypatch, opening_balance=price + 49)
    lpk()  # holds 50, leaving price - 1 available
    assert error_code(order()) == "insufficient_funds"
    assert residents.balance(r["resident_id"]) == price + 49


def test_without_enough_available_the_table_is_not_booked(monkeypatch):
    new_resident(monkeypatch, opening_balance=49)
    assert error_code(lpk()) == "insufficient_funds"
    assert dining.dining_list_reservations() == []


def test_cancelling_early_releases_the_hold(monkeypatch):
    r = new_resident(monkeypatch, opening_balance=1000)
    made = lpk()
    cancelled = dining.dining_cancel_reservation(made["reference"])
    assert cancelled["payment"]["kind"] == "release"
    assert cancelled["deposit_kept"]["amount"] == 0
    assert residents.wallet(r["resident_id"])["held"]["amount"] == 0
    assert residents.balance(r["resident_id"]) == 1000
    assert [t["kind"] for t in residents.list_transactions(r["resident_id"])] == [
        "opening_balance", "hold", "release",
    ]


def test_cancelling_late_keeps_the_deposit(monkeypatch, clock):
    r = new_resident(monkeypatch, opening_balance=1000)
    made = lpk()
    clock.set(datetime(2030, 3, 13, 12, 0))  # 7 hours ahead; the cutoff is 24
    cancelled = dining.dining_cancel_reservation(made["reference"])
    assert cancelled["deposit_kept"]["amount"] == 50
    assert cancelled["payment"]["kind"] == "charge"
    assert residents.balance(r["resident_id"]) == 950
    assert residents.held(r["resident_id"]) == 0
    assert [t["kind"] for t in residents.list_transactions(r["resident_id"])] == [
        "opening_balance", "hold", "release", "charge",
    ]


def test_after_the_meal_the_hold_lapses_by_itself(monkeypatch, clock):
    r = new_resident(monkeypatch, opening_balance=1000)
    lpk()
    clock.set(datetime(2030, 3, 13, 19, 30))  # seated
    assert residents.held(r["resident_id"]) == 50
    clock.set(datetime(2030, 3, 14, 9, 0))  # long after the sitting
    assert residents.held(r["resident_id"]) == 0
    assert residents.available(r["resident_id"]) == 1000
    # Nothing had to be written for it to lapse.
    assert [t["kind"] for t in residents.list_transactions(r["resident_id"])] == ["opening_balance", "hold"]


def test_a_reset_drops_holds(monkeypatch):
    from maya.reset import reset_world

    r = new_resident(monkeypatch, opening_balance=1000)
    lpk()
    reset_world()
    assert residents.wallet(r["resident_id"])["held"]["amount"] == 0


# --- in-process, through the server -------------------------------------------


@pytest.mark.parametrize("domain", ["flights", "hotels"])
def test_a_tool_called_through_the_server_in_process_finds_its_resident(monkeypatch, domain):
    """A harness calling tools in-process (server.call_tool) gets a Context
    with no HTTP request behind it; the resident comes from
    MAYA_RESIDENT_KEY, as on stdio, instead of the call failing."""
    r = new_resident(monkeypatch)
    tool, args = {
        "flights": ("flights_book_flight", {
            "flight_number": "SA101", "date": "2030-03-13", "passenger_names": ["A"], "contact_email": "a@example.mb",
        }),
        "hotels": ("hotels_book_room", {
            "room_type_id": "HLH-STD", "check_in": "2030-03-14", "check_out": "2030-03-15", "guests": 1,
            "guest_name": "A", "contact_email": "a@example.mb",
        }),
    }[domain]
    result = asyncio.run(SERVERS[domain].call_tool(tool, args))
    assert not result.is_error
    assert residents.balance(r["resident_id"]) < 100_000


# --- edges the review found untested ------------------------------------------


def test_a_late_cancel_is_covered_even_with_nothing_else_available(monkeypatch, clock):
    """All the money is held, so none is available -- yet keeping the
    deposit works: the hold is released first, and the charge spends what
    that frees."""
    r = new_resident(monkeypatch, opening_balance=50)
    made = lpk()
    assert residents.available(r["resident_id"]) == 0
    clock.set(datetime(2030, 3, 13, 12, 0))
    cancelled = dining.dining_cancel_reservation(made["reference"])
    assert error_code(cancelled) is None
    assert cancelled["deposit_kept"]["amount"] == 50
    assert residents.wallet(r["resident_id"])["balance"]["amount"] == 0
    assert residents.held(r["resident_id"]) == 0


def test_a_hold_and_a_charge_racing_for_the_same_money_cannot_both_win(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor

    stay = DOMAINS["hotels"][0]
    new_resident(monkeypatch)
    price = stay()["total_price"]["amount"]  # what the same stay costs

    r = new_resident(monkeypatch, opening_balance=price + 49)  # the stay, or the 50 hold -- not both
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda book: book(), [stay, lpk]))
    won = [x for x in results if error_code(x) is None]
    assert len(won) == 1
    assert residents.available(r["resident_id"]) >= 0


def test_a_booking_that_arrives_during_a_reset_is_paid_for(monkeypatch):
    """The reset is one transaction. A booking arriving mid-reset waits for
    it and lands after, charge and all -- it can't slip between the domains
    being emptied and the wallets, keeping its order and losing its charge."""
    import threading

    from maya.reset import reset_world
    from maya.world import clock as world_clock

    r = new_resident(monkeypatch, opening_balance=1000)
    placed = {}
    real_reset = world_clock._reset

    def reset_then_order(conn):
        real_reset(conn)  # the domains are emptied by now; the wallets are next
        worker = threading.Thread(target=lambda: placed.update(order=DOMAINS["delivery"][0]()))
        worker.start()
        worker.join(timeout=0.3)  # it can't get in: the reset holds the database
        placed["waited"] = worker.is_alive()
        placed["worker"] = worker

    monkeypatch.setattr(world_clock, "_reset", reset_then_order)
    reset_world()
    placed["worker"].join(timeout=10)

    assert placed["waited"]
    order = placed["order"]
    assert error_code(order) is None
    assert delivery.delivery_get_order(order["reference"])["reference"] == order["reference"]
    assert residents.balance(r["resident_id"]) == 1000 - order["total_price"]["amount"]
    delivery.delivery_cancel_order(order["reference"])
    assert residents.balance(r["resident_id"]) == 1000  # not 1000 + the refund
