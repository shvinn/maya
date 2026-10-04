"""Flights: direct and one-stop search, connection rules and pricing,
booking both legs together, and refund rules."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from maya.simulations.flights import bookings, connections
from maya.simulations.flights import mcp_tools as t

from .conftest import NOW

#: A Wednesday nine days after NOW -- every daily route flies, and it's well
#: outside the 24-hour cancellation window.
DAY = date(2030, 3, 13).isoformat()
NEXT_DAY = date(2030, 3, 14).isoformat()


def error_code(result: dict) -> str | None:
    return result.get("error", {}).get("code")


def seats_sold(flight_number: str, day: str) -> int:
    return bookings._seats_sold(f"{flight_number}/{day}")


def book_option(option: dict, passengers: int = 1) -> dict:
    first, *rest = option["legs"]
    second = rest[0] if rest else None
    return t.flights_book_flight(
        first["flight_number"], first["date"], [f"Passenger {i}" for i in range(passengers)], "pax@example.mb",
        second["flight_number"] if second else None, second["date"] if second else None,
    )


# --- direct flights -----------------------------------------------------------


def test_direct_search_and_booking_keep_their_shape():
    result = t.flights_search_flights("AIR", "MEG", DAY, max_stops=0)
    assert result["options"] and all(o["stops"] == 0 for o in result["options"])

    booking = t.flights_book_flight("SA101", DAY, ["A"], "a@example.mb")
    assert booking["flight_number"] == "SA101"
    assert booking["airline"] == "SA"
    assert booking["stops"] == 0
    assert "connection_discount_per_passenger" not in booking


def test_a_flight_sells_out_when_asked_for_more_seats_than_are_left():
    tomorrow = (NOW.date() + timedelta(days=1)).isoformat()
    option = next(o for o in t.flights_search_flights("AIR", "MEG", tomorrow, max_stops=0)["options"] if o["flight_number"] == "SA103")
    left = option["seats_available"]
    assert 0 < left < option["seats_total"]  # background demand has filled most of it

    result = t.flights_book_flight("SA103", tomorrow, [f"P{i}" for i in range(left + 1)], "a@example.mb")
    assert error_code(result) == "sold_out"
    assert error_code(t.flights_book_flight("SA103", tomorrow, [f"P{i}" for i in range(left)], "a@example.mb")) is None


def test_puffin_fares_are_never_refundable():
    booking = t.flights_book_flight("PF301", DAY, ["A"], "a@example.mb")
    assert error_code(t.flights_cancel_booking(booking["booking_reference"])) == "not_refundable"


def test_cancelling_within_24_hours_of_departure_is_too_late():
    tomorrow = (NOW.date() + timedelta(days=1)).isoformat()
    booking = t.flights_book_flight("SA101", tomorrow, ["A"], "a@example.mb")  # departs 06:30, under 24h away
    assert error_code(t.flights_cancel_booking(booking["booking_reference"])) == "too_late_to_cancel"


# --- one-stop connections -----------------------------------------------------


def test_connections_open_up_city_pairs_with_no_direct_flight():
    assert t.flights_search_flights("VNP", "AIR", DAY, max_stops=0)["total_found"] == 0

    result = t.flights_search_flights("VNP", "AIR", DAY)
    assert result["total_found"] > 0
    for option in result["options"]:
        assert option["stops"] == 1
        assert option["connection_airport"] == "MEG"
        assert len(option["legs"]) == 2


def test_connections_follow_the_layover_and_airline_rules():
    for origin, destination in [("VNP", "AIR"), ("MEG", "KEL"), ("WSG", "KAI")]:
        for option in t.flights_search_flights(origin, destination, DAY, limit=50)["options"]:
            if option["stops"] == 0:
                continue
            assert not any(leg["airline"] in connections.POINT_TO_POINT_AIRLINES for leg in option["legs"])
            if not option["overnight"]:
                assert connections.MIN_CONNECTION_MINUTES <= option["layover_minutes"] <= connections.MAX_CONNECTION_MINUTES


def test_a_connection_is_one_booking_that_takes_and_releases_both_legs():
    option = t.flights_search_flights("VNP", "AIR", DAY)["options"][0]
    legs = [(leg["flight_number"], leg["date"]) for leg in option["legs"]]

    booking = book_option(option, passengers=2)
    assert booking["stops"] == 1 and len(booking["legs"]) == 2
    assert booking["total_price"]["amount"] == 2 * booking["price_per_passenger"]["amount"]
    assert [seats_sold(*leg) for leg in legs] == [2, 2]

    result = t.flights_cancel_booking(booking["booking_reference"])
    assert result["refund"] == booking["total_price"]
    assert [seats_sold(*leg) for leg in legs] == [0, 0]


@pytest.mark.parametrize(
    ("first", "second", "reason"),
    [
        ("SA101", "SA111", "departs from"),  # lands at MEG, second leaves AIR
        ("SA102", "PF305", "point-to-point"),  # Puffin never connects
        ("SA102", "SA111", "departs before"),  # second leaves before first lands
        ("SA108", "SA129", "minimum connection time"),  # 40-minute layover
    ],
)
def test_invalid_connections_are_rejected(first, second, reason):
    result = t.flights_book_flight(first, DAY, ["A"], "a@example.mb", second, DAY)
    assert error_code(result) == "invalid_connection"
    assert reason in result["error"]["message"]


def test_overnight_connection_needs_the_next_day_date():
    result = t.flights_search_flights("KEL", "HLD", DAY)
    assert result["total_found"] > 0
    option = result["options"][0]
    assert option["overnight"] is True
    assert option["legs"][1]["date"] == NEXT_DAY

    first, second = option["legs"]
    forgot = t.flights_book_flight(first["flight_number"], DAY, ["A"], "a@example.mb", second["flight_number"])
    assert error_code(forgot) == "invalid_connection"

    booked = book_option(option)
    assert booked["legs"][1]["date"] == NEXT_DAY


# --- connection pricing -------------------------------------------------------


def test_single_airline_connections_are_discounted_and_interline_ones_are_not():
    options = [o for o in t.flights_search_flights("MEG", "KEL", DAY, limit=50)["options"] if o["stops"] == 1]
    same = [o for o in options if len({leg["airline"] for leg in o["legs"]}) == 1]
    mixed = [o for o in options if len({leg["airline"] for leg in o["legs"]}) == 2]
    assert same and mixed

    for option in same:
        summed = sum(leg["price_per_passenger"]["amount"] for leg in option["legs"])
        assert option["price_per_passenger"]["amount"] == round(summed * (1 - bookings.CONNECTION_DISCOUNT))
        assert option["connection_discount_per_passenger"]["amount"] == summed - option["price_per_passenger"]["amount"]
    for option in mixed:
        assert option["price_per_passenger"]["amount"] == sum(leg["price_per_passenger"]["amount"] for leg in option["legs"])
        assert "connection_discount_per_passenger" not in option


def test_booking_charges_and_refunds_the_discounted_fare():
    option = next(
        o for o in t.flights_search_flights("MEG", "KEL", DAY, limit=50)["options"]
        if o["stops"] == 1 and len({leg["airline"] for leg in o["legs"]}) == 1
    )
    booking = book_option(option, passengers=2)
    assert booking["connection_discount_per_passenger"]["amount"] > 0
    assert booking["total_price"]["amount"] == 2 * booking["price_per_passenger"]["amount"]
    assert t.flights_cancel_booking(booking["booking_reference"])["refund"] == booking["total_price"]
