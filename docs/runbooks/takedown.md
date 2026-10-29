# Runbook: take down a Ticketmaster event

Decision 0001's condition: remove an event's content **within 24 hours** of its owner's request. This runbook does it with the **Takedown** workflow (`.github/workflows/takedown.yml`, which runs `python -m jobs.takedown`).

## What a takedown does

In one database transaction:

- **Records the takedown** in `takedowns`: source, event ID, your reference, and when the request arrived. The trigger `events_skip_taken_down` (migration 025) then skips the event on every later ingest, so it can't come back.
- **Deletes the event**, matched on source `ticketmaster` **and** its event ID together. Another source's event with the same ID string is never touched.
- **Deletes its price snapshots** (they go with the event).
- **Shows again** any other source's copy that dedupe had hidden behind it (`duplicate_of` is cleared).
- **Deletes its venue** if no other event uses it.
- **Writes a `job_runs` row** (job `takedown`).

`query_results` and `clicks` hold only the source and event ID, never the event's content (decision 0007), so nothing there needs removing.

## Steps

1. **Log the request.** Note when it arrived (with the time zone), the event's Ticketmaster ID and a short reference: a ticket number or email subject. **Keep the requester's personal details out of the reference**; they stay with the request itself.
   - The ID is in the event's Ticketmaster URL (`…/event/<ID>`), or in `events.source_id`.
2. **Dry run** (GitHub → Actions → **Takedown** → *Run workflow*):
   - `event_id`: the ID
   - `reference`: your reference
   - `requested_at`: when the request arrived, ISO 8601 with offset, for example `2026-10-29T09:15:00+00:00` (blank means now)
   - `dry_run`: **on** (the default)

   The log prints `"status": "would_remove"`. If it says `no ticketmaster event … is stored`, check the ID. If the event has already ended and been deleted, use step 3 with `block_only`.
3. **Real run:** the same inputs with `dry_run` **off**.
   - The log prints `"status": "removed"` and the counts (`events`, `snapshots`, `duplicates_shown`, `venues`).
   - **`block_only`** on, for an event that isn't stored (it ended, or was never ingested): it records the takedown so the event can never be ingested. The status is `"blocked"`.
   - Running it again is harmless: it prints `"already_removed"` and changes nothing.
   - If the run finishes more than 24 hours after `requested_at`, the log warns. Note why in the reply.
4. **Check it.** With the service connection (`SUPABASE_DB_URL`):
   ```sql
   select count(*) from events where source = 'ticketmaster' and source_id = '<ID>';   -- 0
   select * from takedowns where source = 'ticketmaster' and source_id = '<ID>';        -- 1 row
   ```
5. **Reply** to the requester that the event was removed, with the time it was done (`removed_at`).

## Limits

- **Ticketmaster only.** It's the only source with a takedown condition (0001). Another source needs its own decision first.
- **No undo in the workflow.** To restore an event wrongly taken down, delete its `takedowns` row by hand with the service connection, then run a manual Ticketmaster ingest. Record why in the decision log.
- **Search results already on a screen** stay until that page is refreshed. Every new search reads the database, so the event is gone from then on.
