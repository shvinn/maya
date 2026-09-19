"""Combines every domain's MCP server into one place.

Each ``simulations/<domain>/`` package owns its own ``mcp_tools.py`` -- that
domain's ``MCPServer`` and its tool registrations (see
``simulations/flights/mcp_tools.py`` and ``simulations/delivery/mcp_tools.py``).
This file defines no tools itself; it is just the one place ``cli.py`` looks
to find every domain that exists.
"""

from __future__ import annotations

from .simulations.delivery.mcp_tools import mcp as delivery_mcp
from .simulations.flights.mcp_tools import mcp as flights_mcp

SERVERS = {"flights": flights_mcp, "delivery": delivery_mcp}


def combined_app():
    """One Starlette app, one process, one port -- each domain still its own
    MCP endpoint at its own path (``/flights/mcp``, ``/delivery/mcp``).

    This is a multi-domain *host*, not a gateway: it does not merge tool
    lists or proxy calls between domains. A client connects to exactly one
    path and sees exactly one domain's tools, same as running two separate
    ``maya serve --domain ...`` processes -- this just saves the second
    port. ``maya serve --domain all`` is the CLI entry point for it.

    Each domain's ``streamable_http_app()`` carries its own lifespan (it
    starts that domain's session manager's task group on startup) that a
    plain ``Mount`` does not propagate from a sub-app to the parent --
    without combining them explicitly, every request would fail with
    "Task group is not initialized." So the parent app's own lifespan
    enters every domain's ``session_manager.run()`` itself; see that
    property's docstring in the ``mcp`` package, which calls this out by
    name as the supported way to host multiple ``MCPServer``\\ s together.
    """
    from contextlib import AsyncExitStack, asynccontextmanager

    from starlette.applications import Starlette
    from starlette.routing import Mount

    sub_apps = {name: server.streamable_http_app() for name, server in SERVERS.items()}

    @asynccontextmanager
    async def lifespan(app):
        async with AsyncExitStack() as stack:
            for server in SERVERS.values():
                await stack.enter_async_context(server.session_manager.run())
            yield

    return Starlette(
        routes=[Mount(f"/{name}", app=sub_app) for name, sub_app in sub_apps.items()],
        lifespan=lifespan,
    )
