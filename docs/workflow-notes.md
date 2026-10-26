# Workflow notes

Things the AI got wrong or nearly got wrong, and what caught them. Newest first.

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
