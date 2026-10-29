# 0024: Party size and the per-ticket price cap

Status: accepted (2026-10-29). Owner: deepika. Revisit when the filter panel needs a "total" toggle, when vibe becomes a filter (0015), or when the live eval shows the model misreading loose amounts or group sizes.

## Context

Event prices are per ticket, so `search_items` compares `price_min` and `price_max` with each event's ticket price. People often give a budget for the whole group ("five of us, about a hundred quid", "£60 between us"). Decision 0012 asked `price_basis` for every total, even "£60 total", where the user had already said which. Nothing read how many people were going. Eval row 58 recorded "for 12" as `group_size`, but nothing scored it.

The owner asked on 29 Oct 2026:
- Add a party size, from the filter panel or the request.
- Divide a total budget by it in code, and leave a per-person budget unchanged.
- Ask `price_basis` when the budget doesn't say which.
- Show the cap in the notes ("Up to £20 each for 5 people (tickets only)").

They then asked for the following, also in plain terms:
- Read loose wording.
- Ask only when the answer changes the result, in one plain sentence with taps.
- Accept typed answers.
- Have follow-ups such as "make it more chilled" update the existing search.

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| Where the division happens | The model; SQL in `search_items`; **the agent, in Python** | Agent | Principle 5: the model reads the numbers, and code does the sums. `price_max` keeps one meaning (per ticket) on every path. |
| The filter panel (owner, option 1a) | **Per-ticket budget, party size kept but not filtered on**; `search_items` divides a total in SQL (1b) | 1a | `search_items`' price filter doesn't change. `party_size` is carried so an AI search's filters load into the panel and the URL with it. A panel "total" toggle can come later. |
| A total with no party size (owner, option 2a) | **Ask `party_size`**; keep asking `price_basis` (2b) | 2a | The user said "total", so the open question is how many. Eval row 60 changes from `price_basis` to `party_size`. |
| A budget that doesn't say which | Always ask; **ask only for two or more** | Two or more | For one person, or no party size, each and total give the same cap. 0012's rule, that a plain "under £60" is per ticket, stands. The 71 budget rows don't change. |
| Rounding a share | To the nearest penny; **down to the penny** | Down | Then the group never goes over: £60 for 7 is £8.57 a ticket, and 7 × £8.57 = £59.99. |
| Refine after a party-size change | Multiply the stored cap back; **store the stated budget** | Store | Multiplying back loses pennies (£8.57 × 7 ≠ £60). `queries.budget` keeps the amounts and basis as stated. |
| "Cheaper" after a divided total | Scale the total; **scale the per-ticket cap** | The cap | It's the number on screen. That cap then counts as per person, so a later party-size change keeps it. |
| Limits on party_size | 1 to 30; **1 to 1000** | 1 to 1000 | A bigger party still divides correctly. A tighter limit would make the model's whole reply fail validation for "a wedding of 150". |

## Decision

- **`UnderstoodQuery.party_size`** is an integer from 1 to 1000, null when not stated. It defaults to null, so v6 replies still validate. `RefineReply` inherits it.
- **`SearchFilters.party_size`** is the same range. `search_items` accepts it and checks it (migration 026), but doesn't filter on it.
- **Rules, in `resolve.price_fields`:**

  | Budget | Party size | Result |
  |---|---|---|
  | Each | any | The amount is the cap |
  | Total | 1 | The amount is the cap |
  | Total | 2 or more | The amount ÷ party size, rounded down to the penny; `price_min` too |
  | Doesn't say | none or 1 | The amount is the cap (0012) |
  | Doesn't say | 2 or more | Ask `price_basis` |
  | Total | none | Ask `party_size` |
  | Free, or no budget | any | Nothing to ask, no note; `party_size` is still kept |

- **`missing`** gains `party_size`, after `price_basis`: `location`, `date`, `budget`, `price_basis`, `party_size`, `event_date`, `category`.
- **Questions:** one plain sentence each, with no "Subject:" prefix.
  - `price_basis`: "Is £100 for each person, or for all 5 of you?", with the taps "Each person" and "All 5 of us".
  - `party_size`: "How many people is the £100 for?", with the taps "2 people" to "6 people".
  - Taps and typed answers go back as text appended to the search, as before (docs/api.md section 8). A typed answer that still doesn't say gets the same question again.
- **Note `ticket_cap`:** "Up to £20 each for 5 people (tickets only)", for two or more people with a cap above zero. It's worked out from the final filters, so a refine's note follows the refined cap.
- **Refine:**
  - The stated budget comes back from `queries.budget`.
  - "It's 6 of us now" divides a stored total again, leaves a per-person cap alone, and asks `price_basis` for a budget that never said which.
  - A new amount uses the earlier party size. "That's per head" re-reads the stored amount.
  - Searches logged before 026 have no stored budget, so their cap stays.
  - "Make it more chilled" changes only `vibe`, which still isn't a filter (0015).
- **Prompts** (migration 026, active):
  - `understand_prompt` v7 adds the `party_size` line ("five of us", "me and 3 mates" is 4, "just me" is 1, and typed counts such as "there'll be six"). It adds loose amounts: "about a hundred quid", "a ton", "under a tenner", "a fiver", "100-ish". "About" counts as the cap, and the model is told "never divide or multiply". It adds more words for each and total ("per head", "all in", "for everyone"), and "calmer" and "quieter" as Chilled.
  - `refine_prompt` v4 adds the same lines, with "it's 6 of us now" and "more chilled".
- **`queries`** (026):
  - A `budget` jsonb column: `{price_min, price_max, basis}` or null, with its shape checked.
  - 013's unnamed `missing` check is replaced by `queries_missing_reasons`, with `party_size` added. The migration finds the old check by its text rather than its generated name.

## Trade-offs

- **The panel can't take a total yet.** Its budget is per ticket. A party size there changes no results; it's kept and shown in the note line.
- **The model must count.** "Me and 3 mates" is 4 only if the model adds one. That's reading, not arithmetic, but a miscount changes the cap. The eval scores it (row 77).
- **Deploy order.** The new agent sends `party_size` to `search_items`, which the old function refuses. Apply 026, then deploy the agent at once. With v7 active, an old agent on Cloudflare (unstructured output) may reject replies that carry `party_size`.

## Metrics and results

- **Agent:** 782 tests pass, 51 of them in the new `test_party_size.py`, with no network. They cover:
  - every row of the rules table
  - rounding down, and a divided `price_min`
  - the note, and when it's left out
  - both questions, their taps and the fixed order
  - two-turn `/search` flows with each tap and two typed answers, and a typed answer that still doesn't say
  - refine: "more chilled", "6 of us now" (total, per person, never said), "per head", a new total, "cheaper", "free", and a search logged before 026
  - both routes reading and writing `queries.budget`
  - the prompts' new lines
- **Deliberate breaks:** 19 mutations, and each fails the suite:
  - rounding half up
  - each question removed
  - never dividing
  - a note for one person, or no note
  - refine not re-dividing, forgetting the party size, or keeping a total after "cheaper"
  - the old tap text
  - the reasons in the wrong order
  - the budget not logged or not read
  - the scorer ignoring the party size (model only and end to end)
  - a prompt line removed
  - `party_size` left out of the filters
  - `search_items` not checking the range
- **Eval set:** 79 rows, up from 73. The run stops before 80 provider calls and keeps room for a fallback, so 79 is the most it can reach without changing 0016's cap.
  - Rows 58 and 60 changed.
  - New rows: 74 (loose wording, asks the basis), 75 (the "All 5 of us" tap), 76 (typed "it's per head"), 77 (typed "me and 3 mates" after a total), and two refines, 78 ("make it more chilled") and 79 ("it's 6 of us now").
  - The scorer's budget field now includes the party size, and the basis wherever it changes the result.
  - A perfect model passes 78 of 79 end to end (row 23, a known resolver gap) and 79 of 79 model only.
- **Database:** against PostGIS 17-3.6 with 001 to 026 applied, `rls_and_indexes.sql` and `search_perf.sql` pass (p95 41 ms).
  - New checks: `search_items` takes `party_size`; the browser path takes it as `anon`; `queries` takes the new reason and good budgets, and refuses bad ones and unknown reasons; v7 and v4 are active.
  - jobs: the new `search_items` tests pass. `party_size` changes no rows, the out-of-range values are refused, and the panel comparison gets an eleventh filter set.
  - On this codespace's local container, 20 role-based tests in `test_search_items.py` and `test_understand_context.py` fail with "permission denied for schema extensions" on `main` too. The branch adds one more case to one of those tests. CI's container is the check.
- **Live eval:** not run for this PR. A full run uses about 70% of Groq's daily tokens, the second baseline already ran today, and the codespace has no provider keys. Run the **Evals** workflow on this branch (config `repo`) on another day, then compare with the second baseline in `docs/baseline.md`.
