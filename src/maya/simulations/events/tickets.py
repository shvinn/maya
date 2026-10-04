"""Event tickets, persisted in SQLite.

A booking and the tickets sold per performance and section survive a
restart; ``reset()`` (the ``events_reset_bookings`` tool) empties them.
Booking is a single step, like every other domain: search, book, confirmed.

Inventory is per section of one performance. Other buyers take tickets on
their own as a show approaches: ``capacity x popularity x ramp``, where the
ramp rises linearly over the ``BACKGROUND_DEMAND_HORIZON_DAYS`` before the
start. Unlike hotels and cars there is no floor -- a show with popularity
above 1 sells out completely before the day (1.3 sells out about a week
early), the way a big concert really does. Real bookings take tickets on
top.

Prices are fixed face values, the way comedy, theatre and lecture tickets
really are: ``base_price x section price_factor``, plus a per-ticket service
fee of ``SERVICE_FEE_PERCENT`` (rounded up; free events carry none). On
cancellation the face value is refunded and the fee is not.

The booking rules: no more than the series' ``max_tickets_per_order``;
age-restricted series need ``youngest_attendee_age`` and refuse anyone
under ``min_age``; sales open ``SALES_OPEN_DAYS`` ahead and close at the
start. Status is computed live -- CONFIRMED until the start, IN_PROGRESS
until it ends, then ENDED. CANCELLED is the one stored override.
"""

from __future__ import annotations

import json
import random
from datetime import date, datetime, timedelta

from maya import storage
from maya.world import clock
from maya.world.geography import maps

from . import catalog, db
from .catalog import EventSeries, Performance, Section

CURRENCY = "bucks"

SALES_OPEN_DAYS = 90
MAX_SEARCH_DAYS = 14
SERVICE_FEE_PERCENT = 10

#: How far out a performance starts looking untouched by other buyers.
BACKGROUND_DEMAND_HORIZON_DAYS = 30

#: Record locators skip letters that look like digits.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


# --- storage ------------------------------------------------------------------


def _get_booking(reference: str) -> dict | None:
    row = db.connect().execute(
        "SELECT data FROM event_bookings WHERE reference = ?", (reference,)
    ).fetchone()
    return json.loads(row["data"]) if row else None


def _save_booking(booking: dict) -> None:
    conn = db.connect()
    conn.execute(
        "INSERT OR REPLACE INTO event_bookings (reference, data) VALUES (?, ?)",
        (booking["booking_reference"], json.dumps(booking)),
    )


def _all_bookings() -> list[dict]:
    return [json.loads(r["data"]) for r in db.connect().execute("SELECT data FROM event_bookings").fetchall()]


def _tickets_sold(event_id: str, day: date, section_id: str) -> int:
    row = db.connect().execute(
        "SELECT count FROM tickets_sold WHERE event_id = ? AND date = ? AND section_id = ?",
        (event_id, day.isoformat(), section_id),
    ).fetchone()
    return row["count"] if row else 0


def _adjust_tickets_sold(event_id: str, day: date, section_id: str, delta: int) -> None:
    conn = db.connect()
    conn.execute(
        "INSERT OR REPLACE INTO tickets_sold (event_id, date, section_id, count) VALUES (?, ?, ?, ?)",
        (event_id, day.isoformat(), section_id, max(0, _tickets_sold(event_id, day, section_id) + delta)),
    )


def _next_reference() -> str:
    """A six-character locator, same generator as flights but salted
    differently from every other domain so references never collide.

    Numbered from the bookings already stored, read inside the booking's
    transaction -- never from an in-memory counter, which two processes
    sharing the database would both hand out (and the second booking would
    overwrite the first).
    """
    n = db.connect().execute("SELECT COUNT(*) AS n FROM event_bookings").fetchone()["n"] + 1
    rng = random.Random((n * 2654435761 + 0xE7E47) % 2**32)
    return "".join(rng.choice(_ALPHABET) for _ in range(6))


# --- prices and availability --------------------------------------------------


def face_price(series: EventSeries, section: Section) -> int:
    return round(series.base_price * section.price_factor)


def service_fee(face: int) -> int:
    """Per-ticket fee: SERVICE_FEE_PERCENT of face value, rounded up (integer
    maths, so 30 bucks is exactly 3, not 4 from float error). Free is free."""
    return (face * SERVICE_FEE_PERCENT + 99) // 100


def _background_sold(perf: Performance, section: Section) -> int:
    """Tickets other buyers have taken, purely a function of time to the start."""
    days_left = (perf.start - clock.now()) / timedelta(days=1)
    ramp = max(0.0, min(1.0, 1 - days_left / BACKGROUND_DEMAND_HORIZON_DAYS))
    return min(section.capacity, round(section.capacity * perf.series.popularity * ramp))


def tickets_left(perf: Performance, section: Section) -> int:
    taken = _background_sold(perf, section) + _tickets_sold(perf.series.event_id, perf.date, section.section_id)
    return max(0, section.capacity - taken)


# --- lookups and validation ---------------------------------------------------


def _parse_date(value: str, field: str = "date") -> date:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        raise InvalidDate(f"{field} {value!r} is not a date in YYYY-MM-DD form.") from None


def find_performance(event_id: str, day_text: str) -> Performance:
    """The performance of a series on a date. Raises EventNotFound,
    InvalidDate or NotOnThatDate."""
    series = catalog.get_series(event_id)
    if series is None:
        raise EventNotFound(f"No event with id {event_id!r}.")
    day = _parse_date(day_text)
    if not series.runs_on(day):
        raise NotOnThatDate(f"{series.title} doesn't run on {day.strftime('%A')} {day.isoformat()}.")
    return catalog.performance(series, day)


def _performance_row(perf: Performance, quantity: int) -> dict:
    series, venue = perf.series, perf.venue
    sections = []
    for section in perf.sections:
        face = face_price(series, section)
        sections.append({
            "section_id": section.section_id,
            "name": section.name,
            "face_price": {"amount": face, "currency": CURRENCY},
            "service_fee": {"amount": service_fee(face), "currency": CURRENCY},
            "tickets_left": tickets_left(perf, section),
        })
    return {
        "event_id": series.event_id,
        "title": series.title,
        "category": series.category,
        "genre": series.genre,
        "venue_code": venue.code,
        "venue_name": venue.name,
        "zone": venue.zone,
        "date": perf.date.isoformat(),
        "start": f"{perf.start:%Y-%m-%d %H:%M}",
        "end": f"{perf.end:%Y-%m-%d %H:%M}",
        "min_age": series.min_age,
        "max_tickets_per_order": series.max_tickets_per_order,
        "refundable": series.refundable,
        "refund_policy": series.policy_text(),
        "sold_out": all(s["tickets_left"] < quantity for s in sections),
        "sections": sections,
    }


def search(
    date_from: str,
    date_to: str | None = None,
    category: str | None = None,
    zone: str | None = None,
    query: str | None = None,
    quantity: int = 1,
    max_price: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """Performances between two dates (inclusive), earliest first. Sold-out
    shows are listed with ``sold_out: true`` rather than hidden."""
    start = _parse_date(date_from, "date_from")
    end = _parse_date(date_to, "date_to") if date_to else start
    if end < start:
        raise InvalidDate("date_to must be on or after date_from.")
    if (end - start).days >= MAX_SEARCH_DAYS:
        raise InvalidDate(f"Search at most {MAX_SEARCH_DAYS} days at a time.")
    zone_code = None
    if zone:
        found = maps.resolve_zone(zone)
        if found is None:
            raise ZoneNotFound(f"{zone!r} is not a known zone.")
        zone_code = found.code
    wanted_category = category.strip().lower() if category else None
    needle = (query or "").strip().lower()
    now = clock.now()

    rows = []
    day = start
    while day <= end:
        for perf in catalog.performances_on(day):
            series = perf.series
            if perf.start <= now:
                continue
            if wanted_category and series.category != wanted_category:
                continue
            if zone_code and perf.venue.zone != zone_code:
                continue
            if needle and needle not in series.title.lower() and needle not in series.genre.lower():
                continue
            if max_price is not None and min(face_price(series, s) for s in perf.sections) > max_price:
                continue
            rows.append(_performance_row(perf, quantity))
        day += timedelta(days=1)
    return rows[:limit]


def details(event_id: str, day_text: str) -> dict:
    return _performance_row(find_performance(event_id, day_text), 1)


# --- booking lifecycle --------------------------------------------------------


def _status(booking: dict) -> str:
    """Booking status, computed live from the clock -- CANCELLED is the only
    value that's actually stored, as a terminal override."""
    if booking["cancelled"]:
        return "CANCELLED"
    now = clock.now()
    if now < datetime.fromisoformat(booking["start"]):
        return "CONFIRMED"
    if now < datetime.fromisoformat(booking["end"]):
        return "IN_PROGRESS"
    return "ENDED"


def _with_live_status(booking: dict) -> dict:
    out = dict(booking)
    out.setdefault("reference", out["booking_reference"])  # records stored before it existed
    out["status"] = _status(booking)
    out.pop("cancelled", None)
    return out


def _book(
    event_id: str,
    day_text: str,
    section_id: str,
    quantity: int,
    attendee_name: str,
    contact_email: str,
    youngest_attendee_age: int | None = None,
) -> dict:
    """Buy tickets in one section of one performance. Raises EventNotFound,
    NotOnThatDate, SectionNotFound, InvalidDate, SalesClosed,
    InvalidQuantity, TicketLimitExceeded, AgeRestricted or SoldOut."""
    perf = find_performance(event_id, day_text)
    series = perf.series
    section = catalog.get_section(section_id)
    if section is None or section.venue != series.venue:
        raise SectionNotFound(f"{section_id!r} is not a section at {perf.venue.name}.")

    now = clock.now()
    if perf.date < now.date():
        raise InvalidDate(f"{perf.date.isoformat()} is in the past.")
    if perf.start <= now:
        raise SalesClosed(f"{series.title} started at {perf.start:%H:%M}; ticket sales have closed.")
    if (perf.date - now.date()).days > SALES_OPEN_DAYS:
        raise InvalidDate(f"Tickets go on sale {SALES_OPEN_DAYS} days before each performance.")
    if quantity < 1:
        raise InvalidQuantity(f"quantity must be at least 1; got {quantity}.")
    if quantity > series.max_tickets_per_order:
        raise TicketLimitExceeded(
            f"{series.title} allows at most {series.max_tickets_per_order} tickets per order; {quantity} requested."
        )
    if series.min_age:
        if youngest_attendee_age is None:
            raise AgeRestricted(f"{series.title} is {series.min_age}+; give youngest_attendee_age to book.")
        if youngest_attendee_age < series.min_age:
            raise AgeRestricted(
                f"{series.title} is {series.min_age}+; the youngest attendee is {youngest_attendee_age}."
            )
    left = tickets_left(perf, section)
    if left < quantity:
        raise SoldOut(
            f"{section.name} for {series.title} on {perf.date.isoformat()} has {left} ticket(s) left; "
            f"{quantity} requested."
        )

    face = face_price(series, section)
    fee = service_fee(face)
    _adjust_tickets_sold(series.event_id, perf.date, section.section_id, quantity)
    booking = {
        "booking_reference": (reference := _next_reference()),
        "reference": reference,
        "cancelled": False,
        "event_id": series.event_id,
        "title": series.title,
        "category": series.category,
        "venue_name": perf.venue.name,
        "zone": perf.venue.zone,
        "section_id": section.section_id,
        "section_name": section.name,
        "date": perf.date.isoformat(),
        "start": f"{perf.start:%Y-%m-%d %H:%M}",
        "end": f"{perf.end:%Y-%m-%d %H:%M}",
        "quantity": quantity,
        "attendee_name": attendee_name,
        "contact_email": contact_email,
        "face_price_per_ticket": {"amount": face, "currency": CURRENCY},
        "service_fee_per_ticket": {"amount": fee, "currency": CURRENCY},
        "total_price": {"amount": (face + fee) * quantity, "currency": CURRENCY},
        "refundable": series.refundable,
        "refund_policy": series.policy_text(),
        "booked_at": now.strftime("%Y-%m-%d %H:%M"),
    }
    _save_booking(booking)
    return _with_live_status(booking)

def book(
    event_id: str,
    day_text: str,
    section_id: str,
    quantity: int,
    attendee_name: str,
    contact_email: str,
    youngest_attendee_age: int | None = None,
) -> dict:
    """Book atomically: every check and write below happens in one
    transaction (see maya.storage.transaction), so concurrent bookings
    can't both take the last of anything."""
    with storage.transaction(db.connect()):
        return _book(event_id, day_text, section_id, quantity, attendee_name, contact_email, youngest_attendee_age)


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
    return sorted(out, key=lambda b: (b["start"], b["booking_reference"]))


def _cancel(reference: str) -> dict:
    """Cancel a booking: face value back if inside the series' refund window,
    the service fee never; non-refundable series can't be cancelled at all."""
    booking = _get_booking((reference or "").strip().upper())
    if booking is None:
        raise NotFound(f"No booking with reference {reference!r}.")
    if booking["cancelled"]:
        raise AlreadyCancelled(f"Booking {booking['booking_reference']} is already cancelled.")

    series = catalog.get_series(booking["event_id"])
    if series is None or not series.refundable:
        raise NotRefundable(f"{booking['title']} tickets are non-refundable.")
    hours_left = (datetime.fromisoformat(booking["start"]) - clock.now()) / timedelta(hours=1)
    if hours_left < series.refund_cutoff_hours:
        raise TooLateToCancel(
            f"{booking['title']} starts in {max(hours_left, 0):.1f} hour(s); cancellation closes "
            + ("at the start." if series.refund_cutoff_hours == 0 else f"{series.refund_cutoff_hours} hours before.")
        )

    booking["cancelled"] = True
    _adjust_tickets_sold(booking["event_id"], date.fromisoformat(booking["date"]), booking["section_id"], -booking["quantity"])
    refund = {"amount": booking["face_price_per_ticket"]["amount"] * booking["quantity"], "currency": CURRENCY}
    booking["refund"] = refund
    _save_booking(booking)
    return {
        "booking_reference": booking["booking_reference"],
        "reference": booking["booking_reference"],
        "status": "CANCELLED",
        "refund": refund,
        "fee_not_refunded": {
            "amount": booking["service_fee_per_ticket"]["amount"] * booking["quantity"], "currency": CURRENCY,
        },
        "reason": "Cancelled inside the refund window: face value refunded, service fee kept.",
    }

def cancel(reference: str) -> dict:
    """Cancel atomically: every check and write below happens in one
    transaction (see maya.storage.transaction), so two requests can't
    cancel the same booking twice or release its inventory twice."""
    with storage.transaction(db.connect()):
        return _cancel(reference)


def reset() -> int:
    """Empty all bookings and tickets sold. The ``events_reset_bookings`` tool.

    Returns the number of bookings cleared.
    """
    cleared = db.reset_world()
    return cleared


# --- errors -------------------------------------------------------------------


class TicketError(Exception):
    """Base class for ticketing failures that agents are expected to handle."""

    code = "ticket_error"
    hint = None


class EventNotFound(TicketError):
    code = "not_found"
    hint = "Check the event id against events_search."


class NotOnThatDate(TicketError):
    code = "not_found"
    hint = "Events run on fixed weekdays; use events_search to find a date it's on."


class SectionNotFound(TicketError):
    code = "not_found"
    hint = "Use a section_id listed for this performance in events_search or events_get_event."


class ZoneNotFound(TicketError):
    code = "not_found"
    hint = "Use an Aira zone code, zone name, or postal code -- see events_list_venues for zones."


class InvalidDate(TicketError):
    code = "invalid_date"
    hint = f"Use YYYY-MM-DD dates, today or later, at most {SALES_OPEN_DAYS} days ahead."


class SalesClosed(TicketError):
    code = "sales_closed"
    hint = "Do not retry. Look for a later performance with events_search."


class InvalidQuantity(TicketError):
    code = "invalid_quantity"
    hint = "quantity must be a whole number, 1 or more."


class TicketLimitExceeded(TicketError):
    code = "ticket_limit_exceeded"
    hint = "Book fewer tickets per order. Bigger groups need several orders, if tickets allow."


class AgeRestricted(TicketError):
    code = "age_restricted"
    hint = "Pass youngest_attendee_age. If anyone is under the minimum age, choose another event."


class SoldOut(TicketError):
    code = "sold_out"
    hint = "Try another section, another date, or another event."


class NotFound(TicketError):
    code = "not_found"
    hint = "Check the reference, or call events_list_bookings to see what exists."


class NotRefundable(TicketError):
    code = "not_refundable"
    hint = "Do not retry. Tell the customer these tickets cannot be refunded."


class TooLateToCancel(TicketError):
    code = "too_late_to_cancel"
    hint = "Do not retry. The refund window for this performance has closed."


class AlreadyCancelled(TicketError):
    code = "already_cancelled"
    hint = "No action needed; the booking is already cancelled."
