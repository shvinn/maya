"""The delivery vendors of Aira.

Reference data. The source of truth is data/vendors.csv; it is loaded into
SQLite on init and read from there, so editing that file changes the world
on next run.

Character is expressed through `style`, not free text, the same way an
airline's rate_per_minute (not its blurb) drives fares: `home_kitchen`
(cheap, slow prep), `chain` (mid-price, reliable, moderate prep),
`ghost_kitchen` (cheap, near-instant prep -- fast enough that cancelling is
effectively impossible once placed, see orders.py), `bistro` (pricier,
slower, higher quality).
"""

from __future__ import annotations

from typing import NamedTuple

from . import db


class Vendor(NamedTuple):
    code: str
    name: str
    zone: str
    cuisine: str
    style: str
    prep_minutes: int
    delivery_fee: int

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "zone": self.zone,
            "cuisine": self.cuisine,
            "style": self.style,
            "prep_minutes": self.prep_minutes,
            "delivery_fee": self.delivery_fee,
        }


def _load() -> tuple[Vendor, ...]:
    rows = db.connect().execute(
        "SELECT code, name, zone, cuisine, style, prep_minutes, delivery_fee FROM vendors"
    ).fetchall()
    return tuple(
        Vendor(
            r["code"], r["name"], r["zone"], r["cuisine"], r["style"],
            r["prep_minutes"], r["delivery_fee"],
        )
        for r in rows
    )


VENDORS: tuple[Vendor, ...] = _load()
BY_CODE: dict[str, Vendor] = {v.code: v for v in VENDORS}


def get(code: str) -> Vendor | None:
    return BY_CODE.get((code or "").strip().upper())


def resolve(value: str) -> Vendor | None:
    """Accept a vendor code or name, because agents supply both."""
    if not value:
        return None
    needle = value.strip().lower()
    for vendor in VENDORS:
        if needle in (vendor.code.lower(), vendor.name.lower()):
            return vendor
    matches = [v for v in VENDORS if needle in v.name.lower()]
    return matches[0] if len(matches) == 1 else None


def search(zone: str | None = None, cuisine: str | None = None, limit: int = 10) -> list[Vendor]:
    """Vendors, optionally filtered by zone code (exact) or cuisine (partial,
    case-insensitive -- "ind" matches "Indian")."""
    out = list(VENDORS)
    if zone:
        wanted = zone.strip().upper()
        out = [v for v in out if v.zone == wanted]
    if cuisine:
        needle = cuisine.strip().lower()
        out = [v for v in out if needle in v.cuisine.lower()]
    return out[:limit]
