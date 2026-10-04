# Contributing to Maya

Maya is an API simulator for AI agents: a fictional world of everyday
services — flights, hotels, car rental, food delivery and events — exposed as MCP
tools and running locally. This document is about contributing to that: the
domain toolsets, the fictional world they run in, and the house style they
all follow.

## How it's built

Standard `src/` layout: `src/maya/` is the one installable package.

- `src/maya/simulations/<domain>/` (`flights/`, `delivery/`, `hotels/`,
  `cars/`, `events/`) is the domain logic — reference data access, booking/ordering rules, and the shared
  SQLite connection in that domain's own `db.py` — plus that domain's own
  `mcp_tools.py`, which is the thin `<domain>_*` MCP tool adapter for it.
  No business logic lives in a domain's `mcp_tools.py`; it calls straight
  into the rest of its own package.
- `src/maya/cli.py` is the `maya` command; `src/maya/mcp_tools.py` combines
  every domain's `MCPServer` (from each `simulations/<domain>/mcp_tools.py`)
  into one registry that `cli.py` dispatches by name. Neither defines any
  tools itself.
- `src/maya/world/` is shared, domain-agnostic world data — today, Aira's
  zones and road graph, used by delivery and hotels.
- Reference data (airports and the weekly schedule, vendors and menus,
  hotels and room types, the rental fleet, venues and event series) lives in each domain's
  `data/*.csv` — human-editable, reloaded into SQLite on every start. Edit a
  row there and the world changes; no code change needed.
- Bookings, orders and rentals — and the seats, rooms and cars they hold —
  are the one part of the world that is real, persisted state (SQLite,
  survives a restart). Everything else is derived fresh from the CSV files.

## House rules

- **No new dependencies** without a note in the PR explaining why the standard
  library will not do. A workshop laptop on hotel wifi has to install this.
- **Never import an agent framework or a model client.** Maya is the
  environment, not the agent.
- **Errors are part of the design.** A new failure mode needs a code, a message
  that names the specific thing, and a hint that is honest about whether
  retrying will help.
- **Character must be mechanical.** "Puffin Air is unreliable" means a lower
  punctuality number that changes outcomes, not an adjective in a description.

## Development

```bash
git clone https://github.com/<you>/maya.git
cd maya
uv sync                  # install, including dev extras
uv run pytest            # tests
uv run maya serve        # MCP over HTTP on :6292
docker build -t maya .   # the Docker image, as CI publishes it
```

CI smoke-tests the image on every PR before anything is published: it starts
the container and checks every domain's endpoint answers (see
`.github/scripts/docker_smoke_test.py`). To run the same check locally:

```bash
docker network create smoke
docker run -d --name maya --network smoke maya
docker run --rm --network smoke -v "$PWD/.github/scripts:/smoke:ro" \
  --entrypoint python maya /smoke/docker_smoke_test.py http://maya:6292
docker rm -f maya && docker network rm smoke
```

Tests live in `tests/`, one file per domain, and run on Python 3.10 and 3.13
in CI. Two things make them deterministic (see `tests/conftest.py`): every
run uses a throwaway database via `MAYA_DB_PATH`, never your `maya.db`, and
the clock is frozen at a fixed Monday morning, moved only by the `clock`
fixture. A test for a time-based rule should set the clock, not depend on
when it runs.

Code style: standard library first, type hints throughout, docstrings that
explain *why*. Comments earn their place by explaining a decision, not by
narrating the next line.

## Reporting a bug

Include the date/time you ran it, the tool calls in order, and (if relevant)
which row of which CSV file is involved. Since reference data is plain files
and bookings are inspectable in `maya.db`, most bugs are reproducible directly
from that.
