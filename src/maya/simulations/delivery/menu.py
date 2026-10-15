"""Menu items across Aira's delivery vendors.

The menus are the restaurants' own (world.places); this is the deliverable
part of them -- items from restaurants that are vendors. Stock is unlimited
in this domain -- there is no sold-out-mid-order mechanic the way flights
has SoldOut; every item is always orderable.

get_menu() is for browsing one vendor once you already know who you're
ordering from. search() is the cross-vendor tool: it matches on item name
and on the owning vendor's cuisine, scored the same way airports.search is
(exact name match > name prefix > substring in name or cuisine), and
flattens each result with that vendor's code/name/zone attached -- an agent
searching "curry" needs to know which vendor to order from in the same
response, not a second call away -- flat beats nested.
"""

from __future__ import annotations

from typing import NamedTuple

from maya.world import places
from maya.world.geography import maps

from . import vendors


class MenuItem(NamedTuple):
    item_id: str
    vendor: str
    name: str
    category: str
    price: int
    diet: str

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "vendor": self.vendor,
            "name": self.name,
            "category": self.category,
            "price": self.price,
            "currency": vendors.CURRENCY,
            "diet": self.diet,
        }


def _load() -> tuple[MenuItem, ...]:
    return tuple(
        MenuItem(*item)
        for item in places.MENU_ITEMS
        if item.restaurant in vendors.BY_CODE
    )


MENU_ITEMS: tuple[MenuItem, ...] = _load()
BY_ITEM_ID: dict[str, MenuItem] = {m.item_id: m for m in MENU_ITEMS}

BY_VENDOR: dict[str, list[MenuItem]] = {}
for _item in MENU_ITEMS:
    BY_VENDOR.setdefault(_item.vendor, []).append(_item)


def get(item_id: str) -> MenuItem | None:
    return BY_ITEM_ID.get((item_id or "").strip().upper())


def get_menu(vendor_code: str) -> list[MenuItem]:
    return BY_VENDOR.get((vendor_code or "").strip().upper(), [])


def _result(item: MenuItem) -> dict:
    """One menu item, flattened with its vendor's identifying fields."""
    vendor = vendors.get(item.vendor)
    out = item.to_dict()
    out["vendor_name"] = vendor.name if vendor else item.vendor
    out["zone"] = vendor.zone if vendor else None
    out["zone_name"] = maps.BY_CODE[vendor.zone].name if vendor else None
    return out


def search(
    query: str = "",
    category: str | None = None,
    diet: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Find menu items by name or by the owning vendor's cuisine, across
    every vendor. An empty query returns items across all vendors, still
    subject to category/diet filters."""
    needle = (query or "").strip().lower()
    scored = []
    for item in MENU_ITEMS:
        if category and item.category != category:
            continue
        if diet and item.diet != diet:
            continue
        vendor = vendors.get(item.vendor)
        cuisine = vendor.cuisine.lower() if vendor else ""
        name = item.name.lower()
        if not needle:
            score = 0
        elif needle == name:
            score = 100
        elif name.startswith(needle):
            score = 70
        elif needle in name or needle in cuisine:
            score = 40
        else:
            continue
        scored.append((score, item.name, item))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [_result(item) for _, _, item in scored[:limit]]
