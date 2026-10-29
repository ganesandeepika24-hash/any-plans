# 0018: Refine, a follow-up to an earlier search (stage 1, step 2)

Status: accepted (2026-10-28). Owner: deepika. Revisit when the app shows refine (wording of the 404 and 409), or if the steps feel too big or small in use.

## Context

After a search, people say "make it cheaper" or "somewhere closer". Step 2 asks for:

- a refine call that takes the earlier `query_id` and the follow-up message
- keeping the earlier filters, applying the change and searching
- the earlier filters stored on the query row
- refine counting toward the 10-a-day cap
- eval rows 25 and 26 un-skipped and scored

Principle 5 applies: the model understands, and code computes. "Cheaper" has no number in it, so the new price has to come from the earlier filters and a rule, not from the model.

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| What the model sees | The earlier search and the follow-up; **the follow-up only** | Follow-up only | The model says what changes. Code holds the earlier search, so the model can't alter what it wasn't asked about, and the prompt stays small (Groq's tokens per minute, 0014). |
| The reply | A whole new search; **the search fields plus `price_change` and `distance_change`, null meaning keep** | Changes | "Cheaper" and "closer" are relative. The search fields cover explicit changes ("Saturday instead", "in Camden", "under £15"), and each replaces the earlier value of its kind. |
| How far one step goes | Fixed in code; **config `refine_steps`** | Config | Principle 8. v1: cheaper ×0.5 (£40 to £20, row 25), closer ×⅓ (3 miles to 1 mile, row 26), never under 0.5 km. "Pricier" and "further" divide by the same steps. |
| Where the earlier filters come from | A new column of "previous filters"; **the earlier row's own `filters`** | Its own row | Every ok row already stores its filters (002), without the user's own point (0013). The new `refines` column links a refine to the row it changed, so chains of refines work. |
| Whose queries | Any id; **the user's own only** | Own only | The service key bypasses RLS, so the read filters by `user_id`. Someone else's query and an unknown id both get 404, before any model call or cap use. |
| Unfinished queries | Refine them; **409** | 409 | A query that asked a question, rested or was capped has nothing settled to change. The user answers or searches again. |
| A near-me search's point | Store it; **ask for it again** | Again | 0013 never stores the user's point. The earlier row shows a radius with no centre. A refine of it needs the location in the request, or it asks where. |
| The route | Through `/search` with an id; **`POST /refine`** | Its own | The body and errors differ (404, 409), and the row records `route = 'refine'`. The search after it is `/search`'s. |

## Decision

- **`POST /refine`:**
  - The body is `{query_id, message, location?}`. Auth and the shared daily cap work as for `/parse` and `/search`.
  - The response is `/search`'s.
  - Errors: 404 (not the user's, or no such query), 409 (not ok), 503 (the earlier row can't be read), 422 (bad body).
- **The Refine step** (`Understander.refine`, `app/understand/refine.py`):
  1. `refine_prompt` v1 with the `RefineReply` schema.
  2. `apply()` resolves what the follow-up says as a search would, and overlays it by kind: dates, categories, place, price.
  3. A stated distance keeps the earlier centre.
  4. Relative changes use `refine_steps`:
     - Cheaper with no earlier limit asks for a budget. Cheaper than free says so.
     - Pricier with no limit says there was none.
     - Closer with no earlier place uses the request's point, or asks.
     - The radius stops at 0.5 km with a note.
  5. Search text, vibe and who carry over unless the follow-up gives new ones.
- **Migration 018:**
  - `refine` is added to `queries.route`.
  - `queries.refines` references `queries(id)`, on delete set null, and only refine rows may set it. It's indexed.
  - Adds `refine_steps` v1 and `refine_prompt` v1 (prompt v4's field list plus the two changes).
- **The node:** the provider loop is shared between Understand and Refine, generic over the prompt key and reply model. Traces are named `refine`, and record the config versions (including `refine_steps`) and the earlier filters.
- **Eval:**
  - Rows 25 and 26 run with a `prior`: an earlier search's stored filters.
    - Row 25 is "Live music this weekend, under £40", and expects £20 with the date and Music kept.
    - Row 26 is comedy within 3 miles of Soho, and expects Soho at about 1 mile, with radius scored.
  - Model only compares `price_change` with budget and `distance_change` with radius.
  - No rows are skipped now.

## Trade-offs

- **Fixed steps.** "Much cheaper" and "a bit cheaper" both halve the limit. A "how much" field can come later if logs show people mean different amounts.
- **The location is sent twice for near me.** The app has to send it again when refining a near-me search. That's the price of never storing it.
- **One more model call.** A refine spends a call and a cap use, as the owner's rule asks. "Make it cheaper" could be done without a model, but phrasing varies too much to match reliably.
- **Overlaying replaces whole kinds.** "In Camden" replaces the centre and radius together, and a new price replaces both ends.

## Metrics and results

- **Tests:** 550 agent tests pass. New tests cover:
  - 26 merge rules (each change, kept fields, the user's point, steps validation)
  - the node (refine prompt and schema, a search reply refused, missing steps config, asking for location)
  - 17 route tests (a change and a search, chains, other users and unknown ids as 404 with nothing spent, unfinished queries as 409, 503, auth, bad bodies, the cap, near me with and without the location, the location not logged, a failed search)
  - the query log's own-row read and results write
  - two wrong refine replies in the eval
- **Database** (local `postgis/postgis:17-3.6`):
  - 018 applies and refuses to run twice.
  - New db checks: `refines` (only refine rows, a real query, set null on delete, indexed) and the config rows.
  - `search_perf.sql` and the 361 jobs tests pass.
  - The two earlier tests that used `refine` as an invalid route now use `admin`.
- **Deliberate breaks:** 14. Twelve were caught at once. Two passed and got tests:
  - The real query log ignoring the user, which would have let anyone refine anyone's query. Nothing had tested its request.
  - Refining a `needs_clarification` query, whose test row had no filters.
- **Ceiling with a perfect model:** end to end 49/73 (67.1%), model only 73/73. Rows 25 and 26 now run and pass.
- **Eval:** not run (Groq's daily limit, owner's hold).

## Changes

- **2026-10-29: party size (decision 0024, migration 026).** The queries row also stores the stated budget (`budget`), and a refine reads it back. "It's 6 of us now" divides a stored total again; a new total uses the earlier party size; "cheaper" and "pricier" change the per-ticket cap, which then counts as per person. The per-ticket cap note is worked out from the refined filters, like the default-area note. `refine_prompt` v4.
