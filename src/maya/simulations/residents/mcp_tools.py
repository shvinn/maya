"""MCP tool registrations for residents: what a resident can see about
themselves.

Thin adapter, on purpose -- same pattern as the other domains'
mcp_tools.py. Read-only, also on purpose: creating, changing, funding or
re-keying a resident is an admin operation, done with ``maya residents``
on the machine that runs Maya, and has no tool here or anywhere else. Every
tool acts as the resident whose key came with the request (see caller.py),
so there is no resident id to pass and no way to look at anyone else.
"""

from __future__ import annotations

from mcp.server.mcpserver import Context, MCPServer

from maya.tool_errors import tool_error as _error
from maya.world.mcp_tools import add_time_tool

from . import caller, residents

mcp = MCPServer("maya-residents")
add_time_tool(mcp)


@mcp.tool()
def residents_get_profile(ctx: Context | None = None) -> dict:
    """Your resident profile and wallet: resident id, first and last name,
    gender, age, email, when you became a resident, and your money --
    ``balance`` (all of it), ``held`` (set aside, e.g. a fine-dining
    deposit, until the booking ends) and ``available`` (what you can spend
    now: balance less held).

    Read-only -- a profile can't be changed from here. Fails with
    resident_required if your MCP client sends no resident key.
    """
    try:
        resident = caller.current(ctx)
    except residents.ResidentError as e:
        return _error(e.code, str(e), e.hint)
    return {**resident, **residents.wallet(resident["resident_id"])}


@mcp.tool()
def residents_list_transactions(ctx: Context | None = None) -> list[dict]:
    """Every movement of money in your wallet, oldest first: the opening
    balance, top-ups, charges for bookings and refunds for cancellations --
    and holds (with when they lapse, ``expires_at``) and their releases.
    Charges and holds are negative. Each names the booking (``reference``)
    and the domain it was for, so it matches what that domain's get tool
    shows. Your balance is the sum of everything but holds and releases.
    """
    try:
        resident = caller.current(ctx)
    except residents.ResidentError as e:
        return _error(e.code, str(e), e.hint)
    return residents.list_transactions(resident["resident_id"])
