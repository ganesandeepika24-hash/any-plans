# 0019: A calendar for named events (stage 1, step 3)

Status: accepted (2026-10-28). Owner: deepika. Revisit before 1 September 2027: the seed runs out after May half-term 2027, so it needs the 2027 to 2028 dates before the autumn events. Also revisit if logs show events people ask for that the calendar lacks.

## Context

The model already names events with no date ("Hanukkah", "Eid", "Remembrance Sunday") in the `event` field. With nothing to look them up in, code asked for a date every time (`event_date`). Step 3 asks for festivals, half-terms and bank holidays in a calendar, with `event_date` asked only when the calendar doesn't have the event.

Decision 0014 pinned the gaps: rows 34, 42, 46, 47, 49, 52, 53 and 55 failed on date for want of a calendar. Row 48 ("February half-term") failed because the model flagged the date as unclear. Row 57 ("Notting Hill Carnival, Sunday") resolved to the coming Sunday instead of asking.

The festival dates come from `claude/festival-dates-2026-2027` (checked 27 Oct 2026). Its rule for unknown dates: say so and ask, never guess.

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| Where the dates live | Agent code or a JSON file, like the bank holidays; config; **a table** | Table | Principle 8: a corrected date is an edited row, with no redeploy. One row per occurrence carries its own source and check date (principle 6). Bank holidays stay in `holidays.py`'s gov.uk snapshot, which already works and has its own refresh script. |
| How rows get there | In the migration; **an idempotent seed, like the areas** | Seed | `db/seed/calendar.sql` upserts on `id`, so a fix is a seed rerun, not a migration. CI loads it twice. |
| How the agent reads it | A second request; **`understand_context`** | Context | The agent already fetches and keeps the city context for an hour. The function returns entries that haven't ended (allowing a day for the 05:00 rule). |
| Moving days with a rule (Mother's Day, Pancake Day, Remembrance Sunday) | Compute from Easter; **rows** | Rows | One mechanism for everything. The owner confirmed the three 2026–27 dates. |
| Half-terms | Per borough; **London-wide, from one named borough** | London-wide | Haringey's published 2026 to 2027 dates, and Southwark's agree. Most boroughs share the weeks; academies can differ. Per-borough dates can come with step 5's areas if they're needed. |
| A name with several entries ("Eid", "half-term") | Ask which; **the next one that hasn't ended** | Next | The next Eid is Eid al-Fitr and the next half-term is the current or coming one, which is what row 52 and the eval expect. A test checks that entries sharing a name never overlap. |
| An event with a weekday ("Carnival, Sunday") | Use the weekday; **that day within the event** | Within | When the calendar has the event, the weekday picks one of its remaining days. If the weekday isn't one of them, or matches two (Hanukkah has two Saturdays), it asks for the date. When the calendar lacks the event, it asks for `event_date`, weekday or not (row 57). |
| An event with a stated date ("Diwali on 7 November") | Calendar; **the stated date** | Stated | The user said when. The prompt leaves `event` null in that case anyway. |
| Estimated dates (Eid) | Treat as certain; ask; **use them with a note** | Note | The `estimated_date` note says the date may move, so the result is honest without stopping the search. |
| Unknown events | A capped web search; **ask** | Ask | The festival doc's rule. Web search is principle 3's last resort and isn't built yet. |

## Decision

- **Migration 019:**
  - `calendar_dates`: `id`, `city_id`, `name`, `aliases`, `kind` (festival, observance, school_holiday, event), `starts_on`, `ends_on` (whole local days, end not before start), `estimate`, `source`, `checked_on`. Indexed on `(city_id, ends_on)`.
  - Row Level Security on. All rights revoked from `anon`, `authenticated` and `service_role`, then `select` granted to `service_role`. Supabase's defaults gave `service_role` full rights, which a db check caught.
  - `understand_context` is replaced to add `calendar`, with its grants set again.
  - `understand_prompt` v5 and `refine_prompt` v2, both active: the `event` line names observances and half-terms ("Mother's Day", "February half-term"), asks for the bare name, and keeps a weekday beside the event. Switching back is `set_active_config` to v4 or v1.
- **The seed** (`db/seed/calendar.sql`, 12 rows for London):

  | Entry | Dates | Source |
  |---|---|---|
  | October half-term | 26 to 30 Oct 2026 | Haringey Council |
  | Diwali | 8 Nov 2026 | Vedantu |
  | Remembrance Sunday | 8 Nov 2026 | Second Sunday of November |
  | Hanukkah | 4 to 12 Dec 2026 | Farmer's Almanac |
  | Chinese New Year | 6 Feb 2027 | ChinaTravel |
  | Pancake Day | 9 Feb 2027 | 47 days before Easter |
  | February half-term | 15 to 19 Feb 2027 | Haringey Council |
  | Mother's Day | 7 Mar 2027 | Three weeks before Easter |
  | Eid al-Fitr (estimate) | 9 to 10 Mar 2027 | Khaleej Times |
  | Vaisakhi | 14 Apr 2027 | Drik Panchang |
  | Chelsea Flower Show | 18 to 22 May 2027 | RHS |
  | May half-term | 31 May to 4 Jun 2027 | Haringey Council |

  Pride in London and Notting Hill Carnival 2027 are left out: neither date is confirmed, so the app asks.
- **The resolver:** `City.find_date` matches the event's normalised name and aliases, as areas do. `event_window` gives the event's days from now on, or the one weekday in them. Not found, or ended, asks for `event_date`.
- **The store:** reads `calendar` from the context. A context without it (011's function, before 019 is applied) gives an empty calendar, so the agent works either way: every event then asks for a date, as before.
- **Eval:** row 48's reply now names "February half-term" as the event. The other rows' replies are unchanged. The eval stays at 73 rows, so the baseline comparison stays fair. The extra "Diwali on Sunday 8 November" row from the festival doc is a resolver test instead.

## Trade-offs

- **Dates are added by hand each year.** When an event's last entry ends, it goes back to asking for a date. That's safe but noticeable. The revisit date above, and the workflow note, say who refreshes it.
- **London-wide half-terms.** A school with different dates gets the common week.
- **Bank holidays stay separate.** They come from a gov.uk snapshot through the `holiday` kind, not this table. Scotland and Northern Ireland aren't covered (PRD M-03, later).
- **The context only drops entries that ended before yesterday,** so the agent still filters by its own day. Both checks are tested.

## Metrics and results

- **Tests:** 574 agent tests pass with no network. New tests cover:
  - the resolver: single and multi-day events, aliases, the next of several entries, an estimate with its note, an event until 05:00 the next day, a weekday within an event (Diwali on Sunday) or in one under way, weekdays that match none or two days, unknown and ended events, a stated date beating the calendar
  - the seed: no overlapping entries sharing a name, and the parser matching the database's shape
  - the store: the calendar from the context, and a context without one
  - both prompts naming observances and half-terms
- **Database** (local `postgis/postgis:17-3.6`):
  - 019 applies, a second run skips, and running it directly fails.
  - The seeds load twice.
  - New checks: `calendar_dates` grants and RLS, its constraints, the seed's 12 rows, and `understand_context`'s calendar (what it drops, its shape).
  - The context from the database matches what the tests parse from the seed (12 entries, 53 areas).
  - `search_perf.sql` passes.
  - Jobs: 357 pass. Four `only_service_role_may_call_it` tests fail locally on `main` too, because this local database has no `extensions` usage for `service_role`. CI is the check for those.
- **Deliberate breaks:** 10. Nine were caught at once. One passed and got a test: a weekday search that counted the days of an event already under way.
- **Ceiling with a perfect model:** end to end 53/73 (72.6%), up from 49/73. Rows 47, 48, 49 and 57 now pass. Rows 34, 42, 46, 52, 53 and 55 pass on date and still fail on area (step 5).
- **Eval:** not run (Groq's daily limit, owner's hold). The rerun is step 6.

## Update 2026-10-29: fixed-date days named as events

- **Why:** this record left fixed-date days out of the calendar, because the model was meant to give them as dates. The second baseline (`docs/baseline.md`, run 40802911863) showed the model often names them as **events** instead. For example, "Bonfire Night this Thursday" came back as event Bonfire Night with a weekday, and "Boxing Day walk" as event Boxing Day with no date. With no calendar row, those asked for the event's date.
- **What changed:** seven rows in `db/seed/calendar.sql`, each with its source and `checked_on` 2026-10-29:

  | Day | Date | Source |
  |---|---|---|
  | Halloween | 31 Oct 2026 | the eve of All Saints' Day, 1 Nov (Church of England calendar) |
  | Bonfire Night | 5 Nov 2026 | UK Parliament |
  | Christmas Day | 25 Dec 2026 | gov.uk bank holidays |
  | Boxing Day | 26 Dec 2026 | gov.uk; the bank holiday is the substitute day, Mon 28 Dec |
  | New Year's Eve | 31 Dec 2026 | the day before New Year's Day (gov.uk) |
  | Burns Night | 25 Jan 2027 | Scotland.org |
  | St George's Day | 23 Apr 2027 | Church of England calendar; English Heritage |

- **Not changed:**
  - Pride and Notting Hill Carnival stay out (owner).
  - **Mother's Day 2027 stays 7 March.** It's the fourth Sunday of Lent (Church of England calendar), three weeks before Easter Sunday, 28 March 2027 (from gov.uk's Good Friday, 26 March). 14 March was asked for, but it's the fifth Sunday of Lent.
  - There's no bare "Christmas" alias: "at Christmas" means the season, and still asks for a date.
- **The limit:** a reply that also flags the date as unclear still asks, whatever the calendar holds. So rows 31, 41 and 65 may still ask.
- **Tests:**
  - 723 agent tests pass, 12 of them new: each new day resolves when named as an event (aliases included), and "Christmas", "Xmas" and "Christmas markets" don't resolve to the day. The seed-parsing count is now 19.
  - The seeds load twice, and `rls_and_indexes.sql` passes with 19 rows. 516 jobs tests pass.
- **Deliberate breaks:** 2, caught after one test fix:
  - Boxing Day moved to the 28th
  - a bare "Christmas" alias, which needed the test changed from "Christmas markets" to a bare "Christmas" to be caught
- **Refresh:** these rows need the next year added before they pass, with the rest of the calendar (before 1 September 2027).
