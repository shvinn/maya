"""Bookings, persisted in SQLite.

A booking and the seats sold per flight-date survive a restart, unlike the
reference data in ``schedule.py`` and ``airports.py`` -- this is the one part
of the world that is actually state rather than a fact re-derived from a CSV
file. ``reset()`` is the explicit way to empty it back out, as part of a
world reset (see maya.reset), which is also what tests use between runs.

Booking is a single step. There is no held-then-paid flow and no priced offer to
expire: you search, you book, you are confirmed. A booking holds one flight, or
two for a one-stop connection -- one reference, one fare (the sum of both legs),
cancelled as a whole. Two rules bite, because a
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

When the airline lets a traveller down, the fare rules give way: a booking
with a cancelled flight can be cancelled for a full refund at any time, and
one facing a missed connection or a delay of at least ``LONG_DELAY_MINUTES``
until its first flight leaves -- Puffin Air included, and inside 24 hours. Each leg carries its live flight
status, and the booking a ``disruption`` (see operations.py).

Fares are computed, not stored: an airline's ``rate_per_minute`` (its price
character -- Puffin cheap, Aira Express dear) times the flight's duration
(the distance stand-in), nudged up by up to ``PRICE_SURGE_MAX`` as the flight
fills. Prices are always whole coins.

A connection's fare is the sum of its legs' fares, less
``CONNECTION_DISCOUNT`` when both legs are on the same airline -- an airline
prices its own connections to compete with a rival's direct flight, while
a connection across two airlines (an interline) gets no such deal.

Every booking belongs to the resident who made it (``resident_id``), and is
paid for from their wallet in the same transaction that takes the seats: no
money, no booking (``insufficient_funds``). Only that resident can see or
cancel it -- anyone else is told there is no such booking -- and a refund
goes back to them. Bookings stored before residents existed have no owner,
so no one can reach them; a world reset clears them.
"""

from __future__ import annotations

import json
import random
from datetime import datetime, timedelta

from maya import storage
from maya.simulations.residents import residents
from maya.world import clock

from . import db, operations, schedule
from .schedule import AIRCRAFT, AIRLINES, CURRENCY, ScheduledFlight

#: Record locators skip letters that look like digits.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

REFUND_CUTOFF_HOURS = 24
NON_REFUNDABLE_AIRLINES = ("PF",)

#: A delay this long lets the traveller cancel for a full refund.
LONG_DELAY_MINUTES = 180

#: How far out a flight starts looking untouched by other travellers.
BACKGROUND_DEMAND_HORIZON_DAYS = 45
#: Seats the background simulation alone will never claim.
BACKGROUND_DEMAND_FLOOR_SEATS = 2
#: ...until departure is this close. Real bookings can still fill them.
BACKGROUND_DEMAND_FLOOR_HOURS = 2

#: Fare surcharge at fully sold-down, on top of the base rate. Kept modest --
#: a full flight should cost more, not wildly more.
PRICE_SURGE_MAX = 0.3

#: Off the summed leg fares, for a connection on a single airline.
CONNECTION_DISCOUNT = 0.2

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


def _next_reference() -> str:
    """A six-character locator. Looks random, is reproducible across runs.

    Numbered from the bookings already stored, read inside the booking's
    transaction -- never from an in-memory counter, which two processes
    sharing the database would both hand out (and the second booking would
    overwrite the first).
    """
    n = db.connect().execute("SELECT COUNT(*) AS n FROM bookings").fetchone()["n"] + 1
    rng = random.Random(n * 2654435761 % 2**32)
    return "".join(rng.choice(_ALPHABET) for _ in range(6))


def _background_sold(flight: ScheduledFlight) -> int:
    """Seats other travellers have taken, purely a function of time to departure."""
    hours_left = (flight.departure - clock.now()) / timedelta(hours=1)
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


def itinerary_fare(legs: list[ScheduledFlight]) -> tuple[int, int]:
    """(fare per passenger, discount per passenger) for a direct flight or
    a connection."""
    summed = sum(price_per_passenger(flight) for flight in legs)
    single_airline = len({flight.pattern.airline for flight in legs}) == 1
    if len(legs) < 2 or not single_airline:
        return summed, 0
    fare = round(summed * (1 - CONNECTION_DISCOUNT))
    return fare, summed - fare


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


def _leg_record(flight: ScheduledFlight, fare: int) -> dict:
    pattern = flight.pattern
    return {
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
        "price_per_passenger": {"amount": fare, "currency": CURRENCY},
    }


def _legs(booking: dict) -> list[dict]:
    """A booking's flights. Bookings made before connections existed were
    flat, single-flight records with no ``legs`` list."""
    return booking.get("legs") or [booking]


def _book(legs: list[ScheduledFlight], passenger_names: list[str], contact_email: str, resident_id: str) -> dict:
    """Confirm a booking on one flight, or two for a one-stop connection
    (the caller has already checked the pair is a valid connection), and
    charge ``resident_id`` for it. Raises ``SoldOut`` if any leg doesn't
    have the seats, or ``residents.InsufficientFunds`` if the wallet can't
    cover it -- and then nothing is booked, not even the leg that did."""
    count = len(passenger_names)
    for flight in legs:
        if seats_available(flight) < count:
            raise SoldOut(
                f"{flight.flight_number} on {flight.date.isoformat()} has "
                f"{seats_available(flight)} seat(s) left; {count} requested."
            )

    records = [_leg_record(flight, price_per_passenger(flight)) for flight in legs]
    fare, discount = itinerary_fare(legs)
    for flight in legs:
        _adjust_seats_sold(flight.key, count)
    first, last = records[0], records[-1]
    booking = {
        "booking_reference": (reference := _next_reference()),
        "reference": reference,
        "status": "CONFIRMED",
        "stops": len(records) - 1,
        **({k: v for k, v in first.items() if k != "price_per_passenger"} if len(records) == 1 else {}),
        "origin": first["origin"],
        "destination": last["destination"],
        "date": first["date"],
        "departure": first["departure"],
        "arrival": last["arrival"],
        "legs": records,
        "passengers": list(passenger_names),
        "passenger_count": count,
        "price_per_passenger": {"amount": fare, "currency": CURRENCY},
        **({"connection_discount_per_passenger": {"amount": discount, "currency": CURRENCY}} if discount else {}),
        "total_price": {"amount": fare * count, "currency": CURRENCY},
        "contact_email": contact_email,
        "resident_id": resident_id,
        "booked_at": clock.now().strftime("%Y-%m-%d %H:%M"),
    }
    paid = residents.charge(db.connect(), resident_id, booking["total_price"]["amount"], reference, "flights")
    _save_booking(booking)
    return {**booking, "payment": residents.payment(paid)}

def book(legs: list[ScheduledFlight], passenger_names: list[str], contact_email: str, resident_id: str) -> dict:
    """Book atomically: every check and write below -- the payment included
    -- happens in one transaction (see maya.storage.transaction), so
    concurrent bookings can't both take the last of anything, or both spend
    the same money."""
    residents.ready()
    with storage.transaction(db.connect()):
        return _book(legs, passenger_names, contact_email, resident_id)


def _flight(leg: dict) -> ScheduledFlight | None:
    return schedule.find(leg["flight_number"], datetime.strptime(leg["date"], "%Y-%m-%d").date())


def disruption(booking: dict) -> str | None:
    """FLIGHT_CANCELLED, MISSED_CONNECTION, LONG_DELAY or DELAYED -- the
    worst that's known now about a booking's flights -- or None."""
    flights = [_flight(leg) for leg in _legs(booking)]
    if any(f is None for f in flights):
        return None
    statuses = [operations.status(f) for f in flights]
    if any(s.cancelled for s in statuses):
        return "FLIGHT_CANCELLED"
    if len(flights) == 2 and operations.misconnects(*flights):
        return "MISSED_CONNECTION"
    worst = max(s.delay_minutes for s in statuses)
    if worst >= LONG_DELAY_MINUTES:
        return "LONG_DELAY"
    return "DELAYED" if worst else None


def _public(booking: dict) -> dict:
    """A stored booking as returned to callers: each leg with its live
    flight status, the booking's ``disruption``, and the shared
    ``reference`` key filled in for records stored before it existed."""
    booking.setdefault("reference", booking["booking_reference"])
    for leg in booking.get("legs") or []:
        flight = _flight(leg)
        if flight is not None:
            leg.update(operations.status(flight).to_dict())
    booking["disruption"] = None if booking["status"] == "CANCELLED" else disruption(booking)
    return booking


def _owned(reference: str, resident_id: str) -> dict | None:
    """The booking, if ``resident_id`` owns it. Someone else's booking is
    None, exactly like one that doesn't exist, so a reference never
    confirms that another resident's booking is there."""
    booking = _get_booking((reference or "").strip().upper())
    return booking if booking and booking.get("resident_id") == resident_id else None


def get(reference: str, resident_id: str) -> dict | None:
    booking = _owned(reference, resident_id)
    return _public(booking) if booking else None


def list_all(resident_id: str, email: str | None = None, status: str | None = None) -> list[dict]:
    out = [_public(b) for b in _all_bookings() if b.get("resident_id") == resident_id]
    if email:
        needle = email.strip().lower()
        out = [b for b in out if b["contact_email"].lower() == needle]
    if status:
        wanted = status.strip().upper()
        out = [b for b in out if b["status"] == wanted]
    return sorted(out, key=lambda b: (b["date"], b["departure"]))


def _cancel(reference: str, resident_id: str) -> dict:
    """Cancel a booking. Puffin Air fares can never be cancelled. Everyone
    else can cancel for a full refund any time up to 24 hours before
    departure; inside that window cancellation isn't possible at all. A
    cancelled flight, missed connection or long delay overrides both rules."""
    booking = _owned(reference, resident_id)
    if booking is None:
        raise NotFound(f"No booking with reference {reference!r}.")
    if booking["status"] == "CANCELLED":
        raise AlreadyCancelled(f"Booking {booking['booking_reference']} is already cancelled.")

    disrupted = disruption(booking)
    first = _flight(_legs(booking)[0])
    if disrupted in ("MISSED_CONNECTION", "LONG_DELAY") and first and operations.status(first).status in ("DEPARTED", "LANDED"):
        disrupted = None  # the traveller flew; a delay no longer refunds
    if disrupted in ("FLIGHT_CANCELLED", "MISSED_CONNECTION", "LONG_DELAY"):
        return _release(booking, {
            "FLIGHT_CANCELLED": "The airline cancelled a flight on this booking: full refund.",
            "MISSED_CONNECTION": "A delay means the connection can't be made: full refund.",
            "LONG_DELAY": f"A delay of {LONG_DELAY_MINUTES // 60} hours or more: full refund.",
        }[disrupted])

    for leg in _legs(booking):
        if not is_refundable(leg["airline"]):
            raise NotRefundable(policy_text(leg["airline"]))

    departure = datetime.strptime(booking["departure"], "%Y-%m-%d %H:%M")
    hours_left = (departure - clock.now()) / timedelta(hours=1)
    if hours_left < REFUND_CUTOFF_HOURS:
        raise TooLateToCancel(
            f"Booking {booking['booking_reference']} departs in "
            f"{max(hours_left, 0):.1f} hour(s); cancellation is only possible "
            f"more than {REFUND_CUTOFF_HOURS} hours before departure."
        )
    return _release(booking, f"Cancelled more than {REFUND_CUTOFF_HOURS} hours before departure: full refund.")


def _release(booking: dict, reason: str) -> dict:
    """Mark a booking cancelled, give its seats back and refund it in full."""
    booking["status"] = "CANCELLED"
    for leg in _legs(booking):
        _adjust_seats_sold(f"{leg['flight_number']}/{leg['date']}", -booking["passenger_count"])

    amount = booking["total_price"]["amount"]
    booking["refund"] = {"amount": amount, "currency": CURRENCY}
    # To the resident who paid -- the booking's owner -- whoever is asking.
    refunded = residents.refund(db.connect(), booking["resident_id"], amount, booking["booking_reference"], "flights")
    _save_booking(booking)
    return {
        "booking_reference": booking["booking_reference"],
        "reference": booking["booking_reference"],
        "status": "CANCELLED",
        "refund": {"amount": amount, "currency": CURRENCY},
        "payment": residents.payment(refunded),
        "reason": reason,
    }

def cancel(reference: str, resident_id: str) -> dict:
    """Cancel atomically: every check and write below -- the refund
    included -- happens in one transaction (see maya.storage.transaction),
    so two requests can't cancel the same booking twice, or release its
    inventory or refund it twice."""
    residents.ready()
    with storage.transaction(db.connect()):
        return _cancel(reference, resident_id)


def reset() -> int:
    """Empty all bookings and seat counts. Part of a world reset (see maya.reset).

    Returns the number of bookings cleared.
    """
    cleared = db.reset_world()
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
