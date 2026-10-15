"""Resetting the whole world at once.

Every domain's bookings, orders, rentals, tickets and reservations -- and
the seats, rooms, cars and tables they hold -- are emptied, and the world clock goes back to
real time. Reference data (the CSV files) is untouched; it isn't state.

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
from maya.world import clock


def reset_world() -> dict:
    """Empty every domain and return the clock to real time. Returns how many
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
    return cleared
