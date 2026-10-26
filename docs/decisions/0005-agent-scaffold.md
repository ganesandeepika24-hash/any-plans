# 0005: Agent scaffold

Status: accepted (2026-10-26). Owner: deepika. Revisit: when the first real LangGraph nodes land (stage 1), or if the Render memory check in 0004 fails.

## Context

Step 0.4 scaffolds the agent service in `agent/`: a FastAPI app that Render can run from a Docker image, with auth, embeddings, a placeholder graph and CI. It has to fit Render's 512 MB free instance (decision 0004), verify Supabase users without a shared secret, and keep `/health` cheap enough for Render's health check and the "waking up" state.

## Options considered

| Area | Options | Choice | Why |
|---|---|---|---|
| Embeddings | sentence-transformers (PyTorch); **fastembed** (ONNX build of all-MiniLM-L6-v2, 384 dimensions) | fastembed | No PyTorch, so a far smaller image and memory footprint. Same model and vectors for the agent and the nightly job (0000). |
| Model delivery | Download at startup; **bake into the image** | Baked | No network call on cold start; the container runs with `--network=none`. `HF_HUB_OFFLINE=1` at runtime. |
| Model loading | Load at startup; **load on first embed** | First embed | Keeps `/health` and cold start fast (about 3 s locally). The first embed pays about 1 s. |
| JWT verification | Shared secret (`SUPABASE_JWT_SECRET`, HS256); **JWKS with ES256** | JWKS, ES256 only | The project uses asymmetric signing keys. Tokens are verified against `{SUPABASE_URL}/auth/v1/.well-known/jwks.json`, the algorithm is pinned to ES256, and `aud=authenticated` and `iss={SUPABASE_URL}/auth/v1` are checked. There is no secret to leak or rotate in the agent. |
| Where auth runs | Middleware; **dependency (`require_user`)** | Dependency | Attached per router (`APIRouter(dependencies=[Depends(require_user)])`), so public routes such as `/health` stay public by construction, and tests can override the JWKS client. |
| Health check | Database ping; **static response** | Static | `{"status": "ok", "config_version": ...}` from the `CONFIG_VERSION` env var (default `dev`). No database call, so Render's checks never wake Supabase or count against its limits. |
| LangGraph | Add later; **placeholder graph compiled at startup** | Placeholder | 0004's memory risk is "fastembed plus LangGraph", so the memory check must load both. |
| Secret scan | Inside `ci.yml`; **separate `secret-scan.yml`** | Separate | `ci.yml` is path-filtered to backend folders. Secrets are most likely to leak from the frontend, so gitleaks runs on every push and PR with full history. |

## Decision

- `agent/` is a uv project (Python 3.12, `uv.lock` committed) with a flat `app/` package: `config.py`, `auth.py`, `embeddings.py`, `graph.py`, `routes/health.py`, `main.py`.
- No protected route exists in the app yet. The auth tests mount a test-only protected router.
- Docker: two stages (uv build, then `python:3.12-slim` runtime), non-root user, model baked into `/opt/fastembed`, binds to `$PORT` (default 8000).
- CI (`.github/workflows/ci.yml`): on pushes to `main` and PRs touching `agent/**`, `jobs/**`, `db/**`, `evals/**` or the workflow file, one `agent` job runs `uv sync --frozen`, `ruff check`, `ruff format --check`, `pytest` and a Docker build.
- Secret scan (`.github/workflows/secret-scan.yml`): gitleaks 8.30.1 (checksum-verified binary, no third-party action) on every push and PR.

## Trade-offs

- **The JWKS fetch is a runtime dependency.** If Supabase's JWKS endpoint is unreachable and the cached keys have expired (10-minute cache), protected routes return 503, not 401. A token with an unknown `kid` makes PyJWT refetch the JWKS, so junk tokens can cause extra fetches. That's acceptable at our traffic. Revisit if we see abuse.
- **Lazy model load** means the first embed after a cold start is about 1 s slower, in exchange for a faster wake-up and a cheaper health check.
- **The model download in CI** comes from Hugging Face on a cold cache, so a Hugging Face outage can make CI red. CI caches the model and the Docker layers to make this rare.
- **CI on `db/`, `jobs/` and `evals/` changes** runs the agent job, which is wasted until those folders get their own jobs.

## Metrics and results

Measured locally on 2026-10-26: a codespace with Docker, cgroup v2, a container limited with `--memory=512m --memory-swap=512m --network=none`. This is a **local cgroup measurement, not a Render measurement**. The Render deploy check in 0004 stays open.

Method (`agent/scripts/memcheck.sh`): one Python process inside the image starts uvicorn (graph compiled at startup), calls `/health` over HTTP, then embeds a batch of 64 texts. cgroup memory is sampled every 20 ms. Working set = `memory.current` − `inactive_file`, the figure `docker stats` reports. Raw `memory.peak` is not used, because page cache fills it to the limit and is reclaimable. An earlier two-process method (server plus a `docker exec` embed process) counted two interpreters and was dropped.

| Measure | Target | Result (4 runs) |
|---|---|---|
| Peak memory, process RSS high-water (VmHWM) | Under 350 MB | **280 to 289 MB** |
| Peak cgroup working set after embed batch | Under 350 MB | **232 to 241 MB** |
| Working set after startup and `/health` | Track | 93 MB |
| OOM kills at 512 MB | 0 | **0** |
| `/health` response | `{"status": "ok", "config_version": ...}` | Correct, with `CONFIG_VERSION` override |
| Embeddings | 384 dimensions | 64 vectors × 384 |
| Image size, unpacked filesystem | Track | **524 MB** (venv 313 MB, model 87 MB) |
| Image size, compressed (`docker save` + gzip) | Track | about 227 MB |
| Image size as `docker image ls` reports it | Track | 788 MB (the containerd store counts compressed and unpacked copies) |
| Tests | Green | 24 passed (`pytest`), ruff clean |

The largest packages are onnxruntime (68 MB) and numpy (70 MB with its libs). Both are fastembed dependencies.

## Reversal

- Embeddings sit behind `app/embeddings.embed()`. Changing the model means changing `MODEL_NAME` and the Dockerfile download line (a test checks they match), then re-embedding stored vectors.
- Auth sits in `require_user`. Supporting another algorithm or issuer is a change to that one function and its tests.
- If Render's real memory exceeds 512 MB, 0004's fallback applies: same image on Cloud Run.

## Open checks

- Deploy to Render and record peak memory on the real instance (0004).
- Revisit the 350 MB headroom once real LangGraph nodes and the Groq client are loaded.
