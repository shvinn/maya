# The car rental domain

Notes for anyone (human or AI agent) working with `cars_*` MCP tools —
things that aren't obvious from the tool descriptions alone. See the
[repo README](../../../../README.md) for install/usage; this page is about
what the simulation actually models, not how to run it.

## One location: the airport

There is a single rental desk, at Aira International in the Skyview zone
(`AIR-APT`), open 24/7. Every rental is picked up and dropped off there —
no one-way rentals, no city branches, and no tool takes a location.

## You rent a model, not a specific car

The fleet is 7 fictional makes/models (Kestrel, Marlin, Tamaru, Orca,
Vela), each with a number of identical cars. There are no plates or VINs.

## Model years are relative to today

A car's `year` is computed on every call as the current year minus the
model's age — a 1-year-old model is always last year's. Don't be surprised
when the same car shows a different year after New Year.

## Billing is per started 24 hours

Days are counted from the pickup time, not by calendar date: 10:00 to 10:00
the next day is 1 day; 10:00 to 11:00 the next day is 2. Each rental day
has its own price (`daily_prices`). Rentals are 1–30 days, booked up to 365
days ahead. Times are `YYYY-MM-DD HH:MM`.

## Price and availability move with time — but same-day always works

Each day fills up on its own over the 14 days before it, levelling off at
about 80% of the fleet, and prices rise up to 30% with it. Because it is an
airport desk, simulated demand never takes the last 2 cars of any model:
a walk-up rental for today always finds every model, until real bookings
take those last cars. Then it's `sold_out`. The price is locked once booked.

## Rules that can say no

- **Driver age:** the driver must be at least 21 (`driver_too_young`).
- **Cancellation:** free, full refund, any time before the pickup time.
  After pickup it fails with `too_late_to_cancel`. No fees, no partial
  refunds.

## Status is computed live, not stored

`RESERVED` until pickup, `PICKED_UP` until drop-off, then `RETURNED`.
`CANCELLED` is the only stored status.

## No payment, no extras

No deposit, no insurance, no fuel policy, no add-ons. `total_price` is
informational, same as every other domain. Prices are in bucks.
