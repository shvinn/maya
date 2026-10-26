"""The world clock: MYT, moving ahead by a duration or to a moment, resetting,
persisting -- plus the world tools and a check that domains use the clock."""

from __future__ import annotations

import asyncio
import sqlite3
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from maya.mcp_tools import SERVERS
from maya.reset import reset_world
from maya.simulations.delivery import mcp_tools as delivery
from maya.world import clock
from maya.world import mcp_tools as world

from .conftest import REAL_NOW, error_code

#: 2030-03-04 17:00 UTC == 09:00 MYT (UTC-08:00), in unix milliseconds.
REAL_START_MS = int(datetime(2030, 3, 4, 17, 0, tzinfo=timezone.utc).timestamp() * 1000)


class FakeRealTime:
    """Stands in for real time, so the clock can be checked without waiting."""

    def __init__(self, ms: int) -> None:
        self.ms = ms

    def __call__(self) -> int:
        return self.ms

    def pass_(self, **delta: float) -> None:
        self.ms += timedelta(**delta) // timedelta(milliseconds=1)


@pytest.fixture
def real(monkeypatch: pytest.MonkeyPatch) -> FakeRealTime:
    """The real clock implementation, driven by fake real time."""
    fake = FakeRealTime(REAL_START_MS)
    monkeypatch.setattr(clock, "now", REAL_NOW)
    monkeypatch.setattr(clock, "_real_now_ms", fake)
    return fake


def test_default_is_real_time_in_myt(real):
    assert clock.now() == datetime(2030, 3, 4, 9, 0)  # UTC-8
    real.pass_(minutes=45)
    assert clock.now() == datetime(2030, 3, 4, 9, 45)
    assert clock.state()["timezone"] == "MYT (UTC-08:00)"


def test_myt_is_utc_minus_eight_without_daylight_saving(real):
    for month in (1, 7):  # winter and summer alike
        real.ms = int(datetime(2030, month, 1, 12, 0, tzinfo=timezone.utc).timestamp() * 1000)
        assert clock.now() == datetime(2030, month, 1, 4, 0)


def test_advance_moves_ahead_and_keeps_running(real):
    clock.advance(hours=3)
    assert clock.now() == datetime(2030, 3, 4, 12, 0)
    real.pass_(minutes=30)
    assert clock.now() == datetime(2030, 3, 4, 12, 30)


def test_advances_add_up(real):
    clock.advance(minutes=20)
    clock.advance(hours=2)
    assert clock.now() == datetime(2030, 3, 4, 11, 20)


def test_set_time_jumps_to_a_moment_and_keeps_running(real):
    clock.set_time("2030-03-08 08:30")
    assert clock.now() == datetime(2030, 3, 8, 8, 30)
    real.pass_(minutes=5)
    assert clock.now() == datetime(2030, 3, 8, 8, 35)


@pytest.mark.parametrize("delta", [{}, {"minutes": 0}, {"hours": -1}, {"days": 1, "hours": -30}])
def test_advance_never_moves_backwards(real, delta):
    with pytest.raises(clock.InvalidDuration):
        clock.advance(**delta)


@pytest.mark.parametrize("target", ["2030-03-04 09:00", "2030-03-01 12:00", "tomorrow"])
def test_set_time_never_moves_backwards_or_takes_nonsense(real, target):
    with pytest.raises(clock.InvalidTime):
        clock.set_time(target)


def test_a_world_reset_clears_bookings_and_returns_to_real_time(real):
    clock.advance(days=3)
    order = delivery.delivery_place_order("OQN", [{"item_id": "OQN-01", "quantity": 1}], "AIR-OLD", "+1")
    assert reset_world()["delivery_orders"] == 1
    assert clock.now() == datetime(2030, 3, 4, 9, 0)
    assert error_code(delivery.delivery_get_order(order["order_id"])) == "not_found"


def test_clock_survives_a_restart_and_runs_while_stopped(real, monkeypatch):
    clock.set_time("2030-04-01 10:00")
    real.pass_(hours=8)  # stopped for a while
    monkeypatch.setattr(clock._local, "connection", None)  # a fresh process: new connection
    assert clock.now() == datetime(2030, 4, 1, 18, 0)


def test_the_database_refuses_maya_time_behind_real_time(real):
    clock.advance(minutes=1)  # creates the table
    with pytest.raises(sqlite3.IntegrityError):
        clock._save(REAL_START_MS - 1, REAL_START_MS)


# --- tools --------------------------------------------------------------------


def test_world_tools_report_errors_as_error_codes(real):
    assert error_code(world.world_advance_time(hours=-1)) == "invalid_duration"
    assert error_code(world.world_set_time("2020-01-01 00:00")) == "invalid_time"
    assert world.world_advance_time(minutes=30)["maya_time"] == "2030-03-04 09:30:00"


def test_every_domain_can_read_the_time_but_only_world_can_change_it():
    for name, server in SERVERS.items():
        tools = {t.name for t in asyncio.run(server.list_tools())}
        assert "world_get_time" in tools, name
        controls = {"world_set_time", "world_advance_time", "world_get_seed", "world_set_flight_status"}
        assert (controls <= tools) == (name == "world"), name
        assert not any("reset" in t for t in tools), name  # resetting is the admin's, from a terminal


def test_skipped_time_and_bookings_go_together_in_a_reset(real):
    world.world_advance_time(days=2)
    delivery.delivery_place_order("OQN", [{"item_id": "OQN-01", "quantity": 1}], "AIR-OLD", "+1")
    assert reset_world()["delivery_orders"] == 1
    assert clock.state()["maya_time"] == "2030-03-04 09:00:00"


def test_skipping_ahead_delivers_an_order_without_waiting(real):
    order = delivery.delivery_place_order("OQN", [{"item_id": "OQN-01", "quantity": 1}], "AIR-OLD", "+1")
    assert order["status"] == "PREPARING"
    world.world_advance_time(minutes=order["eta_minutes"])
    assert delivery.delivery_get_order(order["order_id"])["status"] == "DELIVERED"


def test_domains_never_read_the_host_clock_directly():
    root = Path(__file__).resolve().parents[1] / "src" / "maya" / "simulations"
    hits = subprocess.run(["grep", "-rn", "--include=*.py", "datetime.now(", str(root)], capture_output=True, text=True).stdout
    assert hits == "", f"use maya.world.clock.now() instead:\n{hits}"
