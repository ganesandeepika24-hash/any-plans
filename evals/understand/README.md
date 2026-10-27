# Understand eval cases

`cases.jsonl` has one search per line (decision 0012):

- `query`: what the user types.
- `now`: the moment the search runs, with its offset.
- `reply`: the correct `UnderstoodQuery` for the query: what a good model should return.
- `expected`: the `SearchFilters` code should build from `reply`. Write these by hand, never by running `resolve()`, or the test proves nothing. `area` stands for that area's `lat` and `lon` (from `db/seed/areas.sql`); `radius_km` is given separately.
- `notes`: the note codes code should add for parts it couldn't resolve.

`agent/tests/test_understand_evals.py` checks every `reply` validates and resolves to `expected`, with no model involved. Live scoring, where a real model's reply to `query` is compared with `reply` in Langfuse, comes later and reuses these cases.
