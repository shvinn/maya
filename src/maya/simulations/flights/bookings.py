"""Bookings, persisted in SQLite.

A booking and the seats sold per flight-date survive a restart, unlike the
reference data in ``schedule.py`` and ``airports.py`` -- this is the one part
of the world that is actually state rather than a fact re-derived from a CSV
file. ``reset()`` (the ``flights_reset_bookings`` tool) is the explicit way
to empty it back out, which is also what tests use between runs.

Booking is a single step. There is no held-then-paid flow and no priced offer to
expire: you search, you book, you are confirmed. Two rules bite, because a
domain where nothing can be refused teaches an agent nothing: Puffin Air fares
can never be cancelled, and everyone else can cancel for a full refund any time
up to 24 hours before departure -- inside that window, cancellation isn't
possible at all.

Seats also drain on their own as departure approaches, standing in for other
travellers booking the same flight: availability falls off linearly over the
``BACKGROUND_DEMAND_HORIZON_DAYS`` before departure. That simulated demand
never claims the last ``BACKGROUND_DEMAND_FLOOR_SEATS`` seats until inside
``BACKGROUND_DEMAND_FLOOR_HOURS`` of departure -- real bookings can still sell
those out at any time, this floor only stops the background simulation from
doing it quietly, hours early.

Fares are computed, not stored: an airline's ``rate_per_minute`` (its price
character -- Puffin cheap, Aira Express dear) times the flight's duration
(the distance stand-in), nudged up by up to ``PRICE_SURGE_MAX`` as the flight
fills. Prices are always whole coins.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

from . import db
from .schedule import AIRCRAFT, AIRLINES, CURRENCY, ScheduledFlight

#: Record locators skip letters that look like digits.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

REFUND_CUTOFF_HOURS = 24
NON_REFUNDABLE_AIRLINES = ("PF",)

#: How far out a flight starts looking untouched by other travellers.
BACKGROUND_DEMAND_HORIZON_DAYS = 45
#: Seats the background simulation alone will never claim.
BACKGROUND_DEMAND_FLOOR_SEATS = 2
#: ...until departure is this close. Real bookings can still fill them.
BACKGROUND_DEMAND_FLOOR_HOURS = 2

#: Fare surcharge at fully sold-down, on top of the base rate. Kept modest --
#: a full flight should cost more, not wildly more.
PRICE_SURGE_MAX = 0.3

def _get_booking(reference: str) -> dict | None:
    row = db.connect().execute(
        "SELECT data FROM bookings WHERE reference = ?", (reference,)
    ).fetchone()
    return json.loads(row["data"]) if row else None


def _save_booking(booking: dict) -> None:
    conn = db.connect()
    conn.execute(
        "INSERT OR REPLACE INTO bookings (reference, data) VALUES (?, ?)",
        (booking["booking_reference"], json.dumps(booking)),
    )
    conn.commit()


def _all_bookings() -> list[dict]:
    rows = db.connect().execute("SELECT data FROM bookings").fetchall()
    return [json.loads(row["data"]) for row in rows]


def _seats_sold(flight_key: str) -> int:
    row = db.connect().execute(
        "SELECT count FROM seats_sold WHERE flight_key = ?", (flight_key,)
    ).fetchone()
    return row["count"] if row else 0


def _adjust_seats_sold(flight_key: str, delta: int) -> None:
    conn = db.connect()
    new_count = max(0, _seats_sold(flight_key) + delta)
    conn.execute(
        "INSERT OR REPLACE INTO seats_sold (flight_key, count) VALUES (?, ?)",
        (flight_key, new_count),
    )
    conn.commit()


def _init_counter() -> int:
    """Resume the reference counter from what's already booked, so a
    restart never reissues a locator that's still on disk."""
    row = db.connect().execute("SELECT COUNT(*) AS n FROM bookings").fetchone()
    return row["n"] if row else 0


_counter = _init_counter()


def _next_reference() -> str:
    """A six-character locator. Looks random, is reproducible across runs."""
    global _counter
    _counter += 1
    rng = random.Random(_counter * 2654435761 % 2**32)
    return "".join(rng.choice(_ALPHABET) for _ in range(6))


def _background_sold(flight: ScheduledFlight) -> int:
    """Seats other travellers have taken, purely a function of time to departure."""
    hours_left = (flight.departure - datetime.now()) / timedelta(hours=1)
    days_left = hours_left / 24
    pct_sold = max(0.0, min(1.0, 1 - days_left / BACKGROUND_DEMAND_HORIZON_DAYS))
    sold = round(flight.seats_total * pct_sold)
    if hours_left > BACKGROUND_DEMAND_FLOOR_HOURS:
        sold = min(sold, flight.seats_total - BACKGROUND_DEMAND_FLOOR_SEATS)
    return max(0, sold)


def seats_available(flight: ScheduledFlight) -> int:
    taken = _background_sold(flight) + _seats_sold(flight.key)
    return max(0, flight.seats_total - taken)


def price_per_passenger(flight: ScheduledFlight) -> int:
    """Fare for one seat: airline rate x duration, surcharged by how full it is."""
    rate = AIRLINES[flight.pattern.airline].rate_per_minute
    base = rate * flight.pattern.duration_minutes
    load_factor = 1 - (seats_available(flight) / flight.seats_total)
    return round(base * (1 + PRICE_SURGE_MAX * load_factor))


def is_refundable(airline: str) -> bool:
    return airline not in NON_REFUNDABLE_AIRLINES


def policy_text(airline: str) -> str:
    if not is_refundable(airline):
        return f"{AIRLINES[airline].name} fares are non-refundable. Cancelling forfeits the fare."
    return (
        f"Full refund if cancelled more than {REFUND_CUTOFF_HOURS} hours before "
        f"departure. Cancellation is not possible within {REFUND_CUTOFF_HOURS} hours "
        "of departure."
    )


def book(flight: ScheduledFlight, passenger_names: list[str], contact_email: str) -> dict:
    """Confirm a booking on one flight. Raises ``SoldOut`` if seats have gone."""
    count = len(passenger_names)
    if seats_available(flight) < count:
        raise SoldOut(
            f"{flight.flight_number} on {flight.date.isoformat()} has "
            f"{seats_available(flight)} seat(s) left; {count} requested."
        )

    pattern = flight.pattern
    reference = _next_reference()
    fare = price_per_passenger(flight)
    _adjust_seats_sold(flight.key, count)
    booking = {
        "booking_reference": reference,
        "status": "CONFIRMED",
        "flight_number": pattern.flight_number,
        "airline": pattern.airline,
        "airline_name": AIRLINES[pattern.airline].name,
        "aircraft": AIRCRAFT[pattern.aircraft].name,
        "origin": pattern.origin,
        "destination": pattern.destination,
        "date": flight.date.isoformat(),
        "departure": flight.departure.strftime("%Y-%m-%d %H:%M"),
        "arrival": flight.arrival.strftime("%Y-%m-%d %H:%M"),
        "duration_minutes": pattern.duration_minutes,
        "passengers": list(passenger_names),
        "passenger_count": count,
        "price_per_passenger": {"amount": fare, "currency": CURRENCY},
        "total_price": {"amount": fare * count, "currency": CURRENCY},
        "contact_email": contact_email,
        "booked_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    _save_booking(booking)
    return booking


def get(reference: str) -> dict | None:
    return _get_booking((reference or "").strip().upper())


def list_all(email: str | None = None, status: str | None = None) -> list[dict]:
    out = _all_bookings()
    if email:
        needle = email.strip().lower()
        out = [b for b in out if b["contact_email"].lower() == needle]
    if status:
        wanted = status.strip().upper()
        out = [b for b in out if b["status"] == wanted]
    return sorted(out, key=lambda b: (b["date"], b["departure"]))


def cancel(reference: str) -> dict:
    """Cancel a booking. Puffin Air fares can never be cancelled. Everyone
    else can cancel for a full refund any time up to 24 hours before
    departure; inside that window cancellation isn't possible at all."""
    booking = get(reference)
    if booking is None:
        raise NotFound(f"No booking with reference {reference!r}.")
    if booking["status"] == "CANCELLED":
        raise AlreadyCancelled(f"Booking {booking['booking_reference']} is already cancelled.")

    if not is_refundable(booking["airline"]):
        raise NotRefundable(policy_text(booking["airline"]))

    departure = datetime.strptime(booking["departure"], "%Y-%m-%d %H:%M")
    hours_left = (departure - datetime.now()) / timedelta(hours=1)
    if hours_left < REFUND_CUTOFF_HOURS:
        raise TooLateToCancel(
            f"Booking {booking['booking_reference']} departs in "
            f"{max(hours_left, 0):.1f} hour(s); cancellation is only possible "
            f"more than {REFUND_CUTOFF_HOURS} hours before departure."
        )

    booking["status"] = "CANCELLED"
    key = f"{booking['flight_number']}/{booking['date']}"
    _adjust_seats_sold(key, -booking["passenger_count"])

    amount = booking["total_price"]["amount"]
    booking["refund"] = {"amount": amount, "currency": CURRENCY}
    _save_booking(booking)
    return {
        "booking_reference": booking["booking_reference"],
        "status": "CANCELLED",
        "refund": {"amount": amount, "currency": CURRENCY},
        "reason": f"Cancelled more than {REFUND_CUTOFF_HOURS} hours before departure: full refund.",
    }


def reset() -> int:
    """Empty all bookings and seat counts. The ``flights_reset_bookings`` tool.

    Returns the number of bookings cleared.
    """
    global _counter
    cleared = db.reset_world()
    _counter = 0
    return cleared


class BookingError(Exception):
    """Base class for booking failures that agents are expected to handle."""

    code = "booking_error"
    hint = None


class SoldOut(BookingError):
    code = "sold_out"
    hint = "Search again for another flight on this route or date."


class NotFound(BookingError):
    code = "not_found"
    hint = "Check the reference, or call list_bookings to see what exists."


class NotRefundable(BookingError):
    code = "not_refundable"
    hint = "Do not retry. Tell the traveller the fare cannot be refunded."


class TooLateToCancel(BookingError):
    code = "too_late_to_cancel"
    hint = "Do not retry. Cancellation is not possible this close to departure."


class AlreadyCancelled(BookingError):
    code = "already_cancelled"
    hint = "No action needed; the booking is already cancelled."
