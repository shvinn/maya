# The delivery domain

Notes for anyone (human or AI agent) working with `delivery_*` MCP tools —
things that aren't obvious from the tool descriptions alone, and are easy to
assume incorrectly if you're used to a real delivery app. See the
[repo README](../../../../README.md) for install/usage; this page is about
what the simulation actually models, not how to run it.

## Addresses are zones, not street addresses

There is no house number, street name, apartment number, or precise
coordinate anywhere in this domain. `delivery_zone` (in
`delivery_place_order`) accepts a **zone code** (e.g. `AIR-MKT`), a **zone
name** (e.g. "Lantern Market"), or a **postal code** (e.g. `105`) — that's
the finest granularity delivery addresses get. If you're translating a
"deliver to 42 Harbour Lane" kind of request, there is no street-level
target to map it to; pick the zone it falls in.

See [`world/geography/aira/`](../../world/geography/aira/) for what a zone
actually is: one of 9 named districts of Aira, with a small hand-authored
road graph between them (not real coordinates or routing).

## Delivery only exists in one city

Only Aira has zones defined right now, so it's the only deliverable city —
none of Maya's other 9 cities can receive a delivery order yet. Placing an
order with a zone outside Aira fails with `zone_not_deliverable`, not a
generic error.

## Stock is unlimited

Every menu item is always orderable. Unlike flights, there is no
"sold out mid-order" mechanic here — don't expect an `item_out_of_stock`
error, because there isn't one.

## There's no payment step

`delivery_place_order` confirms in one call: browse, place, done. `total_price`
is informational, the same shallow way flights' `total_price` is — there's no
hold/capture and no payments domain behind either of them yet.

## Order status is computed live, not stored

`delivery_get_order` doesn't return whatever status was true when the order
was placed — it computes `PREPARING` → `OUT_FOR_DELIVERY` → `DELIVERED` from
elapsed real time against the vendor's prep time and the courier's travel
time, every time you call it. Two calls a few minutes apart can legitimately
show different statuses without anything else having happened. There is no
"advance the clock" step to take.

## Cancellation only works while still `PREPARING`

Once the computed status moves to `OUT_FOR_DELIVERY`, `delivery_cancel_order`
fails with `too_late_to_cancel` — a courier can't be recalled. Vendors with
a `ghost_kitchen` style have near-zero prep time, so in practice their
orders are almost never cancellable; that's a consequence of the timing, not
a separate rule.

## ETA is prep time + a road-graph lookup, not real routing

`eta_minutes` = the vendor's `prep_minutes` + the shortest-path courier time
between the vendor's zone and the delivery zone, from Aira's small
hand-authored road graph. It is not a real mapping/routing service, and it
does not account for traffic, weather, or time of day.

## Cuisine names are real, restaurants aren't

Vendor cuisines use real-world terms (Chinese, Indian, Mexican, Thai,
Italian, etc.) so menu variety reads naturally, but every vendor name is
fictional — none of the 21 vendors are real restaurants or chains, per the
project's canon rule against real-brand disguises. (That canon lives in
`.docs/WORLD.md`, which is gitignored/local-only — see `CONTRIBUTING.md` if
you don't have it.)

## Currency is bucks

Maya's fictional currency, not tied to any real-world exchange rate — same
as flights.
