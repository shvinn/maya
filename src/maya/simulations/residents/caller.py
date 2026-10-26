"""Who is calling: the resident whose key came with the request.

A tool that needs a resident -- to book, cancel, or read someone's own
bookings -- asks ``current(ctx)``. Nothing a tool is *given* as an argument
decides who the resident is: the key travels outside the conversation, so a
model can't name, switch to or invent someone else's resident.

Where the key comes from depends on the transport:

- **HTTP** -- the ``Authorization: Bearer <key>`` header, set in the MCP
  client's config. Only the header: the server's own environment is never
  consulted, or every keyless HTTP client would act as whoever set it.
- **stdio, or a direct Python call** -- there are no headers, so the
  ``MAYA_RESIDENT_KEY`` environment variable of the server process, set in
  the client's config for that server.

The key itself is checked by residents.authenticate().
"""

from __future__ import annotations

import os
from collections.abc import Mapping

from mcp.server.mcpserver import Context

from . import residents

ENV_KEY = "MAYA_RESIDENT_KEY"


def _header(headers: Mapping[str, str], name: str) -> str | None:
    # Starlette's headers are case-insensitive already; a plain Mapping isn't.
    value = headers.get(name)
    if value is None:
        value = next((v for k, v in headers.items() if k.lower() == name), None)
    return value


def _headers(ctx: Context | None) -> Mapping[str, str] | None:
    """The request's HTTP headers; None on stdio or with no request at all.

    A tool called in-process through the server (``server.call_tool``, as a
    harness does) still gets a Context, but one with no request behind it,
    whose ``headers`` raises rather than returning None -- that is a direct
    call, not an HTTP one, so it is treated like stdio."""
    if ctx is None:
        return None
    try:
        return ctx.headers
    except ValueError:
        return None


def key(ctx: Context | None) -> str | None:
    """The resident key this request carries, or None if it carries none."""
    headers = _headers(ctx)
    if headers is None:
        return os.environ.get(ENV_KEY, "").strip() or None
    value = (_header(headers, "authorization") or "").strip()
    if not value:
        return None
    scheme, _, credential = value.partition(" ")
    if scheme.lower() != "bearer" or not credential.strip():
        raise residents.InvalidResidentKey("The Authorization header must be `Bearer <resident key>`.")
    return credential.strip()


def current(ctx: Context | None) -> dict:
    """The calling resident's profile. Raises ResidentRequired with no key,
    InvalidResidentKey with a bad one."""
    found = key(ctx)
    if found is None:
        raise residents.ResidentRequired("This needs a resident key, and the request didn't carry one.")
    return residents.authenticate(found)
