"""MCP tool registrations for the events domain.

Thin adapter, on purpose: every tool here validates and shapes arguments,
then calls straight into this package's own modules. No business logic
lives here -- same pattern as simulations/hotels/mcp_tools.py.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import catalog, tickets

mcp = MCPServer("aira-events")


def _error(code: str, message: str, hint: str | None = None) -> dict:
    error: dict = {"code": code, "message": message}
    if hint:
        error["hint"] = hint
    return {"error": error}


@mcp.tool()
def events_list_venues() -> list[dict]:
    """List every event venue in Aira, with its zone and seating sections.

    Small enough to return whole; call this once and keep the result. What's
    on and what's left depend on dates -- use events_search for those.
    """
    return [
        {
            **venue._asdict(),
            "sections": [
                {"section_id": s.section_id, "name": s.name, "capacity": s.capacity}
                for s in catalog.SECTIONS_BY_VENUE.get(venue.code, [])
            ],
        }
        for venue in catalog.VENUES.values()
    ]


@mcp.tool()
def events_search(
    date_from: str,
    date_to: str | None = None,
    category: str | None = None,
    zone: str | None = None,
    query: str | None = None,
    quantity: int = 1,
    max_price: int | None = None,
    limit: int = 20,
) -> list[dict] | dict:
    """Find performances in Aira between two dates (inclusive), earliest first.

    Dates are YYYY-MM-DD; ``date_to`` defaults to ``date_from``, and a
    search spans at most 14 days. ``category`` is one of "comedy",
    "concert", "lecture", "theatre", "family", "film", "workshop".
    ``query`` matches the title or genre (e.g. "jazz", "horror",
    "pottery"). ``zone`` accepts an Aira zone code, name or postal code.
    ``max_price`` keeps shows whose cheapest face price is at most that.

    Each row lists every section with its fixed face price, the per-ticket
    service fee, and tickets left -- other buyers take tickets as a show
    approaches, and popular shows sell out completely before the day.
    Sold-out performances are included with ``sold_out: true`` (judged
    against ``quantity``). Shows run on fixed weekdays every week; started
    performances are left out.
    """
    try:
        return tickets.search(date_from, date_to, category, zone, query, quantity, max_price, limit)
    except tickets.TicketError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def events_get_event(event_id: str, date: str) -> dict:
    """Get one performance in full: times, venue, age limit, ticket limit,
    refund policy, and each section's price, fee and tickets left."""
    try:
        return tickets.details(event_id, date)
    except tickets.TicketError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def events_book_tickets(
    event_id: str,
    date: str,
    section_id: str,
    quantity: int,
    attendee_name: str,
    contact_email: str,
    youngest_attendee_age: int | None = None,
) -> dict:
    """Buy tickets in one section of one performance. Confirms immediately --
    there is no hold or payment step.

    ``total_price`` is (face price + service fee) x quantity. Fails with
    ticket_limit_exceeded above the show's max_tickets_per_order,
    age_restricted for an age-limited show when ``youngest_attendee_age`` is
    missing or under its min_age, sold_out if the section has fewer tickets
    left than ``quantity``, and sales_closed once the show has started.
    """
    try:
        return tickets.book(
            event_id, date, section_id, quantity, attendee_name, contact_email, youngest_attendee_age
        )
    except tickets.TicketError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def events_get_booking(reference: str) -> dict:
    """Retrieve a booking by its reference.

    ``status`` is computed live: CONFIRMED until the show starts,
    IN_PROGRESS while it's on, then ENDED.
    """
    booking = tickets.get(reference)
    if booking is None:
        return _error(
            "not_found",
            f"No booking with reference {reference!r}.",
            "Check the reference, or call events_list_bookings.",
        )
    return booking


@mcp.tool()
def events_list_bookings(email: str | None = None, status: str | None = None) -> list[dict]:
    """List bookings, optionally filtered by contact email or status.

    ``status`` is one of "CONFIRMED", "IN_PROGRESS", "ENDED", "CANCELLED" --
    computed live, not stored.
    """
    return tickets.list_all(email, status)


@mcp.tool()
def events_cancel_booking(reference: str) -> dict:
    """Cancel a booking. Refunds face value only -- the service fee is never
    refunded.

    Each show has its own refund window (see refund_policy): inside it this
    fails with too_late_to_cancel, and non-refundable shows always fail with
    not_refundable. Cancelled tickets go back on sale.
    """
    try:
        return tickets.cancel(reference)
    except tickets.TicketError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def events_reset_bookings() -> dict:
    """Clear every event booking and the tickets they held.

    Reference data (venues, sections, event series) is untouched -- it isn't
    state, it's world content.
    """
    return {"cleared": tickets.reset()}
