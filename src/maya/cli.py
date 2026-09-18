"""The ``maya`` command.

Two ways to run the flights MCP server:

  uv run maya mcp flights     stdio transport, for Claude Desktop/Code-style
                               MCP client configs (spawns as a subprocess)
  uv run maya serve           HTTP transport on :6292, for anything that
                               talks to an MCP server over the network

There is no REST/OpenAPI surface yet -- only MCP is implemented. See
DECISIONS.md and the README for what's real versus what's still aspirational.
"""

from __future__ import annotations

import argparse
import sys

from .mcp_tools import mcp


def main() -> None:
    parser = argparse.ArgumentParser(prog="maya")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="Run the MCP server over HTTP.")
    # 6292 = MAYA on a phone keypad (M=6, A=2, Y=9, A=2) -- a memorable
    # default port, same spirit as 3000/8000/8080 for other frameworks.
    serve.add_argument("--port", type=int, default=6292)

    mcp_cmd = sub.add_parser("mcp", help="Run an MCP server over stdio.")
    mcp_cmd.add_argument("domain", choices=["flights"])

    args = parser.parse_args()

    if args.command == "serve":
        mcp.run(transport="streamable-http", port=args.port)
    elif args.command == "mcp":
        # Only "flights" exists today; the arg is there so a client config
        # naming the domain explicitly (as the README shows) keeps working
        # if more are added later.
        mcp.run(transport="stdio")
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
