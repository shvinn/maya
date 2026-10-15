"""Resetting the whole world at once.

Every domain's bookings, orders, rentals, tickets and reservations -- and
the seats, rooms, cars and tables they hold -- are emptied, flight status
overrides are dropped, the world clock goes back to real time, and the
world gets a new seed (see maya.world.seed), so its luck is new too.
Reference data (the CSV files) is untouched; it isn't state.

Bookings and the clock go together on purpose. Time only moves forward,
except here: turning the clock back while bookings remain would leave
bookings made in the moved-ahead time looking like they're in the future.
"""

from __future__ import annotations

from maya.simulations.cars import rentals
from maya.simulations.delivery import orders
from maya.simulations.dining import reservations
from maya.simulations.events import tickets
from maya.simulations.flights import bookings as flight_bookings
from maya.simulations.hotels import bookings as hotel_bookings
from maya.world import clock, seed as world_seed


def reset_world(seed: int | None = None) -> dict:
    """Empty every domain, return the clock to real time and reseed the
    world -- with ``seed``, else MAYA_SEED, else at random. Returns how many
    records each domain had."""
    cleared = {
        "flight_bookings": flight_bookings.reset(),
        "hotel_bookings": hotel_bookings.reset(),
        "car_rentals": rentals.reset(),
        "delivery_orders": orders.reset(),
        "event_bookings": tickets.reset(),
        "dining_reservations": reservations.reset(),
    }
    clock._reset()  # only ever here, with the bookings: see the module docstring
    world_seed._reset(seed)  # a new world's luck: see maya.world.seed
    return cleared
