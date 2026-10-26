# 0009: Derived data (step 1.2)

Status: accepted for price snapshots (2026-10-26). Owner: deepika. Revisit: when stage 3 builds price-trend views, or if snapshot volume approaches the free-tier limit. Dedupe (1.2b) and OSM venues (1.2c) will add sections here.

## Price snapshots (step 1.2a, migration 006)

### Context

Showing how an event's price moves needs a history of prices. The events table holds only the current price: each ingest overwrites it. Decision 0007 says ended events leave no archive, and that stage 1.2 snapshots follow the same rule: "Snapshots are deleted when their event is deleted, or by the same end-time cutoff."

### Options considered

| Area | Options | Choice | Why |
|---|---|---|---|
| Frequency | Only when the price changes; **every day** | Every day | Trend charts get one point per day with no gaps to fill. The volume is small (below). |
| Which day | UTC date; **the city's local date** | Local date | A London evening shouldn't count as tomorrow. The date is computed in Python from `cities.timezone` with `zoneinfo`, not by the database clock. |
| Which events | All current events; **current events with a price** | Priced only | A snapshot without a price records nothing. Cancelled events with a price are kept, with `status 'cancelled'`. |
| "Current" | **The same rule as `events_public_read` and the delete job** | Shared rule | `coalesce(end_at, start_at + 3 hours) > now`, so a snapshot is never written for an event the delete job is about to remove. |
| Deleting snapshots | A separate snapshot delete; **`on delete cascade` from events** | Cascade | Postgres guarantees no snapshot outlives its event, whichever job deletes the event: the delete job, the Ticketmaster sweep, or a postponed delete. |
| Counting deleted snapshots | Count, then delete in two statements; **one statement** | One statement | `with gone as (delete … returning id) select count(*) from gone, (select count(*) from price_snapshots where event_id in gone)`. Every part reads the same snapshot of the data, so the count includes the rows the cascade removes. |

### Decision

- **Table** `public.price_snapshots` (migration 006):

  | Column | Type |
  |---|---|
  | `event_id` | `uuid not null`, references `events(id)` on delete cascade |
  | `snapshot_date` | `date not null` |
  | `price_min`, `price_max` | `numeric`, with `check (price_min <= price_max)` |
  | `currency` | `text` |
  | `status` | `text not null`, `check (status in ('active', 'cancelled', 'past'))`, the same values as `events.status` in 001 |
  | `captured_at` | `timestamptz not null default now()` |
  | primary key | `(event_id, snapshot_date)` |

  RLS is on, with no policies. Privileges are revoked from `anon` and `authenticated`, and `service_role` gets everything.
- **Job** `python -m jobs.snapshot_prices [--dry-run]`:
  - **Daily rows.** One row per current, priced event per local day, written with `on conflict (event_id, snapshot_date) do update`, so a rerun the same day refreshes the row and never duplicates it.
  - **`job_runs`.** `job = 'snapshot_prices'` with `city_id` null, because the job spans cities. `fetched` is the number of events snapshotted, `new` the rows inserted, and `updated` the rows refreshed. A crash leaves `duration_ms` null. The duration comes from a monotonic clock, not from the run's `now`.
  - **Dry run.** It uses a read-only connection and counts what it would insert and update.
- **Workflow** `snapshot-prices.yml`:
  - Daily at 03:00 UTC, between the Skiddle ingest (02:00) and the delete job (04:00), plus manual dispatch.
  - `dry_run` defaults to true for manual runs. Scheduled runs write.
  - It uses the `db-writes` concurrency group.
- **Delete job.** It reports `events_deleted`, `venues_deleted` and `snapshots_deleted`. `job_runs.deleted` is their total.

### Trade-offs

- **No price history for an event that has ended.** That's 0007's rule, applied as intended.
- **A price only changes when ingest runs.** A daily snapshot of an unchanged price repeats the previous day's values. The planner can read that as "no change".
- **006's revoke from `anon` and `authenticated` is a safety net that tests can't see.** 001 already revoked the default privileges for tables `postgres` creates, so deleting 006's revoke changes nothing, on Supabase or in CI. The tests confirm that anon can't read or insert either way.
- **Ticketmaster events** are only snapshotted while they exist, which today means after a manual Ticketmaster run.

### Metrics and results

- **Expected volume:** about 2,500 to 5,000 rows a day, from the 5,057 Skiddle events in radius (0008), roughly half of them priced. Ended events are deleted, so the table stays at about 225,000 rows or fewer, roughly 20 to 30 MB, within the 500 MB free tier. Record the real figures after the first scheduled runs.
- **Tests:**
  - **Results:** 152 jobs tests and the db checks pass locally against migrations 001, 005 and 006.
  - **Behaviour covered:** one row per event per day; a rerun updates rather than duplicates; only current, priced events are included; the London date at three midnight boundaries (BST and GMT); a read-only dry run; the delete job's three counts.
  - **Deliberate breaks that fail the suite:** a UTC date, unpriced or ended events included, a rerun counted as new, uncounted snapshots on delete, a writable dry run, a dry run that writes, a crash recording a duration, no cascade, two status values instead of three, RLS off, and the wrong primary key.
- **Supabase:** 006 isn't applied yet. Apply it after merge in the SQL editor, with its checksum update in the same run (0006).
