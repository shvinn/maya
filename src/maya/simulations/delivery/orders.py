"""Delivery orders, persisted in SQLite.

An order is a single step, same as a flight booking: you browse a vendor's
menu, you place the order, you are confirmed. There is no hold-then-pay
step and no priced offer to expire: the total (items plus delivery fee) is
paid from the ordering resident's wallet in the same transaction that
places the order (``insufficient_funds`` otherwise), and cancelling refunds
it in full. A resident only sees and cancels their own orders; anyone
else's is not found.

Status is never stored -- it is computed live from elapsed time since
the kitchen started (prep_starts_at), the same way flights computes seat availability from time to
departure rather than storing it. An order is PREPARING for
vendor.prep_minutes, then OUT_FOR_DELIVERY for courier_minutes (the road
distance between the vendor's zone and the delivery zone, from
world.geography.maps), then DELIVERED. CANCELLED is the one state that is
actually persisted, since it overrides whatever the clock would otherwise
say.

An order can be scheduled ahead with ``deliver_at``, up to
``SCHEDULE_AHEAD_DAYS`` out and no sooner than the ETA allows. It is
SCHEDULED until the kitchen starts on it -- prep and courier time before
``deliver_at`` -- and then runs the same PREPARING -> OUT_FOR_DELIVERY ->
DELIVERED course, arriving at ``deliver_at``. An order placed for now starts
at once.

Cancellation is only possible while an order is SCHEDULED or still
PREPARING -- once a courier has picked it up, it can't be recalled. A ghost_kitchen vendor's
near-zero prep_minutes therefore makes its orders mechanically almost
impossible to cancel: not a special rule, just what falls out of the
timing, the same way Puffin Air's fares are non-refundable by a hardcoded
list rather than a timing accident.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta

from maya import storage
from maya.simulations.residents import residents
from maya.world import clock
from maya.world.geography import maps

from . import db, vendors
from .menu import BY_ITEM_ID

CURRENCY = vendors.CURRENCY

#: How far ahead an order can be scheduled.
SCHEDULE_AHEAD_DAYS = 7

_TIME_FORMAT = "%Y-%m-%d %H:%M:%S"


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


def _all_orders() -> list[dict]:
    rows = db.connect().execute("SELECT data FROM orders").fetchall()
    return [json.loads(row["data"]) for row in rows]


def _next_order_id() -> str:
    """A plain, sequential order number -- the way a receipt or a delivery
    app shows one, not a randomised locator like a flight's PNR. There is
    no reason for a food order to hide its sequence the way a booking
    reference does across a whole airline's reservation system.

    Numbered from the orders already stored, read inside the placing
    transaction -- never from an in-memory counter, which two processes
    sharing the database would both hand out.
    """
    return str(1000 + db.connect().execute("SELECT COUNT(*) AS n FROM orders").fetchone()["n"] + 1)


def _status(order: dict) -> str:
    """Order status, computed live from elapsed time -- CANCELLED is the
    only value that's actually stored, as a terminal override."""
    if order["cancelled"]:
        return "CANCELLED"
    # records stored before scheduling existed start when they were placed
    starts_at = datetime.strptime(order.get("prep_starts_at", order["placed_at"]), _TIME_FORMAT)
    elapsed_minutes = (clock.now() - starts_at) / timedelta(minutes=1)
    if elapsed_minutes < 0:
        return "SCHEDULED"
    if elapsed_minutes < order["prep_minutes"]:
        return "PREPARING"
    if elapsed_minutes < order["prep_minutes"] + order["courier_minutes"]:
        return "OUT_FOR_DELIVERY"
    return "DELIVERED"


def _with_live_status(order: dict) -> dict:
    out = dict(order)
    out.setdefault("reference", out["order_id"])  # records stored before it existed
    out["status"] = _status(order)
    out.pop("cancelled", None)
    return out


def _place(
    vendor_code: str,
    items: list[dict],
    delivery_zone: str,
    contact_phone: str,
    resident_id: str,
    deliver_at: str | None = None,
) -> dict:
    """Confirm an order at one vendor, for now or for ``deliver_at``, paid by
    ``resident_id``. Raises VendorNotFound, ItemNotFound,
    ZoneNotDeliverable, InvalidDeliveryTime or residents.InsufficientFunds."""
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
    eta_minutes = vendor.prep_minutes + courier_minutes
    total = subtotal + vendor.delivery_fee

    now = clock.now().replace(microsecond=0)
    prep_starts_at = now
    if deliver_at:
        try:
            arrival = datetime.strptime(deliver_at.strip(), "%Y-%m-%d %H:%M")
        except ValueError:
            raise InvalidDeliveryTime(f"deliver_at {deliver_at!r} is not in YYYY-MM-DD HH:MM form.") from None
        earliest = now + timedelta(minutes=eta_minutes)
        if arrival < earliest:
            raise InvalidDeliveryTime(
                f"{vendor.name} needs {eta_minutes} minutes to reach {zone.name}; the earliest "
                f"delivery is {earliest:%Y-%m-%d %H:%M}."
            )
        if arrival > now + timedelta(days=SCHEDULE_AHEAD_DAYS):
            raise InvalidDeliveryTime(f"Orders can be scheduled at most {SCHEDULE_AHEAD_DAYS} days ahead.")
        prep_starts_at = arrival - timedelta(minutes=eta_minutes)

    order = {
        "order_id": (reference := _next_order_id()),
        "reference": reference,
        "cancelled": False,
        "vendor_code": vendor.code,
        "vendor_name": vendor.name,
        "vendor_zone": vendor.zone,
        "vendor_zone_name": maps.BY_CODE[vendor.zone].name,
        "items": lines,
        "delivery_zone": zone.code,
        "delivery_zone_name": zone.name,
        "contact_phone": contact_phone,
        "resident_id": resident_id,
        "subtotal": {"amount": subtotal, "currency": CURRENCY},
        "delivery_fee": {"amount": vendor.delivery_fee, "currency": CURRENCY},
        "total_price": {"amount": total, "currency": CURRENCY},
        "prep_minutes": vendor.prep_minutes,
        "courier_minutes": courier_minutes,
        "eta_minutes": eta_minutes,
        "placed_at": now.strftime(_TIME_FORMAT),
        "prep_starts_at": prep_starts_at.strftime(_TIME_FORMAT),
        "deliver_at": (prep_starts_at + timedelta(minutes=eta_minutes)).strftime(_TIME_FORMAT),
    }
    paid = residents.charge(db.connect(), resident_id, total, reference, "delivery")
    _save_order(order)
    return {**_with_live_status(order), "payment": residents.payment(paid)}

def place(
    vendor_code: str,
    items: list[dict],
    delivery_zone: str,
    contact_phone: str,
    resident_id: str,
    deliver_at: str | None = None,
) -> dict:
    """Place atomically: every check and write below -- the payment
    included -- happens in one transaction (see maya.storage.transaction)."""
    residents.ready()
    with storage.transaction(db.connect()):
        return _place(vendor_code, items, delivery_zone, contact_phone, resident_id, deliver_at)


def _owned(order_id: str, resident_id: str) -> dict | None:
    """The order, if ``resident_id`` owns it. Someone else's order is
    None, exactly like one that doesn't exist, so a reference never
    confirms that another resident's order is there."""
    order = _get_order((order_id or "").strip().upper())
    return order if order and order.get("resident_id") == resident_id else None


def get(order_id: str, resident_id: str) -> dict | None:
    order = _owned(order_id, resident_id)
    return _with_live_status(order) if order else None


def list_all(resident_id: str, phone: str | None = None, status: str | None = None) -> list[dict]:
    out = [_with_live_status(o) for o in _all_orders() if o.get("resident_id") == resident_id]
    if phone:
        needle = phone.strip()
        out = [o for o in out if o["contact_phone"] == needle]
    if status:
        wanted = status.strip().upper()
        out = [o for o in out if o["status"] == wanted]
    return sorted(out, key=lambda o: o["placed_at"])


def _cancel(order_id: str, resident_id: str) -> dict:
    """Cancel an order, for a full refund. Only possible while it is
    SCHEDULED or still PREPARING; once a courier has it, it can't be
    recalled."""
    order = _owned(order_id, resident_id)
    if order is None:
        raise NotFound(f"No order with reference {order_id!r}.")

    current_status = _status(order)
    if current_status == "CANCELLED":
        raise AlreadyCancelled(f"Order {order['order_id']} is already cancelled.")
    if current_status not in ("SCHEDULED", "PREPARING"):
        raise TooLateToCancel(
            f"Order {order['order_id']} is already "
            f"{current_status.replace('_', ' ').lower()}; cancellation is only "
            "possible while it is still being prepared."
        )

    order["cancelled"] = True
    refund = {"amount": order["total_price"]["amount"], "currency": CURRENCY}
    order["refund"] = refund
    # To the resident who paid -- the order's owner -- whoever is asking.
    refunded = residents.refund(db.connect(), order["resident_id"], refund["amount"], order["order_id"], "delivery")
    _save_order(order)
    return {
        "order_id": order["order_id"],
        "reference": order["order_id"],
        "status": "CANCELLED",
        "refund": refund,
        "payment": residents.payment(refunded),
        "reason": "Cancelled before the courier picked it up: full refund.",
    }

def cancel(order_id: str, resident_id: str) -> dict:
    """Cancel atomically: every check and write below -- the refund
    included -- happens in one transaction (see maya.storage.transaction)."""
    residents.ready()
    with storage.transaction(db.connect()):
        return _cancel(order_id, resident_id)


def reset(conn: sqlite3.Connection) -> int:
    """Empty all orders. Part of a world reset (see maya.reset).

    Returns the number of orders cleared.
    """
    return db.reset_world(conn)


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


class InvalidDeliveryTime(OrderError):
    code = "invalid_delivery_time"
    hint = "Use YYYY-MM-DD HH:MM in Maya time, no sooner than the ETA allows and within a week. Omit it to deliver now."


class NotFound(OrderError):
    code = "not_found"
    hint = "Check the order reference, or call delivery_list_orders."


class TooLateToCancel(OrderError):
    code = "too_late_to_cancel"
    hint = "Do not retry. Cancellation is not possible once a courier has picked up the order."


class AlreadyCancelled(OrderError):
    code = "already_cancelled"
    hint = "No action needed; the order is already cancelled."
