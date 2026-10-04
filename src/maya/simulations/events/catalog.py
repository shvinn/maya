"""Aira's venues, their seating sections, and the weekly event series.

Reference data. The source of truth is data/venues.csv, data/sections.csv
and data/events.csv; they are loaded into SQLite on init and read from
there, so editing a file changes the world on next run.

Events are weekly series, not dated one-offs -- the same model as the
flight timetable. "Open Mic Night every Tuesday and Wednesday at 20:00" is
one row; a *performance* is that series placed on a concrete date it runs.
Nothing dated is stored, so the listings never go stale: there is always a
next performance. Every title is fictional, including the films.

Character is mechanical: ``popularity`` drives how fast a show sells out
(see tickets.py), ``min_age`` and ``max_tickets_per_order`` are hard
booking rules, and an empty ``refund_cutoff_hours`` means no refunds.
``category`` and ``genre`` are labels for searching.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import NamedTuple

from . import db

CATEGORIES = ("comedy", "concert", "lecture", "theatre", "family", "film", "workshop")


class Venue(NamedTuple):
    code: str
    name: str
    zone: str
    venue_type: str


class Section(NamedTuple):
    section_id: str
    venue: str
    name: str
    capacity: int
    price_factor: float


class EventSeries(NamedTuple):
    event_id: str
    title: str
    category: str
    genre: str
    venue: str
    #: ISO weekday digits, 1 = Monday ... 7 = Sunday -- same as the flight timetable.
    days: str
    start: str
    duration_minutes: int
    base_price: int
    min_age: int
    max_tickets_per_order: int
    refund_cutoff_hours: int | None
    popularity: float

    def runs_on(self, day: date) -> bool:
        return str(day.isoweekday()) in self.days

    @property
    def refundable(self) -> bool:
        return self.refund_cutoff_hours is not None

    def policy_text(self) -> str:
        if not self.refundable:
            return "Non-refundable. Tickets cannot be cancelled."
        when = "until the start" if self.refund_cutoff_hours == 0 else (
            f"until {self.refund_cutoff_hours} hours before the start"
        )
        return f"Refundable {when} (face value only; the service fee is not refunded)."


@dataclass(frozen=True)
class Performance:
    """One series on one concrete date, with times resolved."""

    series: EventSeries
    date: date
    start: datetime
    end: datetime

    @property
    def venue(self) -> Venue:
        return VENUES[self.series.venue]

    @property
    def sections(self) -> list[Section]:
        return SECTIONS_BY_VENUE.get(self.series.venue, [])


def _rows(sql: str) -> list:
    return db.connect().execute(sql).fetchall()


VENUES: dict[str, Venue] = {
    r["code"]: Venue(r["code"], r["name"], r["zone"], r["venue_type"])
    for r in _rows("SELECT code, name, zone, venue_type FROM venues")
}

SECTIONS: dict[str, Section] = {
    r["section_id"]: Section(r["section_id"], r["venue"], r["name"], r["capacity"], r["price_factor"])
    for r in _rows("SELECT section_id, venue, name, capacity, price_factor FROM sections")
}

SECTIONS_BY_VENUE: dict[str, list[Section]] = {}
for _section in SECTIONS.values():
    SECTIONS_BY_VENUE.setdefault(_section.venue, []).append(_section)

SERIES: dict[str, EventSeries] = {
    r["event_id"]: EventSeries(
        r["event_id"], r["title"], r["category"], r["genre"], r["venue"], r["days"], r["start"],
        r["duration_minutes"], r["base_price"], r["min_age"], r["max_tickets_per_order"],
        r["refund_cutoff_hours"], r["popularity"],
    )
    for r in _rows(
        "SELECT event_id, title, category, genre, venue, days, start, duration_minutes, base_price, "
        "min_age, max_tickets_per_order, refund_cutoff_hours, popularity FROM event_series"
    )
}


def performance(series: EventSeries, day: date) -> Performance:
    hour, minute = (int(part) for part in series.start.split(":"))
    start = datetime.combine(day, datetime.min.time()).replace(hour=hour, minute=minute)
    return Performance(series, day, start, start + timedelta(minutes=series.duration_minutes))


def performances_on(day: date) -> list[Performance]:
    """Every performance on ``day``, earliest first."""
    out = [performance(s, day) for s in SERIES.values() if s.runs_on(day)]
    return sorted(out, key=lambda p: (p.start, p.series.title))


def get_series(event_id: str) -> EventSeries | None:
    return SERIES.get((event_id or "").strip().upper())


def get_section(section_id: str) -> Section | None:
    return SECTIONS.get((section_id or "").strip().upper())
