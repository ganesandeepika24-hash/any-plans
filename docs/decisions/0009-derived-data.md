# 0009: Derived data (step 1.2)

Status: accepted for price snapshots (2026-10-26), dedupe (2026-10-27), OSM venues (2026-10-27) and event embeddings (2026-10-27). Owner: deepika. Revisit: when stage 3 builds price-trend views, if snapshot volume approaches the free-tier limit, or after the first real Ticketmaster dry run (to calibrate the dedupe threshold).

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

## OpenStreetMap venues (step 1.2c, migration 008)

### Context

The planner needs places as well as events: restaurants, bars, pubs, theatres, museums and galleries. OpenStreetMap is the permitted source (0001; Foursquare stays dropped, 0003). On 26 Oct 2026 an Overpass count found 14,099 named venues of these kinds in Greater London. Of those, 6,090 had a `website` tag and only 9 had a booking tag.

### Options considered

| Area | Options | Choice | Why |
|---|---|---|---|
| Source | The public Overpass API; **Geofabrik's Greater London extract** | Geofabrik | Overpass refused our first query ("at capacity"), and its page asks automated users to run their own instance. Geofabrik rebuilds the extract daily, and one download a week is ordinary use. |
| Boundary | Geofabrik's cut-out; **Greater London (relation 175342), built from the extract** | The relation | Geofabrik's cut-out has a buffer, so the relation is assembled from the extract and each venue's point is tested with shapely. |
| Way locations | The average of the nodes; **the centre of the bounding box** | Bounding box | It's what Overpass's `out center` returns, so the job matches the documented query. |
| Multipolygons | Assemble them; **skip them for now** | Skip | They're 22 of about 14,000 venues. Skipped relations are counted in the summary. |
| Recording the data's date | The run summary only; **`job_runs.data_as_of` (migration 008)** | Column | Each run's row then shows which day's OSM data it used. The download date is the run's `started_at`. |

### Decision

- **Source:** https://download.geofabrik.de/europe/united-kingdom/england/greater-london-latest.osm.pbf (about 130 MB). The brief's `/great-britain/` path returns 404.
  - It's read with pyosmium, which supplies node locations so a way's centre can be computed.
  - **`data_as_of`** is the extract's replication timestamp, from the PBF header (`osmosis_replication_timestamp`). The fallback is Geofabrik's `greater-london-updates/state.txt`.
- **Selection:** the Overpass query in `jobs/osm_venues.py`'s docstring.
  - Named features with `amenity` = restaurant, bar, pub or theatre, or `tourism` = museum or gallery. If a feature has both, `amenity` wins.
  - They must be inside relation 175342.
  - A node uses its location; a way uses its bounding-box centre, rounded to 7 decimal places.
  - Skipped and counted: relations (multipolygons), unnamed features, and features outside the boundary.
- **Mapping:**
  - `source 'osm'`, `source_id` as `node/<id>` or `way/<id>`, `city_id 'london'`.
  - `kind`: restaurant, bar, pub, theatre, museum or gallery, all allowed by `venues_kind_check` (007).
  - `address` from the `addr:*` tags.
  - `booking_url`: the first of `website:booking`, `reservation:website` or `reservation:url`.
  - `website_url`: `website`, or else `contact:website`.
  - The upsert is on `(source, source_id)` and only touches rows whose data changed. So `new` and `updated` are real changes, and unchanged rows appear only in the summary.
- **Complete run:** the file parsed in full, the boundary relation was built, and `data_as_of` is no more than 2 days older than the run's start.
  - A parse error or a missing or incomplete boundary stops the run before any venue is written.
  - An extract more than 2 days old, or with no timestamp, updates venues but deletes none.
- **Vanished venues** (stored but not seen in this run) are deleted only after a complete run, and only if the run saw at least **90%** of the OSM venues already stored. That floor stops a selection bug from wiping the table.
  - A venue with `curated_pages` is never deleted, because the cascade at `001_core.sql:128` would delete the pages. Those venues are kept and counted.
- **Job:** `python -m jobs.osm_venues [--dry-run] [--file PATH]`.
  - One `job_runs` row with `job 'osm_venues'`, `city_id 'london'` and `data_as_of`.
  - `fetched` is every matching feature read from the file, before filters. `new` and `updated` are the upserts, and `deleted` is the vanished venues removed.
  - A dry run is read-only and prints the counts it would write.
- **Workflow:** `ingest-osm-venues.yml` runs weekly on Sundays at 05:00 UTC (`0 5 * * 0`), plus manual dispatch.
  - The schedule was added after the first dry run had been reviewed. 05:00 UTC comes after the nightly Skiddle (02:00), snapshot (03:00) and delete (04:00) jobs.
  - Scheduled runs have no inputs, so they write. Manual runs keep `dry_run` defaulting to true.
  - `test_workflow_guard.py` requires the triggers to be exactly `{schedule, workflow_dispatch}`. It also runs the step's script as a scheduled run would and checks that no `--dry-run` is passed.
  - It uses the `db-writes` concurrency group.
- **Embeddings** for venues wait for stage 2.
- **Diagnostic:** `python -m jobs.osm_venues --dry-run --diagnose`, or the workflow's `diagnose` input, which always adds `--dry-run`, prints facts about the connection and stops. It reports:
  - `current_database()`, `inet_server_addr()`, `current_user` and `session_user`
  - the role's `rolbypassrls` and `rolsuper`
  - who owns `venues`, and whether its RLS is enabled or forced
  - the OSM venue count the floor would see
  - unfiltered and `osm_venues` counts of `job_runs`, with the last five `osm_venues` runs

  It's read-only and downloads nothing. `--diagnose` without `--dry-run` is refused.
- **ODbL:** the attribution, share-alike and no-merging rules in 0006.

### Trade-offs

- **A weekly snapshot.** Venues that open or close show up within a week.
- **No booking links for most venues.** Only 9 London venues carry a booking tag, so the planner mostly links to `website_url`.
- **Skipping multipolygons** drops some large venues, such as the British Museum and Shakespeare's Globe, until they're assembled.
- **The 2-day freshness limit** leaves about half a day's margin if Geofabrik is late.
- **The ODbL rules constrain future features.** Matching OSM venues to event venues needs a new decision first.
- **The floor trusts what the connection can see (open).** With no stored venues visible, the floor treats the run as a first run and passes.
  - A role that neither owns `venues` nor bypasses RLS sees none, so for that role the floor gives no protection.
  - Deletes would also be filtered by RLS, and writes would fail, so no rows would actually be lost.
  - The diagnostic shows which case applies on Supabase.
  - Proposed follow-up: refuse deletions unless the connection owns `venues` or has `rolbypassrls`.

### Metrics and results

- **Tests:**
  - **Results:** 230 jobs tests and the db checks pass locally, against migrations 001 and 005 to 008.
  - **Fixture:** real City of London data, fetched from Overpass on 27 Oct 2026 without contributor metadata. City of London's boundary (relation 51800) stands in for Greater London.
  - **Behaviour covered:**
    - the selection, kinds, tag precedence and way centres
    - the boundary: one real venue just over the City's edge is excluded
    - parse errors, and missing or incomplete boundaries
    - stale extracts, the 90% floor and curated pages
    - dry runs, upserts and `job_runs`
- **Deliberate breaks:** 15 in the job and 3 in migration 008, each applied to a fresh database, all fail the suite.
- **To record after the first dry run:** the extract file and its `data_as_of`, features read, venues selected, skip counts, and run time.
- **Supabase:** 008 isn't applied yet. Apply it after merge in the SQL editor, with its checksum update in the same run (0006).

## Event embeddings (step 1.2d, migration 009)

### Context

Search ranks events by meaning as well as by filters. The agent embeds queries with fastembed's all-MiniLM-L6-v2 (384 dimensions, decision 0005), so events must be embedded with the same model. `events.embedding vector(384)` and its HNSW index have existed since 001.

Step 1.1 already embedded events inside `ingest.run()`, with the text `title · genre · venue`. The upsert cleared the vector when one of those columns changed (0008). That approach had four gaps:
- category was left out, and descriptions weren't stored at all
- renaming a venue didn't trigger a re-embed
- duplicates and cancelled events were embedded, although users never see them

### Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| Change detection | Compare columns in the upsert (1.1); **store a hash of the embedded text on the row**; re-embed everything every night | Hash | Covers every input, including venue renames and fields added later, with no SQL to keep in step. A model change re-embeds too. |
| Where it runs | Inside `ingest.run()`; **after dedupe in `ingest.main()`, plus a manual workflow**; its own schedule | After dedupe | Duplicates are already linked, so they're skipped. No new schedule. Same pattern and connection mode as dedupe. |
| Sharing the model | Import `agent/app/embeddings.py`; **keep the copy in `jobs/jobs/embeddings.py`** | Copy | `agent/` and `jobs/` are separate uv projects. `test_embeddings_model.py` already fails CI if the model name or dimensions differ. |
| Descriptions | Map the fields from memory; **map only fields the provider documents** | Documented only | See the source notes below. |

### Decision

- **Text:** title, category, genre, venue name and description, joined with ` · `, skipping empty fields.
  - Description goes last: MiniLM truncates long input, so that's the part to lose.
- **Hash:** `sha256(model name + "\n" + text)`, stored in `events.embedding_hash`. A run embeds a row only when the hash of its current text differs, so unchanged rows never reach the model.
- **Selection:** only events users can see, `status = 'active'` and `duplicate_of is null`. The service role bypasses `events_public_read`, so the query filters both itself.
  - An event that stops being a duplicate, or is restored from cancelled, has a missing or stale hash and is embedded on the next run.
- **Writes:**
  - Batches of 256, each written by one `update … from unnest(…)` statement and committed on its own, so a failed run keeps the batches before it. (Until 29 Oct 2026 each row was its own update; see "Changes".)
  - Vectors are checked as 384 long before they're written.
  - The ingest upsert no longer clears `embedding`. A changed event keeps its old vector until the embedding step replaces it.
- **Job:** `python -m jobs.embed_events [--dry-run]`.
  - `ingest.main()` runs it after dedupe, on the same connection and in the same mode.
  - One `job_runs` row with `job 'embed_events'` and `city_id` null, because it spans cities.
    - `fetched`: visible events read
    - `new`: rows with no hash, embedded for the first time or for the first time since 009
    - `updated`: rows whose text or model changed
  - A crashed run keeps `duration_ms` null (0006).
- **Dry run:** needs a read-only connection. It counts what it would embed, prints up to five texts, writes nothing and never loads the model. An ingest dry run gets a dry embedding run.
- **Workflow:** `embed-events.yml` is manual only, for dry runs and backfills, with `dry_run` defaulting to true. It caches the model and uses the `db-writes` concurrency group.
  - `test_workflow_guard.py` pins its triggers to exactly `{workflow_dispatch}`.
- **Descriptions:**
  - Stored in `events.description` as plain text: tags stripped, entities decoded, whitespace collapsed, and cut at a word boundary to at most 1,000 characters.
  - **Ticketmaster:** `info`, falling back to `description`.
    - Checked against the Discovery API v2 reference (developer.ticketmaster.com/products-and-docs/apis/discovery-api/v2/), read 27 Oct 2026. Its Event Search response lists:
      - `description` ("Description's of the entity")
      - `additionalInfo` ("Additional information of the entity")
      - `info` ("Any information related to the event")
      - `pleaseNote` ("Any notes related to the event")
    - `pleaseNote` and `additionalInfo` are logistics, such as entry rules, so they're left out.
    - No Ticketmaster sample was fetched. The order of preference is a judgement, not something the docs state.
  - **Skiddle: not mapped.**
    - The Events API docs (github.com/Skiddle/web-api/wiki/Events-API, wiki revision 3664cd1 of 18 Oct 2019, read 27 Oct 2026) document only a `description` request parameter. Its documented effect is that "your results will contain artist and genre information for each event".
    - No page of the wiki names a response field for event text, and the ingest job doesn't send that parameter.
    - Skiddle descriptions stay null until the field is confirmed, either from a saved sample (`ingest-skiddle.yml` with `save_sample`) or by asking dev@skiddle.com.
- **Migration 009:**
  - adds `events.description text` (at most 1,000 characters)
  - adds `events.embedding_hash text`: 64 hex characters, allowed only alongside a vector
  - creates no new objects, so it has no grants
  - 001's table-level select lets `anon` and `authenticated` read both columns on visible events. Neither is sensitive.

### Trade-offs

- **Every run reads every visible event's text** to compare hashes. Retention (0007) bounds this to the events still ahead, about 5,000 to 10,000 rows.
- **Stale vectors.** Between an ingest and its embedding step, a changed event is searched with its old vector. If the step fails, the stale vector stays until the next run. The ingest's own `job_runs` row is already recorded, and the workflow fails visibly.
- **A one-off re-embed.** Vectors written before 009 have no hash, so the first run after merge re-embeds every visible event once.
- **Most scheduled events have no description** while Skiddle's field is unconfirmed. Their text is title, category, genre and venue, which is richer than 1.1's text.
- **Long descriptions are truncated by the model.** The title, category, genre and venue come first, so they're always embedded.

### Metrics and results

- **Tests:**
  - **Results:** 275 jobs tests and the db checks pass locally against a throwaway `postgis/postgis:17-3.6` container (migrations 001 and 005 to 009), with a fake embedder. Nothing ran against Supabase, and no model was downloaded. An autouse fixture fails any test that loads the real model.
  - **Behaviour covered:**
    - the text, its order and the hash
    - description cleaning and the Ticketmaster mapping, and that Skiddle's isn't mapped
    - first runs, unchanged rows, and changes to the title, category, genre, description and venue name
    - pre-009 vectors, a model change, and an event that stops being a duplicate
    - batch commits, wrong-length vectors, dry runs and `job_runs`
    - ingest's order: ingest, then dedupe, then embedding
- **Deliberate breaks:** 15 in the job and 3 in migration 009 all fail the suite. The migration breaks were each applied to a fresh database.
- **To record after the first scheduled run:** the `embed_events` row's `fetched`, `new` and `duration_ms` for the one-off re-embed, then a normal night's figures.
- **Supabase:** apply 009 **before** merging. The nightly Skiddle ingest writes `events.description` and `embedding_hash`, so it fails without them. The migration only adds columns, so today's code runs unchanged on a database that has it. Apply it in the SQL editor with its checksum update in the same run (0006).

## Changes

- **29 Oct 2026: one statement per batch, and an hour for the Skiddle workflow.** Nightly Skiddle runs 7 and 8 were cancelled at the 30-minute limit, with the `embed_events` row's `duration_ms` still null. The model came from the Actions cache and fastembed batched the texts. The hash check was skipping what it should: Skiddle's text is title, category and venue, which is the same every night. But run 6 embedded 4,985 events with the code from before 009, with no hash, so runs 7 and 8 each had about 5,000 rows to re-embed, the one-off case this decision expects. Each row was its own `update` to the session pooler, so the writes took one round trip per row, after an ingest that already writes row by row. The fix writes each batch in one statement, which is 20 round trips instead of about 5,000. `ingest-skiddle.yml` now has `timeout-minutes: 60` and `PYTHONUNBUFFERED=1`. The job's output had been buffered until exit, so every log line carried the same timestamp and the logs couldn't show which phase was slow. Each batch now logs its progress to stderr.
  - To record after the next scheduled run: the `embed_events` row's `duration_ms`, and the ingest row's, from the timestamped log.
