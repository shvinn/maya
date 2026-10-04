<h1 align="center">Maya</h1>

<p align="center">
  <em>A fictional island, exposed as MCP tools —<br/>
  so you can build and test AI agents without renting a real one.</em>
</p>

## Overview

Maya is a simulated island nation for AI agents to act in, exposed as MCP
tools and running locally. Four domains exist today:

- **Flights** — search, book, retrieve and cancel flights on a fictional
  airline network, direct or with one stop. Seats deplete for real, fares
  move with time and demand.
  See [`src/maya/simulations/flights/README.md`](src/maya/simulations/flights/README.md)
  for what's not obvious from the tool descriptions alone.
- **Delivery** — browse vendors and menus, place and cancel food delivery
  orders within Aira, the capital. See
  [`src/maya/simulations/delivery/README.md`](src/maya/simulations/delivery/README.md)
  for the same — in particular, delivery addresses are zones, not street
  addresses.
- **Hotels** — search, book, retrieve and cancel hotel rooms in Aira. Rooms
  sell per night and prices rise as a night approaches. See
  [`src/maya/simulations/hotels/README.md`](src/maya/simulations/hotels/README.md).
- **Cars** — rent a car from the one desk at Aira International, same-day
  included. See
  [`src/maya/simulations/cars/README.md`](src/maya/simulations/cars/README.md).

Each domain has its own bookings/orders that persist across restarts
(SQLite) — so failures (sold-out flights or rooms, non-refundable fares,
cancellation windows, undeliverable zones) are real rules to discover, not canned
responses.

```
your agent  ──►  MCP (stdio or HTTP)  ──►  Maya flights / delivery / hotels / cars
```

## Install

```bash
git clone https://github.com/<you>/maya.git
cd maya
uv sync
```

## Usage

Each domain is its own MCP server. Run one over HTTP:

```bash
uv run maya serve                     # flights, MCP over streamable HTTP on :6292
uv run maya serve --domain delivery   # delivery, same, :6292
uv run maya serve --domain hotels     # hotels, same, :6292
uv run maya serve --domain cars       # car rental, same, :6292
uv run maya serve --domain all        # all at once, one port, one path each:
                                       #   http://localhost:6292/flights/mcp
                                       #   http://localhost:6292/delivery/mcp
                                       #   http://localhost:6292/hotels/mcp
                                       #   http://localhost:6292/cars/mcp
```

Or as stdio servers, e.g. for Claude Desktop / Claude Code:

```json
{
  "mcpServers": {
    "maya-flights": { "command": "uv", "args": ["run", "maya", "mcp", "flights"] },
    "maya-delivery": { "command": "uv", "args": ["run", "maya", "mcp", "delivery"] },
    "maya-hotels": { "command": "uv", "args": ["run", "maya", "mcp", "hotels"] },
    "maya-cars": { "command": "uv", "args": ["run", "maya", "mcp", "cars"] }
  }
}
```

MCP is the only interface today — no REST/OpenAPI yet.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
