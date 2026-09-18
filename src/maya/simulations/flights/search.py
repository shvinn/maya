"""Searching the timetable.

Search does one thing: filter the fixed weekly schedule down to what a traveller
asked for. All flights are direct -- there is no connection-building. If a city
pair has no non-stop service, the answer is that there is no flight, not a
multi-leg itinerary.

Fares are not stored -- see ``bookings.price_per_passenger`` for how they are
computed from the airline's rate, flight duration and how full the flight is.
"""

from __future__ import annotations

from datetime import date as Date
from typing import Iterable

from . import bookings
from .airports import resolve as resolve_airport
from .schedule import CURRENCY, ScheduledFlight, flights_on

SORTS = ("price", "duration", "departure")


class InvalidRequest(Exception):
    """Raised for malformed input, e.g. an airport that doesn't resolve."""

    code = "invalid_request"
    hint = "Call flights_search_airports to find a valid code or city name."


def _option(flight: ScheduledFlight, passengers: int) -> dict:
    """Package one direct flight into a bookable option."""
    option = flight.to_dict()
    option["total_duration_minutes"] = option["duration_minutes"]
    fare = bookings.price_per_passenger(flight)
    option["price_per_passenger"] = {"amount": fare, "currency": CURRENCY}
    option["total_price"] = {"amount": fare * passengers, "currency": CURRENCY}
    option["seats_available"] = bookings.seats_available(flight)
    return option


def search(
    origin: str,
    destination: str,
    day: Date,
    *,
    passengers: int = 1,
    max_price: float | None = None,
    airline: str | None = None,
    sort: str = "price",
    limit: int = 10,
) -> dict:
    """Find bookable options. ``origin``/``destination`` accept an airport code
    or a city name. Returns options plus a human-readable summary."""
    if sort not in SORTS:
        raise ValueError(f"sort must be one of {SORTS}")

    origin_airport = resolve_airport(origin)
    if origin_airport is None:
        raise InvalidRequest(f"Unknown origin {origin!r}.")
    destination_airport = resolve_airport(destination)
    if destination_airport is None:
        raise InvalidRequest(f"Unknown destination {destination!r}.")
    origin, destination = origin_airport.code, destination_airport.code

    options = [_option(f, passengers) for f in flights_on(day, origin, destination)]

    if airline:
        wanted = airline.strip().upper()
        options = [o for o in options if o["airline"] == wanted]
    options = [o for o in options if o["seats_available"] >= passengers]
    if max_price is not None:
        options = [o for o in options if o["total_price"]["amount"] <= max_price]

    keys = {
        "price": lambda o: (o["total_price"]["amount"], o["total_duration_minutes"]),
        "duration": lambda o: (o["total_duration_minutes"], o["total_price"]["amount"]),
        "departure": lambda o: (o["departure_local"], o["total_price"]["amount"]),
    }
    options.sort(key=keys[sort])

    return {
        "origin": origin,
        "destination": destination,
        "date": day.isoformat(),
        "passengers": passengers,
        "currency": CURRENCY,
        "count": len(options[:limit]),
        "total_found": len(options),
        "options": options[:limit],
        "summary": _summarise(origin, destination, day, options),
    }


def _summarise(origin: str, destination: str, day: Date, options: Iterable[dict]) -> str:
    """Say why a result is empty, so an agent does not treat it as a failure."""
    options = list(options)
    if options:
        return f"{len(options)} option(s) found for {origin}-{destination} on {day.isoformat()}."
    weekday = day.strftime("%A")
    return (
        f"No direct flights {origin}-{destination} on {weekday} {day.isoformat()}. "
        "Maya's timetable is fixed, weekly and direct-only, so some routes do not "
        "operate every day and some city pairs have no service at all. Try another "
        "date, or check get_weekly_schedule for which days this route runs."
    )
