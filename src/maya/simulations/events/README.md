# The events domain

Notes for anyone (human or AI agent) working with `events_*` MCP tools —
things that aren't obvious from the tool descriptions alone. See the
[repo README](../../../../README.md) for install/usage; this page is about
what the simulation actually models, not how to run it.

## Shows are weekly, not one-offs

Every event is a series that runs on fixed weekdays — Open Mic every Tuesday
and Wednesday, the Symphony every Saturday — like the flight timetable. A
*performance* is a series on a date it runs. Asking for a show on a day it
doesn't run is `not_found`, not an empty result; `events_search` over a date
range is the way to find when something is on. There's always a next
performance, so listings never go stale.

## Seven kinds of event, all fictional

Comedy, concerts, lectures, theatre, family shows, films and workshops, at 9
venues across Aira's zones. Every title is made up — including the films,
which span genres from drama and horror to animation and documentary.
`query` in `events_search` matches the title **or the genre**, so "horror",
"jazz" or "pottery" all work.

## Prices are fixed; a service fee goes on top

Each section has a fixed face price (stalls, circle, front tables...). Every
ticket also carries a 10% service fee (rounded up; free events have none),
so `total_price` = (face + fee) × quantity — not just face × quantity.

## Popular shows really sell out

Other buyers take tickets as a performance approaches. Quiet shows still have
plenty on the night; popular ones — the arena concert, the headline comedy,
the 12-place pottery class — **sell out completely before the day**. Sold-out
performances still appear in search with `sold_out: true`. Tickets are per
section, so one section can be gone while another has seats.

## Rules that can say no

- **Ticket limit:** each show has `max_tickets_per_order` (4 for the arena
  concert, 2 for workshops). Above it: `ticket_limit_exceeded`.
- **Age limits:** some shows are 12+, 16+ or 18+. For those,
  `youngest_attendee_age` is required, and anyone under the limit means
  `age_restricted`.
- **Sales close at the start**, and open 90 days ahead.

## Refunds are per show, and the fee is never refunded

Some shows are non-refundable (`not_refundable`). Others refund up to a
cutoff before the start (`too_late_to_cancel` inside it); free lectures can
be cancelled right up to the start. A refund is **face value only** — the
service fee is kept, like on real ticket sites. Cancelled tickets go back on
sale.

## Status is computed live

`CONFIRMED` until the show starts, `IN_PROGRESS` while it's on, `ENDED`
after. `CANCELLED` is the only stored status.

## Aira only, bucks only

All venues are in Aira's zones — the same zones hotels and delivery use. No
payment step; `total_price` is informational, as in every domain.
