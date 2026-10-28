# Workflow notes

Things the AI got wrong or nearly got wrong, and what caught them. Newest first.

## 2026-10-28: stage 1 step 1, search route and counters

- **The spec's GET would have put the location in URLs.** "GET /search" and "don't log the user's location" conflict, because access logs record URLs. Raised before coding, and the owner chose POST.
- **A gap between `search_items` and `query_results`.** `search_items` returns event ids but not the `source_id` that `query_results` needs (0007). Caught by reading 010's return columns before writing the node. The node now looks the ids up.
- **A test fixture that returned everything.** The store test's fake server ignores the key filter, so the new `provider_limits` config row leaked into a test about two keys. The fixture now has only the keys asked for.
- **A wiring test that checked the wrong thing.** A deliberate break that bypassed the eval's retry in `main()` passed: the test checked that a retry wrapper was built, not that the call counter used it. It now checks what the counter wraps.
- **CI caught a test I hadn't run.** `jobs/tests/test_query_logging.py` used `route = 'search'` as its example of an unknown route, and 016 makes it valid. I ran the agent suite and the db SQL tests locally, but not the jobs suite's database tests. It now uses `refine`. Lesson: any migration that changes a constraint needs the jobs suite run against a migrated database (`TEST_DATABASE_URL`) before pushing.
- **Docs not available.** `stage-1-remaining-plan.md` and `near-me-decision-1.6.md` are claude.ai Project files that weren't pasted. Step 1 was built from the owner's message alone.

## 2026-10-28: step 1.6, round 2, vibe and who

- **Two of the owner's notes disagreed.** Round 2 said a bare "Saturday" asks (row 39), while rule 3 as built and eight eval rows read it as the coming Saturday. Caught by checking the resolver before changing row 39, and settled by the owner (the coming one) before any row changed.
- **A doc expectation the prompt rules out.** Rows 12 and 13 expect vibes inferred from the activity ("a jazz set" → Cultural), but the new prompt says not to guess a vibe from the activity. Both rows accept no vibe as well.
- **A test that only knew one migration.** The parse route's column check read added `queries` columns from 013 only, so 015's columns showed up as extra keys. It now reads every migration's `alter table public.queries`.
- **A first test that blamed the wrong thing.** The "no vibe is also right" test failed on row 12's known area gap (Bloomsbury), not on vibe. It now checks the vibe mark only.

## 2026-10-28: step 1.6, round 2, making the eval trustworthy

- **The first run's accuracy wasn't what it looked like.** Groq rate-limited 28 of 51 cases, and each fallback spent a second call. The 80-call cap then stopped the run at row 53, so the near-me, ambiguity and chips rows never ran, and 4 rested cases counted as parse failures. The runner had no pacing because the plan assumed requests per minute (30) was the limit. The real limit is tokens per minute (8,000), at about 2,000 tokens a call.
- **A fix proposed from an unchecked assumption.** The plan said to set `reasoning_effort: low` for Cloudflare. Cloudflare's docs don't confirm that its chat completions endpoint takes that setting, and a third-party config says it doesn't. Caught by checking before writing 014. It raises `max_tokens` instead.
- **A regression found by a new test.** Leaving resting cases out of the accuracies also dropped the config versions from a run where every case rested. The versions now come from every case that ran.
- **An untested entry point again.** A deliberate break that turned pacing off in `main()` passed every test, the same gap as the dry-run entry point in step 1.1. A test now runs `main()` and checks the pacer's limits.

## 2026-10-27: step 1.6, eval set and runner

### Run results

| Run | Config | End to end | Model only | Notes |
|---|---|---|---|---|
| Offline, perfect model (fake LLM returning the hand-written replies) | repo | 66.2% (47/71) | 100% (71/71) | The resolver ceiling: every failure is a known gap (0014) |
| First live run (27 Oct, run 40600304576) | supabase | 39.2% (20/51) | 51.0% (26/51) | Not a baseline: 20 cases never ran (call cap), and 4 rested. See 0014, round 2 |
| Paced rerun, the baseline (28 Oct, run 40612306072) | repo (llm v2) | 43.7% (31/71) | 57.8% (41/71) | All 71 ran on Groq, 0 fallbacks, 126,676 tokens |

### What went wrong

- **Tests that couldn't fail.** The first "wrong reply" tests edited the case's own expected reply, so the model-only score compared the reply with itself and passed. Caught when the tests failed in an unexpected way; the fake model now returns the wrong reply and the case keeps its own.
- **Two scorer gaps found by deliberate breaks.** Dropping `include_unpriced` from the budget check, and comparing places by spelling ("South Bank" against "Southbank"), both passed the suite. Each now has a test.
- **Lint config that didn't apply.** `evals/` had no ruff config, so ruff used its defaults (88 characters), not the agent's. Added `evals/ruff.toml`, which extends the agent's settings, and agent CI now lints `evals/`.
- **A sed edit that hit two jobs.** Adding `../evals` to the agent's lint step also changed the jobs job's. Seen in the diff and reverted.
- **Seed rows that disagree with later rules.** Rows 38, 39, 51, 59 and 60 follow the later docs or 0012, not the seed. Listed in 0014 for review.

## 2026-10-27: step 1.5, parser gaps

- **Briefs that pointed at docs Claude Code can't read.** The seed queries and four rule docs live in claude.ai Project files, which aren't reachable from the codespace. Claude Code looked in the repo, the branches and the artifact list, then asked for them to be pasted rather than guess "the 30 queries".
- **Seed-doc rows that contradicted their own query.** Row 32 ("this Thursday" meant 5 Nov, but this Thursday is 29 Oct) and row 22 ("Tuesday" asked on a Tuesday) were flagged before any code. The owner turned both into rules.
- **A test helper that broke on the 31st.** `date.replace(day=day + 1)` failed for 31 Oct. Switched to `timedelta`.
- **A deliberate break that passed for the wrong reason.** Removing a check line from 013 also removed a comma, so the migration failed to apply, and the "caught" result proved nothing. Redone with `check (true)`, which applies and is still caught.
- **A deploy-order trap.** Prompt v2 is in a migration, but old agent code rejects v2 replies (strict schema). Recorded in 0012: apply 013 and deploy together.

## 2026-10-27: /parse smoke test

- **A brief that couldn't work as written, raised before coding.** It asked the script to read the `queries` row with the user's token, but 002 made `queries` service-role only. Asked the owner, who chose a read-own-rows policy (migration 012).
- **`auth.uid()` doesn't exist in CI.** The policy reads `request.jwt.claims` directly, which is what `auth.uid()` does on Supabase.
- **A bad `sub` errors rather than matching nothing.** A test found it. The migration comment now says so, because it fails closed.
- **urllib changes header case** (`apikey` is sent as `Apikey`), so the test compared header names case-sensitively and failed. HTTP headers are case-insensitive, and the test now compares lowercased names.

## 2026-10-27: step 1.6, /parse route

- **The previous step's network guard would have broken CI after a quiet week.** The guard added in 1.5 blocked every real request, including the embedding model download that the `slow` tests make when CI's model cache is cold. CI only passed because the cache was warm, and GitHub evicts caches unused for 7 days. Found when the tests ran here without a cache. Tests marked `slow` are now exempt.
- **A CI collision caught in planning.** The out-of-order probe in `ci.yml` used the number 002, so a real 002 would have failed the db job. The probe now uses 000.
- **A config value that coerced.** `CapConfig` accepted the string `"10"` as 10. A test caught it, and the model is now strict.
- **A test gap found before the breaks.** Every cap test used a UTC clock, so a London-midnight day boundary would have passed. Added a summer-time case (00:30 BST is still the previous day in UTC), and the "local day" break now fails it.
- **Two test-harness slips:** moving 002 aside under a different filename made `migrate.sh` reject it, and a stopped container from an interrupted session blocked `docker run`. Neither was a code problem.
- **An interrupted session.** The work survived on disk. It was checked with `git status` and a full test run before continuing.

## 2026-10-27: step 1.5, Understand node

- **A contradiction in the brief, raised before coding.** It asked for the model's JSON to be validated against `SearchFilters`, whose keys are absolute times and coordinates, while principle 5 gives that work to code. Settled with two models, with approval.
- **An API that had moved.** The approved plan said Langfuse's batch ingestion endpoint. The current OpenAPI spec has none, and the documented plain-HTTP write path is now OTLP/HTTP. Caught by reading the spec before writing the client.
- **PostgREST and geography.** Reading `areas.centre_geom` through PostgREST would have returned WKB, not coordinates. Added `understand_context()` in migration 011 instead.
- **My seed parser invented aliases.** The test helper read `'{}'::text[]` as an alias named "{}". The uniqueness test caught it. `find_area` now also refuses a place that normalises to nothing.
- **Three missed breaks in the first round:** text around a fenced reply, and partial place names ("Green" for Greenwich), had no tests. Both were added. The third "break" (a weekend end computed as start plus 60 hours) wasn't a bug, because Python adds a `timedelta` to an aware datetime in wall-clock time. It was replaced by a real one (summing in UTC), which the tests catch.
- **A mock that didn't match PostgREST.** `httpx.Response(json=None)` sends an empty body, while PostgREST sends `null` for a function that returns null.

## 2026-10-27: step 1.3, search function and keep-alive

- **Said a branch existed when it didn't.** The step 1.3 plan said `feat/search-function` had been created, but the command was never run. Noticed when starting the code.
- **A test fixture on the exact boundary.** The visibility test's "no end time" event started exactly 4 hours ago, so changing the policy's 3-hour fallback to 4 hours still passed, because `>` excluded it either way. Found by a deliberate break. The event now starts 3.5 hours ago.
- **A local helper that hid a real result.** My script that rebuilds the local test database stopped `bootstrap.sql` at "role already exists", which skipped the schema grants. That added two false failures to every break run, which masked the missed break above until a clean baseline run was checked. CI wasn't affected: it bootstraps a new container.
- **A slow test-data generator.** The first performance script made one random vector per row and linked duplicates through a join on a computed key, which took 76 s. A pool of 1,000 vectors and an indexed lookup brought it to about 20 s.
- **A wrong row count caught by its own check.** Some generated "ended" events were multi-day events still running, so 20,039 were visible instead of 20,000. The script checks the count before timing anything.

## 2026-10-27: step 1.2d, event embeddings

- **A CHECK that rejected every ordinary row.** `embedding_hash ~ '^[0-9a-f]{64}$' and embedding is not null` is false, not null, when both columns are null, so no event could be inserted. Caught by the db checks on a local container before commit. Fixed with `embedding_hash is null or (…)`.
- **The brief didn't know embedding already existed.** Step 1.1 embedded events inside ingest. Found while planning, so the plan replaced that path instead of adding a second one.
- **Description field names from memory.** The first plan named Skiddle `description` and Ticketmaster `info`/`description` from recall. Ticketmaster's reference confirmed its fields. Skiddle's docs name no response field, so its description isn't mapped.
- **Two wrong test assumptions, caught on the first run:** a cancelled event counted as embeddable, and the first Skiddle fixture event was assumed to map (it's skipped).

## 2026-10-27: OSM venues diagnostic

- **A test that couldn't see its own bug.** The diagnostic's owner test stored only OSM venues, so dropping the `source = 'osm'` filter from the count still passed. Found by a deliberate break; the test now adds a Skiddle venue as well.
- **A weakness found while investigating.** The deletion floor treats "no stored venues visible" as a first run, so it gives no protection to a role restricted by RLS. Recorded in 0009 as open, with a proposed fix for a later PR.

## 2026-10-27: step 1.2c, OSM venues

- **The brief's extract URL was wrong.** `/europe/great-britain/` returns 404; the extract lives under `/europe/united-kingdom/`. Caught by checking the URL before planning.
- **The ODbL reading in the brief didn't match the licence text.** "Share-alike doesn't apply because we don't publish" conflicts with the ODbL summary, which covers public *use*. Caught by reading the summary and the OSMF guideline before writing 0006; the position was changed with approval.
- **Contributor metadata nearly committed.** The first fixture used Overpass `out meta`, which includes OSM user names and IDs. Re-fetched with `out body`.
- **A batch of edits silently not applied.** A multi-edit script stopped on its first failed match, after ruff had reformatted the file, and wrote nothing. Caught by checking the file before moving on.

## 2026-10-27: step 1.2b, cross-source dedupe

- **Test cleanup broken twice by foreign keys.** The different-cities test added a Manchester city and removed it afterwards. The removal failed first because Manchester still had an event, then because dedupe had correctly written a Manchester `job_runs` row. Both were caught by running the tests against a real database; the cleanup now deletes events, then `job_runs`, then the city.
- **Names checked rather than assumed.** The brief asked to confirm 001's constraint name. Applying 001 to a scratch database confirmed `venues_kind_check` and the `events_public_read` expression before 007 was written.

## 2026-10-26: step 1.2a, price snapshots

- **CI would have broken on the next migration.** The out-of-order probe in `ci.yml` checked for the literal text "005 is already applied", so adding 006 would have failed the db job. Caught while reading `ci.yml` during planning. It now works out the highest migration number from `db/migrations/`.
- **Duration measured against a fixed clock.** The new job first computed `duration_ms` as wall-clock time minus the run's `now`. With a test clock months away, that overflowed the integer column. It now uses a monotonic clock. The ingest and delete jobs still use the old pattern, which is harmless in production, where `now` is the real time. Change them when they're next touched.
- **A deliberate break that tested nothing.** A migration edit run as a jobs-test break "passed" for the wrong reason: the command ran outside the jobs project, and the database was already migrated anyway. Redone properly by applying mutated copies of 006 to a fresh database.
- **A brief that didn't match 001.** The brief gave `price_snapshots.status` two allowed values; 001 allows three. It was matched to 001, and a db test now fails if the two lists ever differ.

## 2026-10-26: step 1.1, event ingestion

- **The Skiddle fixture was written from memory.** It had a `pagelength` field and a `genres` list that Skiddle doesn't send, and it assumed GBP. The real dry run showed `pagecount`, no genres, a `currency` field and a structured `ticketpricing`. Caught by the dry run's sample. The fixture is now 40 trimmed real events.
- **A failure that left no trace.** The Skiddle fetch caught the failed request and recorded only that something failed, so the dry run's one failure couldn't be diagnosed from the log. Failures now carry status, message, window and offset.
- **One unbounded query.** Skiddle was paged as a single 90-day query, so it hit the 10,000-result ceiling. Ticketmaster already had windows; Skiddle didn't get the same treatment.
- **A price rule checked against the data before it was coded.** The approved rule put `ticketpricing` first. The sample showed it never agreed with `entryprice` and looked like a placeholder (Harry Potter at £2.99). Raised before coding, and the order was reversed with approval.
- **A wrong line in 0008.** The Skiddle mapping line said COMEDY and THEATRE map to Arts. The code was right; the record wasn't.
- **A summary detail nobody tested.** A deliberate break that dropped failure details from the run summary passed the tests. Added a check.
- **Page size quoted from memory.** An early plan assumed Ticketmaster's maximum page size is 200. The API reference documents only a default of 20 and no maximum. Caught when asked to check the reference before writing the paging code. The job now pages by the `page.size` each response reports, and a test fails if that's hard-coded.
- **Migration number already reserved.** I proposed 003 for `job_runs.deleted` without seeing the build plan, where 003 is the stage 3 metrics views. It became 005, with ordering rules in 0008.
- **Mistakes caught before commit, while writing the code:**
  - an `__import__` hack in place of a normal import
  - a leftover unused helper in `db.py`
  - a delete job that printed its result twice
  - a meaningless `return 0 if counts.deleted >= 0 else 1`
  - Skiddle times without an offset thrown away instead of read as London local time
- **An untested entry point.** The dry-run guarantee depends on `main()` turning `--dry-run` into a read-only connection, and nothing tested `main()`. Added a test, then checked that a deliberate break (a writable connection) fails it.

## 2026-10-26: step 0.5, core schema

- **The approved CI image couldn't install pgvector.** `postgis/postgis:17-3.5` is on Debian bullseye, whose PGDG apt repo now returns 404. The plan assumed the install would work. A local test before writing any SQL caught it. Switched to `17-3.6` (trixie).
- **Invalid workflow file.** `${{ job.services.postgres.id }}` was used in a job-level `env:`, where the `job` context isn't available. GitHub rejected the file before any job ran, so it showed as a 0-second failure. Fixed by setting it in a step.
- **PostGIS already in the image's default database.** `create extension postgis` failed because the image's init script installs it in `public`. Against that database, `create extension if not exists … with schema extensions` would silently do nothing. CI now uses a fresh database from `template1`.
- **YAML read a step as a mapping.** `run: db/ci/migrate.sh | … grep -q "001_core.sql: already applied"` was a plain scalar containing `": "`, so GitHub rejected `ci.yml` again (a second 0-second failure). Fixed with a block scalar. Lesson: parse workflow YAML locally (`python3 -c 'import yaml; yaml.safe_load(open(...))'`) before pushing.
- **A test that passed for the wrong reason.** The "anon can't call `set_active_config`" check passed even with EXECUTE left on `public`, because the call failed inside the function on the `config` read with the same error. Found by deliberately breaking the migration. Replaced with a direct `has_function_privilege` check.
- **A default-privilege revoke that could fail on Supabase.** The instruction to revoke `supabase_admin`'s defaults would fail if `postgres` isn't a member of `supabase_admin`, and CI couldn't show it because CI's `postgres` is a superuser. Moved into a membership-checked `DO` block.

## 2026-10-26: step 0.4, agent scaffold

- **Personal email in `pyproject.toml`.** `uv init` copied the git author email into `authors`. Removed before commit, because the repo is public. Caught by reading the generated file.
- **Wrong memory metric.** The first memory check reported cgroup `memory.peak` = 512 MB, which looks like a failure. It was page cache, which is reclaimable, and there were no OOM kills. Switched to working set (`memory.current` − `inactive_file`) and process VmHWM.
- **Measurement counted two processes.** The second attempt embedded through `docker exec`, so a second Python interpreter sat in the same cgroup (377 MB working set). Replaced by a single-process check (`agent/scripts/memcheck.py`), which gives 232 to 241 MB.
- **Model cache unreadable by the runtime user.** huggingface_hub wrote a cache file as root with restrictive permissions, and the non-root container user got "Permission denied". Fixed with `chmod -R a+rX /opt/fastembed` in the build stage. Caught in the container logs.
- **Action tag that doesn't exist.** `ci.yml` used `astral-sh/setup-uv@v10`, but setup-uv publishes only full version tags, so the first CI run failed at "Set up job". Pinned to `v10.3.2`. actionlint didn't catch it because it doesn't resolve tags. Lesson: check each `uses:` ref with `gh api repos/<owner>/<repo>/git/ref/tags/<tag>` before pushing.
