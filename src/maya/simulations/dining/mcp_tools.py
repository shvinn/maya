"""MCP tool registrations for the dining domain.

Thin adapter, on purpose: every tool here validates and shapes arguments,
then calls straight into this package's own modules. No business logic
lives here -- same pattern as simulations/events/mcp_tools.py.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from maya.tool_errors import tool_error as _error
from maya.world.mcp_tools import add_time_tool

from . import reservations

mcp = MCPServer("aira-dining")
add_time_tool(mcp)


@mcp.tool()
def dining_search_restaurants(
    zone: str | None = None, cuisine: str | None = None, query: str | None = None, limit: int = 20
) -> list[dict] | dict:
    """Find dine-in restaurants in Aira, with their hours, closed days, max
    party size and deposit/cancellation policy.

    ``zone`` accepts an Aira zone code, name or postal code. ``cuisine``
    matches partially and case-insensitively ("ind" matches "Indian" and
    "South Indian"); ``query`` matches the name or cuisine. Delivery-only
    kitchens have no tables and never appear here. An empty result means
    nothing matches, not an error.
    """
    try:
        return reservations.search_restaurants(zone, cuisine, query, limit)
    except reservations.ReservationError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def dining_get_restaurant(restaurant_code: str) -> dict:
    """Get one restaurant in full: hours, last seating, policies and its
    menu (starters, mains, sides, desserts, drinks, each with diet)."""
    try:
        return reservations.restaurant_details(restaurant_code)
    except reservations.ReservationError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def dining_check_availability(restaurant_code: str, date: str, party_size: int = 2) -> dict:
    """List a restaurant's seating times on a date (YYYY-MM-DD) with seats
    left at each.

    Tables are booked in half-hour slots from opening to the last seating.
    Other diners book as the date approaches, and dinner-rush slots
    (18:30-20:30) fill fastest -- popular places are fully booked at peak
    days ahead. ``available`` is judged against ``party_size``. Fails with
    closed on a day the restaurant doesn't open.
    """
    try:
        return reservations.availability(restaurant_code, date, party_size)
    except reservations.ReservationError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def dining_book_table(
    restaurant_code: str,
    date: str,
    time: str,
    party_size: int,
    guest_name: str,
    contact_phone: str,
) -> dict:
    """Reserve a table. Confirms immediately -- there is no hold step.

    ``time`` is HH:MM and must be one of the restaurant's slots on that
    date. Restaurants with a deposit take ``deposit_per_guest`` x
    ``party_size`` now (see ``deposit``). Fails with closed,
    invalid_time, party_too_large above the restaurant's max_party, or
    fully_booked if fewer seats are left than ``party_size``.
    """
    try:
        return reservations.book(restaurant_code, date, time, party_size, guest_name, contact_phone)
    except reservations.ReservationError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def dining_get_reservation(reference: str) -> dict:
    """Retrieve a reservation by its reference.

    ``status`` is computed live: CONFIRMED until the booking time, SEATED
    during the sitting, then COMPLETED.
    """
    reservation = reservations.get(reference)
    if reservation is None:
        return _error(
            "not_found",
            f"No reservation with reference {reference!r}.",
            "Check the reference, or call dining_list_reservations.",
        )
    return reservation


@mcp.tool()
def dining_list_reservations(phone: str | None = None, status: str | None = None) -> list[dict]:
    """List reservations, optionally filtered by contact phone or status.

    ``status`` is one of "CONFIRMED", "SEATED", "COMPLETED", "CANCELLED" --
    computed live, not stored.
    """
    return reservations.list_all(phone, status)


@mcp.tool()
def dining_cancel_reservation(reference: str) -> dict:
    """Cancel a reservation any time before the booking time; the table goes
    back on offer.

    A deposit is refunded only if cancelled at least the restaurant's
    cancellation cutoff ahead -- later than that it is kept (see
    ``deposit_kept``), but the cancellation still goes through.
    """
    try:
        return reservations.cancel(reference)
    except reservations.ReservationError as e:
        return _error(e.code, str(e), e.hint)
