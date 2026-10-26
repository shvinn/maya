"""The ``maya`` command.

Two ways to run an MCP server:

  uv run maya mcp flights       stdio transport, for Claude Desktop/Code-style
                                 MCP client configs (spawns as a subprocess)
  uv run maya mcp delivery      same, for the delivery domain
  uv run maya mcp hotels        same, for the hotels domain
  uv run maya mcp cars          same, for the car rental domain
  uv run maya mcp events        same, for the events domain
  uv run maya mcp dining        same, for the dining domain
  uv run maya mcp residents     same, for your own resident profile and
                                 wallet (read-only)
  uv run maya mcp world         same, for the world controls (clock, reset)
  uv run maya serve             HTTP transport on :6292, one domain
                                 (flights by default; --domain delivery,
                                 hotels, cars, events, dining, residents or
                                 world for
                                 the others)
  uv run maya serve --domain all
                                 HTTP transport on :6292, every domain at
                                 once -- each still its own MCP endpoint at
                                 its own path (/flights/mcp, /delivery/mcp,
                                 /hotels/mcp, /cars/mcp, /events/mcp,
                                 /dining/mcp, /residents/mcp, /world/mcp),
                                 not a merged tool list. See
                                 mcp_tools.combined_app for what this is and
                                 is not.

And one to start the world over:

  uv run maya reset             empty every domain's bookings, orders,
                                 rentals, tickets and reservations, return
                                 the clock to real time and reseed the
                                 world (--seed N to replay one; see
                                 maya.reset)

And to manage residents -- admin operations, so only here, never as MCP
tools (see maya.simulations.residents.residents):

  uv run maya residents create --first-name Lena --last-name Okafor --age 34
                                 a new resident and wallet; prints the
                                 resident key, once
  uv run maya residents list    everyone, with their balances
  uv run maya residents show R-000001
  uv run maya residents update R-000001 --age 35
  uv run maya residents rotate-key R-000001
  uv run maya residents add-funds R-000001 1500
  uv run maya residents transactions R-000001

In Docker, run them in the container, against its database:
``docker compose exec maya maya residents create ...``

``serve`` binds 127.0.0.1 unless told otherwise (``--host``, or the
``MAYA_HOST`` environment variable -- the Docker image sets it to 0.0.0.0 so
the server is reachable from outside the container).

There is no REST/OpenAPI surface yet -- only MCP is implemented. See the
README for what's real versus what's still aspirational.
"""

from __future__ import annotations

import argparse
import os
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
    serve.add_argument("--host", default=os.environ.get("MAYA_HOST", "127.0.0.1"))
    serve.add_argument("--domain", choices=[*_SERVERS, "all"], default="flights")

    mcp_cmd = sub.add_parser("mcp", help="Run an MCP server over stdio.")
    mcp_cmd.add_argument("domain", choices=list(_SERVERS))

    reset = sub.add_parser("reset", help="Empty every domain, return the clock to real time and reseed the world.")
    reset.add_argument("--seed", type=int, help="replay a world: its delays and cancellations (default: MAYA_SEED, else random)")

    _add_residents_parser(sub)

    args = parser.parse_args()

    if args.command == "serve":
        if args.domain == "all":
            import uvicorn

            uvicorn.run(combined_app(args.host), host=args.host, port=args.port)
        else:
            _SERVERS[args.domain].run(transport="streamable-http", host=args.host, port=args.port)
    elif args.command == "mcp":
        _SERVERS[args.domain].run(transport="stdio")
    elif args.command == "reset":
        from .reset import reset_world
        from .world import seed

        cleared = reset_world(args.seed)
        print("World reset. Cleared " + ", ".join(f"{n} {kind.replace('_', ' ')}" for kind, n in cleared.items()) + ".")
        print(f"Seed: {seed.get()}")
    elif args.command == "residents":
        _residents(args)
    else:
        parser.print_help()
        sys.exit(1)


def _add_residents_parser(sub: argparse._SubParsersAction) -> None:
    from .simulations.residents.residents import DEFAULT_OPENING_BALANCE, GENDERS

    residents_cmd = sub.add_parser("residents", help="Create and manage residents and their wallets (admin).")
    actions = residents_cmd.add_subparsers(dest="action", required=True)

    create = actions.add_parser("create", help="Create a resident; prints their key, once.")
    create.add_argument("--first-name", required=True)
    create.add_argument("--last-name", required=True)
    create.add_argument("--age", type=int, required=True)
    create.add_argument("--gender", choices=GENDERS, default="unspecified")
    create.add_argument("--opening-balance", type=int, default=DEFAULT_OPENING_BALANCE)
    create.add_argument("--email", help="default: first.last@example.com, numbered if taken")
    create.add_argument(
        "--url", default="http://localhost:6292",
        help="where clients reach Maya, for the config line printed (default: %(default)s)",
    )

    actions.add_parser("list", help="Every resident, with their balance.")

    for name, help_text in (
        ("show", "One resident's profile and balance."),
        ("rotate-key", "Give a resident a new key; the old one stops working at once."),
        ("transactions", "Every transaction in a resident's wallet, oldest first."),
    ):
        actions.add_parser(name, help=help_text).add_argument("resident_id")

    update = actions.add_parser("update", help="Change a resident's profile.")
    update.add_argument("resident_id")
    update.add_argument("--first-name")
    update.add_argument("--last-name")
    update.add_argument("--age", type=int)
    update.add_argument("--gender", choices=GENDERS)
    update.add_argument("--email")

    add_funds = actions.add_parser("add-funds", help="Credit a resident's wallet.")
    add_funds.add_argument("resident_id")
    add_funds.add_argument("amount", type=int)


def _residents(args: argparse.Namespace) -> None:
    from .simulations.residents import residents

    def describe(r: dict) -> str:
        return f"{r['resident_id']}  {r['first_name']} {r['last_name']} ({r['gender']}, {r['age']})  {r['email']}"

    def balance(resident_id: str) -> str:
        on_hold = residents.held(resident_id)
        total = f"{residents.balance(resident_id)} {residents.CURRENCY}"
        return total + (f" ({on_hold} held, {residents.available(resident_id)} available)" if on_hold else "")

    def show_key(key: str, url: str | None = None) -> None:
        print("\nResident key (shown once -- save it now):")
        print(f"  {key}")
        if url:
            print("\nTo use it from Claude Code:")
            print(
                f"  claude mcp add --transport http maya-flights {url.rstrip('/')}/flights/mcp "
                f'--header "Authorization: Bearer {key}"'
            )

    try:
        if args.action == "create":
            r = residents.create(
                args.first_name, args.last_name, args.age, args.gender, args.opening_balance, args.email
            )
            print(f"Created {describe(r)}. Balance: {r['balance']} {residents.CURRENCY}.")
            show_key(r["key"], args.url)
        elif args.action == "list":
            everyone = residents.list_residents()
            for r in everyone:
                print(f"{describe(r)}  {balance(r['resident_id'])}")
            if not everyone:
                print("No residents yet. Create one with `maya residents create`.")
        elif args.action == "show":
            r = residents.get(args.resident_id)
            if r is None:
                raise residents.NotFound(f"No resident {args.resident_id!r}.")
            print(f"{describe(r)}\nBalance: {balance(r['resident_id'])}\nCreated: {r['created_at']}")
        elif args.action == "update":
            changes = {
                field: value
                for field, value in (
                    ("first_name", args.first_name), ("last_name", args.last_name),
                    ("age", args.age), ("gender", args.gender), ("email", args.email),
                )
                if value is not None
            }
            if not changes:
                raise residents.InvalidProfile(
                    "Nothing to change: give --first-name, --last-name, --age, --gender or --email."
                )
            print(f"Updated {describe(residents.update(args.resident_id, **changes))}.")
        elif args.action == "rotate-key":
            key = residents.rotate_key(args.resident_id)
            print(f"New key for {args.resident_id.upper()}; the old one no longer works.")
            show_key(key)
        elif args.action == "add-funds":
            result = residents.add_funds(args.resident_id, args.amount)
            print(f"Added {args.amount} {residents.CURRENCY}. Balance: {result['balance']} {residents.CURRENCY}.")
        elif args.action == "transactions":
            for t in residents.list_transactions(args.resident_id):
                print(
                    f"{t['transaction_id']}  {t['maya_time']}  {t['kind']:<15} {t['amount']:>+8}"
                    + (f"  {t['domain']} {t['reference']}" if t["reference"] else "")
                    + (f"  until {t['expires_at']}" if t.get("expires_at") else "")
                )
            print(f"Balance: {balance(args.resident_id)}")
    except residents.ResidentError as e:
        print(f"error: {e}" + (f"\n{e.hint}" if e.hint else ""), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
