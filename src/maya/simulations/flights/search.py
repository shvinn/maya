"""Searching the timetable.

Search does one thing: filter the fixed weekly schedule down to what a traveller
asked for. Options are either a direct flight or a one-stop connection (two
flights sold together under one booking -- see ``connections.py`` for what
makes a pair valid). Never more than one stop: if a city pair has neither,
the answer is that there is no flight.

Fares are not stored -- see ``bookings.price_per_passenger`` for how they are
computed from the airline's rate, flight duration and how full the flight is.
"""

from __future__ import annotations

from datetime import date as Date
from typing import Iterable

from . import bookings, connections
from .airports import resolve as resolve_airport
from .schedule import AIRLINES, CURRENCY, ScheduledFlight, flights_on

SORTS = ("price", "duration", "departure")


class InvalidRequest(Exception):
    """Raised for malformed input, e.g. an airport that doesn't resolve."""

    code = "invalid_request"
    hint = "Call flights_search_airports to find a valid code or city name."


class UnknownAirline(InvalidRequest):
    hint = "Use an airline's code or name: " + ", ".join(f"{a.code} ({a.name})" for a in AIRLINES.values()) + "."


def resolve_airline(value: str) -> str:
    """An airline code from its code or name, the way airports resolve: an
    exact code or name in any case, else a partial name only if one airline
    fits. Anything else is refused rather than filtering to nothing -- an
    empty result would read as "no flights"."""
    needle = value.strip().lower()
    for airline in AIRLINES.values():
        if needle in (airline.code.lower(), airline.name.lower()):
            return airline.code
    matches = [a for a in AIRLINES.values() if needle in a.name.lower()]
    if len(matches) == 1:
        return matches[0].code
    if matches:
        raise UnknownAirline(f"{value!r} matches more than one airline: {', '.join(a.name for a in matches)}.")
    raise UnknownAirline(f"Unknown airline {value!r}.")


def _leg(flight: ScheduledFlight) -> dict:
    leg = flight.to_dict()
    leg["price_per_passenger"] = {"amount": bookings.price_per_passenger(flight), "currency": CURRENCY}
    leg["seats_available"] = bookings.seats_available(flight)
    return leg


def _option(flight: ScheduledFlight, passengers: int) -> dict:
    """Package one direct flight into a bookable option."""
    option = flight.to_dict()
    option["stops"] = 0
    option["total_duration_minutes"] = option["duration_minutes"]
    fare = bookings.price_per_passenger(flight)
    option["price_per_passenger"] = {"amount": fare, "currency": CURRENCY}
    option["total_price"] = {"amount": fare * passengers, "currency": CURRENCY}
    option["seats_available"] = bookings.seats_available(flight)
    option["legs"] = [_leg(flight)]
    return option


def _connection_option(first: ScheduledFlight, second: ScheduledFlight, passengers: int) -> dict:
    """Package a one-stop pair into a bookable option. The fare comes from
    bookings.itinerary_fare (summed legs, discounted on a single airline);
    seats are whatever the fuller leg has left."""
    legs = [_leg(first), _leg(second)]
    fare, discount = bookings.itinerary_fare([first, second])
    option = {
        "stops": 1,
        "origin": first.pattern.origin,
        "destination": second.pattern.destination,
        "date": first.date.isoformat(),
        "departure_local": first.departure.strftime("%Y-%m-%d %H:%M"),
        "arrival_local": second.arrival.strftime("%Y-%m-%d %H:%M"),
        "connection_airport": first.pattern.destination,
        "layover_minutes": connections.layover_minutes(first, second),
        "overnight": connections.is_overnight(first, second),
        "total_duration_minutes": int((second.arrival - first.departure).total_seconds() // 60),
        "price_per_passenger": {"amount": fare, "currency": CURRENCY},
        "total_price": {"amount": fare * passengers, "currency": CURRENCY},
        "seats_available": min(leg["seats_available"] for leg in legs),
        "legs": legs,
    }
    if discount:
        option["connection_discount_per_passenger"] = {"amount": discount, "currency": CURRENCY}
    return option


def search(
    origin: str,
    destination: str,
    day: Date,
    *,
    passengers: int = 1,
    max_price: float | None = None,
    airline: str | None = None,
    max_stops: int = 1,
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
    if max_stops >= 1:
        options += [
            _connection_option(first, second, passengers)
            for first, second in connections.find(origin, destination, day)
        ]

    if airline:
        wanted = resolve_airline(airline)
        options = [o for o in options if all(leg["airline"] == wanted for leg in o["legs"])]
    scheduled = len(options)
    options = [o for o in options if o["seats_available"] >= passengers]
    full = scheduled - len(options)
    if max_price is not None:
        options = [o for o in options if o["total_price"]["amount"] <= max_price]
    over_price = scheduled - full - len(options)

    keys = {
        "price": lambda o: (
            o["total_price"]["amount"], o["stops"], o.get("overnight", False), o["total_duration_minutes"],
        ),
        "duration": lambda o: (o["total_duration_minutes"], o["total_price"]["amount"]),
        "departure": lambda o: (o["departure_local"], o["stops"], o["total_price"]["amount"]),
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
        "not_enough_seats": full,
        "over_max_price": over_price,
        "summary": _summarise(
            origin, destination, day, options, max_stops, passengers, full, over_price,
            AIRLINES[wanted].name if airline else None,
        ),
    }


def _summarise(
    origin: str, destination: str, day: Date, options: Iterable[dict], max_stops: int,
    passengers: int = 1, full: int = 0, over_price: int = 0, airline: str | None = None,
) -> str:
    """Say what was found and, above all, why anything wasn't -- full
    flights and price limits included -- so an agent doesn't mistake a full
    flight for a route that doesn't fly."""
    options = list(options)
    left_out = []
    if full:
        left_out.append(f"{full} without {passengers} seat(s) left")
    if over_price:
        left_out.append(f"{over_price} over max_price")
    if options:
        direct = sum(1 for o in options if o["stops"] == 0)
        return (
            f"{len(options)} option(s) found for {origin}-{destination} on "
            f"{day.isoformat()}: {direct} direct, {len(options) - direct} with one stop."
            + (f" Left out: {' and '.join(left_out)}." if left_out else "")
        )
    if left_out:
        return (
            f"Flights do operate {origin}-{destination} on {day.isoformat()}, but none fits: "
            f"{' and '.join(left_out)}. The route itself is fine; try another date, fewer "
            "passengers or a higher max_price."
        )
    weekday = day.strftime("%A")
    kind = "direct or one-stop" if max_stops >= 1 else "direct"
    if airline:
        return (
            f"No {kind} {airline} flights {origin}-{destination} on {weekday} {day.isoformat()}. "
            "Other airlines may fly it: search again without the airline filter."
        )
    return (
        f"No {kind} flights {origin}-{destination} on {weekday} {day.isoformat()}. "
        "Maya's timetable is fixed and weekly, connections allow one stop at most, "
        "and some routes do not operate every day. Try another date"
        + (", or allow a stop (max_stops=1)." if max_stops < 1 else ".")
    )
