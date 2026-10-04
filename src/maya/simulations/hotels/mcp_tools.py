"""MCP tool registrations for the hotels domain.

Thin adapter, on purpose: every tool here validates and shapes arguments,
then calls straight into this package's own modules. No business logic
lives here -- same pattern as simulations/delivery/mcp_tools.py.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from maya.world.mcp_tools import add_time_tool

from . import bookings, hotels

mcp = MCPServer("aira-hotels")
add_time_tool(mcp)


def _error(code: str, message: str, hint: str | None = None) -> dict:
    error: dict = {"code": code, "message": message}
    if hint:
        error["hint"] = hint
    return {"error": error}


@mcp.tool()
def hotels_list_hotels() -> list[dict]:
    """List every hotel in Aira, with its zone, style, stars and
    cancellation policy.

    Small enough to return whole; call this once and keep the result rather
    than calling it again mid-task. Prices and availability are not here --
    they depend on dates, so use hotels_search_availability for those.
    """
    return [h.to_dict() for h in hotels.HOTELS]


@mcp.tool()
def hotels_get_hotel(hotel_code: str) -> dict:
    """Get one hotel and its room types, with each room type's capacity and
    base nightly rate.

    ``base_nightly_rate`` is the price with no demand surcharge -- the live
    price for specific dates can be up to 30% higher; use
    hotels_search_availability for that. Accepts a hotel code or name.
    """
    hotel = hotels.resolve(hotel_code)
    if hotel is None:
        return _error(
            "not_found",
            f"No hotel matching {hotel_code!r}.",
            "Check the code, or call hotels_list_hotels.",
        )
    return {
        "hotel": hotel.to_dict(),
        "room_types": [r.to_dict() for r in hotels.room_types_for(hotel.code)],
    }


@mcp.tool()
def hotels_search_availability(
    check_in: str,
    check_out: str,
    guests: int = 1,
    zone: str | None = None,
    max_total_price: int | None = None,
    limit: int = 10,
) -> list[dict] | dict:
    """Find room types with a room free on every night of a stay, cheapest
    total first.

    Dates are YYYY-MM-DD; check_out is the morning you leave, so
    2026-05-01 to 2026-05-03 is 2 nights. ``zone`` accepts an Aira zone
    code, name, or postal code. Each result carries its hotel's code, name
    and zone plus per-night prices and the total, so you can go straight to
    hotels_book_room. Prices and ``rooms_left`` are computed live -- nights
    get fuller and dearer as they approach, so the same search can return
    different numbers later. An empty list means nothing fits, not an error.
    """
    try:
        return bookings.search(check_in, check_out, guests, zone, max_total_price, limit)
    except bookings.BookingError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def hotels_book_room(
    room_type_id: str,
    check_in: str,
    check_out: str,
    guests: int,
    guest_name: str,
    contact_email: str,
) -> dict:
    """Book one room for a stay. Confirms immediately -- there is no
    separate hold-then-pay step.

    The price is the live price at the moment of booking, and is locked in
    from then on. Fails with sold_out if any single night has no room left,
    or too_many_guests if ``guests`` exceeds the room type's max_guests --
    for a bigger party, book several rooms.
    """
    try:
        return bookings.book(room_type_id, check_in, check_out, guests, guest_name, contact_email)
    except bookings.BookingError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def hotels_get_booking(reference: str) -> dict:
    """Retrieve a booking by its reference.

    ``status`` is computed live from the clock, not stored: CONFIRMED until
    check-in (15:00 on the check-in date), CHECKED_IN until check-out
    (11:00 on the check-out date), then CHECKED_OUT.
    """
    booking = bookings.get(reference)
    if booking is None:
        return _error(
            "not_found",
            f"No booking with reference {reference!r}.",
            "Check the reference, or call hotels_list_bookings.",
        )
    return booking


@mcp.tool()
def hotels_list_bookings(email: str | None = None, status: str | None = None) -> list[dict]:
    """List bookings, optionally filtered by contact email or status.

    ``status`` is one of "CONFIRMED", "CHECKED_IN", "CHECKED_OUT",
    "CANCELLED" -- computed live, not stored.
    """
    return bookings.list_all(email, status)


@mcp.tool()
def hotels_cancel_booking(reference: str) -> dict:
    """Cancel a booking for a full refund.

    Each hotel has its own cutoff (see cancellation_policy): inside it this
    fails with too_late_to_cancel, and non-refundable hotels always fail with
    not_refundable. There are no partial refunds.
    """
    try:
        return bookings.cancel(reference)
    except bookings.BookingError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def hotels_reset_bookings() -> dict:
    """Clear every hotel booking and the rooms they held.

    Reference data (hotels, room types) is untouched -- it isn't state,
    it's world content.
    """
    cleared = bookings.reset()
    return {"cleared": cleared}
