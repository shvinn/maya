"""Table reservations, persisted in SQLite.

A reservation and the covers booked per slot survive a restart; ``reset()``
empties them, as part of a world reset (see maya.reset). Booking is a single
step, like every other domain: check availability, book, confirmed.

Other diners book on their own as a slot approaches, the same way other
buyers take event tickets: ``covers x popularity x demand x ramp``, where
the ramp rises linearly over the ``BACKGROUND_DEMAND_HORIZON_DAYS`` before
the slot, and ``demand`` is ``PEAK_DEMAND`` for dinner-rush slots and
``OFF_PEAK_DEMAND`` otherwise. So the hardest table in town is fully booked
at 19:30 days ahead while 17:30 still has room on the night. Real
reservations take covers on top.

The booking rules: party size from 1 to the restaurant's ``max_party``; the
date must be a day it's open, within ``BOOKING_WINDOW_DAYS``; the time must
be one of its half-hour slots, still in the future. Restaurants with a
deposit -- fine dining -- *hold* ``deposit_per_guest x party_size`` of the
booking resident's money: still theirs, but not available to spend, until
the sitting ends, when the hold lapses by itself (they came; nothing is
spent). Cancelling at least ``cancel_cutoff_hours`` ahead releases the hold;
cancelling later keeps the deposit -- the hold becomes a charge -- but
still frees the table. Everywhere else a table costs nothing. Not enough
available money for the hold is ``insufficient_funds``. A resident only
sees and cancels their own reservations; anyone else's is not found.

Status is computed live -- CONFIRMED until the booking time, SEATED for
the sitting, then COMPLETED. CANCELLED is the one stored override.
"""

from __future__ import annotations

import json
import sqlite3
import random
from datetime import date, datetime, time, timedelta

from maya import storage
from maya.simulations.residents import residents
from maya.world import clock
from maya.world.geography import maps

from . import db, restaurants
from .restaurants import CURRENCY, DiningRestaurant

BOOKING_WINDOW_DAYS = 30

#: How far out a slot starts looking untouched by other diners.
BACKGROUND_DEMAND_HORIZON_DAYS = 14

#: Dinner rush, inclusive: these seating times fill fastest.
PEAK_START, PEAK_END = time(18, 30), time(20, 30)
PEAK_DEMAND = 1.3
OFF_PEAK_DEMAND = 0.7

#: Record locators skip letters that look like digits.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# --- storage ------------------------------------------------------------------


def _get_reservation(reference: str) -> dict | None:
    row = db.connect().execute(
        "SELECT data FROM dining_reservations WHERE reference = ?", (reference,)
    ).fetchone()
    return json.loads(row["data"]) if row else None


def _save_reservation(reservation: dict) -> None:
    db.connect().execute(
        "INSERT OR REPLACE INTO dining_reservations (reference, data) VALUES (?, ?)",
        (reservation["reference"], json.dumps(reservation)),
    )


def _all_reservations() -> list[dict]:
    return [json.loads(r["data"]) for r in db.connect().execute("SELECT data FROM dining_reservations").fetchall()]


def _covers_booked(code: str, slot: datetime) -> int:
    row = db.connect().execute(
        "SELECT count FROM covers_booked WHERE restaurant = ? AND date = ? AND time = ?",
        (code, slot.date().isoformat(), f"{slot:%H:%M}"),
    ).fetchone()
    return row["count"] if row else 0


def _adjust_covers_booked(code: str, slot: datetime, delta: int) -> None:
    db.connect().execute(
        "INSERT OR REPLACE INTO covers_booked (restaurant, date, time, count) VALUES (?, ?, ?, ?)",
        (code, slot.date().isoformat(), f"{slot:%H:%M}", max(0, _covers_booked(code, slot) + delta)),
    )


def _next_reference() -> str:
    """A six-character locator, same generator as flights but salted
    differently from every other domain so references never collide.

    Numbered from the reservations already stored, read inside the booking's
    transaction -- never from an in-memory counter, which two processes
    sharing the database would both hand out.
    """
    n = db.connect().execute("SELECT COUNT(*) AS n FROM dining_reservations").fetchone()["n"] + 1
    rng = random.Random((n * 2654435761 + 0xD1E7) % 2**32)
    return "".join(rng.choice(_ALPHABET) for _ in range(6))


# --- availability -------------------------------------------------------------


def _background_booked(restaurant: DiningRestaurant, slot: datetime) -> int:
    """Covers other diners have booked, purely a function of time to the slot."""
    days_left = (slot - clock.now()) / timedelta(days=1)
    ramp = max(0.0, min(1.0, 1 - days_left / BACKGROUND_DEMAND_HORIZON_DAYS))
    demand = PEAK_DEMAND if PEAK_START <= slot.time() <= PEAK_END else OFF_PEAK_DEMAND
    covers = restaurant.covers_per_slot
    return min(covers, round(covers * restaurant.popularity * demand * ramp))


def seats_left(restaurant: DiningRestaurant, slot: datetime) -> int:
    taken = _background_booked(restaurant, slot) + _covers_booked(restaurant.code, slot)
    return max(0, restaurant.covers_per_slot - taken)


# --- lookups and validation ---------------------------------------------------


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        raise InvalidDate(f"date {value!r} is not a date in YYYY-MM-DD form.") from None


def _find_restaurant(code: str) -> DiningRestaurant:
    restaurant = restaurants.get(code)
    if restaurant is None:
        raise RestaurantNotFound(f"No dine-in restaurant with code {code!r}.")
    return restaurant


def _bookable_day(restaurant: DiningRestaurant, day_text: str) -> date:
    """A date the restaurant takes bookings for. Raises InvalidDate or Closed."""
    day = _parse_date(day_text)
    today = clock.now().date()
    if day < today:
        raise InvalidDate(f"{day.isoformat()} is in the past.")
    if (day - today).days > BOOKING_WINDOW_DAYS:
        raise InvalidDate(f"Tables can be booked at most {BOOKING_WINDOW_DAYS} days ahead.")
    if not restaurant.open_on(day):
        raise Closed(
            f"{restaurant.name} is closed on {day.strftime('%A')}s "
            f"(closed: {', '.join(restaurant.closed_days)})."
        )
    return day


def search_restaurants(
    zone: str | None = None, cuisine: str | None = None, query: str | None = None, limit: int = 20
) -> list[dict]:
    zone_code = None
    if zone:
        found = maps.resolve_zone(zone)
        if found is None:
            raise ZoneNotFound(f"{zone!r} is not a known zone.")
        zone_code = found.code
    return [r.to_dict() for r in restaurants.search(zone_code, cuisine, query, limit)]


def restaurant_details(code: str) -> dict:
    restaurant = _find_restaurant(code)
    return {**restaurant.to_dict(), "menu": restaurants.menu(restaurant.code)}


def availability(code: str, day_text: str, party_size: int = 2) -> dict:
    """Every seating time on a day with the seats left. Started slots are
    left out; slots too full for ``party_size`` say ``available: false``."""
    restaurant = _find_restaurant(code)
    day = _bookable_day(restaurant, day_text)
    now = clock.now()
    slots = []
    for slot in restaurant.slots(day):
        if slot <= now:
            continue
        left = seats_left(restaurant, slot)
        slots.append({"time": f"{slot:%H:%M}", "seats_left": left, "available": left >= party_size})
    return {
        "restaurant_code": restaurant.code,
        "restaurant_name": restaurant.name,
        "date": day.isoformat(),
        "party_size": party_size,
        "fully_booked": not any(s["available"] for s in slots),
        "slots": slots,
    }


# --- booking lifecycle --------------------------------------------------------


def _status(reservation: dict) -> str:
    """Reservation status, computed live from the clock -- CANCELLED is the
    only value that's actually stored, as a terminal override."""
    if reservation["cancelled"]:
        return "CANCELLED"
    now = clock.now()
    if now < datetime.fromisoformat(reservation["start"]):
        return "CONFIRMED"
    if now < datetime.fromisoformat(reservation["end"]):
        return "SEATED"
    return "COMPLETED"


def _with_live_status(reservation: dict) -> dict:
    out = dict(reservation)
    out["status"] = _status(reservation)
    out.pop("cancelled", None)
    return out


def _book(
    code: str, day_text: str, time_text: str, party_size: int, guest_name: str, contact_phone: str, resident_id: str
) -> dict:
    """Reserve a table for ``resident_id``, holding the deposit if there is
    one. Raises RestaurantNotFound, InvalidDate, Closed, InvalidTime,
    InvalidPartySize, PartyTooLarge, FullyBooked or
    residents.InsufficientFunds."""
    restaurant = _find_restaurant(code)
    day = _bookable_day(restaurant, day_text)
    try:
        at = time.fromisoformat((time_text or "").strip())
    except ValueError:
        raise InvalidTime(f"time {time_text!r} is not a time in HH:MM form.") from None
    slot = datetime.combine(day, at)
    if slot not in restaurant.slots(day):
        raise InvalidTime(
            f"{restaurant.name} seats every {restaurants.SLOT_MINUTES} minutes from "
            f"{restaurant.opens:%H:%M} to {restaurant.last_seating():%H:%M}; {at:%H:%M} isn't one of them."
        )
    now = clock.now()
    if slot <= now:
        raise InvalidTime(f"{slot:%Y-%m-%d %H:%M} has already passed.")
    if party_size < 1:
        raise InvalidPartySize(f"party_size must be at least 1; got {party_size}.")
    if party_size > restaurant.max_party:
        raise PartyTooLarge(f"{restaurant.name} seats parties of up to {restaurant.max_party}; {party_size} requested.")
    left = seats_left(restaurant, slot)
    if left < party_size:
        raise FullyBooked(
            f"{restaurant.name} has {left} seat(s) left at {slot:%H:%M} on {day.isoformat()}; "
            f"{party_size} requested."
        )

    _adjust_covers_booked(restaurant.code, slot, party_size)
    end = slot + timedelta(minutes=restaurant.sitting_minutes)
    reservation = {
        "reference": (reference := _next_reference()),
        "cancelled": False,
        "restaurant_code": restaurant.code,
        "restaurant_name": restaurant.name,
        "zone": restaurant.zone,
        "zone_name": maps.BY_CODE[restaurant.zone].name,
        "date": day.isoformat(),
        "time": f"{slot:%H:%M}",
        "start": f"{slot:%Y-%m-%d %H:%M}",
        "end": f"{end:%Y-%m-%d %H:%M}",
        "party_size": party_size,
        "guest_name": guest_name,
        "contact_phone": contact_phone,
        "resident_id": resident_id,
        "deposit": {"amount": restaurant.deposit_per_guest * party_size, "currency": CURRENCY},
        "cancellation_policy": restaurant.policy_text(),
        "booked_at": now.strftime("%Y-%m-%d %H:%M"),
    }
    held = residents.hold(
        db.connect(), resident_id, reservation["deposit"]["amount"], reference, "dining",
        until=end.isoformat(timespec="seconds"),
    )
    _save_reservation(reservation)
    return {**_with_live_status(reservation), "payment": residents.payment(held)}


def book(
    code: str, day_text: str, time_text: str, party_size: int, guest_name: str, contact_phone: str, resident_id: str
) -> dict:
    """Book atomically: every check and write below -- the hold included --
    happens in one transaction (see maya.storage.transaction), so
    concurrent bookings can't both take the last seats, or both set aside
    the same money."""
    residents.ready()
    with storage.transaction(db.connect()):
        return _book(code, day_text, time_text, party_size, guest_name, contact_phone, resident_id)


def _owned(reference: str, resident_id: str) -> dict | None:
    """The reservation, if ``resident_id`` owns it. Someone else's reservation is
    None, exactly like one that doesn't exist, so a reference never
    confirms that another resident's reservation is there."""
    reservation = _get_reservation((reference or "").strip().upper())
    return reservation if reservation and reservation.get("resident_id") == resident_id else None


def get(reference: str, resident_id: str) -> dict | None:
    reservation = _owned(reference, resident_id)
    return _with_live_status(reservation) if reservation else None


def list_all(resident_id: str, phone: str | None = None, status: str | None = None) -> list[dict]:
    out = [_with_live_status(r) for r in _all_reservations() if r.get("resident_id") == resident_id]
    if phone:
        needle = phone.strip()
        out = [r for r in out if r["contact_phone"] == needle]
    if status:
        wanted = status.strip().upper()
        out = [r for r in out if r["status"] == wanted]
    return sorted(out, key=lambda r: (r["start"], r["reference"]))


def _cancel(reference: str, resident_id: str) -> dict:
    """Cancel before the booking time. The table is freed either way; the
    deposit's hold is released if cancelled at least cancel_cutoff_hours
    ahead, and becomes a charge -- the deposit kept -- if later."""
    reservation = _owned(reference, resident_id)
    if reservation is None:
        raise NotFound(f"No reservation with reference {reference!r}.")
    if reservation["cancelled"]:
        raise AlreadyCancelled(f"Reservation {reservation['reference']} is already cancelled.")
    start = datetime.fromisoformat(reservation["start"])
    hours_left = (start - clock.now()) / timedelta(hours=1)
    if hours_left <= 0:
        raise TooLateToCancel(f"Reservation {reservation['reference']} was for {reservation['start']}; it can't be cancelled now.")

    restaurant = restaurants.get(reservation["restaurant_code"])
    cutoff = restaurant.cancel_cutoff_hours if restaurant else 0
    deposit = reservation["deposit"]["amount"]
    # The deposit was only ever held, never charged: in time it is released,
    # late it is kept. Nothing is refunded either way -- there is nothing to
    # give back that was taken.
    released = deposit if hours_left >= cutoff else 0
    kept = deposit - released

    reservation["cancelled"] = True
    reservation["refund"] = {"amount": 0, "currency": CURRENCY}
    _adjust_covers_booked(reservation["restaurant_code"], start, -reservation["party_size"])
    # The owner's hold ends either way; a deposit kept is then charged. The
    # release comes first, so the charge is covered by the money it freed.
    owner, conn = reservation["resident_id"], db.connect()
    moved = residents.release(conn, owner, reservation["reference"], "dining")
    if kept:
        moved = residents.charge(conn, owner, kept, reservation["reference"], "dining")
    _save_reservation(reservation)
    if not deposit:
        reason = "Cancelled; no deposit was held."
    elif released:
        reason = f"Cancelled at least {cutoff} hours ahead: the deposit's hold is released; nothing was charged."
    else:
        reason = f"Cancelled less than {cutoff} hours ahead: the deposit is kept, and charged."
    return {
        "reference": reservation["reference"],
        "status": "CANCELLED",
        "refund": reservation["refund"],
        "deposit_released": {"amount": released, "currency": CURRENCY},
        "deposit_kept": {"amount": kept, "currency": CURRENCY},
        "payment": residents.payment(moved),
        "reason": reason,
    }


def cancel(reference: str, resident_id: str) -> dict:
    """Cancel atomically: every check and write below -- releasing or
    keeping the deposit included -- happens in one transaction (see
    maya.storage.transaction), so two requests can't cancel the same
    reservation twice, free its seats twice or settle its deposit twice."""
    residents.ready()
    with storage.transaction(db.connect()):
        return _cancel(reference, resident_id)


def reset(conn: sqlite3.Connection) -> int:
    """Empty all reservations and covers booked. Part of a world reset (see
    maya.reset).

    Returns the number of reservations cleared.
    """
    return db.reset_world(conn)


# --- errors -------------------------------------------------------------------


class ReservationError(Exception):
    """Base class for reservation failures that agents are expected to handle."""

    code = "reservation_error"
    hint = None


class RestaurantNotFound(ReservationError):
    code = "not_found"
    hint = "Check the code against dining_search_restaurants. Delivery-only kitchens have no tables."


class ZoneNotFound(ReservationError):
    code = "not_found"
    hint = "Use an Aira zone code, zone name, or postal code."


class InvalidDate(ReservationError):
    code = "invalid_date"
    hint = f"Use a YYYY-MM-DD date, today or later, at most {BOOKING_WINDOW_DAYS} days ahead."


class Closed(ReservationError):
    code = "closed"
    hint = "Pick a day the restaurant is open (see closed_days), or another restaurant."


class InvalidTime(ReservationError):
    code = "invalid_time"
    hint = "Use a time listed by dining_check_availability for that date."


class InvalidPartySize(ReservationError):
    code = "invalid_party_size"
    hint = "party_size must be a whole number, 1 or more."


class PartyTooLarge(ReservationError):
    code = "party_too_large"
    hint = "Choose a restaurant whose max_party fits the group. Do not split the party without asking."


class FullyBooked(ReservationError):
    code = "fully_booked"
    hint = "Try another time with dining_check_availability, another date, or another restaurant."


class NotFound(ReservationError):
    code = "not_found"
    hint = "Check the reference, or call dining_list_reservations."


class TooLateToCancel(ReservationError):
    code = "too_late_to_cancel"
    hint = "Do not retry. The booking time has passed."


class AlreadyCancelled(ReservationError):
    code = "already_cancelled"
    hint = "No action needed; the reservation is already cancelled."
