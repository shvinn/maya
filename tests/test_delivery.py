"""Delivery: order totals and ETA, live status, and cancellation only while
the food is still being prepared."""

from __future__ import annotations

from maya.simulations.delivery import mcp_tools as t
from maya.simulations.delivery import vendors


def error_code(result: dict) -> str | None:
    return result.get("error", {}).get("code") if isinstance(result, dict) else None


def place(zone: str = "AIR-OLD", items: list[dict] | None = None) -> dict:
    return t.delivery_place_order("OQN", items or [{"item_id": "OQN-01", "quantity": 2}], zone, "+00 555 0100")


def test_total_is_items_plus_delivery_fee_and_eta_is_prep_plus_courier():
    vendor = vendors.get("OQN")  # Old Aira, 22 min prep, fee 2
    order = place("AIR-OLD")  # same zone: 8-minute courier hop
    assert order["subtotal"]["amount"] == 2 * 14
    assert order["total_price"]["amount"] == 2 * 14 + vendor.delivery_fee
    assert order["eta_minutes"] == vendor.prep_minutes + order["courier_minutes"]


def test_status_moves_from_preparing_to_out_for_delivery_to_delivered(clock):
    order = place()
    ref = order["order_id"]
    assert order["status"] == "PREPARING"

    clock.advance(minutes=order["prep_minutes"])
    assert t.delivery_get_order(ref)["status"] == "OUT_FOR_DELIVERY"
    assert error_code(t.delivery_cancel_order(ref)) == "too_late_to_cancel"

    clock.advance(minutes=order["courier_minutes"])
    assert t.delivery_get_order(ref)["status"] == "DELIVERED"


def test_cancelling_while_preparing_works_once():
    ref = place()["order_id"]
    assert t.delivery_cancel_order(ref)["status"] == "CANCELLED"
    assert error_code(t.delivery_cancel_order(ref)) == "already_cancelled"


def test_delivery_only_reaches_aira_zones():
    assert error_code(place("Meghas")) == "zone_not_deliverable"


def test_items_must_come_from_the_vendors_menu():
    assert error_code(place(items=[{"item_id": "CBS-01", "quantity": 1}])) == "not_found"
