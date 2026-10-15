# The flights domain

Notes for anyone (human or AI agent) working with `flights_*` MCP tools —
things that aren't obvious from the tool descriptions alone. See the
[repo README](../../../../README.md) for install/usage; this page is about
what the simulation actually models, not how to run it.

## Booking is one step, not search → hold → pay

`flights_book_flight` confirms immediately. There is no
search-then-hold-then-pay lifecycle: no held offer, no expiry window, and no
separate payment call to rely on.

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
moved ahead of the real date — `world_get_time` says what it is.

## Refund rules are per-airline, not per-fare-tier

Puffin Air (`PF`) fares can never be cancelled — not a fee, not a tier,
never. Every other airline allows a full refund any time up to 24 hours
before departure (the first leg's departure, for a connection), and cancellation is flatly impossible inside that window
(`flights_cancel_booking` fails with `too_late_to_cancel`, not a partial
refund). There's no middle case.

## Flights run late and get cancelled — check, nothing tells you

Each airline has its own reliability: Aira Express is on time about 92% of
the time, Maya Air 80%, Coral Wings 70% (and cancels the most), Puffin Air
60%. Delays run from 15 minutes to 4 hours, most under an hour.

A flight's fate is hidden until the airline would announce it: a
cancellation **24 hours** before departure, a delay **3 hours** before.
Before that it's `SCHEDULED` — no news, not a promise. Nothing is pushed:
an agent that booked on Monday only learns on Saturday by asking —
`flights_get_flight_status`, `flights_get_booking` (each leg's
`flight_status`, `delay_minutes`, estimated times, and the booking's
`disruption`), or a new search. Statuses run `SCHEDULED` / `DELAYED` /
`CANCELLED`, then `DEPARTED` and `LANDED` at the estimated times.

Once known, a cancelled flight can't be booked (`flight_cancelled`) and
drops out of search, as does a connection a delay has broken; search counts
both in `cancelled_or_misconnecting`.

## When the airline lets you down, the fare rules give way

A booking whose `disruption` is `FLIGHT_CANCELLED` can be cancelled for a
full refund at any time; one with `MISSED_CONNECTION` (the first flight now
lands too late for the second) or `LONG_DELAY` (3 hours or more) until its
first flight leaves. That holds for Puffin Air too, and inside the usual 24
hours. A plain `DELAYED` changes nothing. There's no automatic rebooking:
cancel and book again.

## Luck is per world

Which flights go wrong is drawn from the world seed, so it's consistent
within a world — ask twice, same answer — but a reset brings new luck. The
same flight on the same date isn't late in every world, and there's no
weekly pattern to learn. Whoever runs the world can pin the seed or set one
flight's fate (see "World controls" in the repo README); the agent can't.

## The world is small and fixed

10 airports, 4 airlines, one weekly timetable. A route or carrier not in
that set doesn't exist — there's no external system to fall back to, and no
route generation beyond what's in `data/schedule.csv`.

## Currency is bucks

Maya's fictional currency, not tied to any real-world exchange rate —
deliberately, so a fare can't be sanity-checked against a real one.
