# 0009: Derived data (step 1.2)

Status: accepted for price snapshots (2026-10-26) and dedupe (2026-10-27). Owner: deepika. Revisit: when stage 3 builds price-trend views, if snapshot volume approaches the free-tier limit, or after the first real Ticketmaster dry run (to calibrate the dedupe threshold). OSM venues (1.2c) will add a section here.

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

## Cross-source dedupe (step 1.2b, migration 007)

### Context

The same event can be listed on both Ticketmaster and Skiddle. Showing it twice is noise, and 0008 left duplicates out of scope for step 1.1. Ticketmaster isn't scheduled yet (0001), so cross-source duplicates only appear after a manual Ticketmaster run. But the rule has to be in place before it's cleared.

### Options considered

| Area | Options | Choice | Why |
|---|---|---|---|
| Storage | Delete the weaker record and keep its link in a new `event_sources` table; **a `duplicate_of` column** | `duplicate_of` | Both records keep their own row, source, ID and URL, so both source links survive. Ingest doesn't change. Each run rebuilds the links, so the result is the same however often it runs. |
| Hiding | Filter in every query; **the public policy** | Policy | 007 adds `duplicate_of is null` to `events_public_read`, so `anon` and `authenticated` never see a duplicate. The service role bypasses RLS, so CLAUDE.md now requires agent and job queries to filter it themselves. |
| Deleting the kept record | Cascade; **`on delete set null`** | Set null | When the kept record goes (its end time, the Ticketmaster sweep, a postponement), the other becomes visible again instead of disappearing too. |
| Titles | Exact match; token overlap alone; **overlap or sequence ratio, plus a two-token rule** | Combined | Overlap of the smaller set catches added words ("EFG London Jazz Festival" against "… 2026"). The sequence ratio catches small spelling differences. The two-token rule stops one shared word from matching ("Jazz" against "Jazz Night"). |
| Pairing | Every matching pair; **one-to-one** | One-to-one | One listing can't swallow two others. |

### Decision

**The rule.** Two events are the same event when all of these hold:
- they come from different sources
- they're in the same city
- they start on the same local date (the city's timezone, computed in Python), within 30 minutes of each other
- they're within 100 m, measured with `extensions.st_distance` and `st_dwithin` on the geography columns (metres, no degree arithmetic)
- they have the same status (a cancelled and an active listing are never merged)
- their titles match

**Titles match** when:
- **Tokens** are lowercased, with accents stripped, apostrophes dropped, "&" turned into "and", punctuation removed, and these filler words dropped: the, a, an, live, presents, tickets, at, in, london.
- **The two-token rule:** at least two tokens are shared, unless both titles are the same single token.
- **The score** — the better of shared tokens ÷ the smaller set, and the `SequenceMatcher` ratio of the sorted tokens — is at least **0.85**. That threshold is a starting point, to be calibrated after the first real Ticketmaster dry run.

**One-to-one.** Pairs are taken in order: best title score, then smallest time gap, then distance. Each event is linked at most once.

**The kept record** has the most of `end_at`, `price_min`, `price_max`, `currency`, `image_url`, `url`, `genre` and `venue_id` filled. Ties go to Ticketmaster, then source name, then `source_id`. The other record gets `duplicate_of` = the kept record's ID.

**Writing.** One transaction per city clears every `duplicate_of` in the city, then sets the new links. A stale link never survives a run.

**Running.**
- `python -m jobs.dedupe [--dry-run]`, and at the end of every `jobs.ingest` run, on the same connection and in the same mode, after the ingest has recorded its `job_runs` row.
- A dry run, including the dedupe after a dry ingest, uses a read-only connection, writes nothing, and prints each pair it would link: both records, titles, score, time gap and distance.
- No workflow file changed.

**`job_runs`.** One row per city: `job = 'dedupe'`, `city_id` set to the city.
- `fetched`: candidate pairs from SQL
- `new`: links added
- `updated`: links removed or pointed at a different event
- `failed` and `deleted`: 0

A crash leaves `duration_ms` null. The duration comes from a monotonic clock.

**Agent.** No agent code reads `events` yet, so nothing changed there. CLAUDE.md now says future queries must filter `duplicate_of is null`.

**Snapshots.** The snapshot job snapshots both records of a linked pair, so each source's prices are kept. Both records still leave at their end time (0007).

### Trade-offs

- **Calibrated only on hand-written Ticketmaster data.** The matching tests pair real Skiddle events with hand-written Ticketmaster versions, so the threshold and the filler list may need changing once real Ticketmaster titles are seen.
- **Overlap of the smaller set is generous.** "Jazz Night" matches "Jazz Night at Ronnie Scotts". The time, place and status conditions limit the risk.
- **The candidate query compares every same-city pair from different sources.** It's close in time and narrowed by the start-time and GIST indexes, which is fine at today's volume.
- **The agent must remember the filter.** RLS doesn't protect service-role queries. The CLAUDE.md rule is the safeguard.

### Metrics and results

- **Tests:** 190 jobs tests and the db checks pass locally, against migrations 001 and 005 to 007.
  - **Matches:** three real Skiddle events matched with hand-written Ticketmaster versions end to end (ingest of both sources, then dedupe).
  - **Non-matches:** different London dates (23:50 against 00:10), different cities, a 31-minute gap, 101 m apart (measured on geography), dissimilar titles, the same source, and different statuses.
  - **Behaviour:** one-to-one assignment, the richer record kept, Ticketmaster winning ties, reruns giving the same result, stale links cleared, anon not seeing duplicates, and a duplicate shown again once its kept record is deleted.
  - **Wiring:** a read-only dry run that writes nothing, and a dry ingest leading to a dry dedupe.
- **Deliberate breaks:** 14 in the job and 6 in migration 007 (each applied to a fresh database) all fail the suite.
- **To record after the first real Ticketmaster dry run:** pairs linked, false matches found by hand, and whether 0.85 holds.
- **Supabase:** 007 isn't applied yet. It goes through the SQL editor after merge, with its checksum update in the same run (0006).
