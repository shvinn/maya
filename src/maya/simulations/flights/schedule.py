"""The fixed weekly timetable.

Maya's schedule is a plain table, not a generator. What flies on a given date
is whatever pattern below operates on that day of the week. This is the whole
model, and it has one property worth pointing out: because real timetables have
gaps, "there are no flights that day" is an ordinary answer rather than an edge
case. Haldrick has no Sunday service; Aira Express does not fly at
weekends; Brenzo only sees a Puffin jet from Thursday to Sunday.

Block times were derived once from plausible city distances and then frozen
here. The airlines, aircraft and schedule tables themselves live in
``data/*.csv`` -- edit a row there and the world changes on next run, no
seeds, no regeneration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date, datetime, timedelta
from typing import NamedTuple

from . import db

CURRENCY = "bucks"


class Airline(NamedTuple):
    code: str
    name: str
    style: str
    #: Coins per minute of flight time -- the airline's base price character.
    rate_per_minute: float


def _load_airlines() -> dict[str, Airline]:
    rows = db.connect().execute(
        "SELECT code, name, style, rate_per_minute FROM airlines"
    ).fetchall()
    return {r["code"]: Airline(r["code"], r["name"], r["style"], r["rate_per_minute"]) for r in rows}


AIRLINES: dict[str, Airline] = _load_airlines()


class Aircraft(NamedTuple):
    code: str
    name: str
    seats: int


def _load_aircraft() -> dict[str, Aircraft]:
    rows = db.connect().execute("SELECT code, name, seats FROM aircraft").fetchall()
    return {r["code"]: Aircraft(r["code"], r["name"], r["seats"]) for r in rows}


AIRCRAFT: dict[str, Aircraft] = _load_aircraft()


class F(NamedTuple):
    """One weekly pattern.

    ``days`` is a string of ISO weekday digits, 1 = Monday … 7 = Sunday.
    ``departure`` is a 24-hour clock time. Price is not stored here -- it is
    computed from the airline's rate, ``duration_minutes`` and how full the
    flight already is. See ``bookings.price_per_passenger``.
    """

    flight_number: str
    airline: str
    origin: str
    destination: str
    departure: str
    duration_minutes: int
    aircraft: str
    days: str

    def operates_on(self, day: Date) -> bool:
        return str(day.isoweekday()) in self.days


def _load_schedule() -> tuple[F, ...]:
    rows = db.connect().execute(
        "SELECT flight_number, airline, origin, destination, departure, "
        "duration_minutes, aircraft, days FROM schedule"
    ).fetchall()
    return tuple(
        F(
            r["flight_number"], r["airline"], r["origin"], r["destination"],
            r["departure"], r["duration_minutes"], r["aircraft"], r["days"],
        )
        for r in rows
    )


SCHEDULE: tuple[F, ...] = _load_schedule()

BY_NUMBER: dict[str, F] = {f.flight_number: f for f in SCHEDULE}


@dataclass(frozen=True)
class ScheduledFlight:
    """One pattern on one concrete date, with times resolved."""

    pattern: F
    date: Date
    departure: datetime
    arrival: datetime

    @property
    def flight_number(self) -> str:
        return self.pattern.flight_number

    @property
    def key(self) -> str:
        """Stable identifier for a flight on a date, e.g. ``SA101/2031-03-14``."""
        return f"{self.pattern.flight_number}/{self.date.isoformat()}"

    @property
    def seats_total(self) -> int:
        return AIRCRAFT[self.pattern.aircraft].seats

    def to_dict(self) -> dict:
        pattern = self.pattern
        out = {
            "flight_number": pattern.flight_number,
            "airline": pattern.airline,
            "airline_name": AIRLINES[pattern.airline].name,
            "aircraft": AIRCRAFT[pattern.aircraft].name,
            "origin": pattern.origin,
            "destination": pattern.destination,
            "date": self.date.isoformat(),
            "departure_local": self.departure.strftime("%Y-%m-%d %H:%M"),
            "arrival_local": self.arrival.strftime("%Y-%m-%d %H:%M"),
            "duration_minutes": pattern.duration_minutes,
            "seats_total": self.seats_total,
        }
        return out


def resolve(pattern: F, day: Date) -> ScheduledFlight:
    """Place a weekly pattern on a concrete date.

    All times are plain 24-hour clock times. A flight that departs at 23:30 and
    runs 140 minutes arrives at 01:50 the next day, and that is the whole rule.
    """
    hour, minute = (int(part) for part in pattern.departure.split(":"))
    departure = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    arrival = departure + timedelta(minutes=pattern.duration_minutes)
    return ScheduledFlight(pattern, day, departure, arrival)


def flights_on(
    day: Date, origin: str | None = None, destination: str | None = None
) -> list[ScheduledFlight]:
    """Every flight operating on ``day``, optionally filtered by route."""
    out = [
        resolve(pattern, day)
        for pattern in SCHEDULE
        if pattern.operates_on(day)
        and (origin is None or pattern.origin == origin)
        and (destination is None or pattern.destination == destination)
    ]
    out.sort(key=lambda flight: flight.departure)
    return out


def find(flight_number: str, day: Date) -> ScheduledFlight | None:
    """Look up one flight on one date. Returns ``None`` if it does not operate."""
    pattern = BY_NUMBER.get((flight_number or "").strip().upper())
    if pattern is None or not pattern.operates_on(day):
        return None
    return resolve(pattern, day)


def routes() -> dict[tuple[str, str], list[str]]:
    """Every non-stop city pair, mapped to the carriers serving it."""
    out: dict[tuple[str, str], list[str]] = {}
    for pattern in SCHEDULE:
        carriers = out.setdefault((pattern.origin, pattern.destination), [])
        if pattern.airline not in carriers:
            carriers.append(pattern.airline)
    return out
