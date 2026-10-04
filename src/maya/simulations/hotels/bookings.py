"""Hotel bookings, persisted in SQLite.

A booking and the rooms sold per room-type-night survive a restart -- the
rest of the domain is reference data re-derived from CSV. ``reset()`` (the
``hotels_reset_bookings`` tool) is the explicit way to empty it back out.

Booking is a single step, same as flights and delivery: search, book,
confirmed. No hold, no rate plan to choose, no payment call.

Inventory is per room type *per night*, because that is what a hotel
actually sells: a 3-night stay needs a free room on each of its 3 nights,
and the stay is only as available as its fullest night. Each night also
fills up on its own as it approaches -- the flights background-demand curve
applied to nights instead of departures: occupancy rises linearly over the
``BACKGROUND_DEMAND_HORIZON_DAYS`` before a night, levelling off at
``BACKGROUND_DEMAND_MAX_OCCUPANCY`` -- roughly where a busy real city hotel
runs, rather than flights' near-full planes. The simulated other guests also
never take the last ``BACKGROUND_DEMAND_FLOOR_ROOMS``, which only matters
for room types small enough that 85% would otherwise leave none -- only
real bookings can sell a room type out completely.

Prices are computed, not stored: the room type's ``nightly_rate``, surged by
up to ``PRICE_SURGE_MAX`` as that night fills. So a last-minute night costs
more than one booked a month ahead, and each night of a stay is priced
separately. The price is locked into the booking once confirmed.

Status is never stored -- like delivery orders, it is computed live from the
clock against check-in (``CHECK_IN_TIME`` on the check-in date) and
check-out (``CHECK_OUT_TIME`` on the check-out date). CANCELLED is the one
stored override.

Cancellation is per hotel: a full refund up to the hotel's
``cancel_cutoff_hours`` before check-in, flatly impossible inside it, and
never possible at all for a non-refundable hotel. No partial refunds.
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, time, timedelta

from maya import storage
from maya.world import clock
from maya.world.geography import maps

from . import db, hotels
from .hotels import Hotel, RoomType

CURRENCY = "bucks"

CHECK_IN_TIME = time(15, 0)
CHECK_OUT_TIME = time(11, 0)
MAX_NIGHTS = 14
MAX_DAYS_AHEAD = 365

#: How far out a night starts looking untouched by other guests.
BACKGROUND_DEMAND_HORIZON_DAYS = 30
#: Occupancy the background simulation levels off at on the night itself.
BACKGROUND_DEMAND_MAX_OCCUPANCY = 0.85
#: Rooms per room type that the background simulation alone will never claim.
BACKGROUND_DEMAND_FLOOR_ROOMS = 1

#: Nightly surcharge at fully sold-down, on top of the base rate.
PRICE_SURGE_MAX = 0.3

#: Record locators skip letters that look like digits.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _get_booking(reference: str) -> dict | None:
    row = db.connect().execute(
        "SELECT data FROM hotel_bookings WHERE reference = ?", (reference,)
    ).fetchone()
    return json.loads(row["data"]) if row else None


def _save_booking(booking: dict) -> None:
    conn = db.connect()
    conn.execute(
        "INSERT OR REPLACE INTO hotel_bookings (reference, data) VALUES (?, ?)",
        (booking["booking_reference"], json.dumps(booking)),
    )


def _all_bookings() -> list[dict]:
    rows = db.connect().execute("SELECT data FROM hotel_bookings").fetchall()
    return [json.loads(row["data"]) for row in rows]


def _rooms_sold(room_type_id: str, night: date) -> int:
    row = db.connect().execute(
        "SELECT count FROM rooms_sold WHERE room_type_id = ? AND night = ?",
        (room_type_id, night.isoformat()),
    ).fetchone()
    return row["count"] if row else 0


def _adjust_rooms_sold(room_type_id: str, nights: list[date], delta: int) -> None:
    conn = db.connect()
    for night in nights:
        new_count = max(0, _rooms_sold(room_type_id, night) + delta)
        conn.execute(
            "INSERT OR REPLACE INTO rooms_sold (room_type_id, night, count) VALUES (?, ?, ?)",
            (room_type_id, night.isoformat(), new_count),
        )


def _next_reference() -> str:
    """A six-character locator, same generator as flights but salted, so
    hotel booking #1 never shares a reference with flight booking #1.

    Numbered from the bookings already stored, read inside the booking's
    transaction -- never from an in-memory counter, which two processes
    sharing the database would both hand out (and the second booking would
    overwrite the first).
    """
    n = db.connect().execute("SELECT COUNT(*) AS n FROM hotel_bookings").fetchone()["n"] + 1
    rng = random.Random((n * 2654435761 + 0x407E1) % 2**32)
    return "".join(rng.choice(_ALPHABET) for _ in range(6))


def _parse_date(value: str, field: str) -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        raise InvalidDates(f"{field} {value!r} is not a date in YYYY-MM-DD form.") from None


def parse_stay(check_in: str, check_out: str) -> tuple[date, date, list[date]]:
    """Validate a stay and return (check_in, check_out, nights). A night is
    named by the date it starts on, so a stay's nights run from check_in up
    to but not including check_out."""
    start = _parse_date(check_in, "check_in")
    end = _parse_date(check_out, "check_out")
    today = clock.now().date()
    if start < today:
        raise InvalidDates(f"check_in {start.isoformat()} is in the past.")
    if end <= start:
        raise InvalidDates("check_out must be at least one day after check_in.")
    if (end - start).days > MAX_NIGHTS:
        raise InvalidDates(f"Stays are limited to {MAX_NIGHTS} nights; this is {(end - start).days}.")
    if (start - today).days > MAX_DAYS_AHEAD:
        raise InvalidDates(f"Bookings open at most {MAX_DAYS_AHEAD} days ahead.")
    nights = [start + timedelta(days=i) for i in range((end - start).days)]
    return start, end, nights


def _check_in_at(night: date) -> datetime:
    return datetime.combine(night, CHECK_IN_TIME)


def _background_sold(room: RoomType, night: date) -> int:
    """Rooms other guests have taken, purely a function of time to the night."""
    days_left = (_check_in_at(night) - clock.now()) / timedelta(days=1)
    ramp = max(0.0, min(1.0, 1 - days_left / BACKGROUND_DEMAND_HORIZON_DAYS))
    pct_sold = ramp * BACKGROUND_DEMAND_MAX_OCCUPANCY
    sold = min(round(room.rooms_total * pct_sold), room.rooms_total - BACKGROUND_DEMAND_FLOOR_ROOMS)
    return max(0, sold)


def rooms_available(room: RoomType, night: date) -> int:
    taken = _background_sold(room, night) + _rooms_sold(room.room_type_id, night)
    return max(0, room.rooms_total - taken)


def price_for_night(room: RoomType, night: date) -> int:
    """One night's rate: base rate, surcharged by how full that night is."""
    load_factor = 1 - rooms_available(room, night) / room.rooms_total
    return round(room.nightly_rate * (1 + PRICE_SURGE_MAX * load_factor))


def quote(room: RoomType, nights: list[date]) -> dict:
    """Live price and availability for one room type across a stay."""
    nightly = [{"night": n.isoformat(), "price": price_for_night(room, n)} for n in nights]
    return {
        "rooms_left": min(rooms_available(room, n) for n in nights),
        "nightly_prices": nightly,
        "total": sum(n["price"] for n in nightly),
    }


def search(
    check_in: str,
    check_out: str,
    guests: int = 1,
    zone: str | None = None,
    max_total_price: int | None = None,
    limit: int = 10,
) -> list[dict]:
    """Room types with a room free on every night of the stay, cheapest
    total first, each flattened with its hotel's identifying fields."""
    start, end, nights = parse_stay(check_in, check_out)
    zone_code = None
    if zone:
        found = maps.resolve_zone(zone)
        if found is None:
            raise ZoneNotFound(f"{zone!r} is not a known zone.")
        zone_code = found.code

    results = []
    for room in hotels.ROOM_TYPES:
        hotel = hotels.get(room.hotel)
        if hotel is None or (zone_code and hotel.zone != zone_code):
            continue
        if room.max_guests < guests:
            continue
        q = quote(room, nights)
        if q["rooms_left"] == 0:
            continue
        if max_total_price is not None and q["total"] > max_total_price:
            continue
        results.append({
            "room_type_id": room.room_type_id,
            "room_name": room.name,
            "max_guests": room.max_guests,
            "hotel_code": hotel.code,
            "hotel_name": hotel.name,
            "zone": hotel.zone,
            "style": hotel.style,
            "stars": hotel.stars,
            "check_in": start.isoformat(),
            "check_out": end.isoformat(),
            "nights": len(nights),
            "rooms_left": q["rooms_left"],
            "nightly_prices": q["nightly_prices"],
            "total_price": {"amount": q["total"], "currency": CURRENCY},
            "refundable": hotel.refundable,
            "cancellation_policy": hotel.policy_text(),
        })
    results.sort(key=lambda r: (r["total_price"]["amount"], r["room_type_id"]))
    return results[:limit]


def _status(booking: dict) -> str:
    """Booking status, computed live from the clock -- CANCELLED is the only
    value that's actually stored, as a terminal override."""
    if booking["cancelled"]:
        return "CANCELLED"
    now = clock.now()
    if now < datetime.fromisoformat(booking["check_in_at"]):
        return "CONFIRMED"
    if now < datetime.fromisoformat(booking["check_out_at"]):
        return "CHECKED_IN"
    return "CHECKED_OUT"


def _with_live_status(booking: dict) -> dict:
    out = dict(booking)
    out.setdefault("reference", out["booking_reference"])  # records stored before it existed
    out["status"] = _status(booking)
    out.pop("cancelled", None)
    return out


def _book(
    room_type_id: str,
    check_in: str,
    check_out: str,
    guests: int,
    guest_name: str,
    contact_email: str,
) -> dict:
    """Confirm a stay in one room. Raises RoomTypeNotFound, InvalidDates,
    InvalidGuests, TooManyGuests or SoldOut."""
    room = hotels.get_room_type(room_type_id)
    if room is None:
        raise RoomTypeNotFound(f"No room type with id {room_type_id!r}.")
    hotel = hotels.get(room.hotel)
    start, end, nights = parse_stay(check_in, check_out)
    if guests < 1:
        raise InvalidGuests(f"guests must be at least 1; got {guests}.")
    if guests > room.max_guests:
        raise TooManyGuests(
            f"{hotel.name} {room.name} sleeps at most {room.max_guests}; {guests} requested."
        )
    for night in nights:
        if rooms_available(room, night) < 1:
            raise SoldOut(
                f"{hotel.name} {room.name} is sold out on the night of {night.isoformat()}."
            )

    q = quote(room, nights)
    _adjust_rooms_sold(room.room_type_id, nights, 1)
    booking = {
        "booking_reference": (reference := _next_reference()),
        "reference": reference,
        "cancelled": False,
        "hotel_code": hotel.code,
        "hotel_name": hotel.name,
        "zone": hotel.zone,
        "room_type_id": room.room_type_id,
        "room_name": room.name,
        "check_in": start.isoformat(),
        "check_out": end.isoformat(),
        "check_in_at": _check_in_at(start).isoformat(sep=" ", timespec="minutes"),
        "check_out_at": datetime.combine(end, CHECK_OUT_TIME).isoformat(sep=" ", timespec="minutes"),
        "nights": len(nights),
        "guests": guests,
        "guest_name": guest_name,
        "contact_email": contact_email,
        "nightly_prices": q["nightly_prices"],
        "total_price": {"amount": q["total"], "currency": CURRENCY},
        "refundable": hotel.refundable,
        "cancellation_policy": hotel.policy_text(),
        "booked_at": clock.now().strftime("%Y-%m-%d %H:%M"),
    }
    _save_booking(booking)
    return _with_live_status(booking)

def book(
    room_type_id: str,
    check_in: str,
    check_out: str,
    guests: int,
    guest_name: str,
    contact_email: str,
) -> dict:
    """Book atomically: every check and write below happens in one
    transaction (see maya.storage.transaction), so concurrent bookings
    can't both take the last of anything."""
    with storage.transaction(db.connect()):
        return _book(room_type_id, check_in, check_out, guests, guest_name, contact_email)


def get(reference: str) -> dict | None:
    booking = _get_booking((reference or "").strip().upper())
    return _with_live_status(booking) if booking else None


def list_all(email: str | None = None, status: str | None = None) -> list[dict]:
    out = [_with_live_status(b) for b in _all_bookings()]
    if email:
        needle = email.strip().lower()
        out = [b for b in out if b["contact_email"].lower() == needle]
    if status:
        wanted = status.strip().upper()
        out = [b for b in out if b["status"] == wanted]
    return sorted(out, key=lambda b: (b["check_in"], b["booking_reference"]))


def _cancel(reference: str) -> dict:
    """Cancel a booking: full refund outside the hotel's cutoff, impossible
    inside it, and never possible at a non-refundable hotel."""
    booking = _get_booking((reference or "").strip().upper())
    if booking is None:
        raise NotFound(f"No booking with reference {reference!r}.")
    if booking["cancelled"]:
        raise AlreadyCancelled(f"Booking {booking['booking_reference']} is already cancelled.")

    hotel: Hotel | None = hotels.get(booking["hotel_code"])
    if hotel is None or not hotel.refundable:
        raise NotRefundable(
            hotel.policy_text() if hotel else f"{booking['hotel_name']} bookings are non-refundable."
        )

    hours_left = (datetime.fromisoformat(booking["check_in_at"]) - clock.now()) / timedelta(hours=1)
    if hours_left < hotel.cancel_cutoff_hours:
        raise TooLateToCancel(
            f"Booking {booking['booking_reference']} checks in in {max(hours_left, 0):.1f} "
            f"hour(s); {hotel.name} only allows cancellation more than "
            f"{hotel.cancel_cutoff_hours} hours before check-in."
        )

    booking["cancelled"] = True
    start = date.fromisoformat(booking["check_in"])
    nights = [start + timedelta(days=i) for i in range(booking["nights"])]
    _adjust_rooms_sold(booking["room_type_id"], nights, -1)
    refund = {"amount": booking["total_price"]["amount"], "currency": CURRENCY}
    booking["refund"] = refund
    _save_booking(booking)
    return {
        "booking_reference": booking["booking_reference"],
        "reference": booking["booking_reference"],
        "status": "CANCELLED",
        "refund": refund,
        "reason": (
            f"Cancelled more than {hotel.cancel_cutoff_hours} hours before check-in: full refund."
        ),
    }

def cancel(reference: str) -> dict:
    """Cancel atomically: every check and write below happens in one
    transaction (see maya.storage.transaction), so two requests can't
    cancel the same booking twice or release its inventory twice."""
    with storage.transaction(db.connect()):
        return _cancel(reference)


def reset() -> int:
    """Empty all bookings and rooms sold. The ``hotels_reset_bookings`` tool.

    Returns the number of bookings cleared.
    """
    cleared = db.reset_world()
    return cleared


class BookingError(Exception):
    """Base class for booking failures that agents are expected to handle."""

    code = "booking_error"
    hint = None


class RoomTypeNotFound(BookingError):
    code = "not_found"
    hint = "Check the room type id against hotels_get_hotel or hotels_search_availability."


class ZoneNotFound(BookingError):
    code = "not_found"
    hint = "Use an Aira zone code, zone name, or postal code -- see hotels_list_hotels for zones."


class InvalidDates(BookingError):
    code = "invalid_dates"
    hint = f"Use YYYY-MM-DD dates, check_in today or later, 1 to {MAX_NIGHTS} nights."


class InvalidGuests(BookingError):
    code = "invalid_guests"
    hint = "guests must be a whole number, 1 or more."


class TooManyGuests(BookingError):
    code = "too_many_guests"
    hint = "Choose a larger room type, or book more than one room."


class SoldOut(BookingError):
    code = "sold_out"
    hint = "Search again for other dates, another room type, or another hotel."


class NotFound(BookingError):
    code = "not_found"
    hint = "Check the reference, or call hotels_list_bookings to see what exists."


class NotRefundable(BookingError):
    code = "not_refundable"
    hint = "Do not retry. Tell the guest this hotel's bookings cannot be refunded."


class TooLateToCancel(BookingError):
    code = "too_late_to_cancel"
    hint = "Do not retry. Cancellation is not possible this close to check-in."


class AlreadyCancelled(BookingError):
    code = "already_cancelled"
    hint = "No action needed; the booking is already cancelled."
