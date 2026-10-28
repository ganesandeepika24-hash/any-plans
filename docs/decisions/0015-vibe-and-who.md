# 0015: Vibe and who (step 1.6, round 2)

Status: accepted (2026-10-28). Owner: deepika. Revisit when search can use vibe or who (ranking, or vibe tags on events), and after the next eval run.

## Context

The owner's round 2 decisions (27 Oct 2026, decision 1) came after the first eval run.

- **The problem.** Mood and group words were asked about as if they were categories: "Something chilled tonight" got "what are you in the mood for?". Seven category failures in the paced baseline (0014) are this kind.
- **The rule.** Mood words map to a `vibe` (the PRD chips: Chilled, Lively, Cultural, Foodie, Outdoors). Group words map to `who` (Solo, Date, Friends, Family with kids). The category question fires only when there's no category, no vibe and no group.
- **"Something fun"** maps to nothing, so it asks with categories and vibes, plus "Just surprise me".

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| Where vibe and who go | In `SearchFilters` and `search_items`; **beside the filters** | Beside (owner, 28 Oct) | `SearchFilters` is `search_items`' argument key for key, and a test keeps them equal. Events carry no vibe or group, so search has nothing to match them to yet. |
| How many of each | Lists; **one value each** | One | Every doc example has one. Two can be added later without breaking stored rows. |
| Does "with the kids" also mean Family? | Who only; **who and Family** | Both (owner, 28 Oct) | Rows 31 and 48 already expect Family for kids' searches. Rows 4 and 13 now agree with them. |
| Who decides whether to ask for a category | The model's `unclear` flag alone; **code, from flag, vibe and who** | Code | Principle 5. A model that sets both a vibe and the category flag still doesn't ask. |
| The two sets of chips | A new question type; **groups on the existing question** | Groups | `choices` stays the flat list, so the app's current chips still work. `groups` says which field each tap fills. |
| Vibe from the activity ("a jazz set" → Cultural) | Infer it; **only when the user says the mood** | Only when said | Guessing a mood from an activity is the model inventing intent. Rows 12 and 13, where the doc inferred one, accept no vibe as well. |

## Decision

- **Schema:** `UnderstoodQuery` gains `vibe` and `who`, each one PRD chip or null. Both are required, as Groq's strict mode needs.
- **Prompt v3** (migration 015): v2 plus the vibe and who fields, with examples. "Things for kids, or with kids" is Family. The category flag isn't set when there's a vibe or group.
- **Resolver:** asks `category` only when the flag is set and there's no category, vibe or who.
- **Clarify:** the category question has three groups: categories (Music, Comedy, Food & Drink, Arts, Nightlife, Family, Sport), vibes, and "Just surprise me" (with no field). `choices` is all three groups in order. Other questions have no groups.
- **`/parse`:** returns `vibe` and `who` next to `filters`, and `groups` on each question (empty unless set). Both changes only add fields: the app can ignore them until it shows them.
- **`queries`:** new `vibe` and `who` columns, checked against the chips (migration 015). They're covered by the table's existing grants and 012's read-own policy.
- **Eval:**
  - Rows 3, 4, 12, 13, 14, 22 and 50 score `vibe` and `who`. `expected` lists the acceptable values.
  - Row 39 is now `needs_clarification`, `missing: ["location"]`. The owner confirmed on 28 Oct that a bare "Saturday" is the coming one, so only "nearby" asks.
  - Details are in 0014.

## Trade-offs

- Vibe and who don't change the results yet. A search for "something chilled" runs with no category, so it shows everything in its date and area. Search will use them in a later step (ranking or event tags). Until then they're logged, so we can see how often people use them.
- One value each: "chilled foodie evening" keeps one vibe.
- The Lovable app needs a change to show the vibe chips as their own group and send a tap back as a vibe. Until then a vibe tap is sent back as text and parsed again, which works.

## Metrics and results

- **Tests:** 440 agent tests pass. New tests cover:
  - the schema: chips only, both fields required, Groq strict mode
  - the active prompt naming every category, vibe and who
  - the resolver rule
  - the groups
  - the route's response and row
  - eval scoring of vibe and who, and the round 2 rows
  - db checks on the new columns and the active prompt v3
- **Deliberate breaks, all caught:**
  - the resolver ignoring vibe and who, or who alone
  - vibe chips dropped
  - who not returned or not logged
  - either vibe score always passing
  - the prompt or schema drifting from the chips
  - the db `who` check dropped
  - prompt v2 left active
- **Eval:** not yet run with prompt v3 (pending, 0014).
