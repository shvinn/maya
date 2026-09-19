# Contributing to Maya

Maya is a flight booking simulator for AI agents. This document is about
contributing to that: the flight toolset, the fictional island it runs on,
and the house style both follow.

## How it's built

Standard `src/` layout: `src/maya/` is the one installable package.

- `src/maya/simulations/<domain>/` (`flights/`, `delivery/`) is the domain
  logic — reference data access, booking/ordering rules, and the shared
  SQLite connection in that domain's own `db.py` — plus that domain's own
  `mcp_tools.py`, which is the thin `<domain>_*` MCP tool adapter for it.
  No business logic lives in a domain's `mcp_tools.py`; it calls straight
  into the rest of its own package.
- `src/maya/cli.py` is the `maya` command; `src/maya/mcp_tools.py` combines
  every domain's `MCPServer` (from each `simulations/<domain>/mcp_tools.py`)
  into one registry that `cli.py` dispatches by name. Neither defines any
  tools itself.
- Reference data (airports, airlines, aircraft, the weekly schedule) lives in
  `src/maya/simulations/flights/data/*.csv` — human-editable, reloaded into
  SQLite on every start. Edit a row there and the world changes; no code
  change needed.
- Bookings and seats sold are the one part of the world that is real,
  persisted state (SQLite, survives a restart). Everything else is derived
  fresh from the CSV files.

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
```

Code style: standard library first, type hints throughout, docstrings that
explain *why*. Comments earn their place by explaining a decision, not by
narrating the next line.

## Reporting a bug

Include the date/time you ran it, the tool calls in order, and (if relevant)
which row of which CSV file is involved. Since reference data is plain files
and bookings are inspectable in `maya.db`, most bugs are reproducible directly
from that.
