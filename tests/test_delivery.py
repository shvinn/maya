"""Delivery: order totals and ETA, live status, and cancellation only while
the food is still being prepared."""

from __future__ import annotations

from maya.simulations.delivery import mcp_tools as t
from maya.simulations.delivery import vendors

from .conftest import error_code



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


def test_every_price_says_its_currency():
    menu = t.delivery_get_menu("OQN")
    assert menu["vendor"]["currency"] == "bucks"
    assert {item["currency"] for item in menu["items"]} == {"bucks"}
    assert {item["currency"] for item in t.delivery_search_menu_items("noodles")} == {"bucks"}


def test_only_restaurants_with_delivery_terms_are_vendors():
    from maya.world import places

    codes = {v["code"] for v in t.delivery_list_vendors()}
    assert "NOR" in codes  # a ghost kitchen: delivery only
    assert "LPK" in places.BY_CODE and "LPK" not in codes  # dine-in only
    assert t.delivery_get_menu("LPK")["items"] == []
    assert all(item["vendor"] in codes for item in t.delivery_search_menu_items(limit=1000))


def test_zones_come_with_their_names():
    assert t.delivery_get_menu("LDG")["vendor"]["zone_name"] == "Finance Quarter"
    assert t.delivery_search_menu_items("sirloin")[0]["zone_name"] == "Finance Quarter"
    order = place("Portside")
    assert (order["vendor_zone_name"], order["delivery_zone_name"]) == ("Old Aira", "Portside")


def test_a_scheduled_order_waits_then_arrives_at_the_set_time(clock):
    # OQN to Old Aira: 22 min prep + an 8-minute hop, so the kitchen starts at 18:30
    order = t.delivery_place_order("OQN", [{"item_id": "OQN-01", "quantity": 1}], "AIR-OLD", "+1", "2030-03-09 19:00")
    assert (order["status"], order["deliver_at"]) == ("SCHEDULED", "2030-03-09 19:00:00")
    clock.set(clock.now.replace(day=9, hour=18, minute=29))
    assert t.delivery_get_order(order["reference"])["status"] == "SCHEDULED"
    clock.set(clock.now.replace(day=9, hour=18, minute=30))
    assert t.delivery_get_order(order["reference"])["status"] == "PREPARING"
    clock.set(clock.now.replace(day=9, hour=19, minute=0))
    assert t.delivery_get_order(order["reference"])["status"] == "DELIVERED"


def test_a_scheduled_order_can_be_cancelled_until_the_courier_has_it():
    ref = t.delivery_place_order("OQN", [{"item_id": "OQN-01", "quantity": 1}], "AIR-OLD", "+1", "2030-03-09 19:00")["reference"]
    assert t.delivery_cancel_order(ref)["status"] == "CANCELLED"


def test_delivery_time_must_be_reachable_and_within_a_week():
    def at(when):
        return t.delivery_place_order("OQN", [{"item_id": "OQN-01", "quantity": 1}], "AIR-OLD", "+1", when)

    assert error_code(at("2030-03-04 09:15")) == "invalid_delivery_time"  # ETA is 30 minutes
    assert at("2030-03-04 09:30")["status"] == "PREPARING"  # exactly the ETA: the kitchen starts now
    assert error_code(at("2030-03-12 09:00")) == "invalid_delivery_time"  # 8 days out
    assert error_code(at("Saturday 7pm")) == "invalid_delivery_time"
