"""What makes two flights a valid one-stop connection.

One stop at most, sold as a single booking. The rules are the ones a real
airline enforces when it sells a connection on one ticket:

- the second flight leaves from the airport the first one lands at;
- the layover is at least ``MIN_CONNECTION_MINUTES`` (the minimum
  connection time -- enough to walk between gates) and at most
  ``MAX_CONNECTION_MINUTES`` (beyond that it's two trips, not a
  connection) -- unless it's an *overnight* connection: the first flight
  lands in the evening (``OVERNIGHT_EARLIEST_ARRIVAL`` or later) and the
  second leaves the next morning (before ``OVERNIGHT_LATEST_DEPARTURE``).
  Airlines sell those on one ticket too, because the timetable gives the
  traveller no better option; the traveller sleeps at the hub, and no
  hotel comes with the ticket;
- neither flight is on a point-to-point-only airline. Puffin Air is an
  ultra-low-cost carrier and, like real ones, sells no connections at all --
  not even Puffin-to-Puffin. Every other airline connects with every other.

Kept in its own module because both search (to build connections) and the
booking tool (to validate one an agent hands back) need the same rules.
"""

from __future__ import annotations

from datetime import date as Date, time, timedelta

from .schedule import ScheduledFlight, flights_on

MIN_CONNECTION_MINUTES = 45
MAX_CONNECTION_MINUTES = 360
OVERNIGHT_EARLIEST_ARRIVAL = time(18, 0)
OVERNIGHT_LATEST_DEPARTURE = time(12, 0)
POINT_TO_POINT_AIRLINES = ("PF",)


def layover_minutes(first: ScheduledFlight, second: ScheduledFlight) -> int:
    return int((second.departure - first.arrival) / timedelta(minutes=1))


def is_overnight(first: ScheduledFlight, second: ScheduledFlight) -> bool:
    """An evening arrival connecting to a next-morning departure."""
    return (
        second.departure.date() == first.arrival.date() + timedelta(days=1)
        and first.arrival.time() >= OVERNIGHT_EARLIEST_ARRIVAL
        and second.departure.time() < OVERNIGHT_LATEST_DEPARTURE
    )


def problem(first: ScheduledFlight, second: ScheduledFlight) -> str | None:
    """Why two flights can't be sold as a connection, or None if they can."""
    if first.pattern.destination != second.pattern.origin:
        return (
            f"{first.flight_number} lands at {first.pattern.destination} but "
            f"{second.flight_number} departs from {second.pattern.origin}."
        )
    for flight in (first, second):
        if flight.pattern.airline in POINT_TO_POINT_AIRLINES:
            return f"{flight.flight_number} is on a point-to-point airline that sells no connections."
    layover = layover_minutes(first, second)
    if layover < 0:
        return f"{second.flight_number} departs before {first.flight_number} lands."
    if layover < MIN_CONNECTION_MINUTES:
        return (
            f"Layover at {first.pattern.destination} would be {layover} minutes; "
            f"the minimum connection time is {MIN_CONNECTION_MINUTES}."
        )
    if layover > MAX_CONNECTION_MINUTES and not is_overnight(first, second):
        return (
            f"Layover at {first.pattern.destination} would be {layover} minutes; "
            f"connections allow at most {MAX_CONNECTION_MINUTES}, or an overnight "
            f"connection from an arrival at {OVERNIGHT_EARLIEST_ARRIVAL:%H:%M} or "
            f"later to a next-day departure before {OVERNIGHT_LATEST_DEPARTURE:%H:%M}."
        )
    return None


def find(origin: str, destination: str, day: Date) -> list[tuple[ScheduledFlight, ScheduledFlight]]:
    """Every valid one-stop pair from origin to destination whose first
    flight departs on ``day``."""
    pairs = []
    for first in flights_on(day, origin=origin):
        if first.pattern.destination == destination:
            continue
        hub = first.pattern.destination
        candidates = flights_on(day, hub, destination) + flights_on(day + timedelta(days=1), hub, destination)
        for second in candidates:
            if problem(first, second) is None:
                pairs.append((first, second))
    return pairs
