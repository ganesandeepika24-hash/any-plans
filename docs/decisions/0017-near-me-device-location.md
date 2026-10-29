# 0017: "Near me" uses the device location (stage 1, between steps 1 and 2)

Status: accepted (2026-10-28). Owner: deepika. Revisit when the app asks for location permission (the refused path needs a manual check), or when the PWA keeps a location between visits.

## Context

The owner's near-me decision (`near-me-decision-1.6.md`, pasted 28 Oct 2026):

- "Near me", "nearby me" and "around me" set the location to the user's device location. The browser reads it once, with permission.
- A location question appears only when permission is refused.
- A vague request near the user still asks for a category.
- Row 61 ("Something near me tonight") expects the location from the device and `needs_clarification` with `missing: ["category"]`. The eval checks that the location is marked as device-based.

Row 63 was the same query with a location, expecting `ok` and no question. With row 61 now carrying a device location, the two rows would have had the same input and contradicting answers. The owner chose (28 Oct) to make row 63 the dropped-pin version, asking for a category too.

The location already reached the resolver as a point with no source. 0013 kept it out of every log.

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| How the source is marked | A separate request field; **`source` on the location** | On the location | The source describes the point, and belongs with it. |
| Required or optional | Required; **optional** | Optional | The app sends `{lat, lon}` today. A required field would break it until the app changes. Unsaid is null. |
| Where it comes back | **`location_source` beside the filters** | Beside | `filters` is `search_items`' argument (0010, 0015). The source is set only when the user's own point was used. A named area wins, and then it's null. |
| Logged? | **No** | No | 0013 keeps the user's point out of logs. Its source is low risk, but nothing needs it yet. |
| Bare "something" | Code decides; **the prompt flags it** | Prompt | Whether a request names a kind of thing is language, the model's job (principle 5). Code still won't ask when there's a category, vibe or who (0015). |
| Permission refused | Model it in the eval; **row 62 stands for it, and the app is checked by hand** | Row 62 and a manual check | The eval sends a request. Permission is the browser's. With no location, "near me" asks where, as row 62 expects. |

## Decision

- **Request:** `location` takes an optional `source`: `device` (read once from the browser, with permission, for "near me") or `pin` (dropped on the map). Anything else is refused with 422.
- **Resolver:** the user's point keeps its source. `Resolution` and `UnderstandResult` carry `location_source` only when that point set the filters' `lat` and `lon`.
- **`/parse` and `/search`:** they return `location_source`. It's not logged, and neither is the point.
- **Prompt v4** (migration 017), which is v3 plus:
  - "nearby me" and "around me" as near me
  - "something" with no activity, vibe or group is flagged for a category ("something near me tonight")
  - Switching back is `set_active_config('understand_prompt', 3)`.
- **Eval:**
  - Row 61: the device location, `missing: ["category"]`, and the area is the request's point, marked device.
  - Row 63: the same, marked pin.
  - The scorer fails area end to end when the source differs.
  - Row 62 is unchanged: no location, asking for location and date.
- **Numbering:** this record takes 0017, so refine is 0018, the calendar 0019, date ranges 0020 and London areas 0021.

## Trade-offs

- **The app doesn't send `source` yet.** Until it does, `location_source` is null for real requests, and only the eval exercises it. A change to the app is needed (owner's rule: no frontend changes in these steps).
- **The permission-refused path is only partly covered.** The eval covers it as "no location". The browser prompt itself needs a manual check.
- **Some broader requests will ask.** "Something tonight" (no "near me") now asks for a category too.

## Metrics and results

- **Tests:** 498 agent tests pass. New tests cover:
  - the source in the resolver (device, pin, none, area centre)
  - row 61's rule
  - the route returning it, not logging it, working without it, and refusing an unknown source
  - `/search` returning it
  - the prompt's near-me phrases and bare "something"
  - the eval rows, a wrong source failing area, and a perfect reply passing
- **Database:** 017 applies locally and refuses to run twice. `rls_and_indexes.sql` (prompt v4 active) and the 361 jobs tests pass.
- **Deliberate breaks:** 9, all caught:
  - the source dropped in the route, the node or the response
  - a source on an area centre
  - the scorer ignoring the source
  - any source accepted
  - the prompt losing the phrases or the bare-"something" rule
  - v3 left active (db test)
- **Ceiling with a perfect model:** unchanged at 47/71.
- **Eval:** not run (Groq's daily limit, owner's hold).

## Update 2026-10-29: the app reads the location before the first call (step 1.4)

- **Why:** under the flow first written into `docs/api.md`, a near-me search cost two AI searches against the daily cap of 10. The app sent the query, got `missing: ["location"]` back, read the position and sent the query again. The near-me decision (`claude/near-me-decision-1.6.md`) has the device location read once with permission. The owner chose (29 Oct 2026) to read it before the first call.
- **What changed:**
  - **The app spots near me itself.** It checks the text for the near-me phrases the prompt uses (v6: "near me", "nearby", "nearby me", "around me", "around here") and reads the position first. Then it sends the search once with `location.source: "device"`.
  - **Refused or failed:** with permission refused, an error or the 8-second timeout, it sends without a location. The agent asks where, as row 62 expects, and the two-call path stays as the fallback.
  - **No agent or database change.** The agent already accepted a device location from the first call. The field is `location.source`; `location_source` is the reply's, and a top-level one in a request is refused with 422.
  - **`docs/api.md` section 8** is the contract, with the phrase list. `agent/tests/test_near_me_phrases.py` fails when that list differs from the active prompt's, so a prompt change updates the doc, then the app.
  - The front end is Lovable's, through a prompt the owner sends.
- **Tests:** 711 agent tests pass, 5 of them new:
  - three drift tests: the list, the version it names, and a later prompt taking over
  - the route test: "something near me tonight" with a device location gives one model call, missing only `category`, `location_source` `"device"`, no search, and the point not logged
  - a top-level `location_source` refused
- **Deliberate breaks:** 3, all caught: a phrase dropped from the doc, a v7 prompt adding "close by", and the resolver ignoring the device location.
- **Still open:** the permission-refused path in a real browser (option 2, deferred by the owner).
