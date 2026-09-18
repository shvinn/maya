<h1 align="center">Maya</h1>

<p align="center">
  <em>A fictional island's flight network, exposed as MCP tools —<br/>
  so you can build and test travel-booking AI agents without renting a real one.</em>
</p>

## Overview

Maya is a flight booking simulator for AI agents: search, book, retrieve and
cancel flights on a fictional airline network, exposed as MCP tools and
running locally. Seats deplete for real, fares move with time and demand, and
bookings persist across restarts (SQLite) — so failures (sold-out flights,
non-refundable fares, cancellation windows) are real rules to discover, not
canned responses.

```
your agent  ──►  MCP (stdio or HTTP)  ──►  Maya flights
```

## Install

```bash
git clone https://github.com/<you>/maya.git
cd maya
uv sync
```

## Usage

```bash
uv run maya serve            # MCP over streamable HTTP on :6292
```

Or as a stdio server, e.g. for Claude Desktop / Claude Code:

```json
{
  "mcpServers": {
    "maya-flights": { "command": "uv", "args": ["run", "maya", "mcp", "flights"] }
  }
}
```

MCP is the only interface today — no REST/OpenAPI yet.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
