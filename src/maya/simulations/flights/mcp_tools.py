"""MCP tool registrations for the flights domain.

Thin adapter, on purpose: every tool here validates and shapes arguments,
then calls straight into this package's own modules. No business logic
lives here, so the same rules hold however the domain is exposed.

Scope note: this exposes only what is implemented. There is no
search -> hold -> pay lifecycle (no held offers, no separate pay step);
booking is the one step ``flights_book_flight`` describes: search, book,
confirmed. Delays and cancellations are only ever found by asking --
nothing is pushed to the caller.

Searching and flight status are open to anyone. Booking, cancelling and
reading bookings act as the calling resident (see
maya.simulations.residents.caller): a booking is paid for from their wallet,
and they only ever see and cancel their own.
"""

from __future__ import annotations

from datetime import date as Date

from mcp.server.mcpserver import Context, MCPServer

from maya.simulations.residents import caller, residents
from maya.tool_errors import tool_error as _error
from maya.world.mcp_tools import add_time_tool

from . import airports, bookings, connections, operations, schedule, search

mcp = MCPServer("maya-flights")
add_time_tool(mcp)


@mcp.tool()
def flights_list_airports() -> list[dict]:
    """List every airport in Maya's flight network.

    Small enough to return whole; call this once and keep the result rather
    than calling it again mid-task.
    """
    return [a.to_dict() for a in airports.AIRPORTS]


@mcp.tool()
def flights_search_airports(query: str, limit: int = 10) -> list[dict]:
    """Find airports by code, city name or airport name.

    Matches on partial names too, so "reef" or "AZS"-style codes both work.
    """
    return [a.to_dict() for a in airports.search(query, limit)]


@mcp.tool()
def flights_search_flights(
    origin: str,
    destination: str,
    departure_date: str,
    passengers: int = 1,
    max_price: float | None = None,
    airline: str | None = None,
    max_stops: int = 1,
    sort: str = "price",
    limit: int = 10,
) -> dict:
    """Find bookable flights between two airports on one date: direct
    flights and one-stop connections.

    ``origin``/``destination`` accept an airport code or a city name.
    ``departure_date`` is ``YYYY-MM-DD`` (the first flight's date). Each
    option has ``stops`` (0 or 1) and a ``legs`` list. Connections with
    ``overnight: true`` land in the evening and continue next morning -- the
    traveller needs somewhere to sleep at the connection airport, which the
    ticket doesn't include. Never more than one stop -- if a city pair has
    neither, there is no service. ``max_stops=0`` returns direct flights
    only. ``airline`` (a code like "PF" or a name like "Puffin Air") keeps
    options where every leg is that airline; an unknown airline is an error.
    ``not_enough_seats``, ``over_max_price`` and
    ``cancelled_or_misconnecting`` count flights that operate but were left
    out, and ``summary`` says why a result is empty. Each flight carries its
    live ``flight_status`` and estimated times; a cancellation is announced
    24 hours before departure and a delay 3 hours before, so a flight
    that's SCHEDULED today can still turn out late. Seats
    and prices are computed live, so a search minutes apart can return
    different numbers. A connection on one airline costs 20% less than its
    legs bought separately (``connection_discount_per_passenger``); across
    two airlines it's the plain sum. Its ``seats_available`` is the fuller
    leg's. An empty result is normal, not an error: read ``summary``. ``sort`` is one of
    "price", "duration", "departure".
    """
    try:
        day = Date.fromisoformat(departure_date)
    except ValueError:
        return _error("invalid_request", f"{departure_date!r} is not a YYYY-MM-DD date.")
    try:
        return search.search(
            origin=origin,
            destination=destination,
            day=day,
            passengers=passengers,
            max_price=max_price,
            airline=airline,
            max_stops=max_stops,
            sort=sort,
            limit=limit,
        )
    except search.InvalidRequest as e:
        return _error(e.code, str(e), e.hint)
    except ValueError as e:
        return _error("invalid_request", str(e))


@mcp.tool()
def flights_book_flight(
    flight_number: str,
    date: str,
    passenger_names: list[str],
    contact_email: str,
    connecting_flight_number: str | None = None,
    connecting_date: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """Book a flight found via flights_search_flights -- direct, or a
    one-stop connection as one booking.

    Confirms immediately and pays the ``total_price`` from your resident's
    wallet in the same step; ``payment`` shows the charge and the balance
    left. Fails with insufficient_funds (and books nothing) if the wallet
    can't cover it. There is no separate hold-then-pay step. ``date``
    is ``YYYY-MM-DD`` and must be a date the flight actually operates on.
    For a connection, pass the option's first leg as ``flight_number`` /
    ``date`` and its second leg as ``connecting_flight_number`` /
    ``connecting_date`` (defaults to ``date``; set it when the second leg
    departs the next day). Both legs are booked together or not at all, and
    fails with invalid_connection if the pair isn't a valid connection.
    """
    try:
        resident = caller.current(ctx)
    except residents.ResidentError as e:
        return _error(e.code, str(e), e.hint)
    legs = []
    for number, day_text in (
        (flight_number, date),
        (connecting_flight_number, connecting_date or date),
    ):
        if number is None:
            continue
        try:
            day = Date.fromisoformat(day_text)
        except ValueError:
            return _error("invalid_request", f"{day_text!r} is not a YYYY-MM-DD date.")
        flight = schedule.find(number, day)
        if flight is None:
            return _error(
                "not_found",
                f"{number} does not operate on {day_text}.",
                "Search again for a date this flight actually flies.",
            )
        legs.append(flight)
    for flight in legs:
        if operations.status(flight).cancelled:
            return _error(
                "flight_cancelled",
                f"{flight.flight_number} on {flight.date.isoformat()} has been cancelled by the airline.",
                "Search again for another flight.",
            )
    if len(legs) == 2:
        problem = connections.problem(*legs)
        if problem is None and operations.misconnects(*legs):
            problem = f"{legs[0].flight_number} is delayed and now lands too late to connect to {legs[1].flight_number}."
        if problem:
            return _error(
                "invalid_connection",
                problem,
                "Book a connection exactly as flights_search_flights returned it.",
            )
    try:
        return bookings.book(legs, passenger_names, contact_email, resident["resident_id"])
    except (bookings.BookingError, residents.ResidentError) as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def flights_get_flight_status(flight_number: str, date: str) -> dict:
    """Check one flight on one date (YYYY-MM-DD): SCHEDULED, DELAYED,
    CANCELLED, DEPARTED or LANDED, with estimated departure and arrival.

    Nothing announces a change -- check again closer to departure. A
    cancellation is known 24 hours ahead and a delay 3 hours ahead, so
    SCHEDULED means no news yet, not a promise. Some airlines run late far
    more often than others.
    """
    try:
        day = Date.fromisoformat(date)
    except ValueError:
        return _error("invalid_request", f"{date!r} is not a YYYY-MM-DD date.")
    flight = schedule.find(flight_number, day)
    if flight is None:
        return _error(
            "not_found",
            f"{flight_number} does not operate on {date}.",
            "Check the flight number and date against your booking or flights_search_flights.",
        )
    return {**flight.to_dict(), **operations.status(flight).to_dict()}


@mcp.tool()
def flights_get_booking(booking_reference: str, ctx: Context | None = None) -> dict:
    """Retrieve one of your bookings by its reference.

    Each leg carries its live ``flight_status``, ``delay_minutes`` and
    estimated times, and ``disruption`` sums it up: FLIGHT_CANCELLED,
    MISSED_CONNECTION, LONG_DELAY (3 hours or more), DELAYED, or null.
    """
    try:
        resident = caller.current(ctx)
    except residents.ResidentError as e:
        return _error(e.code, str(e), e.hint)
    booking = bookings.get(booking_reference, resident["resident_id"])
    if booking is None:
        return _error(
            "not_found",
            f"No booking with reference {booking_reference!r}.",
            "Check the reference, or call flights_list_bookings.",
        )
    return booking


@mcp.tool()
def flights_list_bookings(
    email: str | None = None, status: str | None = None, ctx: Context | None = None
) -> list[dict]:
    """List your bookings, optionally filtered by contact email or status."""
    try:
        resident = caller.current(ctx)
    except residents.ResidentError as e:
        return _error(e.code, str(e), e.hint)
    return bookings.list_all(resident["resident_id"], email, status)


@mcp.tool()
def flights_cancel_booking(booking_reference: str, ctx: Context | None = None) -> dict:
    """Cancel one of your bookings -- both legs together, for a connection.
    Applies the fare's refund rule; a refund goes straight back to your
    resident's wallet (see ``payment``).

    Puffin Air fares can never be cancelled (fails with ``not_refundable``).
    Everyone else can cancel for a full refund any time up to 24 hours before
    departure; inside that window it fails with ``too_late_to_cancel`` rather
    than silently doing nothing. Both rules give way when the airline lets
    the traveller down: a booking whose ``disruption`` is FLIGHT_CANCELLED,
    MISSED_CONNECTION or LONG_DELAY cancels for a full refund (the last two
    only until the first flight leaves).
    """
    try:
        resident = caller.current(ctx)
        return bookings.cancel(booking_reference, resident["resident_id"])
    except (bookings.BookingError, residents.ResidentError) as e:
        return _error(e.code, str(e), e.hint)
