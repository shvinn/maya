# The flights domain

Notes for anyone (human or AI agent) working with `flights_*` MCP tools —
things that aren't obvious from the tool descriptions alone. See the
[repo README](../../../../README.md) for install/usage; this page is about
what the simulation actually models, not how to run it.

## Booking is one step, not search → hold → pay

`flights_book_flight` confirms immediately. There's a fuller
search-then-hold-then-pay lifecycle documented in the project's fuller
design record (`.docs/FLIGHTS.md`, gitignored/local-only — see
`CONTRIBUTING.md`), including tools like `flights_get_offer` and a separate
pay step — none of that exists in the code yet. Don't assume a held offer,
an expiry window, or a separate payment call are things you can rely on.

## Every flight is direct

There is no connection-building or multi-leg itinerary construction. If a
city pair has no non-stop service, the answer is "no flight," not something
to route around via a layover — `flights_search_flights` will never return
a multi-leg option.

## Seats and price are computed live, not fixed

Availability and fare both depend on how close to departure it is and how
full the flight already is (a simulated background demand curve). Calling
`flights_search_flights` for the same route/date minutes apart can
legitimately return different numbers — that's not a bug to work around.

## Refund rules are per-airline, not per-fare-tier

Puffin Air (`PF`) fares can never be cancelled — not a fee, not a tier,
never. Every other airline allows a full refund any time up to 24 hours
before departure, and cancellation is flatly impossible inside that window
(`flights_cancel_booking` fails with `too_late_to_cancel`, not a partial
refund). There's no middle case.

## The world is small and fixed

10 airports, 4 airlines, one weekly timetable. A route or carrier not in
that set doesn't exist — there's no external system to fall back to, and no
route generation beyond what's in `data/schedule.csv`.

## Currency is bucks

Maya's fictional currency, not tied to any real-world exchange rate —
deliberately, so a fare can't be sanity-checked against a real one.
