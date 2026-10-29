# 0023: Filter-only search for everyone, through search_items (step 1.4, fix 4)

Status: accepted (2026-10-28). Owner: deepika. Revisit when filter-only searches get logged (principle 7), if signed-out search needs a cap, or if Supabase's advisor flags the definer function in a way the owner wants to act on.

## Context

PRD F-01 asks that signed-out filter search and signed-in search give the same results. Decision 0013 had the browser read `events` directly for filter-only search, through `events_public_read`. That gives the right rows but no ranking, no distance, no confidence label (0022) and no point. It also leaves the browser to filter dates, prices and distance itself, so the two paths would drift.

`search_items` has all of that, but only `service_role` can run it (0010). Granting it to `anon` alone wouldn't work: it reads its weights and label rules from `config`, which `anon` can't read. `config` also holds prompts, model names and provider limits, which the browser mustn't see.

The owner approved the plan on 28 Oct 2026, deferring two things: logging filter-only searches, and a per-user cap on them.

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| Where signed-out search runs | The browser on `events`; an agent route; **a database function** | Function | 0013: filters must work when the agent is asleep, resting or down. An agent route would take filter search down with it and add Render's cold start. |
| Which function | A copy of `search_items`; **a wrapper that calls it** | Wrapper | One function holds the filters, visibility, scoring and labels, so the paths can't drift. |
| How `anon` reads `config` | Grant `config` to `anon`; a view of the two keys; **security definer** | Definer | `config` stays closed. The wrapper takes only `filters` and passes no embedding. `search_items` checks every key, and its visibility rule is written out, so the owner's rights show nothing `events_public_read` hides. |
| Search text | Accept an embedding; **filters only** | Filters only | The filter panel has no text, and taking a vector from the browser would open a costlier, unchecked path. |
| Columns | All of `search_items`'; **the agent's card fields** | Card fields | `venue_id` and `similarity` aren't shown. The app renders both paths with one card. No `rank`: the array's order is the rank. |
| Who may call it | `anon` only; **`anon`, `authenticated` and `service_role`** | All three | A signed-in user's filter panel is the same call. |
| Logging | Log now; **later** | Later | A stable function can't write, and `anon` has no id. Deferred by the owner. |
| A cap | Per-IP or per-user now; **none for now** | None | As before with direct reads of `events`. At most 100 rows, about 50 ms each. Deferred by the owner. |

## Decision

- **Migration 023** adds `public.search_items_public(filters jsonb)`: `language sql`, `stable`, `security definer`, owned by `postgres`, `search_path` empty. It returns `search_items(filters, null)`'s rows in order, without `venue_id` and `similarity`.
- **Grants:** revoked from `public`, `anon` and `authenticated`, then execute granted to `anon`, `authenticated` and `service_role`. `search_items` and `config` keep their grants: `service_role` only.
- **The browser** calls `supabase.rpc("search_items_public", { filters })` with the publishable key, signed in or out, for the filter panel and as the fallback when AI search rests. `windows` is refused, as `search_items` refuses it: only the agent applies it (fix 5).
- **0013's filter-only fallback** changes from "the browser reads `events` directly" to this function.

## What "identical" means

The same filters give the same rows, order, fields and labels on both paths. An AI search also ranks by the meaning of its words (the embedding). Filter-only search has none, so for the same filters an AI search can order the same events differently. The matched events are the same.

## Trade-offs

- **A definer function in an exposed schema.** Supabase's advisor may flag it. It's deliberate: the function's only input is checked key by key, it can't write (`stable`, and Postgres runs it read-only), and it never reads `config` for the caller beyond what `search_items` uses.
- **Unlogged and uncapped.** Filter-only searches aren't in `queries` yet, so principle 7's metrics miss signed-out use. There's no cap beyond the 100-row limit.
- **Times and numbers are formatted by PostgREST, not the agent.** Times come as `+00:00`, not `Z`, and numbers keep the database's precision. The values are the same.

## Metrics and results

- **Tests:**
  - jobs: 423 pass, 23 of them new, against PostGIS 17-3.6 with 023 applied
  - agent: 705 pass, 1 new
- **The comparison test:** for 10 filter sets, `anon`'s call to the wrapper equals `service_role`'s call to `search_items(filters, null)`, field for field and in order, in one transaction. The sets are none, dates, categories, a price range, free only, a radius, a limit, the default area, all combined, and priced only. The events include stale, fresh, unpriced, free, placeless, embedded and tied rows. Every set finds something, so no comparison passes on two empty lists.
- **Other new database tests:**
  - a signed-in user gets the same
  - the labels come through
  - hidden events stay hidden (the same three as `events_public_read`)
  - `windows`, a bad limit, half a place, an unknown category and an embedding key are refused
  - an embedding argument isn't accepted
  - the browser roles still can't call `search_items` or read `config`
  - it works in a read-only transaction
- **The agent test:** the wrapper's columns are `search_items`' minus `venue_id` and `similarity`, the same as `ResultOut` minus `rank`, and it passes no embedding.
- **`rls_and_indexes.sql`:**
  - the wrapper is stable, security definer, owned by `postgres`, with an empty `search_path`
  - execute for the three roles and not `public`
  - `config` closed to `anon` and `authenticated`
  - `anon` gets rows
- **Speed:** `search_perf.sql` times the wrapper as `anon`: p95 51 ms over 100 searches, against the 500 ms limit. `search_items` itself: p95 50 ms.
- **Deliberate breaks:** 8, all caught after one fix:
  - a filter changed
  - the rows reordered
  - an embedding passed
  - invoker rights
  - the label dropped
  - `anon` able to call `search_items`
  - `config` opened to `anon`
  - `anon`'s grant missing

  The embedding break was missed at first, because none of the comparison's events had an embedding. Two embedded events were added, and it was caught.
- **Eval:** not run. Parsing doesn't change.

## Update 2026-10-28: windows (migration 024)

`search_items_public` no longer refuses `windows`. `search_items` applies them since 024 (decision 0020's update), and the wrapper passes `filters` through, so a URL's windows give the same results on both paths.

## Changes

- **2026-10-29: party size (decision 0024, migration 026).** `search_items`, and so this function, accepts `party_size` (1 to 1000) and checks it, but doesn't filter on it. The filter panel's budget is per ticket. The comparison test has an eleventh filter set, with a party size.
