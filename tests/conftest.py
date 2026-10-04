"""Shared test setup: a throwaway database and a frozen clock.

Every domain fixes its database path when it's first imported, so
MAYA_DB_PATH has to point at a temporary file *before* any maya module is
imported -- which is why it's set at the top of this file, ahead of the
imports below. Tests therefore never touch the repo's own maya.db.

Prices, availability, statuses and cancellation windows all depend on
"now", so the clock is frozen for every test at NOW (a Monday morning)
and only moves when a test moves it. That makes the suite deterministic:
it gives the same answers whatever day or time it runs.
"""

from __future__ import annotations

import os
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

os.environ["MAYA_DB_PATH"] = str(Path(tempfile.mkdtemp(prefix="maya-tests-")) / "maya.db")

from maya.simulations.cars import fleet, rentals  # noqa: E402
from maya.simulations.delivery import orders  # noqa: E402
from maya.simulations.flights import bookings as flight_bookings  # noqa: E402
from maya.simulations.hotels import bookings as hotel_bookings  # noqa: E402

#: Monday 4 March 2030, 09:00 -- the frozen "now" every test starts from.
NOW = datetime(2030, 3, 4, 9, 0)

#: Every module that reads the clock (all via ``datetime.now()``).
_CLOCK_MODULES = (fleet, rentals, orders, flight_bookings, hotel_bookings)


class Clock:
    """Controls what ``datetime.now()`` returns inside Maya."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self._monkeypatch = monkeypatch
        self.now = NOW

    def set(self, now: datetime) -> None:
        self.now = now
        frozen = type("FrozenDatetime", (datetime,), {"now": classmethod(lambda cls, tz=None: now)})
        for module in _CLOCK_MODULES:
            self._monkeypatch.setattr(module, "datetime", frozen)

    def advance(self, **delta: float) -> None:
        self.set(self.now + timedelta(**delta))


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock(monkeypatch)
    c.set(NOW)
    return c


@pytest.fixture(autouse=True)
def fresh_world() -> None:
    """Every test starts with no bookings, orders or rentals in any domain."""
    for module in (flight_bookings, hotel_bookings, rentals, orders):
        module.reset()
