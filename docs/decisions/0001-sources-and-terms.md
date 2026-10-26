# 0001: Sources and terms

Status: draft (2026-10-25). Owner: deepika. Full evidence trail in `verification-log.md`.

## Principle

Only sources whose terms allow our use are included. Where terms are unclear, the source is not used until the terms are read from the provider's own page and recorded here.

## Sources in scope for the MVP

| Source | Used for | Free allowance | Key terms to honour | Status |
|---|---|---|---|---|
| Ticketmaster Discovery API | Main event feed, price checks | 5,000 calls a day; 2 or 5 a second (sources conflict) | Branding and linking rules; attribution; the terms reserve the right to limit bulk, non-user-initiated calls | **NOT CLEARED.** General Terms of Use (last updated 27 June 2023) read 26 Oct 2026. Bulk fetching isn't expressly prohibited, but the clause on rate-limiting calls "not primarily in response to direct user actions" applies. The production schedule stays off until Ticketmaster confirms in writing, or the owner decides to proceed knowingly. See "Ticketmaster conditions". |
| Skiddle API | Clubs, gigs, comedy, festivals (UK) | Free key on application | **Non-commercial use only**; commercial use needs written approval | OK for a portfolio demo. Do not monetise. |
| OpenStreetMap (Overpass) | Venue base layer: restaurants, bars, theatres, galleries | Free, with fair-use limits | ODbL attribution: "© OpenStreetMap contributors" in the footer and on the map | Attribution on UI; read terms before stage 1 |
| Tavily | Capped web fallback: advanced search, max 16 calls a day, monthly ceiling 950 credits | 1,000 credits a month | Results saved for 24 hours only; show "Found on the web today" and link to source | **Set:** confirm on dashboard that failed calls are charged |
| gov.uk bank holidays | Resolving "bank holiday weekend" | Free | Open Government Licence | OK |
| TfL Unified API | Travel times (stage 5) | Free | TfL terms; key needed | Not yet checked |

## Ticketmaster conditions

From the terms review on 26 Oct 2026. These apply whether or not the nightly schedule is cleared.

- **Retention:** delete events after their end time, with no archive (decision 0007).
- **Takedown:** remove content within 24 hours of an owner's request, through an admin path (stage 2).
- **Images:** display only, from Ticketmaster's URLs. Never re-host or copy them.
- **Privacy:** a privacy policy linked in the site footer before launch.

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
- [ ] OSM copyright and attribution page.
- [x] Skiddle: API key requested 25 Oct 2026 as non-commercial portfolio use. Full project description emailed to dev@skiddle.com the same day. Website on the form is a GitHub profile (to be replaced at launch). Approved and key received 25 Oct 2026 (key saved in password manager as SKIDDLE_KEY; not in Lovable or the repo). Record any conditions from the approval email here verbatim. Reminder: replace the GitHub profile website with the live URL at launch, and ask for written approval before any commercial use.
- [ ] Sign-up card requirements recorded for each account.