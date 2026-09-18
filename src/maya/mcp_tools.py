"""MCP tool registrations for the flights domain.

Thin adapter, on purpose: every tool here validates and shapes arguments,
then calls straight into ``simulations.flights``. No business logic lives
here -- see FLIGHTS.md for the tool contract and DECISIONS.md D4 for why.

Scope note: this exposes what is actually implemented. FLIGHTS.md's fuller
spec describes a search -> hold -> pay lifecycle (``flights_get_offer``,
a held booking, a separate pay step) and a ``flights_get_flight_status``
tool; neither the offer/hold system nor flight-status simulation exist in
the code yet, so those tools aren't wired up here. Booking today is the one
step ``flights_book_flight`` describes: search, book, confirmed.
"""

from __future__ import annotations

from datetime import date as Date

from mcp.server.mcpserver import MCPServer

from .simulations.flights import airports, bookings, schedule, search

mcp = MCPServer("maya-flights")


def _error(code: str, message: str, hint: str | None = None) -> dict:
    error: dict = {"code": code, "message": message}
    if hint:
        error["hint"] = hint
    return {"error": error}


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
    sort: str = "price",
    limit: int = 10,
) -> dict:
    """Find bookable direct flights between two airports on one date.

    ``origin``/``destination`` accept an airport code or a city name.
    ``departure_date`` is ``YYYY-MM-DD``. All flights are direct -- there is
    no connection-building, so a missing city pair means there is no
    service, not a itinerary to build. Seats and prices are computed live
    from how close to departure it is and how full the flight already is,
    so a search minutes apart can return different numbers. An empty result
    is normal, not an error -- thin routes and peak dates genuinely sell out.
    ``sort`` is one of "price", "duration", "departure".
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
) -> dict:
    """Book a flight found via flights_search_flights.

    Confirms immediately: there is no separate hold-then-pay step. ``date``
    is ``YYYY-MM-DD`` and must be a date the flight actually operates on.
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
            "Search again for a date this flight actually flies.",
        )
    try:
        return bookings.book(flight, passenger_names, contact_email)
    except bookings.BookingError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def flights_get_booking(booking_reference: str) -> dict:
    """Retrieve a booking by its reference."""
    booking = bookings.get(booking_reference)
    if booking is None:
        return _error(
            "not_found",
            f"No booking with reference {booking_reference!r}.",
            "Check the reference, or call flights_list_bookings.",
        )
    return booking


@mcp.tool()
def flights_list_bookings(email: str | None = None, status: str | None = None) -> list[dict]:
    """List bookings, optionally filtered by contact email or status."""
    return bookings.list_all(email, status)


@mcp.tool()
def flights_cancel_booking(booking_reference: str) -> dict:
    """Cancel a booking. Applies the fare's refund rule.

    Puffin Air fares can never be cancelled (fails with ``not_refundable``).
    Everyone else can cancel for a full refund any time up to 24 hours before
    departure; inside that window it fails with ``too_late_to_cancel`` rather
    than silently doing nothing.
    """
    try:
        return bookings.cancel(booking_reference)
    except bookings.BookingError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def flights_reset_bookings() -> dict:
    """Clear every booking and seat count for this database.

    Reference data (airports, airlines, aircraft, the schedule) is
    untouched -- it isn't state, it's world content.
    """
    cleared = bookings.reset()
    return {"cleared": cleared}
