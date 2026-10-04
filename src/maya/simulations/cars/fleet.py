"""The rental fleet at Aira International.

Reference data. The source of truth is data/cars.csv; it is loaded into
SQLite on init and read from there, so editing that file changes the world
on next run.

There is exactly one rental location: the desk at Aira International, in
the Skyview zone. Pickup and drop-off are always there, so no tool takes a
location argument.

A car's model year is never stored. The CSV holds ``model_age_years`` and
the year is derived from today's date on every call -- so a 1-year-old
Kestrel Swift is always last year's model, whatever year the agent is
running in, and the data never goes stale.

Each row is one make/model with ``fleet_size`` identical cars. You rent the
model, not a specific car -- there are no plates or VINs.
"""

from __future__ import annotations

from datetime import datetime
from typing import NamedTuple

from . import db

LOCATION = {
    "name": "Aira International car rental desk",
    "airport": "AIR",
    "zone": "AIR-APT",
    "zone_name": "Skyview",
    "hours": "24/7",
}


class Car(NamedTuple):
    car_id: str
    make: str
    model: str
    model_age_years: int
    car_class: str
    seats: int
    transmission: str
    fleet_size: int
    daily_rate: int

    @property
    def year(self) -> int:
        return datetime.now().year - self.model_age_years

    def label(self) -> str:
        return f"{self.year} {self.make} {self.model}"

    def to_dict(self) -> dict:
        return {
            "car_id": self.car_id,
            "make": self.make,
            "model": self.model,
            "year": self.year,
            "car_class": self.car_class,
            "seats": self.seats,
            "transmission": self.transmission,
            "fleet_size": self.fleet_size,
            "base_daily_rate": self.daily_rate,
        }


def _load() -> tuple[Car, ...]:
    rows = db.connect().execute(
        "SELECT car_id, make, model, model_age_years, car_class, seats, "
        "transmission, fleet_size, daily_rate FROM cars"
    ).fetchall()
    return tuple(
        Car(
            r["car_id"], r["make"], r["model"], r["model_age_years"], r["car_class"],
            r["seats"], r["transmission"], r["fleet_size"], r["daily_rate"],
        )
        for r in rows
    )


CARS: tuple[Car, ...] = _load()
BY_ID: dict[str, Car] = {c.car_id: c for c in CARS}


def get(car_id: str) -> Car | None:
    return BY_ID.get((car_id or "").strip().upper())
