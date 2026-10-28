# 0014: The parse-accuracy eval (step 1.6)

Status: accepted (2026-10-27). Owner: deepika. Revisit: after the first live run (baseline below), when the resolver closes a gap listed under "Ceiling", and in stage 3 when the eval runs on PRs and nightly.

## Context

The PRD measures parse accuracy (dates, area, budget, category) on an eval set, and decision 0002 sets the target: a baseline in stage 1, then 90% after stage 4. The build plan asks for hand-written queries in `evals/queries.jsonl` and a runner that freezes "today", scores each field and writes to Langfuse. The cases come from five project docs (not in the repo): `eval-seed-queries-1.6` (rows 1 to 60), `eval-location-rules-1.6` (61 to 64), `eval-ambiguity-rules-1.6` (65 to 70, and changes to rows 5, 7, 22, 27, 32 and 60), `clarify-chips-1.6` (71 to 73, and rows 1 and 2) and `festival-dates-2026-2027`. Principle 1 caps a run's cost.

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| What counts as correct | The model's reply; the filters; **both, as two numbers** | Both | End to end says what users get. Model only separates model mistakes from resolver gaps, so stage 4 fixes the right thing. |
| A doc expectation the resolver can't meet (months, unseeded areas, festival dates) | Drop the row; write what the resolver does; **keep the doc's expectation** | Keep | The eval should show the gap, not hide it. A test pins which rows a perfect model still fails. |
| Clarify questions | Score separately; **score inside the field they belong to** | Inside | Asking for a date instead of guessing is the date field's answer. The chip wording isn't scored (owner's decision 6). |
| Fields per row | All five everywhere; **the four PRD fields, plus radius only when stated** | Four plus radius | Owner's decision 6. Pin rows 23 and 24 score date and radius only (decision 7). Round 2 adds vibe and who on seven rows (0015). |
| Config for a run | **Live (Supabase)**, or the migrations in the repo | Both, live by default | Live measures what users get. Repo tests a new prompt before it's applied (principle 8). |
| Call cap | Per case; **per run, 80** | Per run | Owner's decision 8. A case starts only if both providers could still be tried within the cap. |
| Where it runs | CI on every PR; **manual workflow** | Manual | A run spends real provider calls. PR and nightly runs are stage 3, with response caching (build plan). |

## Decision

**The set** (`evals/queries.jsonl`, format in `evals/README.md`): 73 rows, all tagged `synthetic`, with "now" frozen at Tue 27 Oct 2026 12:00 London.
- Rows 25 and 26 (refine) are tagged `needs_prior_filters` and don't run. That leaves 71 rows.
- Each row has a hand-written `reply` (what a good model returns) and `expected` (what the product should do), plus the unscored columns: who, vibe, actions, chips and plan checks.
- Expected date windows follow 0012's rules: days run 05:00 to 05:00, evenings start at 17:00, and a weekend runs Friday 17:00 to Monday 05:00. A doc's day range runs from 05:00 on its first day to 05:00 after its last.

**Category mapping** (the docs' labels to the parser's eight):

| Docs' label | Category |
|---|---|
| Jazz, Live music | Music |
| Comedy | Comedy |
| Musical | Theatre |
| Art / gallery, Exhibition | Arts |
| Restaurant, Pub, Brunch, Dinner, Afternoon tea | Food & Drink |
| Bars, Party | Nightlife |
| Dinner + show | Food & Drink, Theatre |
| Restaurant + drinks; Christmas market + bar + dinner | Food & Drink, Nightlife |
| Christmas market + dinner; Dinner + walk; Outdoors + pub | Food & Drink |
| Christmas market + bar | Nightlife |
| Halloween (with kids), Family | Family |
| Festival, Carnival, Fireworks, Christmas market, Outdoors, Ice skating, Garden show, Civic / memorial, blank | none: the search text does the work |

**Scoring** (`evals/run_evals.py`), per scored field:
- **End to end:** the node's filters and `missing` against `expected`.
  - Date: the window matches one of the row's options exactly. `{unsupported}` never matches.
  - Area: the seeded area's point; the request's own point for `{point}`; no point for null. `{unseeded}` never matches.
  - Budget: `price_min`, `price_max` and `include_unpriced`.
  - Category: the same set.
  - Radius: within 0.8 km (about half a mile).
  - Each field also needs the same clarify reasons: date (`date`, `event_date`), area (`location`), budget (`budget`, `price_basis`) and category (`category`).
- **Model only:** the model's reply against `reply`, or an `also_accept` variant.
  - Date: the same `unclear` flag, an event or not, and a `when` meaning the same window ("Saturday" and "31 October" match).
  - Area: `near_me`, and the same area, or the same place name when it isn't one.
  - Budget: prices, free, whether the price is a total, and the `budget` flag.
  - Category: the set, or the `category` flag when no category is given.
  - Radius: the stated distance in km, within 0.8 km.
- A resting case (no provider answered) is a provider failure, not a parse failure. It's listed with its attempts and left out of both accuracies (round 2, below). It used to fail every field.
- **Parse accuracy** is the share of run rows whose scored fields are all correct. It's reported end to end and model only, with per-field counts.

**Runner and workflow**
- The real Understand node: real providers, real resolver, and the config from Supabase or the repo.
- It writes a JSON result (per row: status, filters, reply, marks, attempts and trace ID), prints both accuracies and the failures, and sends two Langfuse scores per row (`parse_end_to_end`, `parse_model_only`). Each score carries the failed fields as its comment, and the config versions, provider and run ID as metadata.
- It caps at 80 provider calls and exits 1 if every case rested.
- It paces Groq calls to 7,200 tokens a minute, which is 90% of Groq's limit (round 2). It reports tokens per provider, fallbacks and every attempt's outcome.
- `.github/workflows/evals.yml` runs it by hand with the repo secrets and uploads the results. Agent CI lints `evals/` and runs the scorer's tests.

**Where the set departs from the seed doc** (rules made later win):
- Rows 39 and 59: "Saturday" is the coming Saturday (31 Oct), by ambiguity rule 3, not the seed's 12 Dec and 19 Dec. The owner confirmed this reading on 28 Oct, when round 2's note on row 39 suggested otherwise.
- Row 38 names no date, so it asks, by row 37's rule, instead of the seed's 12 Dec.
- Row 51: Easter weekend follows 0012's long-weekend rule (Thu 25 Mar 17:00 to Tue 30 Mar 05:00), not the seed's Sat to Mon.
- Rows 60 and 70: the date may be asked or left out. Row 60's doc lists only `price_basis`, but "Christmas market" with no date asks by row 37's rule.
- Rows 4 and 13: Family is expected (round 2, 0015). It was none, from the seed's blank category.
- The festival doc's suggested extra row ("Diwali on Sunday 8 November") isn't added. The set is the 73 rows the step asks for.

## Trade-offs

- **End to end can't reach 100% yet.** With a perfect model it's 66.2% (47 of 71). The rest are resolver and calendar gaps, pinned in `test_run_evals.py`:
  - 15 unseeded areas, plus "Richmond Park"
  - months, date ranges, seasons and half-term
  - "any Saturday" and "any weekend"
  - "Friday night" starting at 17:00
  - festival and observance dates (no calendar)
  - an event with a weekday (row 57)
- **Model only depends on the hand-written replies.** Where a reply is a judgement call, `also_accept` lists the alternatives; the rest are reviewed in this record.
- **Exact windows are strict.** A model that picks a different but defensible reading fails the date. That's deliberate: the docs say ask rather than guess.
- **Langfuse scores cost units:** about 142 per run, well inside the free tier.

## Metrics and results

- **Tests:** 408 agent tests pass with no network. The eval tests cover:
  - the set's shape, skips, scored fields and clarify rows
  - a perfect model (100% model only; end to end fails only the pinned rows)
  - 11 wrong replies, each failing its field in both scores
  - equivalent dates and spellings, resting replies, the call cap with fallbacks, exit codes, and the Langfuse payload (no keys)
- **Deliberate breaks:** 15 in the scorer, the cap and the data, all caught. The first round missed two (`include_unpriced` and area spelling), which now have tests.
- **Ceiling with a perfect model:** end to end 66.2% (47/71), model only 100%.
- **First live run** (27 Oct 2026, run 40600304576, live config: `llm_understand` v1, `understand_prompt` v2): end to end 39.2% (20/51), model only 51.0% (26/51). It isn't a baseline, because 20 cases never ran (round 2).

## Round 2: making the eval trustworthy (28 Oct 2026)

The owner's round 2 decisions (27 Oct, items 5 and 6) asked for the provider limits and the 20 cases not run to be explained before the eval is trusted.

**Why 20 cases didn't run.** It was the call cap, not the scorer.
- Groq returned HTTP 429 on 28 of the 51 cases, and each of those spent a second call on Cloudflare.
- 51 cases used 79 calls. The runner starts a case only if both providers fit under 80, so it stopped after row 53. Rows 54 to 73 never ran.
- 4 of the 51 scored cases rested: Cloudflare cut off its reply at 1,024 tokens on rows 9, 34 and 49, and timed out on row 24. Each counted as four failed fields.

**Provider limits** (from the providers' pages, checked 28 Oct 2026):

| | Groq `openai/gpt-oss-20b` | Cloudflare Workers AI `@cf/openai/gpt-oss-20b` |
|---|---|---|
| Per minute | 30 requests, 8,000 tokens | 300 requests (default for text generation) |
| Per day | 1,000 requests, 200,000 tokens | 10,000 neurons free (18,182 neurons per million input tokens, 27,273 per million output) |
| Scope | per organisation, shared with the live app | per account |

Groq's page says its table shows the Developer plan's base limits. The free tier's exact figures are on the console's limits page, which only the owner can see.

- Tokens per minute is what we hit. The prompt plus the schema is about 6,300 characters, so each call is roughly 2,000 tokens, which allows about 4 calls a minute. The runner made about 13 a minute.
- A full run of 71 cases needs about 140,000 Groq tokens, about 70% of the daily limit, and the live app shares that limit. Run the eval at most once a day, and not at busy times.
- Cloudflare costs about 50 neurons a call, so about 200 fallback calls a day.

| Question | Options | Choice | Why |
|---|---|---|---|
| Groq's per-minute limit | Raise the cap; retry on 429; **pace calls** | Pace, to 7,200 tokens a minute (90% of 8,000) | Each case keeps Groq's model and uses one call. Retries would still spend refused calls. The live app is untouched: it should still fall back. |
| Resting cases | Fail every field; **leave them out and list them** | Leave out | A provider outage isn't a parse error. The count is reported next to the accuracy, so a run with many is plainly suspect. No Langfuse score is sent for them. |
| Cloudflare cut-offs | `reasoning_effort: low`; **more `max_tokens`** | 2,048 tokens (`llm_understand` v2, migration 014) | Cloudflare's docs don't say whether its chat completions endpoint takes a reasoning effort. A third-party config says it doesn't, so it might be rejected or ignored. Only generated tokens are charged. |

**Trade-offs**
- A paced run takes about 20 minutes (the workflow allows 45).
- Pacing uses the usage that Groq reports. If Groq counts differently (for example, by reserving `max_tokens` up front), 429s come back. The run then reports them as fallbacks, and the pace needs lowering.
- Until 014 is applied to Supabase, a live-config run still has Cloudflare at 1,024 tokens. Run `--config repo` to test v2.

**Results**
- 417 agent tests pass.
- Deliberate breaks, all caught:
  - pacing off
  - pacing off in `main()` (caught only after a test was added)
  - an empty window that waits
  - a 2-minute window
  - cut-off usage not counted
  - resting cases scored
  - fallbacks not counted
  - the limit at 8,000
  - 014 leaving v1 active (db test)
- **Baseline, the paced rerun** (28 Oct 2026, run 40612306072, `--config repo`: `llm_understand` v2, `understand_prompt` v2): **end to end 43.7% (31/71), model only 57.8% (41/71).**
  - All 71 cases ran: no provider failures, no 429s and no fallbacks.
  - It used 71 calls and 126,676 Groq tokens (about 1,800 a call, 63% of the daily limit), and waited 1,262 s for the pace.
  - Per field, end to end: date 49/71, area 53/69, budget 68/69, category 52/69, radius 2/2.
  - Per field, model only: date 55/71, area 66/69, budget 68/69, category 52/69, radius 2/2.
  - Cloudflare was never called, so v2's 2,048 tokens are untested live.
  - Model errors not covered by round 2's rules, for the later prompt work:
    - It asks a category for "Things to do in Soho" (r64), "Just surprise me" (r73) and a pin search (r23). The prompt says not to.
    - It asks a date for Boxing Day (r41), New Year's Eve (r43) and "Friday 6 November" (r67).

## Round 2: vibe and who in the set (28 Oct 2026, decision 0015)

- **New scored fields.** `vibe` and `who` are scored on rows 3, 4, 12, 13, 14, 22 and 50.
  - `expected.vibe` and `expected.who` list the acceptable values.
  - Rows 12 and 13 also accept no vibe: the doc inferred "cultural" and "outdoors" from the activity.
  - Row 22 expects none, so a model that invents a group fails.
  - End to end compares the vibe and who that `/parse` returns. Model only compares the model's own reply.
- **Category.** Rows 4 and 13 expect Family, matching rows 31 and 48.
- **Row 39** ("Christmas markets + drinks nearby, Saturday") changes to `needs_clarification` with `missing: ["location"]`.
  - Round 2 asked for `["location", "date"]`, taking a bare "Saturday" to ask.
  - On 28 Oct the owner kept rule 3's reading instead (a bare weekday is the coming one), so the date resolves to 31 Oct. Rows 4, 6, 12, 13, 14, 27, 31 and 59 depend on the same reading.
- **Ceiling with a perfect model:** unchanged at 47/71 end to end. Row 39 passes with a perfect model, as before.
- **Eval with prompt v3:** pending. It runs on the PR's branch with `--config repo`.
