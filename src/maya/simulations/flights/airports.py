"""The airports of Maya.

Reference data. Every city has exactly one airport, which removes a whole
class of pointless ambiguity while keeping multi-city planning interesting.
The source of truth is ``data/airports.csv``; it is loaded into SQLite on
init and read from there, so editing that file changes the world on next run.

Geography (coordinates, runway length, country, flavour text) lives in
.docs/WORLD.md for now, not here -- see that doc for the fuller picture.
"""

from __future__ import annotations

from typing import NamedTuple

from . import db

class Airport(NamedTuple):
    code: str
    city: str
    name: str

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "city": self.city,
            "airport": self.name,
        }


def _load() -> tuple[Airport, ...]:
    rows = db.connect().execute("SELECT code, city, name FROM airports").fetchall()
    return tuple(Airport(row["code"], row["city"], row["name"]) for row in rows)


AIRPORTS: tuple[Airport, ...] = _load()

BY_CODE: dict[str, Airport] = {a.code: a for a in AIRPORTS}


def get(code: str) -> Airport | None:
    return BY_CODE.get((code or "").strip().upper())


def resolve(value: str) -> Airport | None:
    """Accept an airport code or a city name, because agents supply both."""
    if not value:
        return None
    needle = value.strip().lower()
    for airport in AIRPORTS:
        if needle in (airport.code.lower(), airport.city.lower()):
            return airport
    matches = [a for a in AIRPORTS if needle in a.city.lower() or needle in a.name.lower()]
    return matches[0] if len(matches) == 1 else None


def search(query: str, limit: int = 10) -> list[Airport]:
    needle = (query or "").strip().lower()
    if not needle:
        return list(AIRPORTS)[:limit]
    scored = []
    for airport in AIRPORTS:
        haystack = f"{airport.code} {airport.city} {airport.name}".lower()
        if needle == airport.code.lower():
            score = 100
        elif needle == airport.city.lower():
            score = 90
        elif airport.city.lower().startswith(needle) or airport.name.lower().startswith(needle):
            score = 70
        elif needle in haystack:
            score = 40
        else:
            continue
        scored.append((score, airport.code, airport))
    scored.sort(key=lambda row: (-row[0], row[1]))
    return [airport for _, _, airport in scored[:limit]]
