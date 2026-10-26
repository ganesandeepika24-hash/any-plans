# Workflow notes

Things the AI got wrong or nearly got wrong, and what caught them. Newest first.

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
