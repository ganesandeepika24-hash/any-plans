# 0010: The search function (step 1.3, migration 010)

Status: accepted (2026-10-27). Owner: deepika. Revisit: when the evals exist (to tune the weights), if visible events pass about 100,000 rows or p95 passes 250 ms, or when venues join search.

## Context

Principle 4 says the AI search bar and the filter panel fill the same `SearchFilters` object, and principle 3 says answers come from stored data. Both need one database function that takes the filters and returns ranked events. Principle 5 splits the work: the model extracts intent, and Python resolves it into absolute times, a point and a radius, and prices. So the function receives filters that are already resolved and does no date or place reasoning of its own.

`SearchFilters` isn't written yet, so this function defines the resolved form the agent must send.

The agent will call the function as `service_role`, which bypasses RLS. So the visibility rules in `events_public_read` (001, 007) don't apply on their own and must be written into the function.

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| What it searches | **Events only**; events and venues together | Events only | Venues have no dates or prices, so most filters don't apply to them. The name `search_items` leaves room for venues later. |
| Language | SQL; **PL/pgSQL** | PL/pgSQL | Input checks need `raise`. Declared `stable`, so Postgres runs its queries read-only, and any write inside it fails, exactly as in SQL. |
| Ranking | Order by vector distance (uses HNSW); **weighted score over the filtered rows** | Weighted score | Ranking has to mix meaning, distance and date, and HNSW only orders by vector distance. Retention keeps the table at roughly 5,000 to 10,000 future events, so scoring every filtered row exactly is cheap, and it's correct. |
| Weights | Constants in the function; **the `config` table** | Config | Principle 8. A new version plus `set_active_config` changes ranking without a migration or deploy. |
| Unpriced events (about half of Skiddle's) | Always include; always exclude; **an `include_unpriced` filter, default true** | Filter, default true | By default the UI shows "price unknown" instead of hiding half the listings. A user who wants only known prices can say so. |
| Unknown filter keys | Ignore; **refuse** | Refuse | The agent and the function can't drift apart silently. A misspelled key is an error, not a filter that quietly does nothing. |

## Decision

**Signature:** `public.search_items(filters jsonb, query_embedding extensions.vector default null)`
- **Returns:** `id, source, title, category, genre, start_at, end_at, venue_id, venue_name, price_min, price_max, currency, url, image_url, last_checked_at, distance_m, similarity, score`.
- `source` and `last_checked_at` are there for principle 6: every result shows where it came from and how fresh it is.

**Filters.** Anything else raises SQLSTATE 22023, with a message starting `search_items:`.

| Key | Rule |
|---|---|
| `date_from`, `date_to` | ISO 8601 strings with an offset (a bare local time is refused). Keeps events whose span overlaps `[date_from, date_to)`, so an exhibition running through the weekend matches a weekend search. An event with no end time counts as ending 3 hours after it starts, as in the policy. |
| `categories` | A non-empty array of `events.category` values. A test checks the function's list matches the table's CHECK. |
| `price_min`, `price_max` | Numbers ≥ 0, with `price_min` ≤ `price_max`. Keeps events whose price range overlaps; a single known price stands for both ends. `price_max: 0` means free. |
| `include_unpriced` | A boolean, default true. False drops events with no price, with or without a price filter. |
| `lat`, `lon`, `radius_km` | All three or none, with the radius in (0, 100]. `ST_DWithin` on `geography` (metres), using `events_geom_idx`. Events with no location are dropped only when a radius is given. |
| `limit` | An integer from 1 to 100, default 20. |

**Visibility:** `status = 'active'`, `duplicate_of is null`, and `coalesce(end_at, start_at + interval '3 hours') > now()`. These are the same three conditions as `events_public_read`. A test compares the function's unfiltered results with what `anon` reads through the policy, row for row. The fixture's no-end events sit at 2 and 3.5 hours, so a 2- or 4-hour fallback fails the test.

**Score:** `w_sim × similarity + w_dist × (1 − distance_m / radius_m) + w_date × exp(−days / date_scale_days)`
- `similarity` is `1 − cosine distance`.
- `days` counts from the search's start (`date_from`, or now if that's later), so the first events in the window score highest.
- A term is 0 when its input is missing: no query embedding (filter-panel searches), an event not yet embedded, or no point given.
- Ordered by score, then the earlier start, then `id`, so the order is deterministic.

**Weights:** config key `search_weights`, version 1, inserted active by the migration: `{"similarity": 0.6, "distance": 0.25, "date": 0.15, "date_scale_days": 7}`.
- The function reads the active version on every call.
- No active version, a missing key, a negative weight or a scale of 0 or less raises an error.

**Privileges and safety:**
- `stable`, `security invoker`, `set search_path = ''`, with every name schema-qualified, including `operator(extensions.<=>)`.
- Execute is revoked from `public`, `anon` and `authenticated`, and granted to `service_role` only.

## Trade-offs

- **Every filtered row is scored.** The worst case, no filters with an embedding, scores every visible event. That's fine at today's size. 0009's retention bounds it, and the revisit trigger above covers growth.
- **The visibility rule lives in two places**, the policy and the function. The row-for-row test is what keeps them together. A future migration that changes the policy must change the function in the same file.
- **Strict input checks** mean the agent must send exactly this format. That's the point: drift fails loudly in tests and evals instead of returning wrong results.
- **The weights are a starting guess** until the evals exist.

## Metrics and results

- **Tests:** `jobs/tests/test_search_items.py`, 54 cases against the CI database. They cover:
  - visibility against the policy, and categories against the table's CHECK
  - each filter alone and combined, with the date-overlap, price and radius edges
  - ordering by each signal alone, the mixed formula recomputed in Python, and ties
  - the date reference, missing inputs, and switching config versions
  - bad weights, bad filters (21 cases) and a wrong-length embedding
  - read-only use, and the grants for `anon`, `authenticated` and `service_role`
- **db checks** (`rls_and_indexes.sql`): `stable`, not `security definer`, empty `search_path`, execute for `service_role` only (`public` included), `anon` refused, and the starting weights active.
- **Performance** (`db/tests/search_perf.sql`, a CI db job step, rolled back):
  - **Data:** 20,000 visible events (about twice the expected volume) plus 3,000 hidden ones, with 500 venues, half the events unpriced, 5% without a location and 2% not yet embedded.
  - **Queries:** 100 searches in five shapes, timed with `EXPLAIN (ANALYZE)`. The check fails at a p95 of 500 ms.
  - **Results:** locally, p50 24 to 28 ms and p95 46 to 91 ms across four runs. CI prints its own figures in the step log.
- **Deliberate breaks:**
  - 18 in migration 010, each applied to a fresh database. All fail the suite or the db checks.
  - A 0.6 s sleep in the function fails the performance check (p95 654 ms).
  - The first round missed one break: a 4-hour fallback passed, because the fixture's event sat exactly on 4 hours. That fixture was fixed (see workflow notes).
- **Supabase:** 010 isn't applied yet. Apply it after merge in the SQL editor, with its checksum update in the same run (0006). Nothing calls the function until the agent's search node exists, so merge order doesn't matter this time.
