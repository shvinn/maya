"""The world clock: MYT, scaling against real time, skipping ahead, resetting,
persisting -- plus the world tools and a check that domains use the clock."""

from __future__ import annotations

import asyncio
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from maya.mcp_tools import SERVERS
from maya.simulations.delivery import mcp_tools as delivery
from maya.world import clock
from maya.world import mcp_tools as world

from .conftest import REAL_NOW

#: 2030-03-04 17:00 UTC == 09:00 MYT (UTC-08:00).
REAL_START = datetime(2030, 3, 4, 17, 0, tzinfo=timezone.utc).timestamp()


class FakeRealTime:
    """Stands in for real time, so scaling can be checked without waiting."""

    def __init__(self, ts: float) -> None:
        self.ts = ts

    def __call__(self) -> float:
        return self.ts

    def pass_(self, **delta: float) -> None:
        self.ts += timedelta(**delta).total_seconds()


@pytest.fixture
def real(monkeypatch: pytest.MonkeyPatch) -> FakeRealTime:
    """The real clock implementation, driven by fake real time."""
    fake = FakeRealTime(REAL_START)
    monkeypatch.setattr(clock, "now", REAL_NOW)
    monkeypatch.setattr(clock, "_real_now", fake)
    return fake


def test_default_is_real_time_in_myt_at_one_to_one(real):
    assert clock.now() == datetime(2030, 3, 4, 9, 0)  # UTC-8
    real.pass_(minutes=45)
    assert clock.now() == datetime(2030, 3, 4, 9, 45)
    state = clock.state()
    assert state["scale"] == 1.0 and state["frozen"] is False
    assert state["timezone"] == "MYT (UTC-08:00)"


def test_myt_is_utc_minus_eight_without_daylight_saving(real):
    for month in (1, 7):  # winter and summer alike
        real.ts = datetime(2030, month, 1, 12, 0, tzinfo=timezone.utc).timestamp()
        assert clock.now() == datetime(2030, month, 1, 4, 0)


def test_scale_speeds_time_up_without_jumping(real):
    real.pass_(minutes=10)
    before = clock.now()
    clock.set_scale(60)
    assert clock.now() == before  # no jump on change
    real.pass_(minutes=1)
    assert clock.now() == before + timedelta(hours=1)


def test_scale_zero_freezes_the_world(real):
    clock.set_scale(0)
    frozen = clock.now()
    real.pass_(hours=5)
    assert clock.now() == frozen
    assert clock.state()["frozen"] is True


def test_advance_skips_ahead_and_keeps_the_scale(real):
    clock.set_scale(2)
    start = clock.now()
    clock.advance(hours=3)
    assert clock.now() == start + timedelta(hours=3)
    real.pass_(minutes=30)
    assert clock.now() == start + timedelta(hours=4)


@pytest.mark.parametrize("delta", [{}, {"minutes": 0}, {"hours": -1}, {"days": 1, "hours": -30}])
def test_time_never_moves_backwards(real, delta):
    with pytest.raises(clock.InvalidDuration):
        clock.advance(**delta)


@pytest.mark.parametrize("scale", [-1, 10_001])
def test_scale_must_be_in_range(real, scale):
    with pytest.raises(clock.InvalidScale):
        clock.set_scale(scale)


def test_reset_returns_to_real_time(real):
    clock.set_scale(100)
    clock.advance(days=3)
    clock.reset()
    assert clock.now() == datetime(2030, 3, 4, 9, 0)
    assert clock.state()["scale"] == 1.0


def test_clock_survives_a_restart(real, monkeypatch):
    clock.set_scale(60)
    clock.advance(days=1)
    expected = clock.now()
    monkeypatch.setattr(clock._local, "connection", None)  # a fresh process: new connection
    assert clock.now() == expected
    assert clock.state()["scale"] == 60


def test_maya_time_scale_env_var_applies_at_startup(real, monkeypatch):
    monkeypatch.setenv("MAYA_TIME_SCALE", "1440")
    clock.apply_env_scale()
    real.pass_(minutes=1)
    assert clock.now() == datetime(2030, 3, 5, 9, 0)  # a Maya day per real minute
    monkeypatch.setenv("MAYA_TIME_SCALE", "fast")
    with pytest.raises(clock.InvalidScale):
        clock.apply_env_scale()


# --- tools --------------------------------------------------------------------


def test_world_tools_report_errors_as_error_codes(real):
    assert world.world_set_time_scale(-5)["error"]["code"] == "invalid_scale"
    assert world.world_advance_time(hours=-1)["error"]["code"] == "invalid_duration"
    assert world.world_advance_time(minutes=30)["maya_time"] == "2030-03-04 09:30:00"
    assert world.world_reset_time()["maya_time"] == "2030-03-04 09:00:00"


def test_every_domain_can_read_the_time_but_only_world_can_change_it():
    for name, server in SERVERS.items():
        tools = {t.name for t in asyncio.run(server.list_tools())}
        assert "world_get_time" in tools, name
        controls = {"world_set_time_scale", "world_advance_time", "world_reset_time"}
        assert (controls <= tools) == (name == "world"), name


def test_skipping_ahead_delivers_an_order_without_waiting(real):
    order = delivery.delivery_place_order("OQN", [{"item_id": "OQN-01", "quantity": 1}], "AIR-OLD", "+1")
    assert order["status"] == "PREPARING"
    world.world_advance_time(minutes=order["eta_minutes"])
    assert delivery.delivery_get_order(order["order_id"])["status"] == "DELIVERED"


def test_domains_never_read_the_host_clock_directly():
    root = Path(__file__).resolve().parents[1] / "src" / "maya" / "simulations"
    hits = subprocess.run(["grep", "-rn", "--include=*.py", "datetime.now(", str(root)], capture_output=True, text=True).stdout
    assert hits == "", f"use maya.world.clock.now() instead:\n{hits}"
