"""MCP tools for Maya's world: its clock, and resetting it.

The controls live on their own ``world`` server, meant for whoever runs the
world (you, or a test harness) rather than the agent being tested -- so an
agent can't skip past its own deadlines or wipe the world. Every domain server gets only the
read-only ``world_get_time``, via ``add_time_tool``, so an agent can always
ask what time it is in Maya.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from maya.reset import reset_world
from maya.tool_errors import tool_error

from . import clock

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
def world_reset() -> dict:
    """Start the world over: empty every domain's bookings, orders, rentals
    and tickets, and return Maya time to real time.

    The only way time goes backwards -- bookings and the clock reset
    together, so nothing is left dated in a future that hasn't happened.
    Reference data (airports, hotels, menus, shows) is untouched. Returns
    how many records each domain had.
    """
    return {"cleared": reset_world(), **clock.state()}
