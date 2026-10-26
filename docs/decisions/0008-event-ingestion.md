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
- `ingest-skiddle.yml`: nightly at 02:00 UTC, plus manual dispatch.
- `delete-ended-events.yml`: nightly at 04:00 UTC, plus manual dispatch.

**Guard test.** `jobs/tests/test_workflow_guard.py` runs while 0001 doesn't say CLEARED for Ticketmaster:
- It parses `ingest-ticketmaster.yml` with PyYAML, allowing for PyYAML reading `on` as `True`, and requires its triggers to be exactly `{workflow_dispatch}`.
- It fails if any other workflow mentions Ticketmaster.
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
- `limit=100`, paging by offset. The offset advances by the number of results each page actually returns.
- `EventCode` maps to category: LIVE and FEST → Music; CLUB and BARPUB → Nightlife; COMEDY, THEATRE, EXHIB → Arts, KIDS → Family, SPORT → Sport. Other codes are skipped.
- Skiddle publishes no limit, so the caps are set at 500 calls per run and 1,000 a day.

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

- **The hand-written fixtures follow the documented response shapes, not real responses.** Skiddle's event codes and field names in particular need confirming. The first dry run of each source saves a sample (`save_sample`, uploaded as an artifact with `_links` stripped), and the fixtures get replaced with trimmed real pages.
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
- **Skiddle dry run:** not yet run. Record here the event count, calls made, and confirmed event codes.
