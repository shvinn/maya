"""Delivery orders, persisted in SQLite.

An order is a single step, same as a flight booking: you browse a vendor's
menu, you place the order, you are confirmed. There is no hold-then-pay
step, no priced offer to expire, and no payments domain behind it -- the
total is informational, the same shallow way flights' total_price is today.

Status is never stored -- it is computed live from elapsed time since
placed_at, the same way flights computes seat availability from time to
departure rather than storing it. An order is PREPARING for
vendor.prep_minutes, then OUT_FOR_DELIVERY for courier_minutes (the road
distance between the vendor's zone and the delivery zone, from
world.geography.maps), then DELIVERED. CANCELLED is the one state that is
actually persisted, since it overrides whatever the clock would otherwise
say.

Cancellation is only possible while an order is still PREPARING -- once a
courier has picked it up, it can't be recalled. A ghost_kitchen vendor's
near-zero prep_minutes therefore makes its orders mechanically almost
impossible to cancel: not a special rule, just what falls out of the
timing, the same way Puffin Air's fares are non-refundable by a hardcoded
list rather than a timing accident.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta

from maya.world.geography import maps

from . import db, vendors
from .menu import BY_ITEM_ID

CURRENCY = "bucks"


def _get_order(order_id: str) -> dict | None:
    row = db.connect().execute(
        "SELECT data FROM orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    return json.loads(row["data"]) if row else None


def _save_order(order: dict) -> None:
    conn = db.connect()
    conn.execute(
        "INSERT OR REPLACE INTO orders (order_id, data) VALUES (?, ?)",
        (order["order_id"], json.dumps(order)),
    )
    conn.commit()


def _all_orders() -> list[dict]:
    rows = db.connect().execute("SELECT data FROM orders").fetchall()
    return [json.loads(row["data"]) for row in rows]


def _init_counter() -> int:
    """Resume the reference counter from what's already placed, so a
    restart never reissues a reference that's still on disk."""
    row = db.connect().execute("SELECT COUNT(*) AS n FROM orders").fetchone()
    return row["n"] if row else 0


_counter = _init_counter()


def _next_order_id() -> str:
    """A plain, sequential order number -- the way a receipt or a delivery
    app shows one, not a randomised locator like a flight's PNR. There is
    no reason for a food order to hide its sequence the way a booking
    reference does across a whole airline's reservation system."""
    global _counter
    _counter += 1
    return str(1000 + _counter)


def _status(order: dict) -> str:
    """Order status, computed live from elapsed time -- CANCELLED is the
    only value that's actually stored, as a terminal override."""
    if order["cancelled"]:
        return "CANCELLED"
    placed_at = datetime.strptime(order["placed_at"], "%Y-%m-%d %H:%M:%S")
    elapsed_minutes = (datetime.now() - placed_at) / timedelta(minutes=1)
    if elapsed_minutes < order["prep_minutes"]:
        return "PREPARING"
    if elapsed_minutes < order["prep_minutes"] + order["courier_minutes"]:
        return "OUT_FOR_DELIVERY"
    return "DELIVERED"


def _with_live_status(order: dict) -> dict:
    out = dict(order)
    out["status"] = _status(order)
    out.pop("cancelled", None)
    return out


def place(vendor_code: str, items: list[dict], delivery_zone: str, contact_phone: str) -> dict:
    """Confirm an order at one vendor. Raises VendorNotFound, ItemNotFound or
    ZoneNotDeliverable."""
    vendor = vendors.get(vendor_code)
    if vendor is None:
        raise VendorNotFound(f"No vendor with code {vendor_code!r}.")

    zone = maps.resolve_zone(delivery_zone)
    if zone is None or zone.city not in maps.deliverable_cities():
        raise ZoneNotDeliverable(
            f"{delivery_zone!r} is not a deliverable zone. Delivery is only "
            f"available in {', '.join(sorted(maps.deliverable_cities()))} today."
        )

    lines = []
    subtotal = 0
    for line in items:
        item = BY_ITEM_ID.get((line.get("item_id") or "").strip().upper())
        if item is None or item.vendor != vendor.code:
            raise ItemNotFound(f"{line.get('item_id')!r} is not on {vendor.name}'s menu.")
        quantity = line.get("quantity", 1)
        lines.append({
            "item_id": item.item_id,
            "name": item.name,
            "quantity": quantity,
            "unit_price": item.price,
        })
        subtotal += item.price * quantity

    courier_minutes = maps.distance_minutes(vendor.zone, zone.code)
    total = subtotal + vendor.delivery_fee

    order = {
        "order_id": _next_order_id(),
        "cancelled": False,
        "vendor_code": vendor.code,
        "vendor_name": vendor.name,
        "items": lines,
        "delivery_zone": zone.code,
        "delivery_zone_name": zone.name,
        "contact_phone": contact_phone,
        "subtotal": {"amount": subtotal, "currency": CURRENCY},
        "delivery_fee": {"amount": vendor.delivery_fee, "currency": CURRENCY},
        "total_price": {"amount": total, "currency": CURRENCY},
        "prep_minutes": vendor.prep_minutes,
        "courier_minutes": courier_minutes,
        "eta_minutes": vendor.prep_minutes + courier_minutes,
        "placed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }
    _save_order(order)
    return _with_live_status(order)


def get(order_id: str) -> dict | None:
    order = _get_order((order_id or "").strip().upper())
    return _with_live_status(order) if order else None


def list_all(phone: str | None = None, status: str | None = None) -> list[dict]:
    out = [_with_live_status(o) for o in _all_orders()]
    if phone:
        needle = phone.strip()
        out = [o for o in out if o["contact_phone"] == needle]
    if status:
        wanted = status.strip().upper()
        out = [o for o in out if o["status"] == wanted]
    return sorted(out, key=lambda o: o["placed_at"])


def cancel(order_id: str) -> dict:
    """Cancel an order. Only possible while it is still PREPARING; once a
    courier has it, it can't be recalled."""
    order = _get_order((order_id or "").strip().upper())
    if order is None:
        raise NotFound(f"No order with reference {order_id!r}.")

    current_status = _status(order)
    if current_status == "CANCELLED":
        raise AlreadyCancelled(f"Order {order['order_id']} is already cancelled.")
    if current_status != "PREPARING":
        raise TooLateToCancel(
            f"Order {order['order_id']} is already "
            f"{current_status.replace('_', ' ').lower()}; cancellation is only "
            "possible while it is still being prepared."
        )

    order["cancelled"] = True
    _save_order(order)
    return {
        "order_id": order["order_id"],
        "status": "CANCELLED",
        "reason": "Cancelled before the courier picked it up.",
    }


def reset() -> int:
    """Empty all orders. The ``delivery_reset_orders`` tool.

    Returns the number of orders cleared.
    """
    global _counter
    cleared = db.reset_world()
    _counter = 0
    return cleared


class OrderError(Exception):
    """Base class for order failures that agents are expected to handle."""

    code = "order_error"
    hint = None


class VendorNotFound(OrderError):
    code = "not_found"
    hint = "Check the vendor code, or call delivery_list_vendors."


class ItemNotFound(OrderError):
    code = "not_found"
    hint = "Check the item id against delivery_get_menu for this vendor."


class ZoneNotDeliverable(OrderError):
    code = "zone_not_deliverable"
    hint = "Choose a delivery zone within the deliverable area."


class NotFound(OrderError):
    code = "not_found"
    hint = "Check the order reference, or call delivery_list_orders."


class TooLateToCancel(OrderError):
    code = "too_late_to_cancel"
    hint = "Do not retry. Cancellation is not possible once a courier has picked up the order."


class AlreadyCancelled(OrderError):
    code = "already_cancelled"
    hint = "No action needed; the order is already cancelled."
