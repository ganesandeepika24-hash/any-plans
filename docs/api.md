# Agent API: the contract for the Lovable front end

This file is the contract between the agent (`agent/`) and the Lovable app (step 1.4). The app works from this file, not from guesses. If the code and this file disagree, the code is right and this file has a bug: raise it, don't work around it.

Written 28 Oct 2026 against `main` at `9edf9fd` (after PR #26). Updated for CORS (PR #28) and for migration 022's confidence label and event points (decision 0022). Every example response below was produced by running the real routes with the agent tests' fakes for the model, the database and the embedder (the harnesses in `agent/tests/test_parse_route.py`, `test_search_route.py` and `test_refine_route.py`). The event rows in the examples are invented. Ids and trace ids differ on every call.

Sections marked **Not built yet** describe something the step 1.4 plan asks for that `main` doesn't do. Don't build against them as if they exist.

## Contents

1. [Base URL, CORS and auth](#1-base-url-cors-and-auth)
2. [Shared request body](#2-shared-request-body)
3. [Shared response body](#3-shared-response-body)
4. [POST /parse](#4-post-parse)
5. [POST /search](#5-post-search)
6. [POST /refine](#6-post-refine)
7. [GET /health](#7-get-health)
8. [Clarify questions and how taps go back](#8-clarify-questions-and-how-taps-go-back)
9. [Notes](#9-notes)
10. [Result cards](#10-result-cards)
11. [Errors](#11-errors)
12. [What the app must send, and what it must ignore](#12-what-the-app-must-send-and-what-it-must-ignore)
13. [Filter-only search (Supabase)](#13-filter-only-search-supabase)
14. [Not built yet](#14-not-built-yet)

## 1. Base URL, CORS and auth

- **Base URL:** the agent runs on Render. The app reads its address from one setting, `AGENT_URL` (decision 0004), and makes every agent call from one module, `src/lib/api.ts`.
- **CORS:** only the exact origins in the agent's `CORS_ORIGINS` setting (comma-separated, set in Render) can call it from a browser: the live site and the Lovable project's address. Preview builds (the Lovable editor preview, Vercel or Netlify branch previews) can't, so test AI search on the live site (owner's choice, 28 Oct 2026). Allowing previews later is one Render setting, `CORS_ORIGIN_REGEX`, with no code change.
  - Allowed: `GET` and `POST`, with the `Authorization` and `Content-Type` headers. No cookies: don't use `credentials: "include"`.
  - Errors from the agent, 401 included, carry the CORS header, so the app can read them.
  - A new site address (a custom domain, a move from Vercel to Netlify) must be added to `CORS_ORIGINS` before it can call the agent. Until then the browser blocks the call, and the app sees a network error.
- **Auth:** send the signed-in user's Supabase access token as `Authorization: Bearer <access_token>`. The agent checks it against the project's JWKS (ES256), with `aud` = `authenticated` and `iss` = `<SUPABASE_URL>/auth/v1`, and requires `exp`, `sub`, `aud` and `iss`. `sub` must be a UUID. There's no API key and no cookie.

| Endpoint | Sign-in | Notes |
|---|---|---|
| `GET /health` | No | Liveness only. No database call. |
| `POST /parse` | **Yes** | AI search. Counts against the daily cap. |
| `POST /search` | **Yes** | AI search. Counts against the daily cap. |
| `POST /refine` | **Yes** | AI search. Counts against the daily cap. |
| Filter-only search | No | **Not an agent endpoint.** The browser calls the database function `search_items_public` through Supabase with the publishable key, signed in or out (decision 0023). See [section 13](#13-filter-only-search-supabase). |

Filters work signed out; AI search needs sign-in (PRD U-01, U-02). Signed out, the app doesn't call the agent's POST routes: it shows the filter panel and a prompt to sign in for AI search.

The daily cap is per user: 10 AI searches a day by default (config `ai_search_cap`, editable without a redeploy), counted from midnight UTC. Every `/parse`, `/search` and `/refine` that comes back `ok`, `needs_clarification` or `ai_resting` counts, including a question and a rest. A reply at the cap (`reason: "daily_cap"`) doesn't count.

## 2. Shared request body

`POST /parse` and `POST /search` take the same body, `ParseRequest` (`agent/app/parse.py`):

```json
{
  "query": "Jazz in Soho tonight",
  "location": {"lat": 51.5031, "lon": -0.1132, "source": "device"}
}
```

| Field | Type | Required | Rules |
|---|---|---|---|
| `query` | string | Yes | 1 to 500 characters after trimming. Blank or whitespace only is a 422. |
| `location` | object or null | No | The user's own point, for this request only. Leave it out (or send null) unless the user gave it. |
| `location.lat` | number | Yes, in `location` | -90 to 90. Must be a JSON number: `"51.5"` is a 422. |
| `location.lon` | number | Yes, in `location` | -180 to 180. Must be a JSON number. |
| `location.source` | `"device"`, `"pin"` or null | No | `device`: read once from the browser's geolocation, with permission. `pin`: dropped on the map. Anything else is a 422. Optional, but the app should always send it (decision 0017). |

Unknown keys anywhere in the body are refused with 422. There is no `filters`, `city`, `radius` or `page` field: the agent works these out from `query`.

## 3. Shared response body

All three POST routes answer with the same fields (`ParseResponse`). `/search` and `/refine` add `results` (`SearchResponse`). Every field is always present; unused ones are null or empty.

| Field | Type | Meaning |
|---|---|---|
| `status` | `"ok"`, `"needs_clarification"` or `"ai_resting"` | What happened. See each endpoint. |
| `reason` | `"providers"`, `"daily_cap"`, `"unavailable"` or null | Set only when `status` is `ai_resting`. |
| `message` | string or null | Text to show the user. Set for `ai_resting`, and for an `ok` search whose search step failed. |
| `query_id` | UUID string | This request's id. Keep it: `/refine` needs it, and click logging will use it. |
| `filters` | object or null | The resolved `SearchFilters` (decision 0010). See below. |
| `vibe` | `"Chilled"`, `"Lively"`, `"Cultural"`, `"Foodie"`, `"Outdoors"` or null | The vibe the user asked for. Not a filter yet (decision 0015). |
| `who` | `"Solo"`, `"Date"`, `"Friends"`, `"Family with kids"` or null | The group the user mentioned. Not a filter yet. |
| `location_source` | `"device"`, `"pin"` or null | Set only when the filters' `lat` and `lon` are the user's own point from the request. Null when a named area or the default area was used (decision 0017). |
| `embedding_text` | string or null | The words used for similarity ranking. Internal. |
| `notes` | array of `{code, message}` | Things the user should know about how the search was read. See [Notes](#9-notes). |
| `missing` | array of strings | Set when `status` is `needs_clarification`: `location`, `date`, `budget`, `price_basis`, `event_date`, `category`, always in that order. |
| `questions` | array of `{reason, question, choices, groups}` | One per entry in `missing`, in the same order. See [Clarify](#8-clarify-questions-and-how-taps-go-back). |
| `trace_id` | string or null | The Langfuse trace. Internal. |
| `results` | array or null | `/search` and `/refine` only. See [Result cards](#10-result-cards). |

`filters` holds only the keys that are set:

| Key | Type | Meaning |
|---|---|---|
| `date_from`, `date_to` | ISO 8601 with offset | Events whose span overlaps this window. |
| `categories` | array of `Music`, `Comedy`, `Theatre`, `Sport`, `Nightlife`, `Arts`, `Family`, `Food & Drink` | Never empty when present. |
| `price_min`, `price_max` | number (GBP) | Price per ticket. Events whose price range overlaps. |
| `include_unpriced` | boolean | `false` drops events with no price (set for "free"). |
| `lat`, `lon`, `radius_km` | numbers | All three or none. `radius_km` is in km (0 to 100]. |
| `limit` | integer | 1 to 100. Not set by the parser today: the search returns up to 20. |
| `windows` | array of `[from, to]` pairs | The agent's own key: only these days count inside `date_from` to `date_to` ("the next 4 Saturdays", decision 0020). The filter panel can't express it. |

## 4. POST /parse

Free text in, filters out. No search runs. The app can use it to fill the filter panel from a typed request (principle 4: the AI search bar and the filter panel fill the same `SearchFilters`). **Sign-in required.**

Every outcome below is HTTP **200**. Only the errors in [Errors](#11-errors) use other codes.

### Example request

```http
POST /parse
Authorization: Bearer <supabase access token>
Content-Type: application/json

{"query": "Jazz in Soho tonight"}
```

### `ok`: understood, with filters

```json
{
  "status": "ok",
  "reason": null,
  "message": null,
  "query_id": "b884e65f-7118-455f-be1a-75c243293631",
  "filters": {
    "date_from": "2026-10-28T20:00:00+00:00",
    "date_to": "2026-10-29T05:00:00+00:00",
    "categories": ["Music"],
    "lat": 51.514065,
    "lon": -0.135049,
    "radius_km": 1.0
  },
  "vibe": null,
  "who": null,
  "location_source": null,
  "embedding_text": "jazz",
  "notes": [],
  "missing": [],
  "questions": [],
  "trace_id": "b1bc85ec888d859b3e31149083813a95"
}
```

### `ok` with the default area

No place, "London" or "central London" searches central London from the city's row (decision 0021). **Every search of the default area carries the `default_area` note** (step 1.4 fix 3), and the app shows its message, "Showing central London.", with the results. The app never works out the default area from coordinates: the note is the signal. A named area, an event's own venue area or the user's point gets no such note; an unknown place gets its own `unknown_place` note instead. Request: `{"query": "Comedy in London this weekend"}`.

```json
{
  "status": "ok",
  "reason": null,
  "message": null,
  "query_id": "ef8a5889-76bc-4c88-bb55-625ac401986c",
  "filters": {
    "date_from": "2026-10-30T17:00:00+00:00",
    "date_to": "2026-11-02T05:00:00+00:00",
    "categories": ["Comedy"],
    "lat": 51.507333,
    "lon": -0.127654,
    "radius_km": 10.0
  },
  "vibe": null,
  "who": null,
  "location_source": null,
  "embedding_text": "comedy",
  "notes": [
    {
      "code": "default_area",
      "message": "Showing central London."
    }
  ],
  "missing": [],
  "questions": [],
  "trace_id": "5a7502ba2c5ecd345b6080d6530ab64d"
}
```

### `needs_clarification`: a question with chips

The app asks instead of searching. `filters` holds what did resolve (here the weekend and the £40 budget), so nothing the user said is lost. Request: `{"query": "Something fun this weekend, under £40 each"}`.

```json
{
  "status": "needs_clarification",
  "reason": null,
  "message": null,
  "query_id": "7df5a0d7-5b4c-4be0-9019-2fd156ea907a",
  "filters": {
    "date_from": "2026-10-30T17:00:00+00:00",
    "date_to": "2026-11-02T05:00:00+00:00",
    "price_max": 40.0,
    "lat": 51.507333,
    "lon": -0.127654,
    "radius_km": 10.0
  },
  "vibe": null,
  "who": null,
  "location_source": null,
  "embedding_text": "something fun",
  "notes": [
    {
      "code": "default_area",
      "message": "Showing central London."
    }
  ],
  "missing": ["category"],
  "questions": [
    {
      "reason": "category",
      "question": "Something fun: what are you in the mood for?",
      "choices": ["Music", "Comedy", "Food & Drink", "Arts", "Nightlife", "Family", "Sport", "Chilled", "Lively", "Cultural", "Foodie", "Outdoors", "Just surprise me"],
      "groups": [
        {
          "field": "category",
          "choices": ["Music", "Comedy", "Food & Drink", "Arts", "Nightlife", "Family", "Sport"]
        },
        {
          "field": "vibe",
          "choices": ["Chilled", "Lively", "Cultural", "Foodie", "Outdoors"]
        },
        {
          "field": null,
          "choices": ["Just surprise me"]
        }
      ]
    }
  ],
  "trace_id": "c7d62a7956e194c4dab2a4216371db61"
}
```

### `needs_clarification` with the device location

"Near me" with the device location sent: the location isn't asked, and `location_source` says where the point came from. A vague request still asks for a category (near-me decision). Request:

```json
{"query": "Something near me tonight", "location": {"lat": 51.5031, "lon": -0.1132, "source": "device"}}
```

Response, first fields (the `questions` entry is the same category question as above, with "Something" as its subject):

```json
{
  "status": "needs_clarification",
  "filters": {
    "date_from": "2026-10-28T20:00:00+00:00",
    "date_to": "2026-10-29T05:00:00+00:00",
    "lat": 51.5031,
    "lon": -0.1132,
    "radius_km": 1.609344
  },
  "location_source": "device",
  "missing": ["category"]
}
```

The same request with no `location` comes back with `"missing": ["location", "category"]` and two questions. `location_source` is null and `filters` has no point.

### `ai_resting`: no model answered

The app falls back to filter-only search and shows `message`. `filters` is null. Request: `{"query": "Jazz in Soho tonight"}`, with every provider failing.

```json
{
  "status": "ai_resting",
  "reason": "providers",
  "message": "AI search is resting; filters still work",
  "query_id": "d91feb64-21a8-43ac-ac64-e65af2f26cf0",
  "filters": null,
  "vibe": null,
  "who": null,
  "location_source": null,
  "embedding_text": null,
  "notes": [],
  "missing": [],
  "questions": [],
  "trace_id": "b0d13830a429ddf300ecf70e21160e33"
}
```

`reason` is one of:

| `reason` | When | `message` |
|---|---|---|
| `providers` | Every model provider failed, or hit its daily limit. | "AI search is resting; filters still work" |
| `unavailable` | The cap or config couldn't be read. No model was called. | "AI search is resting; filters still work" |
| `daily_cap` | The user has used today's AI searches. No model was called. | "Daily AI search limit reached; filters still work" |

### The cap reply (HTTP 200, not 429)

**There is no 429 on `main`.** The daily cap is a normal 200 reply with `status: "ai_resting"` and `reason: "daily_cap"` (decision 0013: resting is a designed outcome the browser handles, not an error). Nothing else rate-limits requests. If the 1.4 plan wants a 429, that's a change to the agent and to decision 0013. The app must handle the 200 below.

```json
{
  "status": "ai_resting",
  "reason": "daily_cap",
  "message": "Daily AI search limit reached; filters still work",
  "query_id": "7f32ff33-703b-4a35-a5b1-dec4ed9957fa",
  "filters": null,
  "vibe": null,
  "who": null,
  "location_source": null,
  "embedding_text": null,
  "notes": [],
  "missing": [],
  "questions": [],
  "trace_id": null
}
```

## 5. POST /search

The same as `/parse`, then the search runs if the parse was `ok`. **Sign-in required.** POST, not GET, so the user's location never appears in a URL; `GET /search` is a 405.

- **Request body:** the same as `/parse` ([section 2](#2-shared-request-body)).
- **`needs_clarification` and `ai_resting`:** the same as `/parse`, plus `"results": null`. No search runs.
- **`ok`:** `results` is an array of up to 20 cards, best first. An empty array means nothing matched.
- **`ok` but the search step failed:** `results` is null and `message` is "Search is unavailable right now. Try again in a moment." The filters are still there, so the app can fall back to filter-only search with them.

### Example request

```http
POST /search
Authorization: Bearer <supabase access token>
Content-Type: application/json

{"query": "Jazz in Soho tonight"}
```

### Example response

```json
{
  "status": "ok",
  "reason": null,
  "message": null,
  "query_id": "f6905154-3d25-4aff-bf8f-9dbd6977f7d7",
  "filters": {
    "date_from": "2026-10-28T20:00:00+00:00",
    "date_to": "2026-10-29T05:00:00+00:00",
    "categories": ["Music"],
    "lat": 51.514065,
    "lon": -0.135049,
    "radius_km": 1.0
  },
  "vibe": null,
  "who": null,
  "location_source": null,
  "embedding_text": "jazz",
  "notes": [],
  "missing": [],
  "questions": [],
  "trace_id": "855d1803d7c0e6f704ca93923e494ffb",
  "results": [
    {
      "rank": 1,
      "id": "00000000-0000-0000-0000-000000000001",
      "source": "ticketmaster",
      "title": "Late Late Show",
      "category": "Music",
      "genre": "Jazz",
      "start_at": "2026-10-28T22:30:00Z",
      "end_at": "2026-10-29T02:00:00Z",
      "venue_name": "Ronnie Scott's",
      "price_min": 25.0,
      "price_max": 45.0,
      "currency": "GBP",
      "url": "https://www.ticketmaster.co.uk/event/EXAMPLE1",
      "image_url": "https://s1.ticketm.net/dam/EXAMPLE1.jpg",
      "last_checked_at": "2026-10-28T06:00:00Z",
      "confidence": "checked_today",
      "confidence_label": "Price checked today",
      "distance_m": 412.6,
      "lat": 51.513435,
      "lon": -0.131566,
      "score": 0.86
    },
    {
      "rank": 2,
      "id": "00000000-0000-0000-0000-000000000002",
      "source": "skiddle",
      "title": "Jazz Jam",
      "category": "Music",
      "genre": null,
      "start_at": "2026-10-28T20:00:00Z",
      "end_at": null,
      "venue_name": "The Spice of Life",
      "price_min": null,
      "price_max": null,
      "currency": null,
      "url": "https://www.skiddle.com/whats-on/EXAMPLE2",
      "image_url": null,
      "last_checked_at": "2026-10-28T03:10:00Z",
      "confidence": "check_site",
      "confidence_label": "Check availability on Skiddle",
      "distance_m": 655.0,
      "lat": 51.513765,
      "lon": -0.129752,
      "score": 0.79
    }
  ]
}
```

### Example: the search step failed

```json
{
  "status": "ok",
  "reason": null,
  "message": "Search is unavailable right now. Try again in a moment.",
  "query_id": "820e9c43-1065-41e8-8e65-9086ae40ae26",
  "filters": {
    "date_from": "2026-10-28T20:00:00+00:00",
    "date_to": "2026-10-29T05:00:00+00:00",
    "categories": ["Music"],
    "lat": 51.514065,
    "lon": -0.135049,
    "radius_km": 1.0
  },
  "vibe": null,
  "who": null,
  "location_source": null,
  "embedding_text": "jazz",
  "notes": [],
  "missing": [],
  "questions": [],
  "trace_id": "7ca310dd747269f5398ab3097a0a3a55",
  "results": null
}
```

## 6. POST /refine

A follow-up to an earlier search ("make it cheaper", "somewhere closer", "what about Sunday?"), applied to that search's filters, then searched (decision 0018). **Sign-in required.** It counts against the same daily cap.

### Request body (`RefineRequest`)

| Field | Type | Required | Rules |
|---|---|---|---|
| `query_id` | UUID string | Yes | The `query_id` of an earlier `/parse`, `/search` or `/refine` by the same user, whose `status` was `ok`. |
| `message` | string | Yes | The follow-up, 1 to 500 characters after trimming. |
| `location` | object or null | No | The same shape as in [section 2](#2-shared-request-body). **Send it again if the earlier reply had a non-null `location_source`**: the agent never stores the user's point, so it can't reuse it. |

Unknown keys are a 422.

### Response

The same as `/search` ([section 5](#5-post-search)), with a new `query_id` for the refined search. Refine the latest `query_id` next time, so changes build on each other.

| HTTP | `detail` | When |
|---|---|---|
| 404 | "No such earlier search" | The `query_id` doesn't exist, or belongs to another user. Both look the same on purpose. |
| 409 | "That search didn't finish, so it can't be refined" | The earlier search asked a question, rested or hit the cap. The user answers the question or searches again. |
| 503 | "Can't read the earlier search right now" | The earlier search couldn't be read. Try again. |

### Example request

```http
POST /refine
Authorization: Bearer <supabase access token>
Content-Type: application/json

{"query_id": "5d0c6a8e-0000-4000-8000-000000000002", "message": "make it cheaper"}
```

### Example response

The earlier search was live music this weekend under £40; "cheaper" lowers the limit to £20.

```json
{
  "status": "ok",
  "reason": null,
  "message": null,
  "query_id": "ac99da65-81e3-4dbc-94e2-adfad1202ee2",
  "filters": {
    "date_from": "2026-10-30T17:00:00+00:00",
    "date_to": "2026-11-02T05:00:00+00:00",
    "categories": ["Music"],
    "price_max": 20.0
  },
  "vibe": null,
  "who": null,
  "location_source": null,
  "embedding_text": "live music",
  "notes": [],
  "missing": [],
  "questions": [],
  "trace_id": "3f9c5440e3d95b139f8e610c81bce681",
  "results": [
    {
      "rank": 1,
      "id": "00000000-0000-0000-0000-000000000003",
      "source": "ticketmaster",
      "title": "Sunday Jazz Brunch",
      "category": "Music",
      "genre": "Jazz",
      "start_at": "2026-11-01T12:00:00Z",
      "end_at": "2026-11-01T15:00:00Z",
      "venue_name": "The Jazz Cafe",
      "price_min": 15.0,
      "price_max": 20.0,
      "currency": "GBP",
      "url": "https://www.ticketmaster.co.uk/event/EXAMPLE3",
      "image_url": null,
      "last_checked_at": "2026-10-28T06:00:00Z",
      "confidence": "checked_today",
      "confidence_label": "Price checked today",
      "distance_m": null,
      "lat": 51.538988,
      "lon": -0.142734,
      "score": 0.64
    }
  ]
}
```

A refine can also answer `needs_clarification` or `ai_resting`, exactly as `/search` does.

## 7. GET /health

Public liveness check. No auth, no database call. Use it for an uptime check or a "service is waking up" state. It doesn't tell you whether AI search is resting.

### Example request

```http
GET /health
```

### Example response

```json
{
  "status": "ok",
  "config_version": "eval-9edf9fd"
}
```

`status` is always `"ok"` when the agent answers. `config_version` is the deployed release's label (the `CONFIG_VERSION` setting; `"dev"` when unset).

## 8. Clarify questions and how taps go back

A `needs_clarification` reply has one question per missing reason. Show them together as one step (the 1.4 plan: ask once, combining the missing parts), with each question's chips under it.

| `reason` | `question` (the subject is the user's words, capitalised) | `choices` |
|---|---|---|
| `location` | "{Subject}: which part of London, or should I use your location?" | "Use my location", "Pick an area", "Drop a pin on the map" |
| `date` (recurring day) | "{Subject}: which dates?" | "Next 4 Saturdays", "This month", "Pick a month" (the day and count vary) |
| `date` (other) | "{Subject}: which date do you mean?" | none: typed answer |
| `budget` | "{Subject}: what's your budget per ticket?" | none: typed answer |
| `price_basis` | "{Subject}: is £60 per person or in total?" | "Per person", "In total" |
| `event_date` | "I don't know the date for {event}. Can you give me a date?" | none: typed answer |
| `category` | "{Subject}: what are you in the mood for?" | the three groups below |

- **`choices`** is always the flat list. **`groups`** is set only on the category question, and says which field a tap fills: `category` (Music, Comedy, Food & Drink, Arts, Nightlife, Family, Sport), `vibe` (Chilled, Lively, Cultural, Foodie, Outdoors), and a group with `field: null` for "Just surprise me". Show the three groups as separate rows.
- **An empty `choices`** means a text box, not chips. The user can always type their own answer instead of tapping.

### How an answer goes back

There's no answer endpoint. The app sends a new `/search` (or `/parse`) with the **original query plus the answer appended as text**, and the parser reads it again (decisions 0015 and 0020). For example, "Something fun this weekend, under £40 each" plus a "Comedy" tap is sent as `{"query": "Something fun this weekend, under £40 each. Comedy"}`. Each resend is a new AI search and counts against the cap.

| Tap | What the app sends |
|---|---|
| A category, vibe, date or price-basis chip, "Just surprise me", or a typed answer | The original query with the tap's text appended. |
| "Pick an area" | The original query with the chosen area's name appended (e.g. ". Camden"). |
| "Use my location" | The original query unchanged, with `location: {lat, lon, source: "device"}` read from the browser. |
| "Drop a pin on the map" | The original query unchanged, with `location: {lat, lon, source: "pin"}`. |

### Near me and the device location

The near-me decision says "near me" uses the device location, read once with permission, and asks where only when that's unavailable. The agent can't read the browser's location, so the app does this:

1. Send the query without `location`.
2. If the reply's `missing` contains `location` and the user hasn't refused permission, ask the browser for the position. If permission is granted, send the query again with `source: "device"` and don't show the location question.
3. If permission is refused or unavailable, show the location question. Its "Use my location" chip is then hidden or shows that permission is off; "Pick an area" and "Drop a pin on the map" still work.

This costs two AI searches for a near-me request (one for each call). The permission-refused path needs a manual test before stage 2 (decision 0017).

## 9. Notes

`notes` explain how the request was read. Show each `message` above the results, as plain text. Codes are stable; messages may change.

| `code` | Example `message` |
|---|---|
| `default_area` | "Showing central London." Sent with every search of the default area. |
| `unknown_place` | "Couldn't find 'Narnia', so showing central London." (or "…so searching around your point.") |
| `no_such_date` | "That time has already passed, so dates were ignored." |
| `estimated_date` | "{Event}'s date is an estimate, so check it." |
| `count_capped` | "Showing the next 8, the most." |
| `radius_capped` | "Searching within 100 km, the furthest." |
| `price_range_inverted` | "The lowest price was above the highest, so prices were ignored." |
| `already_free` | "These are already free." (refine) |
| `no_price_limit` | "There was no price limit to raise." (refine) |
| `radius_floor` | "Searching within 0.5 km, the closest." (refine; the number comes from config) |

## 10. Result cards

Each entry in `results` (`ResultOut`, `agent/app/search.py`):

| Field | Type | Card use |
|---|---|---|
| `rank` | integer, from 1 | Order. Results are already sorted. |
| `id` | UUID string | The event's id. Use it as the list key. |
| `source` | string: `ticketmaster` or `skiddle` today | Where the event came from. Feeds the confidence label. |
| `title` | string | Title. |
| `category` | string or null | One of the eight categories. |
| `genre` | string or null | Optional subtitle. |
| `start_at` | ISO 8601, UTC (`Z`) | Start. Show in Europe/London time. |
| `end_at` | ISO 8601 or null | End. Null means unknown; don't invent one. |
| `venue_name` | string or null | Venue. There's no address field. |
| `price_min`, `price_max` | number or null | Price range per ticket. Both null means no price is known: show "Price not listed", not "Free". `price_max` 0 means free. |
| `currency` | string or null | ISO code, `GBP` in London. |
| `url` | string or null | The booking link, on the source's own site. Open it in a new tab. |
| `image_url` | string or null | The source's own image URL. Show it from that URL only, never re-host or copy it (decision 0001). Null means no image. |
| `last_checked_at` | ISO 8601 or null | When the source last confirmed the event. Show freshness on every card (principle 6). |
| `confidence` | `"checked_today"`, `"check_site"` or `"web"` | A stable code for the confidence label. Use it only for an extra colour or icon cue. |
| `confidence_label` | string | The confidence label. Show it on every card, exactly as sent. See below. |
| `distance_m` | number or null | **Metres** from the search centre (the area, the default centre, or the user's point). Null when the search had no point or the event has no location. Show in miles: `distance_m / 1609.344`. |
| `lat`, `lon` | numbers or null | The event's own point, for its map pin. The same point `distance_m` is measured from. Both null when the source gave no location: list the card, but put no pin on the map. |
| `score` | number | Ranking score. Internal: don't show it. |

### The confidence label

PRD R-02 puts a confidence label on every card. The rule (`claude/confidence-label-decision-1.4.md` and decision 0022):

| Result | `confidence` | `confidence_label` |
|---|---|---|
| Official API (`ticketmaster`, `skiddle`), checked within the last 24 hours, with a price | `checked_today` | "Price checked today" |
| Official API, checked more than 24 hours ago or never | `check_site` | "Check availability on Ticketmaster" (or Skiddle) |
| Official API with no price listed, however fresh | `check_site` | "Check availability on Ticketmaster" (or Skiddle) |
| Restaurants, and any source without an official API | `check_site` | "Check availability on {site}", or "Check availability on the booking site" for a source with no site name |
| Web search (Tavily, not built yet) | `web` | "Found on the web today" |

- **Where it's computed:** in the database function `search_items`, from `source`, `last_checked_at` and whether there's a price. Never by the model. Signed-in and signed-out search show the same label for the same event, once signed-out search uses the same function (fix 4).
- **Why 24 hours:** the ingest checks each event once a day, so last night's check still counts as today. An older check gets "Check availability on …" rather than a stale "today" claim. The window and the site names are config (`confidence_labels`), so they can change without a release.
- **No price:** "Price checked today" above "Price not listed" would claim something the data can't back, so an unpriced event always gets "Check availability on …". A free event (price 0) has a price.
- **What the app does:** shows `confidence_label` on the card exactly as sent, and never works it out itself or rewrites it. Colour or an icon may follow `confidence`, but only as an extra cue: the text is always shown.
- **The label is as of the search.** A card kept open for hours isn't updated; a new search is.

## 11. Errors

Every outcome in sections 4 to 6 is HTTP 200, including questions, resting and the cap. Other codes are errors, with FastAPI's body shape: `{"detail": "<text>"}`, or `{"detail": [<errors>]}` for 422.

| HTTP | Endpoints | `detail` | When | What the app does |
|---|---|---|---|---|
| 401 | `/parse`, `/search`, `/refine` | "Invalid or missing token" | No token, an expired or invalid token, or a `sub` that isn't a UUID. Comes with `WWW-Authenticate: Bearer`. Checked before the body, so a bad body without a token is still 401. | Refresh the Supabase session and retry once; then ask the user to sign in. |
| 404 | `/refine` | "No such earlier search" | See [section 6](#6-post-refine). | Run a new search. |
| 405 | `/search` etc. | "Method Not Allowed" | Wrong method (e.g. `GET /search`). | A bug in the app. |
| 409 | `/refine` | "That search didn't finish, so it can't be refined" | See [section 6](#6-post-refine). | Answer the question or search again. |
| 422 | `/parse`, `/search`, `/refine` | a list (below) | The body broke a rule in sections 2 or 6. | A bug in the app: don't show the details to the user. |
| 503 | `/parse`, `/search`, `/refine` | "Auth keys unavailable" | The agent can't reach Supabase's JWKS to check the token. | Show the filter panel; offer to try again. |
| 503 | `/refine` | "Can't read the earlier search right now" | See [section 6](#6-post-refine). | Try again. |
| 500 | any | plain text "Internal Server Error" | An unhandled bug. | Fall back to filter-only search. |
| none | any | none | The agent is asleep or unreachable (a network error, or a long first response while Render starts it). | Fall back to filter-only search. |

There's **no 429**. The cap reply is a 200 ([section 4](#the-cap-reply-http-200-not-429)).

### 422 example

Request: `{"query": "  ", "location": {"lat": 51.5, "lon": -0.1, "source": "gps"}}`.

```json
{
  "detail": [
    {
      "type": "value_error",
      "loc": ["body", "query"],
      "msg": "Value error, query is blank",
      "input": "  ",
      "ctx": {
        "error": {}
      }
    },
    {
      "type": "literal_error",
      "loc": ["body", "location", "source"],
      "msg": "Input should be 'device' or 'pin'",
      "input": "gps",
      "ctx": {
        "expected": "'device' or 'pin'"
      }
    }
  ]
}
```

## 12. What the app must send, and what it must ignore

### Must send

- `Authorization: Bearer <access_token>` on every POST route.
- `query` (`/parse`, `/search`) or `query_id` and `message` (`/refine`).
- `location` **only** when the user has given a point for this request: the device position after permission, or a dropped pin. Always with `source`.
- `location` again on `/refine` when the earlier reply's `location_source` wasn't null.

### Must not send

- Any key not listed in sections 2 and 6 (it's a 422).
- `location` "just in case" on requests that don't need it.

### The user's location

The agent uses the user's point for that one request and never stores or logs it (decision 0013). The app has to keep to the same rule:

- Never put the user's `lat` and `lon` in the URL, `localStorage`, analytics, error reports or logs. When `location_source` isn't null, the `lat` and `lon` in `filters` are the user's point too, so the same applies to them.
- The filters live in the URL (PRD F-02). When `location_source` is set, write `near=me` (or the like) to the URL instead of the coordinates, and read the position again when that URL is opened.
- Hold the position in memory for the session only, so a refine can send it again.
- Area centres and the default centre (`location_source` null) aren't personal and can go in the URL.

### Use

- `status`, `reason`, `message`, `missing`, `questions` and `notes`: they drive what the user sees.
- `query_id`: for `/refine`.
- `filters`: to fill the filter panel and the URL after an AI search (one query model, principle 4). Show `radius_km` on the slider as miles (`radius_km / 1.609344`).
- `vibe` and `who`: to show the chips the user's words chose. They don't filter results yet.
- `location_source`: to know whether the point is the user's (see above).
- `results`, as in [section 10](#10-result-cards).

### Ignore

- `embedding_text`, `trace_id`, and each result's `score`. They're for the agent's logs and evals. Don't show, store or depend on them.
- `filters.limit`, if it's ever set.
- `filters.windows`: the filter panel can't express it, so the URL drops it, and a reload of that URL widens the dates to the whole `date_from` to `date_to` span. See [Not built yet](#14-not-built-yet).
- Any field added to a response later. The app must not break on unknown fields.

## 13. Filter-only search (Supabase)

The filter panel's search, for everyone: signed out, signed in, and as the fallback when AI search rests or the agent is down. It doesn't go through the agent, so it keeps working when the agent is asleep or unreachable. **No sign-in needed.**

It runs the same database function the agent's search runs, `search_items`, with no search text. So the same filters give the same events, in the same order, with the same labels, distances and points as the agent gets for them (PRD F-01, decision 0023). A test compares the two paths row for row.

An AI search also ranks by what the words mean. Filter-only search has no words, so for the same filters an AI search can order the same events differently.

### The call

With supabase-js and the publishable key (the user's session token is fine too; it changes nothing):

```js
const { data, error } = await supabase.rpc("search_items_public", { filters });
```

That's `POST <SUPABASE_URL>/rest/v1/rpc/search_items_public` with the body `{"filters": {...}}`.

### Request: `filters`

The same `SearchFilters` keys as in [section 3](#3-shared-response-body), all optional: `date_from`, `date_to`, `categories`, `price_min`, `price_max`, `include_unpriced`, `lat`, `lon`, `radius_km`, `limit`.

- The filter panel builds this object from the URL, and an AI search's `filters` can be passed straight in.
- **The one exception is `windows`.** Only the agent applies it, and this function refuses it (see [Not built yet](#14-not-built-yet)). Remove it before calling.
- Times must carry an offset (`2026-10-30T17:00:00+00:00`, or `Z`).
- `lat`, `lon` and `radius_km` go together; `radius_km` is km, from 0 up to 100.
- `limit` is 1 to 100, and 20 when left out.
- With no place, send the default area so results match an AI search: the city's centre and radius, `{"lat": 51.507333, "lon": -0.127654, "radius_km": 10}`, and show "Showing central London.". The agent sends those values in `filters` for a default-area search.
- Unknown keys and bad values are refused (below).

### Response

An array of rows, best first. The order is the rank: there's no `rank` field. Each row has the [result card](#10-result-cards) fields, with the same meaning and the same confidence label, so the app renders both paths with one card. The only differences from the agent's JSON:
- times come as `+00:00` rather than `Z`
- numbers keep the database's precision (`distance_m` 251.74325675)

### Example

```js
await supabase.rpc("search_items_public", {
  filters: {"categories": ["Music"], "lat": 51.514065, "lon": -0.135049, "radius_km": 1}
});
```

`data`, from the function run as the signed-out role against two sample events:

```json
[
  {
    "id": "00000000-0000-4000-8000-000000000001",
    "source": "ticketmaster",
    "title": "Late Late Show",
    "category": "Music",
    "genre": "Jazz",
    "start_at": "2026-10-29T22:30:00+00:00",
    "end_at": "2026-10-30T02:00:00+00:00",
    "venue_name": "Ronnie Scott's",
    "price_min": 25,
    "price_max": 45,
    "currency": "GBP",
    "url": "https://www.ticketmaster.co.uk/event/EXAMPLE1",
    "image_url": "https://s1.ticketm.net/dam/EXAMPLE1.jpg",
    "last_checked_at": "2026-10-28T03:10:00+00:00",
    "confidence": "checked_today",
    "confidence_label": "Price checked today",
    "distance_m": 251.74325675,
    "lat": 51.513435,
    "lon": -0.131566,
    "score": 0.3176896301101585
  },
  {
    "id": "00000000-0000-4000-8000-000000000002",
    "source": "skiddle",
    "title": "Jazz Jam",
    "category": "Music",
    "genre": null,
    "start_at": "2026-10-29T20:00:00+00:00",
    "end_at": null,
    "venue_name": null,
    "price_min": null,
    "price_max": null,
    "currency": null,
    "url": "https://www.skiddle.com/whats-on/EXAMPLE2",
    "image_url": null,
    "last_checked_at": "2026-10-26T03:10:00+00:00",
    "confidence": "check_site",
    "confidence_label": "Check availability on Skiddle",
    "distance_m": 369.22612028,
    "lat": 51.513765,
    "lon": -0.129752,
    "score": 0.29027728028129096
  }
]
```

### Errors

| What | When | What the app does |
|---|---|---|
| `error.code` `"22023"`, with a message naming the filter ("search_items: unknown filter keys {windows}", "…limit must be an integer from 1 to 100") | A bad or unknown filter. | A bug in the app: fix the filters. Don't show the message. |
| A network error, or a Supabase 5xx | Supabase is unreachable. | Say search is unavailable and offer to try again. |

### Not logged

Filter-only searches aren't logged yet: the function only reads, and signed-out users have no id (principle 7; deferred by the owner, 28 Oct 2026). There's no per-user cap on them either (also deferred).

## 14. Not built yet

What the step 1.4 plan asks for that `main` doesn't do. Each needs an agent change (a feature branch and PR) or an owner decision before the app relies on it.

| Gap | On `main` | Needed |
|---|---|---|
| **Distance in km** | `distance_m` is in metres. | Nothing, if the app converts metres to miles. A `distance_km` field is a small change if the plan wants it. |
| **Venue address** | Only `venue_name`. No address on the card. Each event's point is there (`lat`, `lon`). | Add if the card needs it. |
| **The 429 cap reply** | The cap is a 200 `ai_resting` with `reason: "daily_cap"` (decision 0013). No endpoint returns 429. | Nothing, unless the plan changes 0013. |
| **Image licence flag** | `image_url` is whatever the source's API supplies. No per-image licence field. Both current sources allow display from their own URLs (decision 0001). | A flag only if a source with other terms is added. |
| **Logging filter-only searches** | `search_items_public` only reads, so filter-only searches aren't logged (principle 7). | A small write path for them. Deferred by the owner (28 Oct 2026). |
| **`windows` in the URL** | "The next 4 Saturdays" comes back with `filters.windows`, which the filter panel can't express. | A URL form for it, or accept that a reload widens the dates. |
| **Answer taps** | Taps go back as appended text (section 8). | Nothing for 1.4. A structured answer (e.g. a vibe tap setting `vibe` directly) is a later option (decision 0015). |
