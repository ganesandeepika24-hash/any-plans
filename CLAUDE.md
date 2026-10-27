# CLAUDE.md: rules for working in this repo

This file tells Claude Code how to work on "Any Plans?". Read it at the start of every session. Decisions live in `docs/decisions/`; the build plan and PRD are the source of truth for scope.

## Stack

- Frontend (Lovable): React + Vite + Tailwind + shadcn/ui at the repo root. Owned by Lovable.
- Agent (`agent/`): Python 3.12, FastAPI, LangGraph, Pydantic v2, managed with uv. Deployed to Render.
- LLMs: Groq (primary), Cloudflare Workers AI (fallback). Models and fallback order live in the config table.
- Database (`db/`): Supabase Postgres with PostGIS, pgvector and pg_cron. Row Level Security on every table.
- Jobs (`jobs/`): Python ingestion, watches, alerts and keep-alive, run by GitHub Actions.
- Evals (`evals/`): query cases with expected filters, scored in Langfuse.

## Folder ownership

- `src/`, `public/`, `index.html`, `package.json`, `vite.config.ts`: Lovable's. Edit only when the task asks for a frontend change, and say so in the commit message.
- `agent/`, `jobs/`, `db/`, `evals/`, `docs/`, `.github/`: Claude Code's. Lovable must not edit these.

## The eight principles (from the PRD)

1. Zero running cost: free tiers only; caps in code; features pause politely at a cap.
2. Permitted sources only: official APIs within their limits.
3. Database first, web last: answers come from stored data; web search is a capped fallback.
4. One query model: the AI search bar and the filter panel fill the same `SearchFilters` object.
5. AI understands, code computes: the model extracts intent; Python resolves dates, distances and prices.
6. Honest data: every result shows freshness and verification.
7. Measure everything: log queries, results, clicks and alerts.
8. Safe surfaces: prompts, models and weights live in config, editable without a redeploy.

## Working rules

- Plan before code. For any change, write a short plan and wait for approval before implementing.
- Every change needs tests. Run `pytest` and the eval suite locally before opening a PR.
- Read every diff before committing, and be able to explain it. Note anything the AI got wrong in `docs/workflow-notes.md`.
- Record each product or architecture choice in `docs/decisions/` (options, choice, metric, result).
- Schema changes always come with a new numbered migration in `db/migrations/`. Never edit an applied migration. Apply migrations only with `db/ci/migrate.sh`, which records a checksum for each one.
- Every migration after 001 revokes and grants explicitly. For each table, sequence and function it creates: revoke all from `anon` and `authenticated` (and from `public` for functions), grant back only what that object needs, and enable RLS on tables. Never rely on default privileges: Supabase's defaults give `anon` and `authenticated` full rights, and 001 may not be able to revoke the `supabase_admin` ones (decision 0006).
- Every ingest job is idempotent, keyed by source and source ID, and writes a `job_runs` row.
- Agent and job queries that read events for users must filter `duplicate_of is null`. The service role bypasses the `events_public_read` policy, so it won't hide duplicates for you (decision 0009).

## Secrets

- Never commit secrets, and never paste them into chat, issues or docs.
- Frontend environment variables: `SUPABASE_URL` and the publishable key only.
- Everything else lives in Render and GitHub Actions secrets: `SUPABASE_SERVICE_KEY`, `SUPABASE_DB_URL` (session pooler connection string, used by the jobs and `migrate.sh`), `GROQ_API_KEY`, `CLOUDFLARE_API_TOKEN`, `CLOUDFLARE_ACCOUNT_ID`, `TAVILY_KEY`, `TICKETMASTER_KEY`, `SKIDDLE_KEY`, `LANGFUSE_*`, `RESEND_KEY`.
- If a secret is exposed, rotate it immediately, then update the decision log.

## Git

- Work on `main` only for small changes; use a feature branch and a PR for anything in `agent/`, `jobs/`, `db/` or `evals/`.
- Run `git pull` before every Claude Code session and `git push` at the end, so Lovable and Claude Code never work on stale copies.
- CI must be green before merge.