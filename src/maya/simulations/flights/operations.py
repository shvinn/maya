"""Flight operations: delays and cancellations.

Every flight on every date has a fate -- on time, delayed, or cancelled --
drawn once from its airline's ``on_time_rate`` and ``cancel_rate`` with a
generator seeded by the world seed and the flight's key. Nothing is stored:
within one world the same flight on the same date always has the same fate,
in every process, the way a flight's fare is computed rather than kept. A
reset reseeds the world (see maya.world.seed), so the next world's flights
fare differently and nothing carries over from a previous run.

Whoever runs the world can also decide a flight's fate outright with
``override`` (the ``world_set_flight_status`` tool) -- to stage one
disruption in a test. An override replaces the drawn fate but is announced
on the same schedule, and a reset drops it.

A fate is not known from the start. It is revealed as departure approaches,
the way an airline announces it: a cancellation ``CANCELLATION_NOTICE_HOURS``
before departure, a delay ``DELAY_NOTICE_HOURS`` before. Until then the
flight is SCHEDULED like any other, so an agent that booked days ahead only
finds out by checking again -- nothing is pushed to it.

Delays run from ``MIN_DELAY_MINUTES`` to ``MAX_DELAY_MINUTES``, skewed
towards the short end. A delay moves departure and arrival together. A
connection whose first flight now lands too late for the minimum connection
time is missed (see ``misconnects``).
"""

from __future__ import annotations

import random
from datetime import timedelta
from typing import NamedTuple

from maya.world import clock, seed

from . import db
from .connections import MIN_CONNECTION_MINUTES
from .schedule import AIRLINES, ScheduledFlight

CANCELLATION_NOTICE_HOURS = 24
DELAY_NOTICE_HOURS = 3
MIN_DELAY_MINUTES = 15
MAX_DELAY_MINUTES = 240
#: An override may hold a flight longer than chance ever does.
MAX_OVERRIDE_DELAY_MINUTES = 24 * 60


class Fate(NamedTuple):
    cancelled: bool
    delay_minutes: int


def fate(flight: ScheduledFlight) -> Fate:
    """What will happen to this flight, known or not yet."""
    row = db.connect().execute(
        "SELECT cancelled, delay_minutes FROM flight_overrides WHERE flight_key = ?", (flight.key,)
    ).fetchone()
    if row is not None:
        return Fate(bool(row["cancelled"]), row["delay_minutes"])
    airline = AIRLINES[flight.pattern.airline]
    rng = random.Random(f"operations/{seed.get()}/{flight.key}")
    roll = rng.random()
    if roll < airline.cancel_rate:
        return Fate(True, 0)
    if roll < airline.cancel_rate + (1 - airline.on_time_rate):
        spread = (MAX_DELAY_MINUTES - MIN_DELAY_MINUTES) / 5
        return Fate(False, MIN_DELAY_MINUTES + 5 * round(rng.random() ** 3 * spread))
    return Fate(False, 0)


def override(flight: ScheduledFlight, delay_minutes: int = 0, cancelled: bool = False) -> Fate:
    """Decide a flight's fate, replacing the drawn one: cancelled, delayed by
    ``delay_minutes``, or (neither) on time. Raises InvalidOverride."""
    if cancelled and delay_minutes:
        raise InvalidOverride("A flight is either cancelled or delayed, not both.")
    if not 0 <= delay_minutes <= MAX_OVERRIDE_DELAY_MINUTES:
        raise InvalidOverride(f"delay_minutes must be from 0 to {MAX_OVERRIDE_DELAY_MINUTES}.")
    conn = db.connect()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO flight_overrides (flight_key, cancelled, delay_minutes) VALUES (?, ?, ?)",
            (flight.key, int(cancelled), delay_minutes),
        )
    return Fate(cancelled, delay_minutes)


class InvalidOverride(Exception):
    code = "invalid_request"
    hint = "Pass cancelled=true, or a delay_minutes from 0 (on time) up to a day."


class Status(NamedTuple):
    """What is known about a flight right now."""

    status: str
    delay_minutes: int
    flight: ScheduledFlight

    @property
    def cancelled(self) -> bool:
        return self.status == "CANCELLED"

    @property
    def estimated_departure(self):
        return self.flight.departure + timedelta(minutes=self.delay_minutes)

    @property
    def estimated_arrival(self):
        return self.flight.arrival + timedelta(minutes=self.delay_minutes)

    def to_dict(self) -> dict:
        if self.cancelled:
            return {"flight_status": self.status, "delay_minutes": 0,
                    "estimated_departure": None, "estimated_arrival": None}
        return {
            "flight_status": self.status,
            "delay_minutes": self.delay_minutes,
            "estimated_departure": self.estimated_departure.strftime("%Y-%m-%d %H:%M"),
            "estimated_arrival": self.estimated_arrival.strftime("%Y-%m-%d %H:%M"),
        }


def status(flight: ScheduledFlight) -> Status:
    """The flight's status as of Maya's now: SCHEDULED (no news -- which may
    still turn out late), DELAYED, CANCELLED, DEPARTED or LANDED."""
    now = clock.now()
    known = fate(flight)
    hours_left = (flight.departure - now) / timedelta(hours=1)
    if known.cancelled and hours_left <= CANCELLATION_NOTICE_HOURS:
        return Status("CANCELLED", 0, flight)
    delay = known.delay_minutes if hours_left <= DELAY_NOTICE_HOURS else 0
    current = Status("SCHEDULED", delay, flight)
    if now >= current.estimated_arrival:
        return current._replace(status="LANDED")
    if now >= current.estimated_departure:
        return current._replace(status="DEPARTED")
    return current._replace(status="DELAYED") if delay else current


def misconnects(first: ScheduledFlight, second: ScheduledFlight) -> bool:
    """Whether, as far as is known now, the first flight lands too late to
    make the second -- under the minimum connection time, or after it left."""
    a, b = status(first), status(second)
    if a.cancelled or b.cancelled:
        return False  # a cancellation, not a missed connection
    layover = (b.estimated_departure - a.estimated_arrival) / timedelta(minutes=1)
    return layover < MIN_CONNECTION_MINUTES
