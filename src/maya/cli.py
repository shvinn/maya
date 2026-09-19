"""The ``maya`` command.

Two ways to run an MCP server:

  uv run maya mcp flights       stdio transport, for Claude Desktop/Code-style
                                 MCP client configs (spawns as a subprocess)
  uv run maya mcp delivery      same, for the delivery domain
  uv run maya serve             HTTP transport on :6292, one domain
                                 (flights by default; --domain delivery for
                                 the other one)
  uv run maya serve --domain all
                                 HTTP transport on :6292, every domain at
                                 once -- each still its own MCP endpoint at
                                 its own path (/flights/mcp, /delivery/mcp),
                                 not a merged tool list. See
                                 mcp_tools.combined_app for what this is and
                                 is not.

There is no REST/OpenAPI surface yet -- only MCP is implemented. See
DECISIONS.md and the README for what's real versus what's still aspirational.
"""

from __future__ import annotations

import argparse
import sys

from .mcp_tools import SERVERS as _SERVERS
from .mcp_tools import combined_app


def main() -> None:
    parser = argparse.ArgumentParser(prog="maya")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the MCP server over HTTP.")
    # 6292 = MAYA on a phone keypad (M=6, A=2, Y=9, A=2) -- a memorable
    # default port, same spirit as 3000/8000/8080 for other frameworks.
    serve.add_argument("--port", type=int, default=6292)
    serve.add_argument("--domain", choices=[*_SERVERS, "all"], default="flights")

    mcp_cmd = sub.add_parser("mcp", help="Run an MCP server over stdio.")
    mcp_cmd.add_argument("domain", choices=list(_SERVERS))

    args = parser.parse_args()

    if args.command == "serve":
        if args.domain == "all":
            import uvicorn

            uvicorn.run(combined_app(), host="127.0.0.1", port=args.port)
        else:
            _SERVERS[args.domain].run(transport="streamable-http", port=args.port)
    elif args.command == "mcp":
        _SERVERS[args.domain].run(transport="stdio")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
