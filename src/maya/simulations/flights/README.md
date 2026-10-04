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

## Direct or one stop — never more

`flights_search_flights` returns direct flights and one-stop connections
together; each option has `stops` (0 or 1) and a `legs` list. Pass
`max_stops=0` for direct only. Two stops are never built — if a city pair
has neither a direct flight nor a one-stop connection, there is no service.

A connection is two flights sold as **one booking**: one reference, one
fare, both legs' seats taken together, and cancelled together. To book one, pass the first leg as `flight_number`/`date` and the
second as `connecting_flight_number`/`connecting_date`. The rules a pair
must meet (anything else fails with `invalid_connection`):

- the layover is 45 minutes to 6 hours, at the airport the first leg lands
  at — **or** it's an overnight connection: the first leg lands at 18:00 or
  later and the second leaves the next morning before 12:00. Those options
  carry `overnight: true`; the traveller sleeps at the connection airport,
  and no hotel comes with the ticket (Aira has hotels — see the hotels
  domain);
- **Puffin Air never connects.** It's ultra-low-cost and sells point-to-point
  only, even Puffin-to-Puffin. Maya Air, Aira Express and Coral Wings all
  connect with one another.

A connection's `seats_available` is whatever its fuller leg has left, so it
can sell out because of either flight.

## Connections on one airline are cheaper

A connection where both legs are on the same airline costs 20% less than
buying the two flights separately — airlines price their own connections to
compete (`connection_discount_per_passenger` shows the saving). A
connection across two airlines costs the plain sum of its legs. So a
one-stop trip can undercut a pricier airline's direct flight, and two
connections over the same hub can differ in price just by who flies them.

## Seats and price are computed live, not fixed

Availability and fare both depend on how close to departure it is and how
full the flight already is (a simulated background demand curve). Calling
`flights_search_flights` for the same route/date minutes apart can
legitimately return different numbers — that's not a bug to work around.
"Now" is Maya time (MYT, UTC−08:00), not your local time, and it may be
running faster than real time — `world_get_time` says what it is.

## Refund rules are per-airline, not per-fare-tier

Puffin Air (`PF`) fares can never be cancelled — not a fee, not a tier,
never. Every other airline allows a full refund any time up to 24 hours
before departure (the first leg's departure, for a connection), and cancellation is flatly impossible inside that window
(`flights_cancel_booking` fails with `too_late_to_cancel`, not a partial
refund). There's no middle case.

## The world is small and fixed

10 airports, 4 airlines, one weekly timetable. A route or carrier not in
that set doesn't exist — there's no external system to fall back to, and no
route generation beyond what's in `data/schedule.csv`.

## Currency is bucks

Maya's fictional currency, not tied to any real-world exchange rate —
deliberately, so a fare can't be sanity-checked against a real one.
