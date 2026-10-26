"""MCP tool registrations for the dining domain.

Thin adapter, on purpose: every tool here validates and shapes arguments,
then calls straight into this package's own modules. No business logic
lives here -- same pattern as simulations/events/mcp_tools.py.
"""

from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer

from maya.simulations.residents import caller, residents
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
    ctx: Context | None = None,
) -> dict:
    """Reserve a table. Confirms immediately.

    ``time`` is HH:MM and must be one of the restaurant's slots on that
    date. Most tables cost nothing. Restaurants with a deposit (fine
    dining) *hold* ``deposit_per_guest`` x ``party_size`` of your
    resident's money (see ``deposit`` and ``payment``): still yours, but
    not available to spend, until the sitting ends -- then it lapses and
    nothing is spent. Fails with insufficient_funds, booking nothing, if
    you don't have that much available. Fails with closed,
    invalid_time, party_too_large above the restaurant's max_party, or
    fully_booked if fewer seats are left than ``party_size``.
    """
    try:
        resident = caller.current(ctx)
        return reservations.book(
            restaurant_code, date, time, party_size, guest_name, contact_phone, resident["resident_id"]
        )
    except (reservations.ReservationError, residents.ResidentError) as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def dining_get_reservation(reference: str, ctx: Context | None = None) -> dict:
    """Retrieve one of your reservations by its reference.

    ``status`` is computed live: CONFIRMED until the booking time, SEATED
    during the sitting, then COMPLETED.
    """
    try:
        resident = caller.current(ctx)
    except residents.ResidentError as e:
        return _error(e.code, str(e), e.hint)
    reservation = reservations.get(reference, resident["resident_id"])
    if reservation is None:
        return _error(
            "not_found",
            f"No reservation with reference {reference!r}.",
            "Check the reference, or call dining_list_reservations.",
        )
    return reservation


@mcp.tool()
def dining_list_reservations(
    phone: str | None = None, status: str | None = None, ctx: Context | None = None
) -> list[dict]:
    """List your reservations, optionally filtered by contact phone or status.

    ``status`` is one of "CONFIRMED", "SEATED", "COMPLETED", "CANCELLED" --
    computed live, not stored.
    """
    try:
        resident = caller.current(ctx)
    except residents.ResidentError as e:
        return _error(e.code, str(e), e.hint)
    return reservations.list_all(resident["resident_id"], phone, status)


@mcp.tool()
def dining_cancel_reservation(reference: str, ctx: Context | None = None) -> dict:
    """Cancel a reservation any time before the booking time; the table goes
    back on offer.

    A deposit is only ever held, so nothing is refunded (``refund`` is
    always 0): cancelled at least the restaurant's cancellation cutoff
    ahead, the hold is released (``deposit_released``); later than that the
    deposit is kept, charged to your wallet (``deposit_kept``), but the
    cancellation still goes through.
    """
    try:
        resident = caller.current(ctx)
        return reservations.cancel(reference, resident["resident_id"])
    except (reservations.ReservationError, residents.ResidentError) as e:
        return _error(e.code, str(e), e.hint)
