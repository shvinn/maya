"""MCP tools for Maya's world clock.

The controls live on their own ``world`` server, meant for whoever runs the
world (you, or a test harness) rather than the agent being tested -- so an
agent can't skip past its own deadlines. Every domain server gets only the
read-only ``world_get_time``, via ``add_time_tool``, so an agent can always
ask what time it is in Maya.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import clock

mcp = MCPServer("maya-world")


def _error(e: clock.ClockError) -> dict:
    error: dict = {"code": e.code, "message": str(e)}
    if e.hint:
        error["hint"] = e.hint
    return {"error": error}


def add_time_tool(server: MCPServer) -> None:
    """Register the read-only ``world_get_time`` tool on a server."""

    def world_get_time() -> dict:
        """The current time in Maya: Mayan Meridian Time (MYT, UTC-08:00).

        Every deadline, status and price in Maya runs on this clock, not your
        local time -- and it may be running faster than real time
        (``scale`` > 1) or frozen (``scale`` 0).
        """
        return clock.state()

    server.tool()(world_get_time)


add_time_tool(mcp)


@mcp.tool()
def world_set_time_scale(scale: float) -> dict:
    """Set how fast Maya time runs against real time, from now on.

    1 = real time (the default), 60 = one Maya hour per real minute,
    1440 = one Maya day per real minute, 0 = frozen. Maximum 10000. The
    clock never jumps when the scale changes.
    """
    try:
        return clock.set_scale(scale)
    except clock.ClockError as e:
        return _error(e)


@mcp.tool()
def world_advance_time(minutes: float = 0, hours: float = 0, days: float = 0) -> dict:
    """Skip Maya time ahead instantly -- e.g. 45 minutes for a delivery to
    arrive, or 2 days to reach a check-out.

    Time only moves forward; a zero or negative total fails with
    invalid_duration. The scale is unchanged.
    """
    try:
        return clock.advance(minutes, hours, days)
    except clock.ClockError as e:
        return _error(e)


@mcp.tool()
def world_reset_time() -> dict:
    """Return Maya to real time (in MYT) at 1:1.

    If time had been skipped ahead this steps backwards, so bookings made in
    the skipped-ahead time may look like they're in the future -- reset the
    domains' bookings too for a clean world.
    """
    return clock.reset()
