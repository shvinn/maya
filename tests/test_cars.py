"""Car rental: dynamic model years, 24-hour billing, same-day availability,
driver age, and cancellation up to pickup."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from maya.simulations.cars import fleet, rentals
from maya.simulations.cars import mcp_tools as t

from .conftest import NOW, error_code


def at(days: float = 0, hours: float = 0) -> str:
    return (NOW + timedelta(days=days, hours=hours)).strftime("%Y-%m-%d %H:%M")


def book(car: str = "KSW", pickup: str | None = None, dropoff: str | None = None, age: int = 30, email: str = "driver@example.mb") -> dict:
    return t.cars_book_car(car, pickup or at(days=5), dropoff or at(days=7), "Test Driver", age, email)



def test_single_airport_location():
    listing = t.cars_list_cars()
    assert listing["location"]["zone"] == "AIR-APT"
    assert len(listing["cars"]) == len(fleet.CARS)


def test_model_year_is_derived_from_the_current_date(clock):
    swift = fleet.get("KSW")  # one year old
    assert swift.year == NOW.year - 1
    clock.set(datetime(2034, 6, 1))
    assert swift.year == 2033
    assert t.cars_list_cars()["cars"][[c.car_id for c in fleet.CARS].index("KSW")]["year"] == 2033


@pytest.mark.parametrize(("hours", "days"), [(24, 1), (25, 2), (48, 2), (3, 1)])
def test_billing_is_per_started_24_hours(hours, days):
    result = t.cars_search_availability(at(days=3), at(days=3, hours=hours))
    assert result[0]["rental_days"] == days
    assert all(r["total_price"]["amount"] == sum(d["price"] for d in r["daily_prices"]) for r in result)


def test_every_model_is_available_for_same_day_pickup():
    result = t.cars_search_availability(at(hours=1), at(days=1, hours=1))
    assert {r["car_id"] for r in result} == {c.car_id for c in fleet.CARS}


def test_days_fill_up_and_get_dearer_as_they_approach():
    car = fleet.get("OVY")
    far, soon = NOW.date() + timedelta(days=30), NOW.date()
    assert rentals.cars_available(car, far) == car.fleet_size
    assert rentals.price_for_day(car, far) == car.daily_rate
    assert rentals.cars_available(car, soon) == rentals.BACKGROUND_DEMAND_FLOOR_CARS
    assert rentals.price_for_day(car, soon) > car.daily_rate


def test_same_day_sells_out_only_after_real_bookings():
    results = [book("OVY", at(hours=1), at(days=1, hours=1), email=f"d{i}@example.mb") for i in range(3)]
    assert [error_code(r) or "OK" for r in results] == ["OK", "OK", "sold_out"]


def test_search_filters_by_seats_and_class():
    assert [r["car_id"] for r in t.cars_search_availability(at(days=3), at(days=4), min_seats=6)] == ["OVY"]
    assert {r["car_class"] for r in t.cars_search_availability(at(days=3), at(days=4), car_class="SUV")} == {"suv"}


@pytest.mark.parametrize(("age", "code"), [(20, "driver_too_young"), (21, None)])
def test_minimum_driver_age(age, code):
    assert error_code(book(age=age)) == code


@pytest.mark.parametrize(
    ("pickup", "dropoff"),
    [
        (at(hours=-2), at(days=1)),  # pickup in the past
        (at(days=2), at(days=1)),  # drop-off before pickup
        (at(days=1), at(days=40)),  # longer than 30 days
        ("soon", at(days=1)),  # not a datetime
    ],
)
def test_invalid_dates(pickup, dropoff):
    assert error_code(book(pickup=pickup, dropoff=dropoff)) == "invalid_dates"


def test_status_and_cancellation_around_pickup(clock):
    rental = book("TRG", at(days=1), at(days=3))
    ref = rental["rental_reference"]
    assert rental["status"] == "RESERVED"
    assert rental["car"] == f"{NOW.year} Tamaru Ridge"

    clock.advance(days=1, minutes=1)
    assert t.cars_get_rental(ref)["status"] == "PICKED_UP"
    assert error_code(t.cars_cancel_rental(ref)) == "too_late_to_cancel"

    clock.advance(days=2)
    assert t.cars_get_rental(ref)["status"] == "RETURNED"


def test_cancelling_before_pickup_refunds_and_frees_the_car():
    car = fleet.get("VGR")
    day = NOW.date() + timedelta(days=5)
    before = rentals.cars_available(car, day)

    rental = book("VGR")
    assert rentals.cars_available(car, day) == before - 1

    result = t.cars_cancel_rental(rental["rental_reference"])
    assert result["refund"] == rental["total_price"]
    assert rentals.cars_available(car, day) == before
    assert error_code(t.cars_cancel_rental(rental["rental_reference"])) == "already_cancelled"
