# Evals

## Parse accuracy (`queries.jsonl`, `run_evals.py`)

73 hand-written searches from the project docs (decision 0014), one per line:

- `id`, `row` (the docs' row number), `uc` (use case, or null), `tag` (`synthetic`), `source` (the doc it comes from).
- `query`, `now` (frozen at Tue 27 Oct 2026 12:00 London), `location` (a pin or the user's position, or null). A location may carry `source`: `device` or `pin` (decision 0017).
- `skip`: why the row doesn't run, or null. No row is skipped now.
- `prior`: for refine rows (25 and 26), the earlier search as its queries row stored it: `filters`, `embedding_text`, `vibe` and `who`. The row's `query` is the follow-up, and its `reply` is a `RefineReply` (decision 0018).
- `scored`: the fields scored for this row: `date`, `area`, `budget`, `category`, `radius` only when the user states a distance, and `vibe` and `who` on the rows that test them (decision 0015).
- `reply`: the `UnderstoodQuery` a good model should return. `also_accept`: changes to `reply` that are also right.
- `expected`: what the product should do, written by hand from the docs, never by running the resolver:
  - `status` and `missing`: the clarify reasons.
  - `date`: a list of acceptable options, each `{window, missing}` (a window of two ISO times, or null for no date filter), or `{unsupported}` for something no filter can express yet.
  - `area`: `{id}` (a seeded area), `{unseeded}` (a place the product should know but doesn't), `{point}` (the request's location, with `source` when it must be marked device or pin), or null.
  - `budget`: `price_min`, `price_max`, `include_unpriced`. `categories`. `radius_km` (only when stated).
  - `vibe` and `who`: the acceptable values (a list, which may include null), only where they're scored.
- `unscored`: actions, chips, plan checks, and who or vibe on rows that don't score them, from the docs: recorded, not scored.
- `note`: why a row is the way it is, where it isn't obvious.

Run it from `agent/`:

```
uv run python ../evals/run_evals.py --config repo     # prompt and models from the migrations
uv run python ../evals/run_evals.py --config supabase # the live config
```

It needs the provider keys in the environment; without them every case rests. On GitHub, run the **Evals** workflow by hand: it uses the repo secrets and uploads the results.

- A run stops before passing 80 provider calls.
- It paces Groq to 7,200 tokens a minute, so a full run takes about 20 minutes.
- A full run uses about 70% of Groq's daily tokens, which the live app shares. Run it at most once a day.
- Cases where no provider answered are listed as provider failures and left out of the accuracies.
- If Groq rate-limits a call anyway, the runner waits as long as Groq asks and retries Groq rather than falling back to Cloudflare. A wait over 120 s means Groq's daily limit is used up: the run stops, and the rest of the cases are reported as not run (decision 0016).

`agent/tests/test_run_evals.py` checks the set and the scorer with a fake model, including which rows a perfect model still fails because of resolver gaps.

## Understand cases (`understand/cases.jsonl`)

The resolver's own cases: see `understand/README.md`.
