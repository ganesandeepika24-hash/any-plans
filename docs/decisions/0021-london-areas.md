# 0021: London areas, the default area and landmarks (stage 1, step 5)

Status: accepted (2026-10-28). Owner: deepika. Revisit when the Lovable app shows the default area (the radius circle around Charing Cross), if logs show searches for places the seed leaves out, or when ONS publishes new boundaries.

## Context

The seed had London's 33 boroughs (points from Nominatim) and 20 hand-picked neighbourhoods. Fifteen eval rows failed on area:

- seven places the seed lacked: Bloomsbury, Kentish Town, Hampstead, Wembley, Golders Green, Whitechapel and Southall
- "central London" (rows 34 and 56)
- landmarks: Regent Street, Somerset House, Liverpool Street, Richmond Park and Chinatown
- an event's own venue: the Chelsea Flower Show, row 55

A search with no area searched all of London, though PRD F-04 says the default is central London.

Step 5 asks for all 33 boroughs from ONS and neighbourhoods from OpenStreetMap. The owner's step 5 decisions (`claude/step-5-areas-decisions.md`, 28 Oct 2026) settle the rest:

1. A search with no area uses central London (F-04). About 40 eval rows change, each recorded in 0014.
2. "Central London" typed by the person resolves from the city's row (M-02).
3. Landmarks are a short hand-picked list. Chinatown, Richmond Park and Somerset House become aliases of Soho, Richmond upon Thames and the Strand. Regent Street, Liverpool Street and the Strand become areas of kind `landmark`, 1 km each.
4. Calendar entries get an optional `area_id`: the Chelsea Flower Show → Kensington and Chelsea. Pride stays as it is until it has a confirmed date.

At approval the owner added that "London" and "in London" resolve to central London with the note "Showing central London", so the person can refine if they meant the whole city.

## Options considered

| Question | Options | Choice | Why |
|---|---|---|---|
| Where OSM places come from | Overpass; **the Geofabrik extract** | Extract | 0009: Overpass refused automated use. The script reuses `osm_venues`' reader, now split into a shared `scan()`, and its Greater London boundary. |
| How areas reach the database | A scheduled job; **a script that writes the seed, run by hand** | Seed | Areas change rarely. A generated `areas.sql` is reviewed in git, CI loads it, and the agent tests parse it, so they test what ships. |
| Which OSM places | Every `place`; **town, suburb, neighbourhood, quarter** | Four kinds | Cities, villages, localities and squares are the wrong scale or not places people name for an outing. |
| Name clashes | Keep all; **boroughs, then hand-picked areas and landmarks, then towns, suburbs, and neighbourhoods or quarters** | That order | A name has to mean one area. The current areas keep their names (OSM's "Greenwich" town loses to the borough, and its Chinatown to Soho's alias). |
| Two OSM places of the same rank with one name | Pick one; **leave both out, and list them** | Leave out | "Hayes" (Hillingdon and Bromley) and "Plaistow" (Newham and Bromley) are genuinely two places. A guess sends someone to the wrong one. The unknown-place note then says what happened. |
| Borough points | Keep Nominatim's; **ONS's** | ONS | The step asks for ONS. The points moved by up to about 2 km. Ids don't change. |
| A street landmark's point | The whole street's bounding box; **one road segment, the one nearest the street's middle** | One segment | There's no single OSM object for Regent Street, and the Strand's relations are partial. One named segment is reproducible and in the extract. With 1 km, it covers nearly the whole street. Liverpool Street uses the mainline station's node. |
| The default area | All of London; **the city's centre and default radius** | City's row | F-04 and M-02. The centre (Charing Cross) and radius (10 km) come from `cities`, through `understand_context`, never from code. |
| What wins | — | **A named place, then the event's venue area, then the user's point, then the default** | A typed place is the clearest. The venue is where the event is. A pin or "near me" beats a default. "Near me" or a distance with no point still asks (0012). |
| An unknown place | Drop the area; **fall back to the default and say so** | Default | Decision 1: a search with no area uses central London. With a pin, it searches the pin. A stated distance is dropped, because it was from the unknown place. |
| A context from before 021 | Fail; **no default, as before** | As before | The agent can deploy before 021 is applied. |

## Decision

- **Migration 021:**
  - `areas.kind` gains `landmark`.
  - `areas.gss_code` is unique, boroughs only, `E09` plus six digits.
  - `calendar_dates.area_id` references `areas(id)`, with `on delete set null`.
  - `understand_context` adds the city's `lat`, `lon` and `default_radius_km`, and each calendar entry's `area_id`.
  - Grants are stated again for `areas` (read by all, written by `service_role`), `calendar_dates` (read by `service_role`) and the function (`service_role`).
- **`jobs/jobs/build_area_seed.py`** (`python -m jobs.build_area_seed [--file PATH]`):
  - reads ONS's 33 boroughs (refusing anything else), `db/seed/area_curation.json` and the extract
  - applies the clash order and writes `db/seed/areas.sql`, which names each row's source
  - prints what was taken and what was left out
- **The seed** (extract of 27 Oct 2026): 1,239 areas, made of 33 boroughs, 1,203 neighbourhoods and 3 landmarks. It names the attribution:
  - "Source: Office for National Statistics licensed under the Open Government Licence v.3.0. Contains OS data © Crown copyright and database right 2024."
  - "© OpenStreetMap contributors, ODbL."
- **Left out as same-rank duplicates:** Boundary Estate, Chobham Manor, East Village, The Grove, Hayes, Ledbury Estate, Lilestone Estate, Lindley Estate, Peabody Estate, Percival Street Estate, Plaistow, Scotch Estate, St James Estate, St John's Estate, Upper Sydenham, White City Estate and Winsor Terrace. Most are one place mapped twice.
- **The resolver:**
  - "London" and "central London" are the city's row (`City.is_city`).
  - The order of what wins is as above.
  - "London" adds `default_area` ("Showing central London."), and "central London" adds no note.
  - An unknown place says "Couldn't find X, so showing central London", or "…searching around your point" when there's a pin.
- **The calendar:** the Chelsea Flower Show has `area_id` Kensington and Chelsea.
- **The eval:** a new area form, `{"city_centre": true}`, matches the city's centre and radius. Model-only scoring treats no place, "London" and "central London" as the same. Each row change is in 0014.

## Trade-offs

- **The default leaves out events with no location** (0010's rule once a radius is set), and places beyond 10 km of Charing Cross, such as Wembley and Twickenham, unless they're named. Ingest still covers 25 km (0008).
- **A bigger context:** `understand_context` now returns 1,239 areas. It's cached for an hour (0012), and name matching is a linear scan, fine at this size.
- **Neighbourhoods are 1 km circles.** Large suburbs and small estates get the same radius. A per-place radius would need OSM boundaries.
- **The seed is a snapshot.** New OSM places arrive only when someone reruns the script, then reviews and commits the diff.
- **The front end will show a default circle.** Every search now carries a centre and radius, so the Lovable map (step 8) should show "Showing central London" rather than a bare circle.
- **Searches stored before 021 keep no area when refined,** as they were logged.

## Metrics and results

- **Agent tests:** 662 pass with no network. New or changed tests cover:
  - the default, "London" (with its note) and "central London" (without one)
  - the order of what wins, and the event's area beating the user's point
  - an unknown place, alone, with a distance, and with a pin
  - "near me" still asking
  - a context from before 021
  - the seeded places, landmarks and clash order, including Hayes and Plaistow left out
  - the store parsing the centre and `area_id`, and refine around the default
  - the scorer's new form, including a wrong place on a default-area row
- **Jobs tests:** 381 pass against a local database. New tests cover:
  - ONS: 33 codes in order, and five bad replies refused
  - the extract: kinds, the boundary and the landmark (real fixture data), and a missing landmark
  - the clash rules, duplicates, punctuation, and errors for the curation and ids
  - the rendered SQL, and the committed seed agreeing with the curation
  - the script's `normalise` being the agent's
- **Database** (local `postgis/postgis:17-3.6`):
  - 021 applies, a second run skips, and running it directly fails.
  - The seeds load twice.
  - New checks: 33 codes and their constraints, landmark counts, the calendar's area and its `set null`, the context's centre, and the grants.
  - `search_perf.sql` passes.
- **Deliberate breaks:** 15. Fourteen were caught at once. The scorer accepting any area for `city_centre` passed, and now has a test.
- **Ceiling with a perfect model:** end to end 72/73 (98.6%), up from 57/73. Only row 23 ("Friday night" from 17:00) still fails. Model only: 73/73.
- **Eval:** not run (Groq's daily limit, owner's hold). The rerun is step 6.

## Update 2026-10-28: the note on every default-area search (step 1.4, fix 3)

- **Why:** the owner's step 1.4 plan has results say "Showing central London" whenever no area is given. Under this decision, only a typed "London" got the note. A search with no place got the same area but no note, so the app would have had to guess the default from coordinates.
- **What changed:**
  - Every search of the default area carries `default_area` ("Showing central London."): no place, "London", or "central London".
  - "Central London" now gets the note too. The owner's instruction was "whenever the default area is used". A line saying what was searched is harmless when they asked for it.
  - Unchanged:
    - An unknown place still gets only its own `unknown_place` note, which already says central London.
    - A named area, an event's venue area and the user's point get no note.
    - A context from before 021 has no default and no note.
  - **Refine:** the follow-up alone always reads as the default area, so its own note is dropped. The refined search gets the note when its centre is still the city's centre and isn't the user's point. "Make it cheaper" on a Soho search shows no note; on a default-area search it keeps one, and "closer" keeps it with the smaller radius.
- **Eval:** 11 resolver cases (`evals/understand/cases.jsonl`) expect `default_area` now. The parse eval doesn't score notes, so its ceiling is unchanged.
- **Tests:** 704 agent tests pass, 13 of them new:
  - the resolver's default and non-default cases
  - nine refine cases, including a user standing exactly at the centre
  - the `/parse` route returning and logging the note
- **Deliberate breaks:** 7, all caught:
  - the note for typed places only
  - a second note on unknown places
  - the text changed
  - refine keeping the follow-up's own note
  - refine never adding the note
  - refine ignoring the user's point
  - refine requiring the default radius
