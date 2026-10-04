# The hotels domain

Notes for anyone (human or AI agent) working with `hotels_*` MCP tools —
things that aren't obvious from the tool descriptions alone, and are easy to
assume incorrectly if you're used to a real hotel booking site. See the
[repo README](../../../../README.md) for install/usage; this page is about
what the simulation actually models, not how to run it.

## Hotels only exist in Aira

10 hotels, all in Aira, one or two per zone (see
[`world/geography/aira/`](../../world/geography/aira/)). A hotel's location
is its zone — there is no street address. `zone` in
`hotels_search_availability` accepts a zone code (`AIR-APT`), name
("Skyview") or postal code (`109`). None of Maya's other cities has hotels
yet. A hotel's zone is the same zone delivery uses, so "order food to my
hotel" means delivering to that zone.

## Price and availability move with time

Each room type has a fixed number of rooms, sold **per night**. Every night
fills up on its own as it approaches (simulated other guests): empty 30+
days out, about 40% full 15 days out, and about 85% full on the night
itself — like a busy real city hotel. Background demand never takes the
last room; only real bookings can sell a room type out completely, so small
room types (suites, the guesthouse) are the ones that run out.

The price per night is the room type's base rate plus up to 30%, in step
with how full that night is (about +25% from background demand alone; real
bookings push it the rest of the way). So:

- the same search can return different prices and `rooms_left` later on;
- each night of a multi-night stay has its own price (`nightly_prices`);
- a stay is only available if **every** night has a room, and `sold_out`
  names the first night that doesn't.

The price is locked in when you book; later changes don't touch it.

## One booking is one room

`hotels_book_room` books a single room. `guests` can't exceed the room
type's `max_guests` (`too_many_guests`) — for a bigger party, book more
rooms. There is no rate plan to choose, no hold, and no payment step:
`total_price` is informational, same as flights and delivery.

## Cancellation is per hotel, and all-or-nothing

Each hotel has a `cancel_cutoff_hours` before check-in (15:00 on the
check-in date). Cancel before that for a full refund; inside it,
`hotels_cancel_booking` fails with `too_late_to_cancel`. Two hotels (the
hostel and the budget lodge) are non-refundable and always fail with
`not_refundable`. There are no partial refunds or cancellation fees.

## Status is computed live, not stored

`CONFIRMED` until check-in (15:00 on the check-in date), `CHECKED_IN` until
check-out (11:00 on the check-out date), then `CHECKED_OUT`. `CANCELLED` is
the only stored status. A booking made today for tonight may already show
`CHECKED_IN`. All times are Maya time (MYT, UTC−08:00), which
the `world` server can speed up or skip ahead — see "Maya time" in the repo
README.

## Dates

`YYYY-MM-DD`. `check_out` is the day you leave, so a stay from the 1st to
the 3rd is 2 nights. Check-in can be today, up to 365 days ahead; a stay is
1–14 nights. Anything else is `invalid_dates`.

## Names are fictional, currency is bucks

No hotel is a real hotel or chain. Prices are in bucks, Maya's fictional
currency, on the same scale as flights and delivery.
