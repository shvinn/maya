"""Flight operations: each airline's reliability, delays and cancellations
revealed as departure approaches, and the refunds they unlock.

Fates are fixed per flight and date, so these tests look up a flight with
the fate they need rather than hard-coding one. NOW is Monday 2030-03-04
09:00; every flight used here is at least three days out, so nothing about
it is known yet when it's booked.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

from maya.mcp_tools import SERVERS
from maya.reset import reset_world
from maya.simulations.flights import connections, operations, schedule
from maya.simulations.flights import mcp_tools as t
from maya.simulations.flights.schedule import ScheduledFlight
from maya.world import mcp_tools as world

from .conftest import NOW, error_code


def find_flight(predicate, airline: str | None = None) -> ScheduledFlight:
    for days in range(3, 60):
        for flight in schedule.flights_on(NOW.date() + timedelta(days=days)):
            if (airline is None or flight.pattern.airline == airline) and predicate(operations.fate(flight)):
                return flight
    raise AssertionError("no flight with that fate in the next two months")


def book(*legs: ScheduledFlight) -> dict:
    first, *rest = legs
    second = rest[0] if rest else None
    return t.flights_book_flight(
        first.flight_number, first.date.isoformat(), ["Ada Traveller"], "ada@example.mb",
        second.flight_number if second else None, second.date.isoformat() if second else None,
    )


def status(flight: ScheduledFlight) -> dict:
    return t.flights_get_flight_status(flight.flight_number, flight.date.isoformat())


def test_reliability_is_each_airlines_character():
    def delayed_share(airline: str) -> float:
        flights = [
            f for d in range(56) for f in schedule.flights_on(date(2030, 3, 4) + timedelta(days=d))
            if f.pattern.airline == airline
        ]
        return sum(operations.fate(f).delay_minutes > 0 for f in flights) / len(flights)

    assert delayed_share("PF") > delayed_share("SA") > delayed_share("MX")


def test_a_fate_is_the_same_every_time_it_is_asked():
    flight = schedule.flights_on(date(2030, 3, 9))[0]
    assert len({operations.fate(flight) for _ in range(5)}) == 1


def test_a_cancellation_is_only_announced_24_hours_ahead(clock):
    flight = find_flight(lambda f: f.cancelled)
    assert status(flight)["flight_status"] == "SCHEDULED"
    assert "reference" in book(flight)  # bookable while nobody knows

    clock.set(flight.departure - timedelta(hours=24))
    assert status(flight)["flight_status"] == "CANCELLED"
    assert error_code(book(flight)) == "flight_cancelled"
    found = t.flights_search_flights(flight.pattern.origin, flight.pattern.destination, flight.date.isoformat())
    assert flight.flight_number not in {leg["flight_number"] for o in found["options"] for leg in o["legs"]}
    assert found["cancelled_or_misconnecting"] >= 1


def test_a_cancelled_flight_is_refunded_in_full_even_on_puffin_and_inside_24_hours(clock):
    flight = find_flight(lambda f: f.cancelled, airline="PF")
    made = book(flight)
    assert error_code(t.flights_cancel_booking(made["reference"])) == "not_refundable"

    clock.set(flight.departure - timedelta(hours=2))
    booking = t.flights_get_booking(made["reference"])
    assert booking["disruption"] == "FLIGHT_CANCELLED"
    assert booking["legs"][0]["flight_status"] == "CANCELLED"
    cancelled = t.flights_cancel_booking(made["reference"])
    assert cancelled["refund"]["amount"] == made["total_price"]["amount"]


def test_a_delay_is_announced_3_hours_ahead_and_moves_both_times(clock):
    flight = find_flight(lambda f: 30 <= f.delay_minutes < 180)
    delay = operations.fate(flight).delay_minutes

    clock.set(flight.departure - timedelta(hours=4))
    assert status(flight)["flight_status"] == "SCHEDULED"

    clock.set(flight.departure - timedelta(hours=3))
    now_known = status(flight)
    assert (now_known["flight_status"], now_known["delay_minutes"]) == ("DELAYED", delay)
    assert now_known["estimated_arrival"] == f"{flight.arrival + timedelta(minutes=delay):%Y-%m-%d %H:%M}"

    clock.set(flight.departure + timedelta(minutes=delay))
    assert status(flight)["flight_status"] == "DEPARTED"
    clock.set(flight.arrival + timedelta(minutes=delay))
    assert status(flight)["flight_status"] == "LANDED"


def test_a_short_delay_changes_no_fare_rule(clock):
    flight = find_flight(lambda f: 0 < f.delay_minutes < 180, airline="SA")
    made = book(flight)
    clock.set(flight.departure - timedelta(hours=2))
    assert t.flights_get_booking(made["reference"])["disruption"] == "DELAYED"
    assert error_code(t.flights_cancel_booking(made["reference"])) == "too_late_to_cancel"


def test_a_long_delay_refunds_until_the_flight_leaves(clock):
    flight = find_flight(lambda f: f.delay_minutes >= 180, airline="PF")
    late = book(flight)
    clock.set(flight.departure - timedelta(hours=2))
    assert t.flights_get_booking(late["reference"])["disruption"] == "LONG_DELAY"
    assert t.flights_cancel_booking(late["reference"])["refund"]["amount"] == late["total_price"]["amount"]


def test_a_long_delay_refunds_nothing_once_the_traveller_has_flown(clock):
    flight = find_flight(lambda f: f.delay_minutes >= 180, airline="SA")
    made = book(flight)
    clock.set(flight.arrival + timedelta(minutes=operations.fate(flight).delay_minutes + 10))
    assert error_code(t.flights_cancel_booking(made["reference"])) == "too_late_to_cancel"


def test_a_delay_that_breaks_a_connection_makes_it_missed_and_refundable(clock):
    pair = None
    for days in range(3, 60):
        day = NOW.date() + timedelta(days=days)
        for origin, destination in schedule.routes():
            for first, second in connections.find(origin, destination, day):
                delay = operations.fate(first).delay_minutes
                second_fate = operations.fate(second)
                if not second_fate.cancelled and not second_fate.delay_minutes and \
                        connections.layover_minutes(first, second) - delay < connections.MIN_CONNECTION_MINUTES:
                    pair = first, second
                    break
            if pair:
                break
        if pair:
            break
    assert pair, "no connection broken by a delay in the next two months"
    first, second = pair

    made = book(first, second)
    clock.set(first.departure - timedelta(hours=3))
    assert t.flights_get_booking(made["reference"])["disruption"] == "MISSED_CONNECTION"
    assert error_code(book(first, second)) == "invalid_connection"  # no longer sold
    assert t.flights_cancel_booking(made["reference"])["refund"]["amount"] == made["total_price"]["amount"]


def test_status_of_a_flight_that_does_not_operate_is_not_found():
    assert error_code(t.flights_get_flight_status("MX201", "2030-03-10")) == "not_found"  # weekdays only
    assert error_code(t.flights_get_flight_status("ZZ999", "2030-03-10")) == "not_found"


# --- the world seed and overrides (the world server's controls) -----------------


def fates(days: int = 14) -> list:
    return [
        operations.fate(f) for d in range(days) for f in schedule.flights_on(NOW.date() + timedelta(days=d))
    ]


def test_a_seed_replays_a_world_and_a_new_seed_brings_new_luck():
    reset_world(seed=1)
    first = fates()
    reset_world(seed=2)
    assert fates() != first
    reset_world(seed=1)
    assert fates() == first


def test_a_reset_without_a_seed_uses_maya_seed():
    reset_world()
    assert world.world_get_seed() == {"seed": 20300304}


def test_the_seed_is_not_shown_to_the_agent_under_test():
    agent_tools = {t.name for name, server in SERVERS.items() if name != "world" for t in asyncio.run(server.list_tools())}
    assert "world_get_seed" not in agent_tools
    assert "seed" not in str(asyncio.run(SERVERS["flights"].call_tool("world_get_time", {})))


def test_an_override_decides_a_flight_and_is_announced_on_schedule(clock):
    flight = find_flight(lambda f: not f.cancelled and not f.delay_minutes, airline="MX")
    made = world.world_set_flight_status(flight.flight_number, flight.date.isoformat(), cancelled=True)
    assert made["announced_from"] == f"{flight.departure - timedelta(hours=24):%Y-%m-%d %H:%M}"
    assert status(flight)["flight_status"] == "SCHEDULED"  # not announced yet
    clock.set(flight.departure - timedelta(hours=24))
    assert status(flight)["flight_status"] == "CANCELLED"


def test_an_override_can_delay_a_flight_and_a_reset_drops_it(clock):
    flight = find_flight(lambda f: not f.cancelled and not f.delay_minutes)
    world.world_set_flight_status(flight.flight_number, flight.date.isoformat(), delay_minutes=300)
    clock.set(flight.departure - timedelta(hours=1))
    assert (status(flight)["flight_status"], status(flight)["delay_minutes"]) == ("DELAYED", 300)
    reset_world()
    clock.set(flight.departure - timedelta(hours=1))
    assert status(flight)["flight_status"] == "SCHEDULED"


def test_an_override_must_make_sense():
    flight = schedule.flights_on(date(2030, 3, 9))[0]
    args = flight.flight_number, flight.date.isoformat()
    assert error_code(world.world_set_flight_status(*args, delay_minutes=30, cancelled=True)) == "invalid_request"
    assert error_code(world.world_set_flight_status(*args, delay_minutes=-5)) == "invalid_request"
    assert error_code(world.world_set_flight_status("MX201", "2030-03-10", cancelled=True)) == "not_found"
