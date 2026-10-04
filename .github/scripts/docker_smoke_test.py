"""Smoke test for the Docker image: does it start, and does every domain answer?

CI runs this from a second container on the same Docker network as the one
under test, using the image's own Python and the MCP client it already ships
-- so nothing extra is installed, and reaching the server by container name
also exercises the DNS-rebinding setup that a localhost-only check would miss.

Not a behaviour test: it only proves the image starts, every domain's HTTP
endpoint lists tools, and the stdio entrypoint works. Rules and prices are
the job of real tests.

    python docker_smoke_test.py http://maya:6292
"""

from __future__ import annotations

import asyncio
import sys
import time

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client

DOMAINS = ("flights", "delivery", "hotels", "cars", "events")
STARTUP_TIMEOUT_SECONDS = 30


async def list_tools_http(base_url: str, domain: str) -> list[str]:
    async with streamable_http_client(f"{base_url}/{domain}/mcp") as (read, write, *_):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return [t.name for t in (await session.list_tools()).tools]


async def list_tools_stdio(domain: str) -> list[str]:
    params = StdioServerParameters(command="maya", args=["mcp", domain])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            return [t.name for t in (await session.list_tools()).tools]


def check(label: str, domain: str, names: list[str]) -> bool:
    wrong = [n for n in names if not n.startswith(f"{domain}_")]
    ok = bool(names) and not wrong
    detail = f"{len(names)} tools" if ok else f"{len(names)} tools, unexpected: {wrong or 'none listed'}"
    print(f"{'PASS' if ok else 'FAIL'}  {label:<6} {domain:<9} {detail}")
    return ok


async def main(base_url: str) -> int:
    deadline = time.monotonic() + STARTUP_TIMEOUT_SECONDS
    while True:
        try:
            first = await list_tools_http(base_url, DOMAINS[0])
            break
        except Exception as e:  # server still starting
            if time.monotonic() > deadline:
                print(f"FAIL  server at {base_url} not reachable after {STARTUP_TIMEOUT_SECONDS}s: {e!r}")
                return 1
            await asyncio.sleep(1)

    results = [check("http", DOMAINS[0], first)]
    for domain in DOMAINS[1:]:
        results.append(check("http", domain, await list_tools_http(base_url, domain)))
    results.append(check("stdio", "cars", await list_tools_stdio("cars")))
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "http://localhost:6292")))
