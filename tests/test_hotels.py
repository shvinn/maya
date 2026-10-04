"""Hotels: per-night inventory and pricing, cancellation windows, live status.

Driven through the MCP tool functions, so the error codes asserted here are
exactly what an agent sees.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from maya.simulations.hotels import bookings, hotels
from maya.simulations.hotels import mcp_tools as t

from .conftest import NOW, error_code, error_of


def day(n: int) -> str:
    return (NOW.date() + timedelta(days=n)).isoformat()


def book(room: str = "HLH-STD", check_in: int = 10, nights: int = 2, guests: int = 2, email: str = "guest@example.mb") -> dict:
    return t.hotels_book_room(room, day(check_in), day(check_in + nights), guests, "Test Guest", email)



# --- search -------------------------------------------------------------------


def test_search_totals_are_the_sum_of_nightly_prices_cheapest_first():
    results = t.hotels_search_availability(day(10), day(13), guests=2)
    assert results
    for r in results:
        assert r["nights"] == 3
        assert r["total_price"]["amount"] == sum(n["price"] for n in r["nightly_prices"])
    totals = [r["total_price"]["amount"] for r in results]
    assert totals == sorted(totals)


def test_search_filters_by_zone_name_and_guest_count():
    results = t.hotels_search_availability(day(10), day(11), guests=3, zone="Riverside Heights")
    assert results
    assert {r["zone"] for r in results} == {"AIR-RIV"}
    assert all(r["max_guests"] >= 3 for r in results)


def test_search_with_unknown_zone_is_an_error_not_an_empty_list():
    assert error_code(t.hotels_search_availability(day(10), day(11), zone="Atlantis")) == "not_found"


# --- price and availability move with time ------------------------------------


def test_nights_fill_up_and_get_dearer_as_they_approach():
    room = hotels.get_room_type("TCG-STD")
    far, mid, tonight = (NOW.date() + timedelta(days=n) for n in (60, 15, 0))

    assert bookings.rooms_available(room, far) == room.rooms_total
    assert bookings.price_for_night(room, far) == room.nightly_rate

    assert bookings.rooms_available(room, far) > bookings.rooms_available(room, mid) > bookings.rooms_available(room, tonight)
    assert bookings.price_for_night(room, far) < bookings.price_for_night(room, mid) < bookings.price_for_night(room, tonight)
    # Background demand levels off around 85%, like a busy real hotel -- not sold out.
    assert 0 < bookings.rooms_available(room, tonight) <= room.rooms_total * 0.2


def test_background_demand_never_takes_the_last_room():
    suite = hotels.get_room_type("TCG-STE")  # only 2 rooms
    assert bookings.rooms_available(suite, NOW.date()) >= bookings.BACKGROUND_DEMAND_FLOOR_ROOMS


def test_price_is_locked_at_booking_time():
    room = hotels.get_room_type("RVS-1BR")
    night = NOW.date() + timedelta(days=20)
    first = book("RVS-1BR", check_in=20, nights=1)
    for i in range(3):
        book("RVS-1BR", check_in=20, nights=1, email=f"other{i}@example.mb")

    assert bookings.price_for_night(room, night) > first["total_price"]["amount"]
    assert t.hotels_get_booking(first["booking_reference"])["total_price"] == first["total_price"]


# --- booking rules ------------------------------------------------------------


def test_a_room_type_sells_out_on_its_fullest_night():
    results = [book("TCG-STE", check_in=1, nights=2, guests=2, email=f"g{i}@example.mb") for i in range(5)]
    codes = [error_code(r) or "OK" for r in results]
    assert codes[0] == "OK"
    assert "sold_out" in codes
    sold_out = next(r for r in results if error_code(r) == "sold_out")
    assert day(1) in error_of(sold_out)["message"]


def test_too_many_guests_for_the_room():
    assert error_code(t.hotels_book_room("TWH-DRM", day(5), day(6), 2, "X", "x@example.mb")) == "too_many_guests"


def test_guests_must_be_at_least_one():
    assert error_code(t.hotels_book_room("HLH-STD", day(5), day(6), 0, "X", "x@example.mb")) == "invalid_guests"


@pytest.mark.parametrize(
    ("check_in", "check_out"),
    [
        (day(-1), day(1)),  # in the past
        (day(3), day(3)),  # zero nights
        (day(1), day(20)),  # longer than 14 nights
        ("tomorrow", day(2)),  # not a date
        (day(400), day(401)),  # beyond 365 days
    ],
)
def test_invalid_dates(check_in, check_out):
    assert error_code(t.hotels_book_room("HLH-STD", check_in, check_out, 1, "X", "x@example.mb")) == "invalid_dates"


def test_unknown_room_type():
    assert error_code(t.hotels_book_room("NOPE-1", day(5), day(6), 1, "X", "x@example.mb")) == "not_found"


# --- cancellation -------------------------------------------------------------


def test_non_refundable_hotel_can_never_be_cancelled():
    booking = book("TWH-PRV", check_in=20)
    assert error_code(t.hotels_cancel_booking(booking["booking_reference"])) == "not_refundable"


def test_cancelling_inside_the_hotels_cutoff_is_too_late():
    booking = book("TCG-STD", check_in=1)  # The Capitol Grand: 72h cutoff
    assert error_code(t.hotels_cancel_booking(booking["booking_reference"])) == "too_late_to_cancel"


def test_cancelling_outside_the_cutoff_refunds_in_full_and_frees_the_room():
    room = hotels.get_room_type("HLH-SEA")
    night = NOW.date() + timedelta(days=10)
    before = bookings.rooms_available(room, night)

    booking = book("HLH-SEA", check_in=10)
    assert bookings.rooms_available(room, night) == before - 1

    result = t.hotels_cancel_booking(booking["booking_reference"])
    assert result["status"] == "CANCELLED"
    assert result["refund"] == booking["total_price"]
    assert bookings.rooms_available(room, night) == before
    assert error_code(t.hotels_cancel_booking(booking["booking_reference"])) == "already_cancelled"


# --- live status --------------------------------------------------------------


def test_status_follows_check_in_and_check_out_times(clock):
    booking = book("SAH-STD", check_in=0, nights=2, guests=1)
    ref = booking["booking_reference"]
    assert t.hotels_get_booking(ref)["status"] == "CONFIRMED"

    clock.set(datetime.combine(NOW.date(), bookings.CHECK_IN_TIME))
    assert t.hotels_get_booking(ref)["status"] == "CHECKED_IN"

    clock.set(datetime.combine(NOW.date() + timedelta(days=2), bookings.CHECK_OUT_TIME))
    assert t.hotels_get_booking(ref)["status"] == "CHECKED_OUT"


def test_list_bookings_filters_by_email_case_insensitively():
    book(email="Alice@Example.mb")
    book(email="bob@example.mb")
    assert len(t.hotels_list_bookings(email="alice@example.mb")) == 1
