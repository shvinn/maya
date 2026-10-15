"""Restaurants and their menus, city by city.

Domain-agnostic world infrastructure, same footing as world.geography: a
restaurant is a place in the world, not something one domain sells. Each
service that uses restaurants (delivery today) keeps only its own terms in
its own data/, keyed by restaurant code -- whether a restaurant delivers is
whether it has a row there, so there is no "delivers" flag to keep in sync.

Each city gets its own subdirectory holding that city's restaurants.csv and
menu_items.csv (only aira/ exists so far). The menu belongs to the
restaurant, not to a service: it's the same food however you get it.

Character is expressed through `style`, not free text: `home_kitchen`,
`chain`, `ghost_kitchen` (no dining room, kitchen only), `bistro` and
`fine_dining`. What a
style means for prep time or price is up to each service's own terms.

Static reference facts with no persisted state, so this is plain CSV loaded
into memory at import time -- no SQLite, same as zones.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import NamedTuple

from maya.world.geography import maps

_PLACES_DIR = Path(__file__).resolve().parent


class Restaurant(NamedTuple):
    code: str
    name: str
    zone: str
    cuisine: str
    style: str


class MenuItem(NamedTuple):
    item_id: str
    restaurant: str
    name: str
    category: str
    price: int
    diet: str


def _city_dirs() -> list[Path]:
    return sorted(p for p in _PLACES_DIR.iterdir() if p.is_dir() and not p.name.startswith("__"))


def _rows(filename: str) -> list[dict]:
    rows = []
    for city_dir in _city_dirs():
        csv_path = city_dir / filename
        if not csv_path.exists():
            continue
        with open(csv_path, newline="") as f:
            rows.extend(csv.DictReader(f))
    return rows


def _load_restaurants() -> tuple[Restaurant, ...]:
    restaurants = tuple(
        Restaurant(r["code"], r["name"], r["zone"], r["cuisine"], r["style"])
        for r in _rows("restaurants.csv")
    )
    for r in restaurants:
        if r.zone not in maps.BY_CODE:
            raise ValueError(f"Restaurant {r.code} is in unknown zone {r.zone!r}.")
    return restaurants


def _load_menu_items() -> tuple[MenuItem, ...]:
    items = tuple(
        MenuItem(r["item_id"], r["restaurant"], r["name"], r["category"], int(r["price"]), r["diet"])
        for r in _rows("menu_items.csv")
    )
    for item in items:
        if item.restaurant not in BY_CODE:
            raise ValueError(f"Menu item {item.item_id} belongs to unknown restaurant {item.restaurant!r}.")
    return items


RESTAURANTS: tuple[Restaurant, ...] = _load_restaurants()
BY_CODE: dict[str, Restaurant] = {r.code: r for r in RESTAURANTS}

MENU_ITEMS: tuple[MenuItem, ...] = _load_menu_items()
