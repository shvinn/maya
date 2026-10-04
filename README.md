<h1 align="center">Maya</h1>

<p align="center">
  <em>An API simulator.<br/>
  A SimCity.<br/>
  The Matrix — for AI agents.</em>
</p>

## Overview

Maya is an API simulator for AI agents: a fictional world of everyday
services — flights, hotels, car rental, food delivery and events — exposed as MCP
tools and running locally. Five domains exist today:

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
- **Events** — comedy, concerts, lectures, theatre, family shows, films and
  workshops at venues across Aira. Popular shows sell out; some are 18+. See
  [`src/maya/simulations/events/README.md`](src/maya/simulations/events/README.md).

Each domain has its own bookings/orders that persist across restarts
(SQLite) — so failures (sold-out flights or rooms, non-refundable fares,
cancellation windows, undeliverable zones) are real rules to discover, not canned
responses.

```mermaid
flowchart LR
    agent["Your AI agent"] -->|"MCP · stdio or HTTP"| maya

    subgraph maya["Maya"]
        flights["Flights"]
        delivery["Delivery"]
        hotels["Hotels"]
        cars["Cars"]
        events["Events"]
    end

    clock(["World clock · MYT · scalable"]) -.->|"now"| maya

    csv["World data (CSV)<br/>airports · vendors · hotels · fleet · shows"] -.->|"loaded on start"| maya
    maya <-->|"real state"| db[("maya.db<br/>bookings · orders · rentals · tickets")]
```

## Install

```bash
git clone https://github.com/<you>/maya.git
cd maya
uv sync
```

Or run it straight from GitHub without cloning:

```bash
uvx --from git+https://github.com/shvinn/maya maya serve --domain all
```

Bookings, orders and rentals are kept in `maya.db` — at the repo root when
running from a clone, otherwise in the folder you start the server from. Set
`MAYA_DB_PATH` to put it somewhere else.

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
                                       #   http://localhost:6292/events/mcp
                                       #   http://localhost:6292/world/mcp
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

## Maya time

Maya runs on its own clock: **Mayan Meridian Time (MYT), a fixed UTC−08:00**
with no daylight saving. Every deadline, status and price uses it, so Maya
agrees on "now" whether it runs on your laptop, in Docker or in CI.

By default Maya time follows real time 1:1. To test something that takes
hours or days — a delivery arriving, a check-out, a show ending — speed it up
or skip ahead instead of waiting:

| Tool (on the `world` server) | Effect |
| --- | --- |
| `world_get_time` | What time it is in Maya (also on every domain server) |
| `world_set_time_scale(scale)` | `1` real time, `60` one Maya hour per real minute, `1440` a Maya day per minute, `0` frozen |
| `world_advance_time(minutes, hours, days)` | Skip ahead instantly — forward only |
| `world_reset_time` | Back to real time at 1:1 |

At scale 60, a 45-minute delivery arrives in 45 real seconds. Set a scale at
startup with `MAYA_TIME_SCALE=60` (works in Docker too). The controls live on
their own `world` server (`maya mcp world`, `/world/mcp`) so the agent you're
testing — connected to a domain server — can read the time but can't skip
past its own deadlines.

The clock is saved in `maya.db` with the bookings, so Maya time carries on
across restarts — and keeps running against real time while the server is
stopped. Freeze it first (scale `0`) if you want to pick up exactly where
you left off.

## Run with Docker

No clone or Python needed. One image serves every domain:

```bash
docker run -p 127.0.0.1:6292:6292 -v maya-data:/data ghcr.io/shvinn/maya
                                       # all domains, one path each:
                                       #   http://localhost:6292/flights/mcp
                                       #   http://localhost:6292/delivery/mcp
                                       #   http://localhost:6292/hotels/mcp
                                       #   http://localhost:6292/cars/mcp
                                       #   http://localhost:6292/events/mcp
                                       #   http://localhost:6292/world/mcp
docker run -p 127.0.0.1:6292:6292 -v maya-data:/data ghcr.io/shvinn/maya serve --domain hotels
docker compose up                      # same as the first, from a clone
```

Or as stdio servers for an MCP client config:

```json
{
  "mcpServers": {
    "maya-flights": {
      "command": "docker",
      "args": ["run", "-i", "--rm", "-v", "maya-data:/data", "ghcr.io/shvinn/maya", "mcp", "flights"]
    }
  }
}
```

- Bookings, orders and rentals live in the `maya-data` volume, so they
  survive restarts and are shared by every container using it. Remove the
  volume (`docker volume rm maya-data`) for a fresh world.
- Publish the port on `127.0.0.1` as above: the server has no
  authentication.
- Images are built for `linux/amd64` and `linux/arm64`, which covers Linux,
  macOS (Intel and Apple Silicon) and Windows (x64 and ARM, via Docker
  Desktop's default Linux containers).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
