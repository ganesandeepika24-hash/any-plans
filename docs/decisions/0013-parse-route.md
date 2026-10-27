# 0013: The /parse route, query logging and the daily cap (step 1.6, migration 002)

Status: accepted (2026-10-27). Owner: deepika. Revisit: when Q-02's own column list is checked against the proposal below, when the search route starts writing `query_results`, before running more than one agent instance (the cap's reservation is in-process), and when a retention rule for `queries` is set.

## Context

The browser needs one call that turns a search into filters: the Understand step (0012) behind a protected route. Principle 7 says to log every query and result, per Q-02. Decision 0002 sets a cap of 10 AI searches a day per user as a config value, and principle 1 says caps belong in code, with features pausing politely at a cap. When the LLM providers fail, search must keep working without AI.

Q-02 itself (in the PRD) isn't in the repo, so the `queries` columns below are a proposal. The number 002 was reserved for logging (0006, 0008). Supabase already has 005 to 011, so 002 lands there out of name order.

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| `queries` columns | Wait for Q-02's text; **propose them now and record them here** | Proposal | Unblocks the route. The proposal covers what the metrics in 0000 and 0002 need: provider error rate, latency, config versions and Langfuse links. |
| `query_results` from `/parse` | **Write none**; write the parse output there | None | 0007 defines `query_results` as the events a user was shown, stored as `source` and `source_id`. A parse shows no events. The table is created now, for the search route. |
| When to write the `queries` row | Before replying; **after replying (a background task)** | After | Adds no latency. A failed write is logged and never fails the request. |
| The daily cap | Leave for later; **count today's `queries` rows** | Count rows | `/parse` is what spends LLM calls. The count needs no new table. |
| The cap's day | London day; **UTC day** | UTC | It matches the providers' daily resets and `usage_daily` (0002). |
| A failed cap check | Allow the search; **rest without calling a model** | Rest | Zero cost comes first (principle 1). If Supabase is down, filter-only search is down too, so little is lost. |
| `ai_resting` status code | 503; **200 with `status` and `reason`** | 200 | Resting is a designed outcome the browser handles, not an error. |
| Filter-only fallback | An agent route; **the browser reads `events` directly** | Browser | `anon` can already read visible events through `events_public_read`. `search_items` stays `service_role`-only (0010). |

## Decision

**Migration 002** (depends only on 001)
- **`queries`**, the proposed Q-02 columns:

  | Column | Type | Notes |
  |---|---|---|
  | `id` | uuid | Primary key. Generated in the agent, so the reply can return it before the row is written. |
  | `created_at` | timestamptz | Defaults to now(). |
  | `user_id` | uuid | The JWT's `sub`. No foreign key, because CI has no `auth` schema. |
  | `city_id` | text | References `cities`. |
  | `route` | text | `parse` |
  | `query_text` | text | 1 to 500 characters, trimmed. |
  | `status` | text | `ok`, `ai_resting` or `capped`. |
  | `provider` | text | The provider that answered. Null unless `ok`. |
  | `attempts` | jsonb | `[{provider, model, outcome}]` |
  | `filters` | jsonb | `search_items`' argument. Set exactly when `status` is `ok`. |
  | `embedding_text` | text | |
  | `notes` | jsonb | `[{code, message}]` |
  | `config_versions` | jsonb | For example `{"llm_understand": 1, "understand_prompt": 1, "ai_search_cap": 1}`. |
  | `release` | text | `CONFIG_VERSION` |
  | `trace_id` | text | The Langfuse trace, 32 hex characters. Null when capped. |
  | `latency_ms` | integer | ≥ 0 |

  An index on `(user_id, created_at)` serves the cap count.
- **`query_results`:** `query_id` (references `queries`, `on delete cascade`), `rank` (≥ 1), `source`, `source_id`, `score`. Primary key `(query_id, rank)`. There's no foreign key to `events`, and no event details (0007).
- **`ai_search_cap`** v1: `{"per_user_per_day": 10}`.
- **Privileges:** both tables have all rights revoked from `anon` and `authenticated`, are granted to `service_role`, and have RLS on with no policies.
- **CI:** the "out-of-order apply" probe in `ci.yml` used the number 002, which now collides with the real file. It now uses 000, which can never be a real migration and always sorts below the latest.

**`POST /parse`** (`app/routes/parse.py`, `app/parse.py`)
- **Request:** `{"query": "..."}`, 1 to 500 characters after trimming. Unknown fields are refused.
- **Auth:** `require_user`, the existing Supabase JWT check (ES256, JWKS, `aud` and `iss`).
  - A missing or invalid token, or a `sub` that isn't a UUID, gets 401 before the body is read, so nothing runs and nothing is logged.
  - The JWKS endpoint being down gets 503, as before.
- **Errors:** a bad body with a valid token gets 422.
- **200, `ok`:** `status`, `query_id`, `filters` (exactly `SearchFilters.to_json()`), `embedding_text`, `notes` and `trace_id`.
- **200, `ai_resting`:** `filters`, `embedding_text` and `notes` are empty. `reason` and `message` say why:
  - `providers`: every provider failed. "AI search is resting; filters still work".
  - `daily_cap`: the user hit the cap. "Daily AI search limit reached; filters still work". No model is called, and the row is logged as `capped`.
  - `unavailable`: config or the cap count couldn't be read. "AI search is resting; filters still work". No model is called.
- **Logging:** one `queries` row per authenticated request, written through PostgREST with the service key after the reply is sent.

**The daily cap**
- **Counted rows:** the user's rows since midnight UTC with `status` in (`ok`, `ai_resting`). Capped requests don't count.
- **Changing it:** the cap is read from `ai_search_cap`, so it changes with `set_active_config` (principle 8). Config values must have the right types: a cap written as `"10"` makes the cap unusable, so the route rests rather than guess.
- **No slipping past it:** the check and a reservation happen under one lock, and the reservation lasts until the row is written. So parallel requests from one user can't pass the cap together. Reservations are released even if the write or the Understand step fails.

## Trade-offs

- **The reservation is per process.** Render runs one instance (0004). A second instance would need the reservation in the database, for example a pending row.
- **Query text is stored with the user's ID**, and there's no retention rule yet. Deleting a user doesn't delete their queries, because there's no foreign key to `auth.users`. A retention decision is needed before launch, and the privacy notice must cover it.
- **A failed log write loses that row.** It would then not count against the cap: a small leak, in the user's favour.
- **Deferred to later steps:** `profiles`, `clicks` and `feedback`. They'll arrive in migrations numbered above the latest one on Supabase, because 002 is now taken.

## Metrics and results

- **Agent tests:** 265 pass with no network. The `/parse` tests use the real JWT check with an in-memory JWKS, the fake LLM and an in-memory query log. They cover:
  - every auth failure (nothing runs or is logged)
  - ok replies, notes, every `ai_resting` reason, and 422 bodies
  - the logged row, including its keys matching migration 002's columns
  - a failed write still answering
  - the cap: the 10th search runs and the 11th doesn't; capped rows don't count, resting ones do, and other users' don't; the cap comes from config; a bad or uncountable cap rests; parallel requests can't pass together; reservations are released; the day is the UTC day even in summer time
  - PostgREST's count and insert requests
- **Jobs tests:** 357 pass against a local `postgis/postgis:17-3.6`. They cover the agent's row shape, every check constraint, the cascade, `query_results`' columns, the grants, and the cap count's SQL.
- **db checks:** RLS, grants, no foreign key to `events`, the cascade, the ok-needs-filters rule and the cap config.
- **Apply order:** tested locally in both orders. In Supabase's order, 002 after 011 prints the out-of-order notice and applies cleanly. In name order it applies second. The `000` probe still triggers its notice.
- **Deliberate breaks:** 15 in the agent and 7 in migration 002, and all 22 fail the suite or the db checks.
- **Supabase:** 002 isn't applied yet. Apply it with its checksum update (0006). `migrate.sh` will print the out-of-order notice, which is expected.
