"""Cross-cutting guarantees: refusals are MCP errors, bookings are atomic,
references are unique and uniform, and the database location rules hold."""

from __future__ import annotations

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from pathlib import Path

import pytest

from maya import storage
from maya.mcp_tools import SERVERS
from maya.simulations.cars import mcp_tools as cars
from maya.simulations.delivery import mcp_tools as delivery
from maya.simulations.events import mcp_tools as events
from maya.simulations.flights import mcp_tools as flights
from maya.simulations.hotels import bookings as hotel_bookings
from maya.simulations.hotels import hotels
from maya.simulations.hotels import mcp_tools as hotels_tools

from .conftest import NOW, error_code


def day(n: int) -> str:
    return (NOW.date() + timedelta(days=n)).isoformat()


# --- refusals are MCP tool errors ---------------------------------------------


def test_a_refusal_reaches_the_client_as_an_mcp_error_with_structured_details():
    result = asyncio.run(SERVERS["hotels"].call_tool(
        "hotels_book_room",
        {"room_type_id": "TWH-DRM", "check_in": day(5), "check_out": day(6), "guests": 2,
         "guest_name": "X", "contact_email": "x@example.mb"},
    ))
    assert result.is_error is True
    error = result.structured_content["error"]
    assert error["code"] == "too_many_guests"
    assert error["message"] and error["hint"]
    assert '"too_many_guests"' in result.content[0].text  # text clients see it too


def test_a_success_is_not_an_mcp_error():
    result = asyncio.run(SERVERS["hotels"].call_tool(
        "hotels_book_room",
        {"room_type_id": "HLH-STD", "check_in": day(10), "check_out": day(11), "guests": 1,
         "guest_name": "X", "contact_email": "x@example.mb"},
    ))
    assert not result.is_error
    assert json.loads(result.content[0].text)["status"] == "CONFIRMED"


# --- atomic inventory ---------------------------------------------------------


def test_concurrent_bookings_cannot_oversell_the_last_room():
    room = hotels.get_room_type("TCG-STE")
    night = NOW.date() + timedelta(days=1)
    left = hotel_bookings.rooms_available(room, night)
    assert 0 < left < 8

    def attempt(i: int):
        return hotels_tools.hotels_book_room("TCG-STE", day(1), day(2), 2, f"G{i}", f"g{i}@example.mb")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(8)))

    confirmed = [r for r in results if error_code(r) is None]
    assert len(confirmed) == left
    assert all(error_code(r) == "sold_out" for r in results if error_code(r) is not None)
    assert hotel_bookings.rooms_available(room, night) == 0


def test_concurrent_bookings_get_unique_references():
    def attempt(i: int):
        return flights.flights_book_flight("SA101", "2030-03-13", [f"P{i}"], f"p{i}@example.mb")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(attempt, range(16)))

    references = [r["booking_reference"] for r in results]
    assert len(set(references)) == 16
    assert len(flights.flights_list_bookings()) == 16  # none overwrote another


# --- one reference field everywhere -------------------------------------------


@pytest.mark.parametrize("domain", ["flights", "hotels", "cars", "events", "delivery"])
def test_every_domain_returns_a_shared_reference_field(domain):
    if domain == "flights":
        made = flights.flights_book_flight("SA101", "2030-03-13", ["A"], "a@example.mb")
        got, listed, cancelled = (
            flights.flights_get_booking(made["reference"]), flights.flights_list_bookings(),
            flights.flights_cancel_booking(made["reference"]),
        )
    elif domain == "hotels":
        made = hotels_tools.hotels_book_room("HLH-STD", day(10), day(11), 1, "A", "a@example.mb")
        got, listed, cancelled = (
            hotels_tools.hotels_get_booking(made["reference"]), hotels_tools.hotels_list_bookings(),
            hotels_tools.hotels_cancel_booking(made["reference"]),
        )
    elif domain == "cars":
        made = cars.cars_book_car("KSW", "2030-03-09 10:00", "2030-03-10 10:00", "A", 30, "a@example.mb")
        got, listed, cancelled = (
            cars.cars_get_rental(made["reference"]), cars.cars_list_rentals(),
            cars.cars_cancel_rental(made["reference"]),
        )
    elif domain == "events":
        made = events.events_book_tickets("SYM", "2030-03-09", "ACH-STL", 1, "A", "a@example.mb")
        got, listed, cancelled = (
            events.events_get_booking(made["reference"]), events.events_list_bookings(),
            events.events_cancel_booking(made["reference"]),
        )
    else:
        made = delivery.delivery_place_order("OQN", [{"item_id": "OQN-01", "quantity": 1}], "AIR-OLD", "+1")
        got, listed, cancelled = (
            delivery.delivery_get_order(made["reference"]), delivery.delivery_list_orders(),
            delivery.delivery_cancel_order(made["reference"]),
        )
    reference = made["reference"]
    assert reference
    assert got["reference"] == reference
    assert [x["reference"] for x in listed] == [reference]
    assert cancelled["reference"] == reference


# --- where the database lives -------------------------------------------------


def test_maya_db_path_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("MAYA_DB_PATH", str(tmp_path / "custom.db"))
    assert storage.db_path() == tmp_path / "custom.db"


def test_a_clone_keeps_its_database_at_the_repo_root(monkeypatch):
    monkeypatch.delenv("MAYA_DB_PATH", raising=False)
    repo = Path(storage.__file__).resolve().parents[2]
    assert storage.db_path() == repo / "maya.db"


def test_an_installed_copy_uses_a_per_user_data_folder(monkeypatch, tmp_path):
    monkeypatch.delenv("MAYA_DB_PATH", raising=False)
    # Pretend maya is installed somewhere with no repo above it.
    monkeypatch.setattr(storage, "__file__", str(tmp_path / "site-packages" / "maya" / "storage.py"))
    monkeypatch.setattr(storage.sys, "platform", "linux")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    assert storage.db_path() == tmp_path / "xdg" / "maya" / "maya.db"
    assert (tmp_path / "xdg" / "maya").is_dir()


@pytest.mark.parametrize(
    ("platform", "expected"),
    [("darwin", "Library/Application Support/maya"), ("linux", ".local/share/maya")],
)
def test_per_user_folder_follows_the_platform(monkeypatch, tmp_path, platform, expected):
    monkeypatch.setattr(storage.sys, "platform", platform)
    monkeypatch.setattr(storage.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    assert storage.user_data_dir() == tmp_path / expected


def test_first_booking_on_a_fresh_database_with_the_real_clock(monkeypatch):
    # Regression: reading the time used to create the clock's table -- a write
    # on its own connection, made inside the booking's transaction, which then
    # waited on that transaction's lock until it timed out ("database is locked").
    from maya.world import clock

    from .conftest import REAL_NOW

    monkeypatch.setattr(clock, "now", REAL_NOW)
    conn = clock._connect()
    conn.execute("DROP TABLE IF EXISTS world_clock")
    conn.commit()

    real_tomorrow = (REAL_NOW().date() + timedelta(days=10)).isoformat()
    real_after = (REAL_NOW().date() + timedelta(days=11)).isoformat()
    result = hotels_tools.hotels_book_room("HLH-STD", real_tomorrow, real_after, 1, "X", "x@example.mb")
    assert error_code(result) is None
    assert result["status"] == "CONFIRMED"
