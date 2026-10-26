"""MCP tool registrations for the delivery domain.

Thin adapter, on purpose: every tool here validates and shapes arguments,
then calls straight into this package's own modules. No business logic
lives here -- same pattern as simulations/flights/mcp_tools.py.
"""

from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer

from maya.simulations.residents import caller, residents
from maya.tool_errors import tool_error as _error
from maya.world.mcp_tools import add_time_tool

from . import menu, orders, vendors

mcp = MCPServer("aira-delivery")
add_time_tool(mcp)


@mcp.tool()
def delivery_list_vendors() -> list[dict]:
    """List every delivery vendor in Aira.

    Small enough to return whole; call this once and keep the result rather
    than calling it again mid-task. Each entry includes the vendor's zone
    code, which is the only place to discover which zones exist without a
    separate maps tool -- delivery is only available within those zones.
    """
    return [v.to_dict() for v in vendors.VENDORS]


@mcp.tool()
def delivery_search_vendors(
    zone: str | None = None, cuisine: str | None = None, limit: int = 10
) -> list[dict]:
    """Find delivery vendors, optionally filtered by zone code or cuisine.

    ``cuisine`` matches partially and case-insensitively (e.g. "ind" matches
    "Indian"). An empty result means no vendor matches that zone/cuisine,
    not an error.
    """
    return [v.to_dict() for v in vendors.search(zone=zone, cuisine=cuisine, limit=limit)]


@mcp.tool()
def delivery_get_menu(vendor_code: str) -> dict:
    """Get one vendor's full menu, once you already know who you're ordering
    from.

    Call delivery_list_vendors or delivery_search_vendors first to get a
    vendor code. An empty ``items`` list means the vendor code doesn't
    exist.
    """
    vendor = vendors.get(vendor_code)
    items = menu.get_menu(vendor_code)
    return {
        "vendor": vendor.to_dict() if vendor else None,
        "items": [i.to_dict() for i in items],
    }


@mcp.tool()
def delivery_search_menu_items(
    query: str = "",
    category: str | None = None,
    diet: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Search menu items across every vendor in Aira, by item name or
    cuisine (e.g. "curry" or "noodles").

    Each result carries its vendor's code, name and zone alongside the item,
    so you can go straight to delivery_place_order without a second lookup.
    ``category`` is one of "starter", "main", "side", "dessert", "drink". ``diet`` is
    one of "vegan", "vegetarian", "non_veg". An empty query returns items
    across all vendors, still subject to any category/diet filters given.
    """
    return menu.search(query, category=category, diet=diet, limit=limit)


@mcp.tool()
def delivery_place_order(
    vendor_code: str,
    items: list[dict],
    delivery_zone: str,
    contact_phone: str,
    deliver_at: str | None = None,
    ctx: Context | None = None,
) -> dict:
    """Place an order at one vendor, to arrive now or at a set time. Confirms
    immediately and pays the ``total_price`` from your resident's wallet in
    the same step (``payment`` shows the charge and what's left); fails with
    insufficient_funds, ordering nothing, if you can't cover it.

    ``items`` is a list of {"item_id": ..., "quantity": ...} using item ids
    from delivery_get_menu or delivery_search_menu_items. ``delivery_zone``
    accepts a zone code, zone name, or postal code, and must be within the
    deliverable area. ``total_price`` includes the vendor's delivery fee.
    ``eta_minutes`` is prep time plus courier travel time from the vendor's
    zone to the delivery zone.

    ``deliver_at`` (YYYY-MM-DD HH:MM, Maya time) schedules the order to
    arrive then, up to 7 days ahead; it must be at least ``eta_minutes``
    away, or the order fails with invalid_delivery_time. Leave it out to
    deliver as soon as possible. A scheduled order is SCHEDULED until the
    kitchen starts on it.
    """
    try:
        resident = caller.current(ctx)
        return orders.place(
            vendor_code, items, delivery_zone, contact_phone, resident["resident_id"], deliver_at=deliver_at
        )
    except (orders.OrderError, residents.ResidentError) as e:
        return _error(e.code, str(e), e.hint)


@mcp.tool()
def delivery_get_order(order_id: str, ctx: Context | None = None) -> dict:
    """Retrieve one of your orders by its reference.

    ``status`` is computed live from elapsed time, not stored -- an order
    placed a few minutes ago may already show OUT_FOR_DELIVERY or DELIVERED.
    """
    try:
        resident = caller.current(ctx)
    except residents.ResidentError as e:
        return _error(e.code, str(e), e.hint)
    order = orders.get(order_id, resident["resident_id"])
    if order is None:
        return _error(
            "not_found",
            f"No order with reference {order_id!r}.",
            "Check the reference, or call delivery_list_orders.",
        )
    return order


@mcp.tool()
def delivery_list_orders(
    phone: str | None = None, status: str | None = None, ctx: Context | None = None
) -> list[dict]:
    """List your orders, optionally filtered by contact phone or status.

    ``status`` is one of "SCHEDULED", "PREPARING", "OUT_FOR_DELIVERY",
    "DELIVERED", "CANCELLED" -- computed live, not stored.
    """
    try:
        resident = caller.current(ctx)
    except residents.ResidentError as e:
        return _error(e.code, str(e), e.hint)
    return orders.list_all(resident["resident_id"], phone, status)


@mcp.tool()
def delivery_cancel_order(order_id: str, ctx: Context | None = None) -> dict:
    """Cancel one of your orders for a full refund, straight back to your
    resident's wallet. Only possible while it is SCHEDULED or still
    PREPARING.

    Once a courier has picked it up (OUT_FOR_DELIVERY or later), this fails
    with too_late_to_cancel rather than silently doing nothing. A
    near-instant-prep vendor may already be past PREPARING by the time you
    call this.
    """
    try:
        resident = caller.current(ctx)
        return orders.cancel(order_id, resident["resident_id"])
    except (orders.OrderError, residents.ResidentError) as e:
        return _error(e.code, str(e), e.hint)
