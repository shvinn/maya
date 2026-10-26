"""MCP tools for Maya's world: its clock and its luck.

The controls live on their own ``world`` server, meant for whoever runs the
world (you, or a test harness) rather than the agent being tested -- so an
agent can't skip past its own deadlines. Every domain server gets only the
read-only ``world_get_time``, via ``add_time_tool``, so an agent can always
ask what time it is in Maya.

Resetting the world is not a tool at all: it empties every resident's
bookings and wallet, and needs no key, so anything connected could wipe
everyone. It is ``maya reset`` from a terminal, or ``maya.reset.reset_world``
for a harness that imports Maya.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from datetime import date as Date, timedelta

from maya.simulations.flights import operations, schedule
from maya.tool_errors import tool_error

from . import clock
from . import seed as world_seed

mcp = MCPServer("maya-world")


def _error(e: clock.ClockError):
    return tool_error(e.code, str(e), e.hint)


def add_time_tool(server: MCPServer) -> None:
    """Register the read-only ``world_get_time`` tool on a server."""

    def world_get_time() -> dict:
        """The current time in Maya: Mayan Meridian Time (MYT, UTC-08:00).

        Every deadline, status and price in Maya runs on this clock, not your
        local time -- and it may have been moved ahead of the real date.
        """
        return clock.state()

    server.tool()(world_get_time)


add_time_tool(mcp)


@mcp.tool()
def world_set_time(maya_time: str) -> dict:
    """Move Maya time ahead to a given moment -- e.g. "2030-03-08 09:00" to
    jump to just before a departure. ``maya_time`` is YYYY-MM-DD HH:MM in
    Maya time (MYT); time keeps running at real speed from there.

    Time only moves forward; a moment at or before now fails with
    invalid_time.
    """
    try:
        return clock.set_time(maya_time)
    except clock.ClockError as e:
        return _error(e)


@mcp.tool()
def world_advance_time(minutes: float = 0, hours: float = 0, days: float = 0) -> dict:
    """Skip Maya time ahead instantly -- e.g. 45 minutes for a delivery to
    arrive, or 2 days to reach a check-out.

    Time only moves forward; a zero or negative total fails with
    invalid_duration.
    """
    try:
        return clock.advance(minutes, hours, days)
    except clock.ClockError as e:
        return _error(e)


@mcp.tool()
def world_get_seed() -> dict:
    """This world's seed. Reset with it to replay the same delays and
    cancellations. Keep it from the agent under test: with it, every future
    delay could be worked out."""
    return {"seed": world_seed.get()}


@mcp.tool()
def world_set_flight_status(
    flight_number: str, date: str, delay_minutes: int = 0, cancelled: bool = False
) -> dict:
    """Decide what happens to one flight on one date (YYYY-MM-DD), replacing
    chance: ``cancelled=true``, a ``delay_minutes`` (up to a day), or
    neither for on time.

    It's announced like any other disruption -- a cancellation 24 hours
    before departure, a delay 3 hours before -- so move the clock forward to
    see it. A reset drops every override.
    """
    try:
        day = Date.fromisoformat(date)
    except ValueError:
        return tool_error("invalid_request", f"{date!r} is not a YYYY-MM-DD date.")
    flight = schedule.find(flight_number, day)
    if flight is None:
        return tool_error("not_found", f"{flight_number} does not operate on {date}.",
                          "Check the flight number and date against the timetable.")
    try:
        operations.override(flight, delay_minutes, cancelled)
    except operations.InvalidOverride as e:
        return tool_error(e.code, str(e), e.hint)
    notice = operations.CANCELLATION_NOTICE_HOURS if cancelled else operations.DELAY_NOTICE_HOURS
    return {
        "flight_number": flight.flight_number,
        "date": day.isoformat(),
        "cancelled": cancelled,
        "delay_minutes": delay_minutes,
        "announced_from": f"{flight.departure - timedelta(hours=notice):%Y-%m-%d %H:%M}",
    }
