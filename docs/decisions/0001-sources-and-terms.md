# 0001: Sources and terms

Status: draft (2026-10-25). Owner: deepika. Full evidence trail in `verification-log.md`.

## Principle

Only sources whose terms allow our use are included. Where terms are unclear, the source is not used until the terms are read from the provider's own page and recorded here.

## Sources in scope for the MVP

| Source | Used for | Free allowance | Key terms to honour | Status |
|---|---|---|---|---|
| Ticketmaster Discovery API | Main event feed, price checks | 5,000 calls a day; 2 or 5 a second (sources conflict) | Branding and linking rules; attribution; the terms reserve the right to limit bulk, non-user-initiated calls | **NOT CLEARED.** General Terms of Use (last updated 27 June 2023) read 26 Oct 2026. Bulk fetching isn't expressly prohibited, but the clause on rate-limiting calls "not primarily in response to direct user actions" applies. The production schedule stays off until Ticketmaster confirms in writing, or the owner decides to proceed knowingly. See "Ticketmaster conditions". |
| Skiddle API | Clubs, gigs, comedy, festivals (UK) | Free key on application | **Non-commercial use only**; commercial use needs written approval | OK for a portfolio demo. Do not monetise. See "Skiddle conditions". |
| OpenStreetMap (Geofabrik extract) | Venue base layer: restaurants, bars, pubs, theatres, museums, galleries | Free; Geofabrik extract downloaded weekly | ODbL: "© OpenStreetMap contributors" with a link to openstreetmap.org/copyright wherever OSM venues appear; OSM-derived rows offered under the ODbL on request; never matched or merged with Ticketmaster or Skiddle venues | **Terms read 27 Oct 2026.** See the ODbL section of 0006 and the OSM venues section of 0009. |
| Tavily | Capped web fallback: advanced search, max 16 calls a day, monthly ceiling 950 credits | 1,000 credits a month | Results saved for 24 hours only; show "Found on the web today" and link to source | **Set:** confirm on dashboard that failed calls are charged |
| gov.uk bank holidays | Resolving "bank holiday weekend" | Free | Open Government Licence | OK |
| TfL Unified API | Travel times (stage 5) | Free | TfL terms; key needed | Not yet checked |

## Ticketmaster conditions

From the terms review on 26 Oct 2026. These apply whether or not the nightly schedule is cleared.

- **Retention:** delete events after their end time, with no archive (decision 0007).
- **Takedown:** remove content within 24 hours of an owner's request, through an admin path (stage 2).
- **Images:** display only, from Ticketmaster's URLs. Never re-host or copy them.
- **Privacy:** a privacy policy linked in the site footer before launch.

## Skiddle conditions

Approval and API key received 25 Oct 2026. Conditions from the approval email, quoted exactly:

> Please use this for all requests. Your account will initially be rate limited. If you wish for this limit to be increased, please contact dev@skiddle.com.
>
> Please note, for any commercial use of this API, please contact us on dev@skiddle.com or phone 03333 01 01 01 for approval before use

Skiddle schedule: APPROVED (conditions recorded 26 Oct 2026)

## Sources explicitly out of scope

| Source | Reason |
|---|---|
| Foursquare | Free allowance cut to 500 calls a month (June 2026). See 0003. |
| Eventbrite | No public search API since 2020. Reached only via capped web search. |
| Restaurant booking platforms (OpenTable, Resy, Tock) | Terms prohibit scraping. Availability isn't available, so we link out and offer booking-opens reminders. |
| Gemini API | Not allowed for UK end users on the free tier. See 0002. |

## Display rules

- Every card shows a confidence label derived from `source` and `last_checked_at`, never from the model (R-02).
- Restaurant availability is always labelled "Check availability on [site]".
- Demo restaurant feed is labelled "Demo data".
- Booking links open the official site in a new tab.

## Caching rules

- Event data refreshed nightly; no per-visitor calls to Ticketmaster.
- Web search results kept at most 24 hours (`web_results`).
- Tavily: advanced search (2 credits per call), at most 16 calls a day, and a monthly ceiling of 950 credits. Both limits are config values, counted atomically in `usage_daily`. When either is reached, web fallback pauses with the normal "filters still work" message.
- Images only where the source licence allows. Ticketmaster images are display only: link to Ticketmaster's image URLs, never re-host or copy them.

## Checks before step 0.2 is ticked

- [x] Ticketmaster Terms of Use, especially bulk and non-user-initiated calls, and whether the nightly job fits. **Result: not cleared** (read 26 Oct 2026; see the Ticketmaster row and "Ticketmaster conditions").
- [ ] Tavily: confirm on the dashboard that advanced search costs 2 credits, and whether failed or empty calls are charged.
- [x] OSM copyright and attribution page. Read 27 Oct 2026, with the ODbL summary and the OSMF Collective Database Guideline. Conditions recorded in 0006 (ODbL section).
- [x] Skiddle: API key requested 25 Oct 2026 as non-commercial portfolio use. Full project description emailed to dev@skiddle.com the same day. Website on the form is a GitHub profile (to be replaced at launch). Approved and key received 25 Oct 2026 (key saved in password manager as SKIDDLE_KEY; not in Lovable or the repo). Conditions from the approval email are quoted under "Skiddle conditions" (recorded 26 Oct 2026). Reminder: replace the GitHub profile website with the live URL at launch, and ask for written approval before any commercial use.
- [ ] Sign-up card requirements recorded for each account.
## Update 2026-10-29: Ticketmaster compliance, PR A (migration 025)

Ticketmaster's written reply on a scheduled bulk fetch hasn't come. Until it does, the source stays **NOT CLEARED** and out of the nightly job. This update builds the conditions above that don't depend on that reply. The owner approved the plan on 29 Oct 2026, choosing (a) on both open questions.

| Question | Options | Choice | Why |
|---|---|---|---|
| Takedown path | A SQL snippet in a doc; **a tested job and a manual workflow** | Job and workflow | Repeatable inside 24 hours, with a dry run, a `job_runs` row and tests. Runbook: `docs/runbooks/takedown.md`. |
| Matching the event | `source_id` alone; **`source` and `source_id` together** | Both | Source IDs are only unique within a source. A test keeps a Skiddle row with the same ID string, and a deliberate break caught the single-key delete. |
| Keeping it gone | Rely on nobody ingesting it again; filter in the ingest code; **a `takedowns` table and a trigger on `events`** | Trigger | The ingest code stays as it is: a taken-down event is skipped on insert or update, by any job. The one change is that `upsert_event` treats the skipped row as not new rather than failing. |
| Retention for Ticketmaster | **(a) 0 days after the end, in config**; (b) 1 day after the end | (a) | "Delete after the end time, no archive" (this record and 0007). The 04:00 job deletes within a day of the end. Config `event_retention` holds the days per source (most 7), so changing them needs no code. A missing or malformed config uses 0, never longer. |
| Scheduled runs | The workflow guard only; **the guard plus a check at run time** | Both | `ingest_schedule` (`{"skiddle": true, "ticketmaster": false}`) is checked in `jobs.ingest.main()` before any call. A scheduled run of a source that isn't true stops with exit 3, and so does every scheduled run if the config can't be read. A test keeps the config in step with this record's status lines. |

**Still open:**
- **Ticketmaster's reply** on bulk, non-user-initiated calls. If it clears the nightly job:
  - change the Ticketmaster row's status
  - add a version of `ingest_schedule` with `"ticketmaster": true`
  - then give the workflow its schedule. The guard tests follow the status line.
- **PR B, the on-demand fetch** behind `ticketmaster_live` (off by default, not built yet). Live results would go in a separate list (choice (a)), so the main results stay identical to signed-out search (PRD F-01).
- **The takedown condition** above is now met by the job, not a stage 2 admin path.
