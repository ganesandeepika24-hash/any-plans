# 0008: Event ingestion (step 1.1)

Status: accepted (2026-10-26). Owner: deepika. Revisit: after the first Ticketmaster dry run (fill in the results below), when 0001 says CLEARED for Ticketmaster, or if either API's terms or limits change.

## Context

Step 1.1 fills the `events` table from Ticketmaster and Skiddle. The constraints:
- **Terms.** Ticketmaster's terms are not cleared for a scheduled bulk fetch (0001), so its job may only run by hand. Ended events must be deleted, with no archive (0007).
- **Data.** Rows must fit the migration 001 columns exactly. Every run writes a `job_runs` row.
- **Limits.** Ticketmaster allows 5,000 calls a day and 2 a second (developer FAQ; the API reference says 5 a second). Deep paging stops at the 1,000th item.
- **Secrets** live in GitHub Actions only.

## Options considered

| Area | Options | Choice | Why |
|---|---|---|---|
| Database access | PostgREST with `SUPABASE_SERVICE_KEY`; **direct Postgres with psycopg through the session pooler (`SUPABASE_DB_URL`)** | Direct | The same SQL runs against the migration 001 and 005 schema in CI. Upserts report new versus updated (`xmax = 0`), `usage_daily` increments are atomic, and the delete is one statement. Cost: one more secret. |
| Ticketmaster scheduling | Schedule with an `if:` gate; **a separate workflow with `workflow_dispatch` only** | Separate | A test can check the trigger set mechanically. |
| Dry run | A CLI flag that skips writes; **a read-only connection** | Read-only | `--dry-run` opens the connection with `read_only = True`, so every transaction is `READ ONLY` and Postgres rejects any write. `run()` refuses a dry run on a writable connection. A dry run writes no `job_runs` or `usage_daily` rows either. |
| Page size | Assume 200; **read `page.size` from each response** | Read it | Ticketmaster's reference gives `size` a default of 20 and documents no maximum. The job asks for 200 and pages by what the response reports. |
| London filter | `dmaId=602`; **`cities.centre_geom` with 25 km** | Our own centre | The same filter for both sources. The city's 10 km search radius would miss Wembley and Twickenham. |
| Postponed events | Store as cancelled; **don't insert, and delete existing ones** | Delete | No status fits. They come back when rescheduled with a date. |
| Vanished events | Keep until their end time; **sweep after a clean run** | Sweep | A Ticketmaster event in the horizon that a complete run didn't return has gone from the source. |
| Venue rows | Wait for OSM matching; **upsert from the event payload, `kind 'other'`** | Upsert | `events` has no venue-name column, so venue names need venue rows. Matching to OSM venues comes later. |

## Decision

**Layout.** The jobs live in `jobs/`, a uv project on Python 3.12 using httpx, psycopg 3, pydantic and fastembed.
- `python -m jobs.ingest {ticketmaster|skiddle} [--dry-run] [--save-sample DIR]`
- `python -m jobs.delete_ended`

**Workflows** all share the `db-writes` concurrency group, so they never overlap:
- `ingest-ticketmaster.yml`: `workflow_dispatch` only, with `dry_run` defaulting to true.
- `ingest-skiddle.yml`: `workflow_dispatch` only for now, with `dry_run` defaulting to true. **Why:** the first Skiddle run is a manual dry run, because the fixtures are hand-written and Skiddle's field names and event codes are unconfirmed. The schedule (planned for 02:00 UTC) is added only after that dry run has been reviewed and the approval conditions are recorded in 0001.
- `delete-ended-events.yml`: nightly at 04:00 UTC, plus manual dispatch.

**Guard test.** `jobs/tests/test_workflow_guard.py` runs while 0001 doesn't say CLEARED for Ticketmaster:
- It parses `ingest-ticketmaster.yml` with PyYAML, allowing for PyYAML reading `on` as `True`, and requires its triggers to be exactly `{workflow_dispatch}`.
- It fails if any other workflow mentions Ticketmaster.
- It also requires `ingest-skiddle.yml`'s triggers to be exactly `{workflow_dispatch}`. That check has no automatic release: adding the Skiddle schedule means changing the test in the same PR, after the 0001 update.
- CI runs on changes to any workflow and to 0001.

**Ticketmaster:**
- Searches with `countryCode=GB`, `geoPoint` (the geohash of `cities.centre_geom`), `radius=25` and `unit=km`, over a 90-day horizon in 7-day windows.
- A window whose first page reports more than 1,000 items is split in half, down to a 1-hour minimum. A minimum-size window that still overflows is fetched up to the paging limit and counted as failed.
- Requests are 0.5 s apart. The caps are 2,000 calls per run and 4,500 calls a day, counted in `usage_daily`. Retries count as calls.
- Category comes from segment and genre:
  - Music → Music
  - Sports → Sport
  - Film → Arts
  - Arts & Theatre: Comedy → Comedy; Theatre, Musical and Opera → Theatre; Children's Theatre → Family; anything else → Arts
  - Miscellaneous: Family → Family; Food & Drink → Food & Drink; anything else is skipped
  - Any other segment is skipped
- Images are URLs only (the largest 16:9 image up to 1,024 px). Nothing is copied.

**Skiddle:**
- Searches with a 16-mile radius (25 km rounded up), filtered back to 25 km in code. Requests are 1 s apart.
- **Date windows.** The 90-day horizon is split into 7-day windows, passed as `minDate`/`maxDate`. Each window's `maxDate` is the next window's `minDate`, so nothing is missed whether `maxDate` is inclusive or exclusive. Events seen twice are dropped by source ID.
- **The 10,000-result ceiling.** A window whose `totalcount` is over 10,000 is split in half, down to one day. Paging never asks for offset 10,000 or beyond. A one-day window that still has more than 10,000 results is fetched up to 10,000 and counted as failed.
- `limit=100`, paging by offset. The offset advances by the number of results each page actually returns.
- **Event codes**, reviewed against the dry-run sample:
  - Music: LIVE, FEST
  - Nightlife: CLUB, BARPUB, DATE, GAMING, DANCE, SEASONAL
  - Comedy: COMEDY
  - Theatre: THEATRE
  - Arts: EXHIB, ARTS, FILM, BOOKS
  - Family: KIDS
  - Sport: SPORT
  - Food & Drink: FOOD
  - Skipped: TOUR, EDU, EXPER, NET, HEALTH, CONF, RELIG, HOBBY, AWARD, FUND, and an empty code. A code nobody has reviewed yet is skipped as "unmapped", so it shows up separately in the run summary.
- **Prices:** `entryprice` first, then `ticketpricing` as the fallback.
  - In the sample, the two never agreed when both were set (2,421 events), and `entryprice` held the real range. For example, Harry Potter and the Cursed Child: "£30.00 - £210.00" against `ticketpricing` 2.99.
  - `ticketpricing` of 0/0 appeared on paid shows, so it counts as unknown, not free.
  - An `entryprice` of "0" or "0.00" counts as unknown too: it appeared on two paid festivals, whose `ticketpricing` values were placeholders. Only "Free" means free.
  - `entryprice` formats handled, all seen in the sample: "£N - £N", "N", "£N", "General Admission: GBP N", "GBP N - GBP N", "N-N".
- **Currency** comes from the event's `currency` field (GBP or EUR in the sample), not an assumed GBP. **Genre** is null, because Skiddle's results have no genres field.
- Skiddle publishes no limit, so the caps are set at 500 calls per run and 1,000 a day.

**Run summary.**
- Every failed window is listed in `failed_windows`, with its window, HTTP status, message and the page (Ticketmaster) or offset (Skiddle) it failed on. The status is null for a network error or an overflowing window. Any API key in the message is replaced with `<redacted>`.
- `windows` lists each window's total and result count. Skiddle windows also list every page's offset, result count and `pagecount`.
- `page_sizes` holds the page sizes seen: Ticketmaster's `page.size`, and Skiddle's `pagecount`.

**Times** are computed in code, never by the model:
- A UTC time is used as is. A local time is resolved in the city's timezone.
- An event with a date but no time runs from local midnight to the end of that local day.
- Multi-day events take `end_at` from the source.
- An event that has already ended under the 0007 rule is skipped.

**Writes:**
- Venues and events are upserted on `(source, source_id)`, one savepoint per event.
- `last_checked_at` is set on every upsert. `embedding` is cleared when the title, genre or venue changes, and missing embeddings are filled after the writes.
- **The sweep** runs only for Ticketmaster, and only after a run with no failed windows, no cap hit and no failed items. That last condition is stricter than the plan: an event that failed to write shouldn't then be deleted as "vanished".

**`job_runs`:**
- The row is inserted at the start.
- At the end it gets `duration_ms`, `fetched`, `new`, `updated`, `failed` and `deleted` (postponed and swept events, or the delete job's events plus orphaned Ticketmaster venues). A crashed run keeps `duration_ms` null.
- Skipped items aren't stored: skipped = fetched − new − updated − failed. Their reasons are printed in the run summary.

## Migration order (002 to 005)

Migration 005 (`job_runs.deleted`) is on Supabase before 002 (logging), 003 (stage 3 metrics views) and 004 (watches) exist. A fresh database applies them in name order.

Rules:
- 002, 003 and 004 must not create or alter `job_runs.deleted`.
- 005 doesn't depend on them.
- The 003 views can't reference `job_runs.deleted`. CI builds a fresh database in name order, so a 003 that references the column fails. If a view needs the column, a migration numbered 006 or later adds or replaces it.
- `migrate.sh` prints a NOTICE for each file applied out of order.

## Trade-offs

- **The Ticketmaster fixture is still hand-written** from the documented response shape. It gets replaced with trimmed real pages after the Ticketmaster dry run.
- **The Skiddle fixture is 40 real events** from dry run 40386725066, cut down to the fields the code uses. Fields that could identify people (`goingtos`, `imgoing`, `rep`, venue phone numbers) are dropped. The only EUR event in the sample is a food tour, which is skipped, so the currency test uses a copy of it with a mapped code.
- **`ticketpricing` is a weak fallback.** It's used for 2,490 events that have no `entryprice`, and its values look plausible there (club nights £5–7.50). But the same values (2.99, 5–25, 6.5–8.5) recur as placeholders where `entryprice` exists, so some fallback prices will be wrong.
- **Overlapping window boundaries** mean a boundary day's events are fetched twice, which costs calls. It's a deliberate choice, made because Skiddle doesn't say whether `maxDate` is inclusive.
- **Ticketmaster events that started before the run but haven't ended** (long runs, exhibitions) aren't in any window. They aren't refreshed, aren't swept, and leave by their end time.
- **Duplicates across sources** (the same gig on both Ticketmaster and Skiddle) are out of scope for 1.1.
- **The database tests connect as a superuser in CI.** On Supabase the jobs connect as `postgres`, the owner of the tables, and RLS doesn't apply to owners unless forced. CI doesn't model that difference.
- **GitHub's schedules are best effort**, and they stop after 60 days without repository activity.

## Metrics and results

- **Unit and database tests:** 76 locally against `postgis/postgis:17-3.6` with migrations 001 and 005 and the seeds. Twelve deliberate breaks each fail the suite:
  - a writable dry-run connection, set either by the flag or on the connection itself
  - the sweep ignoring completeness
  - a hard-coded page size
  - no window split
  - postponed events not deleted
  - embeddings never cleared
  - no 3-hour fallback in the delete
  - orphan deletion across all sources
  - no rate limit
  - calls not recorded after a crash
  - a 1,000-call cap
- **Ticketmaster dry run:** not yet run. Record here the London event count over 90 days, calls made, page size applied, windows split, run time, and any cap hit.
- **Skiddle dry run 1** (run 40386725066, 26 Oct 2026, before the changes below):
  - 10,000 of 10,425 results fetched in 104 calls, with one failed window.
  - 4,499 events within 25 km. The results reached only 19 Jan 2027, so 425 events were missed.
  - 46 duplicate IDs on neighbouring pages, which suggests some events were also skipped.
  - 28 event codes in the data, 19 of them unmapped at the time.
- **Skiddle dry run 2:** not yet run. Acceptance is `complete = true` with no failed windows. Record here the windows, totals, calls, the page sizes seen, and the skipped-code counts.
- **Tests after the Skiddle changes:** 132 locally, with migrations 001 and 005 and the seeds. Fourteen more deliberate breaks each fail the suite:
  - paging past 10,000
  - no window split
  - `ticketpricing` before `entryprice`
  - 0/0 counted as free
  - an `entryprice` of "0" counted as free
  - currency assumed GBP
  - DATE skipped instead of mapped
  - TOUR mapped instead of skipped
  - a failure without its status
  - an unscrubbed key
  - `pagecount` not recorded
  - windows not sharing their boundary
  - a failure without its offset
  - failure details missing from the summary

## Changes

- **2026-10-26: Skiddle fixes after dry run 1.**
  - The fetch now uses date windows and respects the 10,000-result ceiling. Before, it paged one 90-day query by offset, hit the ceiling and missed 425 events.
  - Failures now record status, message, window and offset, for both sources. Before, a failure was a bare count, and the cause of the dry-run failure wasn't in the log.
  - Prices come from `entryprice` first, with `ticketpricing` as the fallback, and the event's own currency is used. Before, only `entryprice` was read and GBP was assumed.
  - Genre is null. Before, the code read a `genres` field that doesn't exist.
  - The mapping above was reviewed against real titles. Before, 19 codes were unmapped.
  - Page and window sizes are recorded. Before, `page_sizes` was empty for Skiddle.
  - The fixture is rebuilt from the real sample. The old one was written from memory and had fields Skiddle doesn't send (`pagelength`, `genres`).
  - Corrected this record's earlier Skiddle mapping line, which wrongly said COMEDY and THEATRE map to Arts. The code always mapped them to Comedy and Theatre.
