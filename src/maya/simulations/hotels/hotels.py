"""The hotels of Aira and their room types.

Reference data. The source of truth is data/hotels.csv and
data/room_types.csv; they are loaded into SQLite on init and read from
there, so editing either file changes the world on next run.

Character is mechanical, not prose: a hotel's ``cancel_cutoff_hours`` is its
whole cancellation policy (empty = non-refundable -- the hostel and the
budget lodge), and a room type's ``rooms_total`` and ``nightly_rate`` drive
how fast it sells out and what it costs (see bookings.py). ``style`` and
``stars`` are descriptive labels for agents to filter and reason with; they
don't change any rule on their own.
"""

from __future__ import annotations

from typing import NamedTuple

from . import db


class Hotel(NamedTuple):
    code: str
    name: str
    zone: str
    style: str
    stars: int
    cancel_cutoff_hours: int | None

    @property
    def refundable(self) -> bool:
        return self.cancel_cutoff_hours is not None

    def policy_text(self) -> str:
        if not self.refundable:
            return f"{self.name} bookings are non-refundable. Cancelling is not possible."
        return (
            f"Full refund if cancelled more than {self.cancel_cutoff_hours} hours "
            "before check-in. Cancellation is not possible within "
            f"{self.cancel_cutoff_hours} hours of check-in."
        )

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "name": self.name,
            "zone": self.zone,
            "style": self.style,
            "stars": self.stars,
            "refundable": self.refundable,
            "cancel_cutoff_hours": self.cancel_cutoff_hours,
            "cancellation_policy": self.policy_text(),
        }


class RoomType(NamedTuple):
    room_type_id: str
    hotel: str
    name: str
    max_guests: int
    rooms_total: int
    nightly_rate: int

    def to_dict(self) -> dict:
        return {
            "room_type_id": self.room_type_id,
            "hotel": self.hotel,
            "name": self.name,
            "max_guests": self.max_guests,
            "rooms_total": self.rooms_total,
            "base_nightly_rate": self.nightly_rate,
        }


def _load_hotels() -> tuple[Hotel, ...]:
    rows = db.connect().execute(
        "SELECT code, name, zone, style, stars, cancel_cutoff_hours FROM hotels"
    ).fetchall()
    return tuple(
        Hotel(r["code"], r["name"], r["zone"], r["style"], r["stars"], r["cancel_cutoff_hours"])
        for r in rows
    )


def _load_room_types() -> tuple[RoomType, ...]:
    rows = db.connect().execute(
        "SELECT room_type_id, hotel, name, max_guests, rooms_total, nightly_rate FROM room_types"
    ).fetchall()
    return tuple(
        RoomType(
            r["room_type_id"], r["hotel"], r["name"],
            r["max_guests"], r["rooms_total"], r["nightly_rate"],
        )
        for r in rows
    )


HOTELS: tuple[Hotel, ...] = _load_hotels()
BY_CODE: dict[str, Hotel] = {h.code: h for h in HOTELS}

ROOM_TYPES: tuple[RoomType, ...] = _load_room_types()
ROOM_TYPES_BY_ID: dict[str, RoomType] = {r.room_type_id: r for r in ROOM_TYPES}

ROOM_TYPES_BY_HOTEL: dict[str, list[RoomType]] = {}
for _room in ROOM_TYPES:
    ROOM_TYPES_BY_HOTEL.setdefault(_room.hotel, []).append(_room)


def get(code: str) -> Hotel | None:
    return BY_CODE.get((code or "").strip().upper())


def resolve(value: str) -> Hotel | None:
    """Accept a hotel code or name, because agents supply both."""
    if not value:
        return None
    needle = value.strip().lower()
    for hotel in HOTELS:
        if needle in (hotel.code.lower(), hotel.name.lower()):
            return hotel
    matches = [h for h in HOTELS if needle in h.name.lower()]
    return matches[0] if len(matches) == 1 else None


def get_room_type(room_type_id: str) -> RoomType | None:
    return ROOM_TYPES_BY_ID.get((room_type_id or "").strip().upper())


def room_types_for(hotel_code: str) -> list[RoomType]:
    return ROOM_TYPES_BY_HOTEL.get((hotel_code or "").strip().upper(), [])
