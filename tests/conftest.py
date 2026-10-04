"""Shared test setup: a throwaway database and a frozen clock.

Every domain fixes its database path when it's first imported, so
MAYA_DB_PATH has to point at a temporary file *before* any maya module is
imported -- which is why it's set at the top of this file, ahead of the
imports below. Tests therefore never touch the repo's own maya.db.

Prices, availability, statuses and cancellation windows all depend on
"now", which every domain reads from the world clock
(``maya.world.clock.now``). That one function is replaced for every test
with a frozen NOW (a Monday morning), which only moves when a test moves
it. That makes the suite deterministic:
it gives the same answers whatever day or time it runs.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

os.environ["MAYA_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="maya-tests-")) / "maya.db")

from maya.simulations.cars import rentals  # noqa: E402
from maya.simulations.delivery import orders  # noqa: E402
from maya.simulations.events import tickets  # noqa: E402
from maya.simulations.flights import bookings as flight_bookings  # noqa: E402
from maya.simulations.hotels import bookings as hotel_bookings  # noqa: E402
from maya.world import clock as world_clock  # noqa: E402

#: Monday 4 March 2030, 09:00 -- the frozen "now" every test starts from.
NOW = datetime(2030, 3, 4, 9, 0)

#: The real clock function, for tests of the clock itself (see test_clock.py).
REAL_NOW = world_clock.now


class Clock:
    """Controls what Maya's world clock says inside tests."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        self.now = NOW

    def set(self, now: datetime) -> None:
        self.now = now
        self._monkeypatch.setattr(world_clock, "now", lambda: now)

    def advance(self, **delta: float) -> None:
        self.set(self.now + timedelta(**delta))


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock(monkeypatch)
    c.set(NOW)
    return c


@pytest.fixture(autouse=True)
def fresh_world() -> None:
    """Every test starts with no bookings, orders or rentals in any domain,
    and an unmodified world clock."""
    for module in (flight_bookings, hotel_bookings, rentals, orders, tickets):
        module.reset()
    world_clock.reset()
