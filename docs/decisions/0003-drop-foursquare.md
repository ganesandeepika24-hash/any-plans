# 0003: Drop Foursquare for the MVP

Status: accepted (2026-10-25). Owner: deepika. Revisit: stage 4 or later, if ratings prove worth paying for.

## Context

The brief planned Foursquare for venue ratings, tips and photos, with "$200 a month in free credits". Foursquare changed its terms on 1 June 2026:
- Pro endpoints: 500 free calls a month (down from 10,000), then $15 per 1,000.
- Premium endpoints (ratings, tips, photos): no free tier, from $18.75 per 1,000.

Ratings are a nice-to-have for restaurant cards. Our cards link to the restaurant's own booking page, and the planner's job is ordering and linking, not judging quality.

## Decision

Remove Foursquare from stage 1 and the MVP. Venues come from OpenStreetMap only. `venues.rating` stays in the schema but is nullable and unpopulated.

## Trade-offs

- **Lose:** ratings, tips and photos on venue cards. The "Found on the web" and OSM data remain.
- **Gain:** zero cost and no risk of a bill or of exceeding the 500-call allowance.
- **Also gain:** one fewer source to attribute, rate-limit and monitor.
- **Ranking impact:** ranking uses distance, time and category match, not ratings. Plan quality may be lower for "best" requests. Track this with the thumbs-up rate and the failure tag "bad plan order / irrelevant".

## Metrics

- Thumbs-up rate on plans in which at least one venue is a restaurant (baseline in stage 3).
- Failure tag "irrelevant results" count, before and after stage 4 changes.

## Reversal

Re-add Foursquare only if the stage 4 review shows ratings would fix a top-2 failure type, and only with a budget check against the 500-call allowance. Record the new cost in a new decision.

## Checks

- Confirm Foursquare's current free allowance on its pricing page before any reversal (sources conflict: 500 on the change notice, 10,000 on older pages).