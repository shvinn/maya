"""Car rentals, persisted in SQLite.

A rental and the cars out per day survive a restart; ``reset()`` (the
``cars_reset_rentals`` tool) empties them. Booking is a single step, same as
every other domain: search, book, confirmed -- no hold, no deposit, no
payment call.

A rental is charged per started 24-hour period from pickup, the way real
rental desks bill: 10:00 to 10:00 the next day is 1 day, 10:00 to 11:00 the
next day is 2. Rental day *i* is pinned to the calendar date
``pickup.date() + i``, and that date is what inventory and price are
counted against -- the same per-night model hotels use, with days instead
of nights.

Each day fills up on its own as it approaches (other renters), rising
linearly over the ``BACKGROUND_DEMAND_HORIZON_DAYS`` before it and levelling
off at ``BACKGROUND_DEMAND_MAX_OCCUPANCY`` -- cars are booked later than
hotels, hence the shorter horizon. It is an airport desk, so walk-up rentals
have to work: background demand never takes the last
``BACKGROUND_DEMAND_FLOOR_CARS`` of any model, and same-day pickup is always
possible unless real bookings have taken them.

Price per day is the model's ``daily_rate`` surged by up to
``PRICE_SURGE_MAX`` as that day fills, locked in once booked.

Status is computed live -- RESERVED until pickup, PICKED_UP until drop-off,
then RETURNED. CANCELLED is the one stored override. Cancellation is free
any time before pickup and impossible after it, which is how most real
pay-at-the-counter rentals work.
"""

from __future__ import annotations

import json
import math
import random
from datetime import date, datetime, time, timedelta

from maya.world import clock

from . import db, fleet
from .fleet import Car

CURRENCY = "bucks"

MIN_DRIVER_AGE = 21
MAX_RENTAL_DAYS = 30
MAX_DAYS_AHEAD = 365

#: How far out a day starts looking untouched by other renters.
BACKGROUND_DEMAND_HORIZON_DAYS = 14
#: Share of the fleet the background simulation levels off at on the day itself.
BACKGROUND_DEMAND_MAX_OCCUPANCY = 0.8
#: Cars per model the background simulation will never claim, so walk-ups work.
BACKGROUND_DEMAND_FLOOR_CARS = 2

#: Daily surcharge at fully rented-out, on top of the base rate.
PRICE_SURGE_MAX = 0.3

#: Record locators skip letters that look like digits.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def _get_rental(reference: str) -> dict | None:
    row = db.connect().execute(
        "SELECT data FROM car_rentals WHERE reference = ?", (reference,)
    ).fetchone()
    return json.loads(row["data"]) if row else None


def _save_rental(rental: dict) -> None:
    conn = db.connect()
    conn.execute(
        "INSERT OR REPLACE INTO car_rentals (reference, data) VALUES (?, ?)",
        (rental["rental_reference"], json.dumps(rental)),
    )
    conn.commit()


def _all_rentals() -> list[dict]:
    rows = db.connect().execute("SELECT data FROM car_rentals").fetchall()
    return [json.loads(row["data"]) for row in rows]


def _cars_out(car_id: str, day: date) -> int:
    row = db.connect().execute(
        "SELECT count FROM cars_out WHERE car_id = ? AND day = ?",
        (car_id, day.isoformat()),
    ).fetchone()
    return row["count"] if row else 0


def _adjust_cars_out(car_id: str, days: list[date], delta: int) -> None:
    conn = db.connect()
    for day in days:
        new_count = max(0, _cars_out(car_id, day) + delta)
        conn.execute(
            "INSERT OR REPLACE INTO cars_out (car_id, day, count) VALUES (?, ?, ?)",
            (car_id, day.isoformat(), new_count),
        )
    conn.commit()


def _init_counter() -> int:
    """Resume the reference counter from what's already booked, so a
    restart never reissues a locator that's still on disk."""
    row = db.connect().execute("SELECT COUNT(*) AS n FROM car_rentals").fetchone()
    return row["n"] if row else 0


_counter = _init_counter()


def _next_reference() -> str:
    """A six-character locator, same generator as flights but salted
    differently from flights and hotels so references never collide."""
    global _counter
    _counter += 1
    rng = random.Random((_counter * 2654435761 + 0xCA125) % 2**32)
    return "".join(rng.choice(_ALPHABET) for _ in range(6))


def _parse_datetime(value: str, field: str) -> datetime:
    try:
        parsed = datetime.fromisoformat((value or "").strip())
    except ValueError:
        raise InvalidDates(f"{field} {value!r} is not a datetime in 'YYYY-MM-DD HH:MM' form.") from None
    return parsed.replace(second=0, microsecond=0, tzinfo=None)


def parse_rental(pickup_at: str, dropoff_at: str) -> tuple[datetime, datetime, list[date]]:
    """Validate a rental window and return (pickup, dropoff, rental days)."""
    pickup = _parse_datetime(pickup_at, "pickup_at")
    dropoff = _parse_datetime(dropoff_at, "dropoff_at")
    now = clock.now().replace(second=0, microsecond=0)
    if pickup < now:
        raise InvalidDates(f"pickup_at {pickup:%Y-%m-%d %H:%M} is in the past.")
    if dropoff <= pickup:
        raise InvalidDates("dropoff_at must be after pickup_at.")
    day_count = math.ceil((dropoff - pickup) / timedelta(hours=24))
    if day_count > MAX_RENTAL_DAYS:
        raise InvalidDates(f"Rentals are limited to {MAX_RENTAL_DAYS} days; this is {day_count}.")
    if (pickup.date() - now.date()).days > MAX_DAYS_AHEAD:
        raise InvalidDates(f"Rentals open at most {MAX_DAYS_AHEAD} days ahead.")
    days = [pickup.date() + timedelta(days=i) for i in range(day_count)]
    return pickup, dropoff, days


def _background_out(car: Car, day: date) -> int:
    """Cars other renters have taken, purely a function of time to the day."""
    days_left = (datetime.combine(day, time(12, 0)) - clock.now()) / timedelta(days=1)
    ramp = max(0.0, min(1.0, 1 - days_left / BACKGROUND_DEMAND_HORIZON_DAYS))
    out = round(car.fleet_size * ramp * BACKGROUND_DEMAND_MAX_OCCUPANCY)
    return max(0, min(out, car.fleet_size - BACKGROUND_DEMAND_FLOOR_CARS))


def cars_available(car: Car, day: date) -> int:
    taken = _background_out(car, day) + _cars_out(car.car_id, day)
    return max(0, car.fleet_size - taken)


def price_for_day(car: Car, day: date) -> int:
    """One rental day's rate: base rate, surcharged by how busy that day is."""
    load_factor = 1 - cars_available(car, day) / car.fleet_size
    return round(car.daily_rate * (1 + PRICE_SURGE_MAX * load_factor))


def quote(car: Car, days: list[date]) -> dict:
    """Live price and availability for one model across a rental."""
    daily = [{"day": d.isoformat(), "price": price_for_day(car, d)} for d in days]
    return {
        "cars_left": min(cars_available(car, d) for d in days),
        "daily_prices": daily,
        "total": sum(d["price"] for d in daily),
    }


def search(
    pickup_at: str,
    dropoff_at: str,
    min_seats: int | None = None,
    car_class: str | None = None,
    max_total_price: int | None = None,
    limit: int = 10,
) -> list[dict]:
    """Models with a car free on every day of the rental, cheapest total first."""
    pickup, dropoff, days = parse_rental(pickup_at, dropoff_at)
    results = []
    for car in fleet.CARS:
        if min_seats and car.seats < min_seats:
            continue
        if car_class and car.car_class != car_class.strip().lower():
            continue
        q = quote(car, days)
        if q["cars_left"] == 0:
            continue
        if max_total_price is not None and q["total"] > max_total_price:
            continue
        results.append({
            **car.to_dict(),
            "pickup_at": f"{pickup:%Y-%m-%d %H:%M}",
            "dropoff_at": f"{dropoff:%Y-%m-%d %H:%M}",
            "rental_days": len(days),
            "cars_left": q["cars_left"],
            "daily_prices": q["daily_prices"],
            "total_price": {"amount": q["total"], "currency": CURRENCY},
        })
    results.sort(key=lambda r: (r["total_price"]["amount"], r["car_id"]))
    return results[:limit]


def _status(rental: dict) -> str:
    """Rental status, computed live from the clock -- CANCELLED is the only
    value that's actually stored, as a terminal override."""
    if rental["cancelled"]:
        return "CANCELLED"
    now = clock.now()
    if now < datetime.fromisoformat(rental["pickup_at"]):
        return "RESERVED"
    if now < datetime.fromisoformat(rental["dropoff_at"]):
        return "PICKED_UP"
    return "RETURNED"


def _with_live_status(rental: dict) -> dict:
    out = dict(rental)
    out["status"] = _status(rental)
    out.pop("cancelled", None)
    return out


def book(
    car_id: str,
    pickup_at: str,
    dropoff_at: str,
    driver_name: str,
    driver_age: int,
    contact_email: str,
) -> dict:
    """Reserve one car. Raises CarNotFound, InvalidDates, DriverTooYoung or
    SoldOut."""
    car = fleet.get(car_id)
    if car is None:
        raise CarNotFound(f"No car with id {car_id!r}.")
    pickup, dropoff, days = parse_rental(pickup_at, dropoff_at)
    if driver_age < MIN_DRIVER_AGE:
        raise DriverTooYoung(
            f"Drivers must be at least {MIN_DRIVER_AGE} to rent; driver is {driver_age}."
        )
    for day in days:
        if cars_available(car, day) < 1:
            raise SoldOut(f"No {car.make} {car.model} is free on {day.isoformat()}.")

    q = quote(car, days)
    _adjust_cars_out(car.car_id, days, 1)
    rental = {
        "rental_reference": _next_reference(),
        "cancelled": False,
        "car_id": car.car_id,
        "car": car.label(),
        "car_class": car.car_class,
        "seats": car.seats,
        "transmission": car.transmission,
        "location": fleet.LOCATION["name"],
        "pickup_at": f"{pickup:%Y-%m-%d %H:%M}",
        "dropoff_at": f"{dropoff:%Y-%m-%d %H:%M}",
        "rental_days": len(days),
        "driver_name": driver_name,
        "driver_age": driver_age,
        "contact_email": contact_email,
        "daily_prices": q["daily_prices"],
        "total_price": {"amount": q["total"], "currency": CURRENCY},
        "cancellation_policy": "Free cancellation any time before pickup; not possible after.",
        "booked_at": clock.now().strftime("%Y-%m-%d %H:%M"),
    }
    _save_rental(rental)
    return _with_live_status(rental)


def get(reference: str) -> dict | None:
    rental = _get_rental((reference or "").strip().upper())
    return _with_live_status(rental) if rental else None


def list_all(email: str | None = None, status: str | None = None) -> list[dict]:
    out = [_with_live_status(r) for r in _all_rentals()]
    if email:
        needle = email.strip().lower()
        out = [r for r in out if r["contact_email"].lower() == needle]
    if status:
        wanted = status.strip().upper()
        out = [r for r in out if r["status"] == wanted]
    return sorted(out, key=lambda r: (r["pickup_at"], r["rental_reference"]))


def cancel(reference: str) -> dict:
    """Cancel a rental: free any time before pickup, impossible after."""
    rental = _get_rental((reference or "").strip().upper())
    if rental is None:
        raise NotFound(f"No rental with reference {reference!r}.")
    current_status = _status(rental)
    if current_status == "CANCELLED":
        raise AlreadyCancelled(f"Rental {rental['rental_reference']} is already cancelled.")
    if current_status != "RESERVED":
        raise TooLateToCancel(
            f"Rental {rental['rental_reference']} is already "
            f"{current_status.replace('_', ' ').lower()}; cancellation is only "
            "possible before pickup."
        )

    rental["cancelled"] = True
    start = datetime.fromisoformat(rental["pickup_at"]).date()
    days = [start + timedelta(days=i) for i in range(rental["rental_days"])]
    _adjust_cars_out(rental["car_id"], days, -1)
    refund = {"amount": rental["total_price"]["amount"], "currency": CURRENCY}
    rental["refund"] = refund
    _save_rental(rental)
    return {
        "rental_reference": rental["rental_reference"],
        "status": "CANCELLED",
        "refund": refund,
        "reason": "Cancelled before pickup: full refund.",
    }


def reset() -> int:
    """Empty all rentals and cars out. The ``cars_reset_rentals`` tool.

    Returns the number of rentals cleared.
    """
    global _counter
    cleared = db.reset_world()
    _counter = 0
    return cleared


class RentalError(Exception):
    """Base class for rental failures that agents are expected to handle."""

    code = "rental_error"
    hint = None


class CarNotFound(RentalError):
    code = "not_found"
    hint = "Check the car id against cars_list_cars or cars_search_availability."


class InvalidDates(RentalError):
    code = "invalid_dates"
    hint = (
        f"Use 'YYYY-MM-DD HH:MM', pickup now or later, drop-off after pickup, "
        f"at most {MAX_RENTAL_DAYS} days."
    )


class DriverTooYoung(RentalError):
    code = "driver_too_young"
    hint = f"Do not retry with the same driver. The minimum age is {MIN_DRIVER_AGE}."


class SoldOut(RentalError):
    code = "sold_out"
    hint = "Search again for other times or another car."


class NotFound(RentalError):
    code = "not_found"
    hint = "Check the reference, or call cars_list_rentals to see what exists."


class TooLateToCancel(RentalError):
    code = "too_late_to_cancel"
    hint = "Do not retry. A rental can't be cancelled once the car has been picked up."


class AlreadyCancelled(RentalError):
    code = "already_cancelled"
    hint = "No action needed; the rental is already cancelled."
