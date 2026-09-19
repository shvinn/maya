"""Zones and road distances, city by city.

Domain-agnostic "world engine" infrastructure (VISION.md: "small,
domain-agnostic, shared by everything") -- distinct from
simulations/<domain>/, which owns what's actually being sold. Zones have no
price and are not inventory; any domain (delivery today, others later) can
depend on this without owning it.

Each city gets its own subdirectory here holding that city's zones.csv and
zone_distances.csv (only aira/ exists so far). A city with no subdirectory
simply has no zones, and is therefore not deliverable -- there is no
separate "is this city enabled" flag to keep in sync.

Zones and their road distances are static reference facts with no persisted
state, so this is plain CSV loaded into memory at import time -- no SQLite,
unlike bookings or (later) any domain with real depleting state.

The road graph in each zone_distances.csv is deliberately sparse: only real
roads, plus one self-edge per zone, are hand-authored. The distance between
two zones with no direct road is derived once via Floyd-Warshall at import
time and cached, so a pair missing from the CSV isn't an error -- it just
means there's no direct road, and the shortest path through the graph is
used instead.
"""

from __future__ import annotations

import csv
from pathlib import Path
from typing import NamedTuple

_GEOGRAPHY_DIR = Path(__file__).resolve().parent


class Zone(NamedTuple):
    code: str
    city: str
    name: str
    postal_code: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "city": self.city,
            "name": self.name,
            "postal_code": self.postal_code,
        }


def _city_dirs() -> list[Path]:
    return sorted(p for p in _GEOGRAPHY_DIR.iterdir() if p.is_dir() and not p.name.startswith("__"))


def _load_zones() -> tuple[Zone, ...]:
    zones = []
    for city_dir in _city_dirs():
        csv_path = city_dir / "zones.csv"
        if not csv_path.exists():
            continue
        with open(csv_path, newline="") as f:
            for row in csv.DictReader(f):
                zones.append(Zone(row["code"], row["city"], row["name"], row["postal_code"]))
    return tuple(zones)


def _load_edges() -> list[tuple[str, str, int]]:
    edges = []
    for city_dir in _city_dirs():
        csv_path = city_dir / "zone_distances.csv"
        if not csv_path.exists():
            continue
        with open(csv_path, newline="") as f:
            for row in csv.DictReader(f):
                edges.append((row["from_zone"], row["to_zone"], int(row["minutes"])))
    return edges


ZONES: tuple[Zone, ...] = _load_zones()
BY_CODE: dict[str, Zone] = {z.code: z for z in ZONES}


def _shortest_paths() -> dict[tuple[str, str], int]:
    """All-pairs shortest minutes over the sparse road graph (Floyd-Warshall,
    run once at import so distance_minutes() stays an O(1) lookup)."""
    codes = [z.code for z in ZONES]
    inf = float("inf")
    dist: dict[tuple[str, str], float] = {(a, b): (0 if a == b else inf) for a in codes for b in codes}
    for a, b, minutes in _load_edges():
        dist[(a, b)] = min(dist[(a, b)], minutes)
        dist[(b, a)] = min(dist[(b, a)], minutes)
    for k in codes:
        for i in codes:
            if dist[(i, k)] == inf:
                continue
            for j in codes:
                via_k = dist[(i, k)] + dist[(k, j)]
                if via_k < dist[(i, j)]:
                    dist[(i, j)] = via_k
    return {pair: int(minutes) for pair, minutes in dist.items() if minutes != inf}


_SHORTEST: dict[tuple[str, str], int] = _shortest_paths()


def zones_in(city: str) -> list[Zone]:
    needle = (city or "").strip().lower()
    return [z for z in ZONES if z.city.lower() == needle]


def deliverable_cities() -> set[str]:
    """Cities with at least one zone -- there is no separate enabled flag."""
    return {z.city for z in ZONES}


def resolve_zone(value: str) -> Zone | None:
    """Accept a zone code, zone name, or postal code, because agents supply
    any of the three."""
    if not value:
        return None
    needle = value.strip().lower()
    for zone in ZONES:
        if needle in (zone.code.lower(), zone.name.lower(), zone.postal_code.lower()):
            return zone
    matches = [z for z in ZONES if needle in z.name.lower()]
    return matches[0] if len(matches) == 1 else None


def distance_minutes(zone_a: str, zone_b: str) -> int:
    """Courier minutes between two zone codes via the shortest road path.

    Raises ValueError if either code isn't a known zone, or if no path
    exists between them (only possible across disconnected city graphs)."""
    key = (zone_a, zone_b)
    if key not in _SHORTEST:
        raise ValueError(f"No known route between {zone_a!r} and {zone_b!r}.")
    return _SHORTEST[key]
