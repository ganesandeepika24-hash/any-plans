# 0020: Months, date ranges, seasons and recurring days (stage 1, step 4)

Status: accepted (2026-10-28). Owner: deepika. Revisit when spring, summer and autumn get dates (config only), when the Lovable app sends taps back (the contract below), or if logs show the 100-row fetch leaving recurring-day searches short.

## Context

`search_items` takes one window, `date_from` to `date_to`. The parser had no way to say "in March", "14 to 15 November" or "this winter", so the model flagged them as unclear and the app asked. "Any Saturday" and "any weekend" (rows 15 and 16, price watches) had no form at all, and the eval marked them `unsupported`. Decision 0014 pinned rows 15, 16, 21, 36 and 68 on date.

Step 4 asks for whole months, seasons, half-term windows, and range options for recurring phrases. The owner settled the rules on 28 Oct 2026, from the PRD and the parser decisions:

- A named month is a clear window (row 21: the whole of March 2027).
- "This winter" is 1 November to 31 January (row 36).
- A recurring phrase asks for a range rather than guessing. It offers three choices: "Next 4 Saturdays" (or weekends), "This month" and "Pick a month".
- A "Next 4 Saturdays" search fetches, then filters to the Saturdays, with a test for a month where weekday events would otherwise fill the limit.
- Half-term needs nothing more: the calendar has it (0019).
- Estimated dates stay whole windows, with the estimate note (0019). No change.
- Row 17 (a saved search with no date) and row 48 don't change.

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| How the model says it | Free text for code to parse; **new `when` kinds** | Kinds | Principle 5: the model names the phrase, code works out the days. New kinds: `month`, `this_month`, `next_month`, `range`, `season`, `every_weekday`, `every_weekend`. New fields default to null, so v5 replies still validate. |
| Season dates | In code; **config `date_ranges`** | Config | Principle 8. Only winter is defined, as the parser decisions do. A season with no dates asks rather than guessing. |
| A long range | Search it; **ask past a limit** | Ask | "1 November to 31 January" as a range is more likely a mistake than a plan. The limit is 62 days, in config. A season isn't limited. |
| Several Saturdays in one search | A weekdays filter in `search_items` (a migration); several searches; **one span plus agent-side `windows`** | Windows | The owner chose fetch-then-filter, and migration 020 adds config rows only. `SearchFilters` gets `windows`, the agent's own key (principle 4: still one query model). It's stored and refined with the rest. The Search node removes it before calling `search_items`. |
| Weekday events filling the limit | Filter what comes back; **fetch the most (100), filter, then cut to the limit** | Fetch 100 | With the default 20, a busy month's higher-scoring weekday events take every place, and the filter leaves nothing. A test builds that month. |
| What counts as "on a Saturday" | Starts that day; **on during it** | On during | An exhibition running Thursday to Sunday is on during Saturday. An event with no end is the moment it starts. A 01:00 Sunday start is still Saturday night, by the 05:00 rule. |
| A weekend's window | Saturday and Sunday; **Friday 17:00 to Monday 05:00** | As `this_weekend` | The same rule as every other weekend (0012). |
| A recurring day with nothing to narrow it | Guess the next one; **ask, with three choices** | Ask | The owner's rule. The question is "which dates?", with "Next 4 Saturdays", "This month" and "Pick a month" as taps. The count comes from config. |
| No days left ("Saturdays this month" on 31 January) | Drop the date; **ask again, with the choices** | Ask | Dropping it would search every date. |
| How a tap comes back | A new endpoint; **the search with the tap appended, parsed again** | Appended | As 0015's taps: text parsed again. "Hamilton on a Saturday. Next 4 Saturdays" gives `every_weekday` with count 4. "This month" gives `within: this_month`, and a picked month sets `month`. |

## Decision

- **Migration 020** (config only):
  - `date_ranges` v1: winter 1 Nov to 31 Jan, ranges up to 62 days, the "Next 4" choice, and at most 8 recurring days.
  - `understand_prompt` v6 and `refine_prompt` v3, both active. They add the new kinds and their fields, and stop flagging months and seasons as unclear. "On Saturday" stays the coming one, while "on a Saturday", "any Saturday" and "Saturdays" are `every_weekday`. Switching back is `set_active_config` to v5 or v2.
- **The schema:** `When` gains `end_month`, `end_day`, `season`, `count` and `within`, checked against the kind. `SearchFilters.windows` must hold at least two windows, in order, inside `date_from` to `date_to`.
- **The resolver** (`date_windows`, `date_window`):
  - **A month:** the 1st at 05:00 to the 1st of the next month. The current month starts now, and a month already over this year means next year.
  - **A range or season:** the end is the next end date, and the start is the last start date on or before it. So "28 Dec to 2 Jan" crosses the year, and a span under way starts now.
  - **A recurring day:** with a count, the next n days, today's included if it hasn't ended. With `within` or a month, every one in that month. With neither, it asks. More than `max_count` stops there, with a `count_capped` note. One window needs no `windows` list.
- **The node:** reads `date_ranges` with the prompt. A missing or invalid row rests, like any bad config. The version goes into the trace and the query row.
- **Clarify:** a recurring day's date question is "which dates?", with its choices.
- **The Search node:** with `windows`, asks `search_items` for 100 rows over the span, keeps the events on during a window, and cuts to the requested limit (20 by default).
- **Refine:** `windows` is a date key. A price or place change keeps the Saturdays, and a new date replaces them.
- **Eval:**
  - Rows 21, 36 and 68 get the new kinds in their replies. Their expected windows don't change.
  - Rows 15 and 16 get `every_weekday` and `every_weekend`. They now expect `needs_clarification` with `missing: ["date"]`, recorded in 0014.
  - Model-only scoring compares the windows each reply means, or the choices it would offer.

## Trade-offs

- **Fetch-then-filter can still come up short.** If more than 100 higher-scoring events fall outside the windows, Saturday events past the 100th are missed. A weekdays filter in `search_items` would fix it at the cost of a migration. The revisit line watches for it.
- **Every recurring-day search fetches 100 rows.** That's the size `search_perf.sql` already times (`limit` 100, p95 under 500 ms), and it's still one call.
- **Only winter has dates.** "This summer" asks until someone adds its months to `date_ranges`.
- **Ranges between weekdays** ("Friday to Sunday") still ask for a date.
- **The tap contract isn't built yet.** The Lovable app (step 8) has to append the tap to the original search. A bare "Next 4 Saturdays" with no search parses too, but loses the search's other words.

## Metrics and results

- **Tests:** 631 agent tests pass with no network. New tests cover:
  - months: a later month, the current one, one already over, December into January, this and next month, the clock change
  - ranges: both days included, across the year, under way, too long, a day that doesn't exist
  - winter before, during and after, and a season with no dates
  - recurring days:
    - the choices, and the count from config
    - four Saturdays, and a Saturday afternoon or 02:00 Saturday
    - weekends, including the one under way
    - this month, next month and a named month
    - none left (asks again), and the cap
  - the schema: each kind's parts, v5 replies still valid, `windows` order and bounds, the JSON round trip
  - the config: shipped values, impossible days, extra keys
  - the Search node: a busy month where weekday events would fill the limit, the requested limit after the filter, events running across a Saturday, filters unchanged without windows
  - refine: keeping or replacing windows, a recurring follow-up asking
  - the node: choices on the question, and resting on missing or invalid `date_ranges`
  - both prompts naming the new kinds
- **Database** (local `postgis/postgis:17-3.6`):
  - 020 applies, a second run skips, and running it directly fails.
  - The seeds load twice.
  - `rls_and_indexes.sql` passes, with new checks for `date_ranges` v1 and both prompt versions.
  - `search_perf.sql` passes, and all 361 jobs tests pass.
- **Deliberate breaks:** 18, all caught. Among them:
  - no 100-row fetch, which the busy-month test caught
  - not cutting back to the limit
  - an overlap check on start time only
  - sending `windows` to `search_items`
  - past windows kept
  - a month already started treated as next year's
  - a range that can't cross the year
  - refine keeping old windows
  - the node dropping the choices
- **Ceiling with a perfect model:** end to end 57/73 (78.1%), up from 53/73. Rows 15, 16, 21 and 68 now pass. Row 36 passes on date and still fails on area (step 5).
- **Eval:** not run (Groq's daily limit, owner's hold). The rerun is step 6.
