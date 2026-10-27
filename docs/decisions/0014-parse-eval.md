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
| Fields per row | All five everywhere; **the four PRD fields, plus radius only when stated** | Four plus radius | Owner's decision 6. Pin rows 23 and 24 score date and radius only (decision 7). |
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
- A resting reply fails every field.
- **Parse accuracy** is the share of run rows whose scored fields are all correct. It's reported end to end and model only, with per-field counts.

**Runner and workflow**
- The real Understand node: real providers, real resolver, and the config from Supabase or the repo.
- It writes a JSON result (per row: status, filters, reply, marks, attempts and trace ID), prints both accuracies and the failures, and sends two Langfuse scores per row (`parse_end_to_end`, `parse_model_only`). Each score carries the failed fields as its comment, and the config versions, provider and run ID as metadata.
- It caps at 80 provider calls and exits 1 if every case rested.
- `.github/workflows/evals.yml` runs it by hand with the repo secrets and uploads the results. Agent CI lints `evals/` and runs the scorer's tests.

**Where the set departs from the seed doc** (rules made later win):
- Rows 39 and 59: "Saturday" is the coming Saturday (31 Oct), by ambiguity rule 3, not the seed's 12 Dec and 19 Dec.
- Row 38 names no date, so it asks, by row 37's rule, instead of the seed's 12 Dec.
- Row 51: Easter weekend follows 0012's long-weekend rule (Thu 25 Mar 17:00 to Tue 30 Mar 05:00), not the seed's Sat to Mon.
- Rows 60 and 70: the date may be asked or left out. Row 60's doc lists only `price_basis`, but "Christmas market" with no date asks by row 37's rule.
- Rows 4 and 13: the seed has no category, so none is expected, although the prompt reads "for kids" as Family.
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
- **Baseline (first live run):** pending. It runs after this merges, and the result is recorded here and in `docs/workflow-notes.md`.
