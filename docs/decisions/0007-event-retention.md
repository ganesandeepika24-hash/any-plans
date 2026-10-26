# 0007: Event retention

Status: accepted (2026-10-26). Owner: deepika. Revisit: if Ticketmaster's terms change or a source's terms allow longer retention, or before migration 002 if the measurement tables need more than the source and source ID.

## Context

Ticketmaster's terms (read 26 Oct 2026, decision 0001) don't allow us to keep its content beyond reasonable periods, and we must keep no archive. An event that has ended has no use to a visitor, so ended events must leave the database. They shouldn't just be hidden.

Migration 001 already hides ended events from the public: the `events_public_read` policy treats an event as ended when `coalesce(end_at, start_at + interval '3 hours') <= now()`. Hidden isn't deleted, though: `events.status` has a `past` value, and nothing removes rows.

Later tables point at events. Migration 002 (`query_results`, `clicks`) and stage 1.2 (`price_snapshots`) record what users saw and what prices were. If they copied event details, they would be an archive. If they held a hard foreign key, deletes would fail or cascade away our measurements.

## Options considered

| Area | Options | Choice | Why |
|---|---|---|---|
| Ended events | Keep and mark `past`; keep for N days; **delete after the end time** | Delete | The terms allow no archive. The public already can't see ended events, so deleting changes nothing a visitor sees. |
| No `end_at` | Never delete; delete at `start_at`; **`start_at` + 3 hours** | Start plus 3 hours | Same rule as the public-read policy, so an event leaves the table the moment it stops being visible. |
| Where the delete runs | pg_cron in Supabase; **GitHub Actions job** | GitHub Actions | Same workflow family as ingest, writes a `job_runs` row, and shows in the Actions log. pg_cron stays available if Actions proves unreliable. |
| Links from measurement tables | Copy event fields; hard FK with cascade; **source and source ID, with no FK or an FK with `on delete set null`** | Source and source ID | Keeps the measurement (which result, which position, clicked or not) without keeping a copy of the event. |

## Decision

- A nightly job deletes every event, from every source, where `coalesce(end_at, start_at + interval '3 hours') <= now()`. Python computes the cutoff (principle 5). The job writes a `job_runs` row, inserted at the start with `duration_ms` filled at the end. The deleted count goes in `job_runs.deleted`, added by migration 005.
- The same nightly job deletes Ticketmaster venues that no longer have any events. It applies the "no archive" condition (0001) to venue details too. `curated_pages` rows for those venues go with them (`on delete cascade`). The deleted venues count towards `job_runs.deleted`.
- Ingest must set `end_at` for multi-day events. Otherwise they are deleted 3 hours after they open, as they already disappear from the public view (0006).
- Ingest doesn't insert an event that has already ended under this rule.
- **Migration 002:** `query_results`, `clicks` and any other table that refers to an event stores `source` and `source_id`. It has no FK to `events`, or an FK with `on delete set null`. It never stores the event's title, venue, price, image or URL.
- **Stage 1.2:** `price_snapshots` for Ticketmaster events follow the same rule. Snapshots are deleted when their event is deleted, or by the same end-time cutoff.
- `events.status = 'past'` is no longer needed for retention. It stays in the schema (never edit an applied migration) and can be dropped in a later migration.

## Trade-offs

- **No history of past events.** We can't show "events you went to" or analyse past listings. Measurements keep only the source and source ID, so they can be joined to an event only while it still exists.
- **The 3-hour rule deletes long events that have no end time.** That's the cost of not guessing durations. Ingest is responsible for `end_at`.
- **GitHub's scheduled runs are best effort.** They can start late, and they are disabled after 60 days without repository activity. A missed night leaves ended events in the table but still hidden from the public. The metric below catches it.

## Metrics and results

- **Deleted rows per night:** `job_runs.deleted` on the delete job's row (migration 005), covering events and orphaned Ticketmaster venues.
- **Zero events older than their end time:** `select count(*) from events where coalesce(end_at, start_at + interval '3 hours') <= now() - interval '1 day'` should be 0. It allows one day for the next nightly run.
- Results: none yet. The job is built in step 1.1.
