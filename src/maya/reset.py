"""Resetting the whole world at once.

Every domain's bookings, orders, rentals, tickets and reservations -- and
the seats, rooms, cars and tables they hold -- are emptied, flight status
overrides are dropped, the world clock goes back to real time, and the
world gets a new seed (see maya.world.seed), so its luck is new too.
Reference data (the CSV files) is untouched; it isn't state. Residents
and their keys are kept too -- they are who is in the world, not what
happened in it -- but every wallet goes back to its opening balance.

Bookings and the clock go together on purpose. Time only moves forward,
except here: turning the clock back while bookings remain would leave
bookings made in the moved-ahead time looking like they're in the future.
"""

from __future__ import annotations

import sqlite3

from maya import storage
from maya.simulations.cars import rentals
from maya.simulations.delivery import orders
from maya.simulations.dining import reservations
from maya.simulations.events import tickets
from maya.simulations.flights import bookings as flight_bookings
from maya.simulations.hotels import bookings as hotel_bookings
from maya.simulations.residents import residents
from maya.world import clock, seed as world_seed

_DOMAINS = {
    "flight_bookings": flight_bookings,
    "hotel_bookings": hotel_bookings,
    "car_rentals": rentals,
    "delivery_orders": orders,
    "event_bookings": tickets,
    "dining_reservations": reservations,
}


def reset_world(seed: int | None = None) -> dict:
    """Empty every domain, return the clock to real time and reseed the
    world -- with ``seed``, else MAYA_SEED, else at random. Returns how many
    records each domain had.

    All of it is one transaction on one connection, so it is all or
    nothing, and nothing can happen in between: a booking that landed after
    one domain was emptied but before the wallets were would keep its seat
    and lose its charge -- a booking never paid for, and a refund later out
    of nothing.
    """
    # Every domain's tables must exist before the transaction takes the
    # write lock: a domain creates them on its first connection, and that
    # would need the lock too.
    for domain in _DOMAINS.values():
        domain.db.connect()
    residents.ready()

    conn = sqlite3.connect(storage.db_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        with storage.transaction(conn):
            cleared = {name: domain.reset(conn) for name, domain in _DOMAINS.items()}
            clock._reset(conn)  # only ever here, with the bookings: see the module docstring
            world_seed._reset(conn, seed)  # a new world's luck: see maya.world.seed
            cleared["resident_transactions"] = residents.reset(conn)
    finally:
        conn.close()
    residents.restamp_opening_balances()
    return cleared
