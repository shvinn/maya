"""The dine-in restaurants of Aira: the restaurants that seat guests.

A dine-in restaurant is a restaurant from world.places plus its dining terms
from data/dining.csv (hours, seats, policies). A restaurant with no row
there has no dining room -- ghost kitchens, for one. The terms are loaded
into SQLite on init and read from there, so editing that file changes the
world on next run.

Tables are booked in half-hour slots, from opening until the last seating
(the latest slot whose ``sitting_minutes`` still end by closing time).
Each slot seats ``covers_per_slot`` guests, counted on their own: a slot
holds that many people however its neighbours are booked.

Character is mechanical: ``popularity`` drives how fast other diners fill
the slots (see reservations.py), ``deposit_per_guest`` is what a booking
puts down, and ``cancel_cutoff_hours`` is how late it can be cancelled with
the deposit back.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import NamedTuple

from maya.world import places

from . import db

#: Maya's currency. Deposits and menu prices are in it.
CURRENCY = "bucks"

SLOT_MINUTES = 30

_WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")


class DiningRestaurant(NamedTuple):
    code: str
    name: str
    zone: str
    cuisine: str
    style: str
    opens: time
    closes: time
    closed_days: tuple[str, ...]
    covers_per_slot: int
    max_party: int
    sitting_minutes: int
    popularity: float
    deposit_per_guest: int
    cancel_cutoff_hours: int

    def open_on(self, day: date) -> bool:
        return _WEEKDAYS[day.weekday()] not in self.closed_days

    def last_seating(self) -> time:
        """The latest slot whose sitting still ends by closing time."""
        span = datetime.combine(date.min, self.closes) - datetime.combine(date.min, self.opens)
        slots = (span - timedelta(minutes=self.sitting_minutes)) // timedelta(minutes=SLOT_MINUTES)
        return (datetime.combine(date.min, self.opens) + slots * timedelta(minutes=SLOT_MINUTES)).time()

    def slots(self, day: date) -> list[datetime]:
        """Every bookable seating time on a day, earliest first; none on a
        closed day."""
        if not self.open_on(day):
            return []
        start = datetime.combine(day, self.opens)
        last = datetime.combine(day, self.last_seating())
        out = []
        while start <= last:
            out.append(start)
            start += timedelta(minutes=SLOT_MINUTES)
        return out

    def policy_text(self) -> str:
        if not self.deposit_per_guest:
            return "No deposit. Free cancellation until the booking time."
        return (
            f"Deposit of {self.deposit_per_guest} {CURRENCY} per guest. Refunded if cancelled at least "
            f"{self.cancel_cutoff_hours} hours ahead; kept if cancelled later."
        )

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "zone": self.zone,
            "cuisine": self.cuisine,
            "style": self.style,
            "opens": f"{self.opens:%H:%M}",
            "closes": f"{self.closes:%H:%M}",
            "closed_days": list(self.closed_days),
            "last_seating": f"{self.last_seating():%H:%M}",
            "sitting_minutes": self.sitting_minutes,
            "max_party": self.max_party,
            "deposit_per_guest": {"amount": self.deposit_per_guest, "currency": CURRENCY},
            "cancellation_policy": self.policy_text(),
        }


def _load() -> tuple[DiningRestaurant, ...]:
    rows = db.connect().execute(
        "SELECT code, opens, closes, closed_days, covers_per_slot, max_party, sitting_minutes,"
        " popularity, deposit_per_guest, cancel_cutoff_hours FROM dining_terms"
    ).fetchall()
    out = []
    for r in rows:
        place = places.BY_CODE.get(r["code"])
        if place is None:
            raise ValueError(f"Dining terms for unknown restaurant {r['code']!r}.")
        out.append(DiningRestaurant(
            *place,
            time.fromisoformat(r["opens"]), time.fromisoformat(r["closes"]),
            tuple(r["closed_days"].split()), r["covers_per_slot"], r["max_party"],
            r["sitting_minutes"], r["popularity"], r["deposit_per_guest"], r["cancel_cutoff_hours"],
        ))
    return tuple(out)


RESTAURANTS: tuple[DiningRestaurant, ...] = _load()
BY_CODE: dict[str, DiningRestaurant] = {r.code: r for r in RESTAURANTS}


def get(code: str) -> DiningRestaurant | None:
    return BY_CODE.get((code or "").strip().upper())


def menu(code: str) -> list[dict]:
    """The restaurant's menu -- the same one delivery sells from."""
    return [
        {
            "item_id": item.item_id,
            "name": item.name,
            "category": item.category,
            "price": item.price,
            "currency": CURRENCY,
            "diet": item.diet,
        }
        for item in places.MENU_ITEMS
        if item.restaurant == code
    ]


def search(
    zone: str | None = None, cuisine: str | None = None, query: str | None = None, limit: int = 20
) -> list[DiningRestaurant]:
    """Dine-in restaurants, optionally filtered by zone code (exact), cuisine
    (partial, case-insensitive) or a query matching the name or cuisine."""
    out = list(RESTAURANTS)
    if zone:
        wanted = zone.strip().upper()
        out = [r for r in out if r.zone == wanted]
    if cuisine:
        needle = cuisine.strip().lower()
        out = [r for r in out if needle in r.cuisine.lower()]
    if query:
        needle = query.strip().lower()
        out = [r for r in out if needle in r.name.lower() or needle in r.cuisine.lower()]
    return out[:limit]
