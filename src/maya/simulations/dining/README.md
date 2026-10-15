# The dining domain

Notes for anyone (human or AI agent) working with `dining_*` MCP tools —
things that aren't obvious from the tool descriptions alone. See the
[repo README](../../../../README.md) for install/usage; this page is about
what the simulation actually models, not how to run it.

## The restaurants are the same ones delivery uses

Restaurants and their menus live in
[`world/places/aira/`](../../world/places/aira/), not in any one domain.
Dining keeps only its own terms in `data/dining.csv` (hours, seats,
policies); delivery keeps its prep times and fees in its own file. A
restaurant takes bookings if it has a row in `dining.csv`, and delivers if
it has one in delivery's — so Harbor Catch does both, Lamplight Kitchen
only seats guests, and the ghost kitchens only deliver. A delivery-only
code here is `not_found`.

## Only Aira, like delivery

Every restaurant is in one of Aira's zones. `zone` in
`dining_search_restaurants` accepts a zone code, name or postal code.

## Half-hour slots, up to the last seating

Tables are booked at :00 and :30, from opening time to the last seating —
the latest slot whose sitting (`sitting_minutes`) still ends by closing.
Any other time is `invalid_time`. Each slot seats `covers_per_slot` guests,
counted on its own: there are no table sizes, and a slot's seats don't
depend on its neighbours. Some restaurants close on fixed weekdays
(`closed_days`); booking those days fails with `closed`.

## Other diners book too

As a slot approaches, other diners fill it — faster at popular places, and
faster in the dinner rush (18:30–20:30) than at lunch or late. The hardest
tables in town are gone at peak several days ahead while 17:30 still has
room on the night. `dining_check_availability` shows seats left per slot;
`fully_booked` means fewer seats are left than the party.

## Party size is capped per restaurant

`max_party` ranges from 6 at a small home kitchen to 12 at the tapas bar.
Above it is `party_too_large` — the agent should find somewhere that fits,
not split the group without asking.

## Deposits are kept on late cancellation

Most restaurants take no deposit and can be cancelled free until the
booking time. Fine dining takes `deposit_per_guest` × party size on
booking: cancel at least `cancel_cutoff_hours` ahead and it comes back;
cancel later and the table is still freed but the deposit is kept
(`deposit_kept`). Once the booking time has passed, cancelling is
`too_late_to_cancel`.

## Status is computed live

`CONFIRMED` until the booking time, `SEATED` for the sitting, then
`COMPLETED`. Only `CANCELLED` is stored. There are no no-shows yet.

## Currency is bucks

Maya's fictional currency, the same as every other domain.
