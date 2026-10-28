# 0022: The confidence label and each event's point on search results (step 1.4, fix 2)

Status: accepted (2026-10-28). Owner: deepika. Revisit when web search (Tavily) adds results, when a source without an official API is added, when Ticketmaster's nightly schedule is cleared, or when fix 4 opens `search_items` to signed-out search.

## Context

PRD R-02 puts a confidence label on every result card, derived from `source` and `last_checked_at`, never from the model (0001). The API contract (`docs/api.md`, PR #27) listed it as not built. The same list noted that results carry no point, so the map (F-06) can't place them.

The owner's rule (`claude/confidence-label-decision-1.4.md`, 28 Oct 2026):

- **"Price checked today"**: official-API results whose `last_checked_at` is within the last 24 hours. The nightly job runs once a day, so this covers last night's check.
- **"Check availability on [site]"**: anything older than 24 hours, restaurants, and any result without an official source.
- **"Found on the web today"**: web search results.
- The label is computed in the database or the agent, so signed-in and signed-out search show the same label. Lovable shows the text as sent, with colour only as an extra cue.

At plan approval the owner chose (a) for an event the source lists with no price: "Check availability on [site]", even when it was checked today. "Price checked today" above "Price not listed" would claim something the data can't back (principle 6).

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| Where the label is computed | The agent; **`search_items`** | Database | Fix 4 makes signed-out search call `search_items` too. One function means one rule and the same label on both paths, as the owner's rule requires. |
| The window and the site names | In code; **in config (`confidence_labels`)** | Config | Principle 8: the 24 hours and the site names change without a migration. A new version and `set_active_config`, as `search_weights`. |
| A source not in the config | Official by default; **never official** | Never | Only a listed source can claim "Price checked today". An unlisted one gets "Check availability on the booking site". A new ingest job adds its source to the config. |
| An event with no price | "Price checked today"; **"Check availability on [site]"** | Check availability | The owner's choice (a). A known price of 0 (free) is a price. |
| What the API sends | The text only; **a code and the text** | Both | `confidence` (`checked_today`, `check_site`, `web`) is stable, for colour. `confidence_label` is the text the card shows as sent. The app never maps one to the other. |
| The window's edge | Same calendar day in London; **the last 24 hours** | 24 hours | The owner's rule. A check at 03:00 still counts at 23:00, and a check from yesterday evening still counts this morning. |
| The point on each result | The venue's; **the event's own (`events.geom`)** | Event's | `distance_m` and the radius filter already use the event's point, so the map and the distance agree. The ingest jobs set it from the source. |
| Changing the result columns | `create or replace`; **drop and create** | Drop and create | Postgres can't change a function's result columns in place. The body is 010's, with the new columns. The grants are stated again. |
| Results from before 022 | Optional fields; **required** | Required | A card with no label breaks R-02. Without 022, every row fails the agent's check and the search answers "Search is unavailable", so the problem shows at once. |

## Decision

- **Migration 022** drops `search_items` and creates it again with four more result columns. Filters, visibility and scoring are 010's, unchanged.
  - `confidence`: `checked_today`, `check_site` or `web`.
  - `confidence_label`: "Price checked today", "Check availability on {site}" or "Found on the web today".
  - `lat`, `lon`: the event's point, null when it has none.
- **The rule**, in order:
  1. A source of kind `web` gets `web`.
  2. A source of kind `official`, checked within `fresh_hours` (`last_checked_at >= now() - fresh_hours`), with a price (`price_min` or `price_max` set), gets `checked_today`.
  3. Everything else gets `check_site`, with the source's `site`, or "the booking site" for an unlisted source.
- **Config `confidence_labels` v1**: `fresh_hours` 24, and Ticketmaster and Skiddle as official, named "Ticketmaster" and "Skiddle". A missing or malformed config is an error (22023), as for `search_weights`.
- **Grants:** execute for `service_role` only, revoked from `public`, `anon` and `authenticated`. Fix 4 changes this.
- **Agent:** `ResultOut` gains the four fields, all required. The search step copies them by name. `query_results` still logs no point and no distance.
- **Apply 022 before merging.** The agent needs its columns.

## Trade-offs

- **Ticketmaster cards will mostly say "Check availability on Ticketmaster" for now.** Its nightly schedule isn't cleared (0001), so its events refresh only on manual runs, and most checks are older than 24 hours. That's the rule working. It changes when the schedule runs.
- **The label is as of the search.** A card kept open past the 24-hour edge still says "Price checked today". A new search corrects it.
- **Restaurants have no rows yet.** OSM venues aren't in `events`. When a restaurant source arrives, it's listed with kind `official` only if it has an official API. Otherwise it's left out, and gets "Check availability on the booking site" until it's given a site name. A third kind may be wanted then.
- **"Found on the web today" is ready but unused.** Web results will live in `web_results` (0001), not `events`, so the web path may compute its label elsewhere. The config kind is there so the rule has one home.

## Metrics and results

- **Tests:**
  - jobs: 400 pass, 19 of them new, against PostGIS 17-3.6 with 022 applied
  - agent: 691 pass, 5 of them new
  - `rls_and_indexes.sql` passes, with a new 022 block: the four columns, and config v1 active
- **The new database tests cover:**
  - checks 23.5 h ago (labelled) and 24.5 h ago, 30 h ago and never (not labelled)
  - an event with no price, one with only a minimum, one with only a maximum, and a free one
  - an unlisted source
  - a web source
  - the window and the site name changed in config
  - the label never changing which rows come back or their order
  - eight malformed configs and no active config
  - the point, null without one, and matching `distance_m`
- **The new agent tests cover:**
  - the fields passed through as sent
  - a row from before 022 giving "Search is unavailable"
  - an unknown code or a missing label being refused
  - `ResultOut` matching the function's columns, read from the latest migration that creates it
- **Deliberate breaks:** 11, all caught:
  - the window inverted
  - the window measured from `start_at`
  - unlisted sources treated as official
  - the price check removed
  - `lat` and `lon` swapped
  - the web branch removed
  - the config check removed
  - the label text changed
  - `anon` given execute
  - the agent dropping `lat` and `lon`
  - the agent accepting any code
- **Speed:** `search_perf.sql` p95 is 51 ms over 100 searches, against the 500 ms limit (0010).
- **Eval:** not run. Parsing doesn't change.
