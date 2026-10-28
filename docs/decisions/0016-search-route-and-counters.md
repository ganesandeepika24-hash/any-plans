# 0016: The search route and daily provider counters (stage 1, step 1)

Status: accepted (2026-10-28). Owner: deepika. Revisit when the app calls `/search` for real (latency, result count), when a provider changes its free tier, or when the agent runs as more than one instance.

## Context

`/parse` turns text into filters but shows no events. Step 1 asks for:

- a Search node that runs `search_items` (migration 010) on an ok parse and never on a clarifying one
- a `/search` route behind the same auth and the same 10-a-day cap
- the events shown logged to `query_results`, without the user's location
- daily counters for Groq and Cloudflare that stop calls at the limit
- an eval runner that waits and retries on a Groq rate limit instead of falling back to Cloudflare

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| Method | GET with the location in the query string; GET with it in a header; **POST with `/parse`'s body** | POST (owner, 28 Oct) | A GET URL carries the location, and access logs record URLs. POST keeps it in the body. A GET to `/search` gets 405. |
| What `/search` takes | A `query_id` from an earlier `/parse`; **the text, as `/parse` does** | The text | One request per search. A `query_id` would need the user's point again (it's never stored), so the location would travel twice. Refine (step 2) reads earlier filters by `query_id`. |
| The cap | Separate caps; **one cap shared with `/parse`** | Shared | Each request spends one model call either way. Both write `queries` rows, and the cap counts them all. |
| `source_id` for `query_results` | Change `search_items` to return it; **look it up by event id** | Look up | `search_items` stays as tested in 010. The lookup is one indexed read of at most 100 ids. An event deleted in between is dropped and the ranks close up. |
| vibe and who | **Not passed** | Not passed | `search_items` takes only `SearchFilters`' keys and rejects others (0010, 0015). They're returned and logged beside the filters. |
| Where the counters live | **`usage_daily`, via two functions** | `usage_daily` | The table exists (001) and holds counts only. Tokens are kept under `<provider>_tokens`, because Groq's binding daily limit is tokens. |
| Racing for the last call | Read then write; **one upsert with a `where`** | Upsert | Two requests can't both take the last call. Tested with 8 at once: exactly 1 succeeded. |
| When the counter can't be read | Call anyway; **don't call** | Don't call | Principle 1: stop spending when unsure. The node then rests. |
| Eval on a Groq 429 | Fall back (node default); **wait as asked and retry Groq** | Retry | A fallback spends a second call under the 80-call cap and swaps the model. A wait over 120 s means a daily limit, so the run stops and reports the rest as not run. |

## Decision

**`POST /search`** (`app/routes/search.py`, `app/search.py`):
- Takes the same body as `/parse`: `{query, location?}`. Needs a Supabase JWT. Shares `/parse`'s cap (`ai_search_cap`).
- The parser runs first and writes a `queries` row with `route = 'search'`.
- **ok:** the Search node embeds `embedding_text` with fastembed, in process, and calls `search_items` with the filters. The response is `/parse`'s plus `results`: rank, event fields, `last_checked_at` (freshness, principle 6), `distance_m` and score.
- **needs_clarification and ai_resting:** answered as `/parse` does, with `results: null` and no search.
- **A search failure:** the response stays `ok`, with `results: null` and a message. The query is still logged, because the model call was spent.
- **Logging:** `query_results` gets `(query_id, rank, source, source_id, score)`, written after the `queries` row it references.
  - The user's point isn't in `queries.filters` (as for `/parse`), and `query_results` has no distance.
  - A test checks that the coordinates and the distance appear in neither table.

**Graph:** Understand → Search when the status is ok, otherwise it ends. Rank and plan come later.

**Counters** (migration 016, `app/usage.py`):
- `provider_limits` v1 in config:
  - Groq: 1,000 calls and 200,000 tokens a day.
  - Cloudflare: 200 calls a day (10,000 neurons at about 50 a call). Its tokens aren't capped.
- `reserve_provider_call(service, day, call_limit, token_limit)` counts one call and returns true, or returns false without counting once either limit is reached.
- `add_provider_tokens` adds the tokens a call reported, failed calls included.
- Both are security invoker, with an empty `search_path`, and executable by `service_role` only.
- `MeteredLLM` wraps the provider client:
  - At a limit it raises `daily_limit`, and the node falls back to the next provider.
  - With every provider at its limit, no call is made and the node rests.
  - If the counter can't be read, it raises `usage_unavailable` and makes no call.
- Days are UTC, as the providers reset.

**Eval runner** (`evals/run_evals.py`):
- `RateLimitRetry` sits between the call counter and the pacer.
- On a Groq 429 it waits for `retry-after` (30 s if there's none) and retries Groq, up to 5 times.
- A wait over 120 s, or a 6th refusal in a row, stops the run. The remaining cases are `not_run`, and the summary says why.
- Refused calls don't count toward the 80-call cap. Cloudflare and other errors are unchanged.
- `LLMError` now carries `retry_after_s`.

## Trade-offs

- **The eval spends Groq outside the counters.** The eval runs on GitHub with the repo config, so its calls never reach `usage_daily`. The live app may then meet Groq's own 429 before its counter's limit, and fall back to Cloudflare as before. The eval's tokens are in its own summary.
- **Counter calls cost latency.** Each model call adds two PostgREST calls: reserve before, tokens after. That's tens of milliseconds next to a 1 to 3 s model call.
- **The token limit can be overshot.** It's checked before a call and added after, so parallel calls near the limit can pass it by a few calls' worth (about 2,000 tokens each). Groq's own limit then refuses, and the node falls back.
- **`limit` isn't exposed.** `/search` returns `search_items`' default of 20.
- **No frontend change** (owner's rule). The app needs one to call `/search`.

## Metrics and results

- **Tests:** 482 agent tests pass with no network. The new tests cover:
  - the route: auth, GET refused, cap shared with `/parse`, a search counting toward the cap, needs_clarification and ai_resting never searching, a failed search, a failed results write, no results
  - the location in neither table
  - the Search node: no embedding without text, embedding failure, events gone before the lookup, a row that doesn't validate
  - the graph's branch
  - the counters: call limit, token limit, fallback, every provider at its limit, new day, failing closed, missing config, failed calls' tokens, a lost token count, the RPC bodies
  - the eval retry: waits, default wait, daily-limit stop, too many in a row, no retry for others or Cloudflare, cap not spent, `main()` wiring
  - `retry-after` parsing
- **Database** (local `postgis/postgis:17-3.6`):
  - 016 applies and refuses to run twice.
  - New db checks: the route constraint, the function grants, call and token limits, a raised limit, a new day, zero or null limits, negative tokens, and the config row.
  - `search_items` accepts the embedding in pgvector's text form.
  - 8 concurrent reserves for the last call: 1 succeeded, and the count stopped at the limit.
- **Deliberate breaks:** 13. Twelve were caught at once. "The retry not wired into `main()`" passed until the wiring test checked what the call counter wraps.
- **Eval:** not run (Groq's daily limit is used up, owner 28 Oct). The baseline rerun is pending (0014).
