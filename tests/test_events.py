"""Events: weekly schedule, fixed prices plus fees, sell-outs, ticket and age
limits, refund rules (fee never refunded), and live status.

NOW is Monday 2030-03-04 09:00, so the coming Saturday (2030-03-09) is five
days out -- close enough that the most popular shows have sold out.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from maya.simulations.events import catalog, tickets
from maya.simulations.events import mcp_tools as t

from .conftest import error_code

TUE, FRI, SAT, SUN = "2030-03-05", "2030-03-08", "2030-03-09", "2030-03-10"
SAT_FAR = "2030-04-06"  # 33 days out: outside the demand horizon



def book(event_id="SYM", day=SAT, section="ACH-STL", quantity=2, age=None, email="fan@example.mb") -> dict:
    return t.events_book_tickets(event_id, day, section, quantity, "Test Fan", email, age)


def left(event_id: str, day: str, section_id: str) -> int:
    perf = tickets.find_performance(event_id, day)
    return tickets.tickets_left(perf, catalog.get_section(section_id))


# --- catalog and search -------------------------------------------------------


def test_venues_and_sections():
    venues = t.events_list_venues()
    assert len(venues) == len(catalog.VENUES)
    assert all(v["sections"] for v in venues)


def test_search_lists_a_days_performances_earliest_first_with_prices_and_fees():
    rows = t.events_search(SAT)
    assert rows
    assert [r["start"] for r in rows] == sorted(r["start"] for r in rows)
    for r in rows:
        assert r["category"] in catalog.CATEGORIES
        for s in r["sections"]:
            assert s["service_fee"]["amount"] == tickets.service_fee(s["face_price"]["amount"])


@pytest.mark.parametrize(("face", "fee"), [(45, 5), (30, 3), (25, 3), (12, 2), (8, 1), (0, 0)])
def test_service_fee_is_ten_percent_rounded_up_and_free_is_free(face, fee):
    assert tickets.service_fee(face) == fee


def test_shows_only_run_on_their_weekdays():
    assert error_code(t.events_get_event("SYM", FRI)) == "not_found"
    monday = {r["event_id"] for r in t.events_search("2030-03-11")}
    assert {"LEC", "FSR"} <= monday
    assert "SYM" not in monday


def test_search_by_genre_category_and_zone():
    assert {r["event_id"] for r in t.events_search(TUE, "2030-03-10", query="horror")} == {"FFM"}
    assert {r["event_id"] for r in t.events_search(TUE, "2030-03-10", query="jazz")} == {"JAZ"}
    assert {r["category"] for r in t.events_search(TUE, "2030-03-10", category="Workshop")} == {"workshop"}
    assert {r["zone"] for r in t.events_search(TUE, "2030-03-10", zone="Embassy Row")} == {"AIR-EMB"}


def test_search_range_is_limited():
    assert error_code(t.events_search(TUE, "2030-03-30")) == "invalid_date"
    assert error_code(t.events_search(SAT, TUE)) == "invalid_date"


def test_no_venue_hosts_two_shows_at_once():
    week = 7 * 24 * 60
    slots: dict[str, list[tuple[int, int, str]]] = {}
    for s in catalog.SERIES.values():
        hour, minute = map(int, s.start.split(":"))
        for d in s.days:
            start = (int(d) - 1) * 1440 + hour * 60 + minute
            slots.setdefault(s.venue, []).append((start, start + s.duration_minutes, s.event_id))
    for venue, shows in slots.items():
        for i, (a_start, a_end, a_id) in enumerate(shows):
            for b_start, b_end, b_id in shows[i + 1:]:
                for shift in (-week, 0, week):  # a late Sunday show runs into Monday
                    assert a_end <= b_start + shift or b_end + shift <= a_start, (venue, a_id, b_id)


# --- demand and sell-outs -----------------------------------------------------


def test_a_popular_show_sells_out_before_the_day_while_a_quieter_one_is_open():
    rows = {r["event_id"]: r for r in t.events_search(SAT)}
    assert rows["TWL"]["sold_out"] is True
    assert rows["SYM"]["sold_out"] is False
    assert error_code(book("TWL", SAT, "CRA-FLR", 1)) == "sold_out"


def test_tickets_are_plentiful_far_out():
    section = catalog.get_section("CRA-FLR")
    assert left("TWL", SAT_FAR, "CRA-FLR") == section.capacity


def test_booking_takes_tickets_and_cancelling_releases_them():
    before = left("SYM", SAT, "ACH-STL")
    booking = book(quantity=3)
    assert left("SYM", SAT, "ACH-STL") == before - 3
    t.events_cancel_booking(booking["booking_reference"])
    assert left("SYM", SAT, "ACH-STL") == before


# --- booking rules ------------------------------------------------------------


def test_total_is_face_plus_fee_per_ticket():
    booking = book(quantity=2)
    assert booking["total_price"]["amount"] == (45 + 5) * 2
    assert booking["status"] == "CONFIRMED"


def test_free_lecture_costs_nothing():
    booking = book("LEC", "2030-03-07", "UGH-GA", 2)
    assert booking["total_price"]["amount"] == 0


def test_per_order_ticket_limit():
    assert error_code(book("TWL", SAT_FAR, "CRA-FLR", 5)) == "ticket_limit_exceeded"
    assert error_code(book("TWL", SAT_FAR, "CRA-FLR", 4)) is None


@pytest.mark.parametrize(
    ("event_id", "day", "section", "age", "code"),
    [
        ("OPN", TUE, "LNT-GA", None, "age_restricted"),  # 18+ needs an age
        ("OPN", TUE, "LNT-GA", 17, "age_restricted"),
        ("OPN", TUE, "LNT-GA", 18, None),
        ("LHK", SAT, "RVP-STL", 10, "age_restricted"),  # 12+
        ("SYM", SAT, "ACH-STL", None, None),  # no age limit, no age needed
    ],
)
def test_age_limits(event_id, day, section, age, code):
    assert error_code(book(event_id, day, section, 1, age)) == code


def test_sales_close_at_the_start(clock):
    clock.set(datetime(2030, 3, 5, 20, 1))  # Open Mic started at 20:00
    assert error_code(book("OPN", TUE, "LNT-GA", 1, 25)) == "sales_closed"
    assert "OPN" not in {r["event_id"] for r in t.events_search(TUE)}


@pytest.mark.parametrize(
    ("event_id", "day"),
    [
        ("CHM", "2030-03-03"),  # a Sunday already past
        ("SYM", "2030-07-06"),  # a Saturday beyond the 90-day sales window
        ("SYM", "next saturday"),  # not a date
    ],
)
def test_invalid_dates(event_id, day):
    assert error_code(book(event_id, day, "ACH-STL", 1)) == "invalid_date"


def test_section_must_belong_to_the_venue():
    assert error_code(book("SYM", SAT, "LNT-GA", 1)) == "not_found"


# --- refunds ------------------------------------------------------------------


def test_non_refundable_show():
    booking = book("JAZ", FRI, "HAM-LAWN", 2)
    assert error_code(t.events_cancel_booking(booking["booking_reference"])) == "not_refundable"


def test_refund_is_face_value_only(clock):
    booking = book(quantity=2)  # Symphony, 72h window, five days out
    result = t.events_cancel_booking(booking["booking_reference"])
    assert result["refund"]["amount"] == 45 * 2
    assert result["fee_not_refunded"]["amount"] == 5 * 2
    assert error_code(t.events_cancel_booking(booking["booking_reference"])) == "already_cancelled"


def test_cancelling_inside_the_window_is_too_late(clock):
    booking = book(quantity=1)
    clock.set(datetime(2030, 3, 7, 12, 0))  # 55.5h before a 72h cutoff
    assert error_code(t.events_cancel_booking(booking["booking_reference"])) == "too_late_to_cancel"


def test_free_lecture_can_be_cancelled_until_the_start(clock):
    booking = book("LEC", "2030-03-07", "UGH-GA", 1)
    clock.set(datetime(2030, 3, 7, 18, 0))  # 30 minutes before
    assert t.events_cancel_booking(booking["booking_reference"])["status"] == "CANCELLED"


# --- live status --------------------------------------------------------------


def test_status_follows_the_show(clock):
    booking = book("CHM", SUN, "ACH-STL", 1)  # 15:00, 90 minutes
    ref = booking["booking_reference"]
    clock.set(datetime(2030, 3, 10, 15, 0))
    assert t.events_get_booking(ref)["status"] == "IN_PROGRESS"
    clock.set(datetime(2030, 3, 10, 16, 30))
    assert t.events_get_booking(ref)["status"] == "ENDED"
    assert [b["booking_reference"] for b in t.events_list_bookings(status="ENDED")] == [ref]
