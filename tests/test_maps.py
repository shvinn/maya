"""Aira's zone road graph: within-zone times and shortest paths."""

from __future__ import annotations

import csv

import pytest

from maya.world.geography import maps

SELF_EDGES = {
    row["from_zone"]: int(row["minutes"])
    for row in csv.DictReader(open(maps._GEOGRAPHY_DIR / "aira" / "zone_distances.csv", newline=""))
    if row["from_zone"] == row["to_zone"]
}


@pytest.mark.parametrize("zone", sorted(SELF_EDGES))
def test_within_zone_time_comes_from_the_self_edge(zone):
    # Regression: the self-edge used to lose to the path search's 0, so a
    # same-zone delivery got 0 courier minutes.
    assert maps.distance_minutes(zone, zone) == SELF_EDGES[zone] > 0


def test_routes_between_zones_never_include_local_travel_time():
    codes = [z.code for z in maps.ZONES]
    for a in codes:
        for b in codes:
            if a == b:
                continue
            for k in codes:
                if k in (a, b):
                    continue
                assert maps.distance_minutes(a, b) <= maps.distance_minutes(a, k) + maps.distance_minutes(k, b)


def test_distance_is_symmetric_and_direct_roads_are_used():
    assert maps.distance_minutes("AIR-OLD", "AIR-MKT") == maps.distance_minutes("AIR-MKT", "AIR-OLD") == 6


def test_zone_lookup_accepts_code_name_or_postal_code():
    assert maps.resolve_zone("air-old") == maps.resolve_zone("Old Aira") == maps.resolve_zone("101")
    assert maps.resolve_zone("Meghas") is None
