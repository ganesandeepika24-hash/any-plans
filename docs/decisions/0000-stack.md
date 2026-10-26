# 0000: Stack

Status: accepted (2026-10-25). Owner: deepika. Revisit: at the end of stage 4, or if any provider's free terms change.

## Context

"Any Plans?" is a free public AI planner for things to do in London. It is a portfolio project, built with Claude Code, with real users and measured results. Hard constraints: zero running cost, no card on file except where unavoidable (Render, for verification only), only permitted data sources, and secrets never in frontend code.

## Decision

| Layer | Choice | Why |
|---|---|---|
| Frontend | Lovable (React + Vite + Tailwind + shadcn/ui), synced to GitHub, deployed from the repo root to Vercel or Netlify | Builds a working app from chat; the code is standard and portable, and the site stays live if Lovable is cancelled |
| Maps | MapLibre with OpenFreeMap tiles | No billing account needed |
| Agent | Python FastAPI + LangGraph on Render (free web service) | Keeps AI logic in Python, where the portfolio story is; the free host is permanent |
| Structured output | Pydantic v2 | Validates every AI output before it is used |
| LLM, primary | Groq: gpt-oss-20b (Understand), gpt-oss-120b (Rank and plan) | Free, no card, JSON-schema output (decision 0002) |
| LLM, fallback | Cloudflare Workers AI, same model family | Separate infrastructure; covers a Groq outage (decision 0002) |
| Web fallback | Tavily, advanced search, 16 calls a day, 950-credit monthly ceiling | Capped; results saved for 24 hours (decision 0001) |
| Embeddings | fastembed (ONNX all-MiniLM-L6-v2, 384 dimensions) | Runs without an AI quota; shared by the agent and the nightly job |
| Database | Supabase Postgres with PostGIS, pgvector and pg_cron; Row Level Security | Radius search, meaning search and scheduled jobs in one free service |
| Auth | Supabase Auth (email magic link and Google) | Gates AI search and alerts; no separate auth service |
| Ingest | GitHub Actions (cron) calling Python jobs | Free for public repos; run logs and manual triggers included |
| Events | Ticketmaster Discovery API (bulk-use terms to be read before step 1.1), Skiddle (non-commercial, approved) | Permitted official sources (decision 0001) |
| Venues | OpenStreetMap (Overpass) | Free, with attribution (decision 0003 drops Foursquare) |
| Email | Resend | Daily digests; 3,000 a month on the free plan |
| Observability and evals | Langfuse Cloud (Hobby, 50k units a month) | Traces, cost per query, eval scores |
| Cache | None for MVP (decision 0002) | Supabase tables are enough at this traffic |
| Code workflow | Claude Code writes the agent, jobs and SQL; Lovable owns `src/` | Matches the target roles; keeps the two tools in separate folders |

## Trade-offs

- **Two codebases, one repo.** Lovable's app sits at the root and the backend sits beside it. The cost is path filters and a project-knowledge rule so Lovable doesn't touch backend folders. The benefit is that the Python agent, which carries the AI work, stays in standard tooling.
- **Free tiers may change.** Gemini and Foursquare have already changed, and Tavily's credit model needs a monthly ceiling. Every free limit is a config value or a documented cap, so a change means editing config and re-running evals, not rewriting code.
- **Render may sleep.** The free web service spins down after 15 minutes idle. The planner shows a "waking up" state. If that's unacceptable in testing, the fallback is Cloud Run (decision 0004).
- **Groq logs for 30 days.** The privacy notice says so until zero data retention is confirmed.

## Reversal

Each layer has one config value or one adapter behind it (`LLMClient`, `TravelTimeProvider`, `Source`). Swapping a provider means a config change plus an eval run before switching in production.

## Metrics

- Zero running cost: monthly usage per service against its free limit (usage dashboard, stage 3).
- Plan latency and failure rate by provider (Langfuse, `queries` table).
- Eval accuracy by version (Langfuse evals).