# Agent

FastAPI + LangGraph service, deployed to Render as a Docker image (root directory `agent/`).
Decisions: `docs/decisions/0004-agent-host.md`, `docs/decisions/0005-agent-scaffold.md`.

```sh
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest                     # -m "not slow" skips the embedding-model tests
SUPABASE_URL=https://<project>.supabase.co uv run uvicorn app.main:app --reload
docker build -t any-plans-agent .
./scripts/memcheck.sh             # memory check at 512 MB
```

Environment: `SUPABASE_URL` (required), `CONFIG_VERSION` (default `dev`), `PORT` (default 8000).
