"""Dining: shared restaurants, opening days and slots, party limits, peak
slots filling up ahead, deposits kept on late cancellation, and live status.

NOW is Monday 2030-03-04 09:00, so the coming Saturday (2030-03-09) is five
days out -- close enough that the hardest tables are gone at peak.
"""

from __future__ import annotations

from maya.simulations.delivery import mcp_tools as delivery
from maya.simulations.dining import mcp_tools as t

from .conftest import error_code

TUE, WED, SAT = "2030-03-05", "2030-03-06", "2030-03-09"
WED_NEXT = "2030-03-13"
FAR = "2030-04-02"  # 29 days out: outside the demand horizon


def book(code="OQN", day=WED, at="13:00", party=2, phone="+00 555 0101") -> dict:
    return t.dining_book_table(code, day, at, party, "Test Diner", phone)


def seats(code: str, day: str, at: str) -> int:
    slots = t.dining_check_availability(code, day)["slots"]
    return next(s["seats_left"] for s in slots if s["time"] == at)


# --- restaurants --------------------------------------------------------------


def test_restaurants_are_shared_with_delivery_but_not_every_one_has_tables():
    dine_in = {r["code"] for r in t.dining_search_restaurants(limit=100)}
    delivers = {v["code"] for v in delivery.delivery_list_vendors()}
    assert "HBC" in dine_in and "HBC" in delivers  # both
    assert "LPK" in dine_in and "LPK" not in delivers  # dine-in only
    assert "NOR" in delivers and "NOR" not in dine_in  # a ghost kitchen
    assert error_code(t.dining_get_restaurant("NOR")) == "not_found"


def test_the_menu_is_the_same_one_delivery_sells():
    dining_menu = {i["item_id"] for i in t.dining_get_restaurant("HBC")["menu"]}
    delivery_menu = {i["item_id"] for i in delivery.delivery_get_menu("HBC")["items"]}
    assert dining_menu == delivery_menu


def test_search_by_zone_name_and_cuisine():
    found = t.dining_search_restaurants(zone="Lantern Market", cuisine="indian")
    assert {r["code"] for r in found} >= {"SRC", "TDA"}
    assert all(r["zone"] == "AIR-MKT" and r["zone_name"] == "Lantern Market" for r in found)
    assert error_code(t.dining_search_restaurants(zone="Atlantis")) == "not_found"


# --- the booking rules --------------------------------------------------------


def test_a_closed_day_is_refused():
    assert error_code(t.dining_check_availability("LPK", TUE)) == "closed"
    assert error_code(book("LPK", TUE)) == "closed"


def test_only_half_hour_slots_up_to_the_last_seating():
    assert error_code(book(at="19:15")) == "invalid_time"
    # OQN closes at 21:30 and a sitting is 75 minutes, so 20:00 is the last
    assert t.dining_check_availability("OQN", WED)["slots"][-1]["time"] == "20:00"
    assert error_code(book(at="20:30")) == "invalid_time"


def test_party_size_limits():
    assert error_code(book(party=0)) == "invalid_party_size"
    assert error_code(book(party=7)) == "party_too_large"  # OQN seats up to 6
    assert "reference" in book("ANC", FAR, party=12)  # the tapas bar takes groups


def test_dates_must_be_inside_the_booking_window():
    assert error_code(book(day="2030-03-03")) == "invalid_date"
    assert error_code(book(day="2030-04-30")) == "invalid_date"


# --- other diners -------------------------------------------------------------


def test_peak_slots_at_popular_places_fill_up_days_ahead():
    assert error_code(book("CLD", SAT, "19:30")) == "fully_booked"
    assert seats("CLD", SAT, "17:30") > 0  # early tables are still there
    assert seats("CLD", FAR, "19:30") == 10  # a month out, nobody has booked yet


def test_booking_takes_seats_and_cancelling_gives_them_back():
    before = seats("OQN", WED, "13:00")
    ref = book(party=4)["reference"]
    assert seats("OQN", WED, "13:00") == before - 4
    t.dining_cancel_reservation(ref)
    assert seats("OQN", WED, "13:00") == before


# --- deposits and cancellation ------------------------------------------------


def test_no_deposit_means_free_cancellation_until_the_booking_time(clock):
    made = book()
    assert made["deposit"]["amount"] == 0
    clock.set(clock.now.replace(day=6, hour=12, minute=59))
    assert t.dining_cancel_reservation(made["reference"])["status"] == "CANCELLED"


def test_a_deposit_is_released_only_if_cancelled_early_enough(clock):
    early = book("LPK", WED_NEXT, "19:00")
    assert early["deposit"]["amount"] == 50  # 25 a guest
    released = t.dining_cancel_reservation(early["reference"])
    assert released["deposit_released"]["amount"] == 50
    assert released["refund"]["amount"] == 0  # it was held, never charged

    late = book("LPK", WED_NEXT, "19:00")
    clock.set(clock.now.replace(day=13, hour=12))  # 7 hours ahead; the cutoff is 24
    cancelled = t.dining_cancel_reservation(late["reference"])
    assert cancelled["refund"]["amount"] == 0
    assert cancelled["deposit_released"]["amount"] == 0
    assert cancelled["deposit_kept"]["amount"] == 50
    assert error_code(t.dining_cancel_reservation(late["reference"])) == "already_cancelled"


def test_status_runs_confirmed_seated_completed(clock):
    ref = book()["reference"]
    assert t.dining_get_reservation(ref)["status"] == "CONFIRMED"
    clock.set(clock.now.replace(day=6, hour=13, minute=30))
    assert t.dining_get_reservation(ref)["status"] == "SEATED"
    assert error_code(t.dining_cancel_reservation(ref)) == "too_late_to_cancel"
    clock.set(clock.now.replace(day=6, hour=14, minute=15))
    assert t.dining_get_reservation(ref)["status"] == "COMPLETED"
    assert [r["reference"] for r in t.dining_list_reservations(status="COMPLETED")] == [ref]
