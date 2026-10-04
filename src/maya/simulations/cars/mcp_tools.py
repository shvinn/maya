"""MCP tool registrations for the car rental domain.

Thin adapter, on purpose: every tool here validates and shapes arguments,
then calls straight into this package's own modules. No business logic
lives here -- same pattern as simulations/hotels/mcp_tools.py.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from maya.tool_errors import tool_error as _error
from maya.world.mcp_tools import add_time_tool

from . import fleet, rentals

mcp = MCPServer("aira-cars")
add_time_tool(mcp)


@mcp.tool()
def cars_list_cars() -> dict:
    """List the rental location and every car model in its fleet, with
    make, model, year, class, seats, transmission and base daily rate.

    There is one location -- the 24/7 desk at Aira International (Skyview
    zone) -- and every rental is picked up and dropped off there. Prices
    and availability depend on dates; use cars_search_availability for
    those.
    """
    return {"location": fleet.LOCATION, "cars": [c.to_dict() for c in fleet.CARS]}


@mcp.tool()
def cars_search_availability(
    pickup_at: str,
    dropoff_at: str,
    min_seats: int | None = None,
    car_class: str | None = None,
    max_total_price: int | None = None,
    limit: int = 10,
) -> list[dict] | dict:
    """Find car models free for a whole rental, cheapest total first.

    Times are 'YYYY-MM-DD HH:MM'. Rentals are charged per started 24 hours
    from pickup, so 10:00 to 11:00 the next day is 2 days. ``car_class`` is
    one of "mini", "economy", "compact", "electric", "suv", "minivan",
    "premium". Same-day pickup works -- it's an airport desk. Prices and
    ``cars_left`` are computed live and rise as the days approach. An empty
    list means nothing fits, not an error.
    """
    try:
        return rentals.search(pickup_at, dropoff_at, min_seats, car_class, max_total_price, limit)
    except rentals.RentalError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def cars_book_car(
    car_id: str,
    pickup_at: str,
    dropoff_at: str,
    driver_name: str,
    driver_age: int,
    contact_email: str,
) -> dict:
    """Reserve one car. Confirms immediately -- there is no hold, deposit or
    payment step.

    The price is the live price at the moment of booking, locked in from
    then on. The driver must be at least 21 (driver_too_young otherwise).
    Fails with sold_out if any day of the rental has no car of that model.
    """
    try:
        return rentals.book(car_id, pickup_at, dropoff_at, driver_name, driver_age, contact_email)
    except rentals.RentalError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def cars_get_rental(reference: str) -> dict:
    """Retrieve a rental by its reference.

    ``status`` is computed live from the clock, not stored: RESERVED until
    pickup, PICKED_UP until drop-off, then RETURNED.
    """
    rental = rentals.get(reference)
    if rental is None:
        return _error(
            "not_found",
            f"No rental with reference {reference!r}.",
            "Check the reference, or call cars_list_rentals.",
        )
    return rental


@mcp.tool()
def cars_list_rentals(email: str | None = None, status: str | None = None) -> list[dict]:
    """List rentals, optionally filtered by contact email or status.

    ``status`` is one of "RESERVED", "PICKED_UP", "RETURNED", "CANCELLED" --
    computed live, not stored.
    """
    return rentals.list_all(email, status)


@mcp.tool()
def cars_cancel_rental(reference: str) -> dict:
    """Cancel a rental for a full refund. Free any time before pickup.

    Once the pickup time has passed, this fails with too_late_to_cancel.
    """
    try:
        return rentals.cancel(reference)
    except rentals.RentalError as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def cars_reset_rentals() -> dict:
    """Clear every rental and the cars they held.

    Reference data (the fleet) is untouched -- it isn't state, it's world
    content.
    """
    cleared = rentals.reset()
    return {"cleared": cleared}
