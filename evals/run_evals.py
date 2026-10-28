"""Parse-accuracy eval: the real Understand node on evals/queries.jsonl (step 1.6, decision 0014).

Usage, from agent/ (so the agent's environment and packages are used):

    uv run python ../evals/run_evals.py [--config supabase|repo] [--out PATH]

Each case runs with "now" frozen at its `now` (27 Oct 2026 12:00 London) and its
`location`, if any. Rows with a `prior` (refine rows 25 and 26) run the Refine step on
that earlier search's stored filters instead (decision 0018). Rows tagged with a `skip`
reason don't run; none are, now. The run stops starting cases before it could pass
MAX_CALLS provider calls, so it never spends more than that.

Two scores per case, over the fields it lists in `scored` (date, area, budget, category,
radius only when the user stated one, and vibe and who on the rows that test them):

- end to end: the filters and clarify reasons the node produced, against `expected`,
  which says what the product should do. Resolver gaps (months, ranges, unseeded
  areas, festival dates) fail here.
- model only: the model's own reply against the hand-written `reply` (or an
  `also_accept` variant), field by field. This isolates the model from the resolver.

A field also checks its clarify reasons: date (date, event_date), area (location),
budget (budget, price_basis) and category (category).

A case where no provider answered (resting) is a provider failure, not a parse failure:
it's listed with its attempts but left out of both accuracies. Groq calls are paced to
stay under its tokens-per-minute limit. If Groq still rate-limits a call, the runner
waits as long as Groq asks and retries Groq, rather than letting the node fall back:
a fallback spends a second call under the cap and swaps in another model (decision
0016). A wait longer than MAX_RETRY_WAIT_S means Groq's daily limit is used up, so the
run stops there and the rest of the cases are reported as not run. The run reports
tokens per provider against the daily limits, which the eval shares with the live app.

Environment (never printed): GROQ_API_KEY, CLOUDFLARE_API_TOKEN, CLOUDFLARE_ACCOUNT_ID for
the providers; SUPABASE_URL and SUPABASE_SERVICE_KEY for --config supabase (the live
config and areas); LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY, LANGFUSE_HOST for traces and
scores; CONFIG_VERSION tags the traces. --config repo reads the models and prompt from
the migrations and the areas from the seed instead, so it needs no Supabase access.

Exit codes: 0 when the run finished (whatever the accuracy), 1 if no case ran or every
case rested (no provider answered, so there's no accuracy to report), 2 for a usage problem.
"""

import argparse
import asyncio
import base64
import json
import os
import sys
import time
import uuid
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "agent"))

import httpx  # noqa: E402
from app.understand.llm import Completion, LLMError, OpenAICompatibleClient, Usage  # noqa: E402
from app.understand.node import Understander, UnderstandResult  # noqa: E402
from app.understand.refine import Prior  # noqa: E402
from app.understand.resolve import (  # noqa: E402
    KM_PER_MILE,
    AmbiguousDate,
    City,
    Point,
    date_window,
    normalise,
)
from app.understand.schema import RefineReply, UnderstoodQuery, When  # noqa: E402
from app.understand.store import SupabaseStore  # noqa: E402
from app.understand.tracing import LangfuseTracer, NullTracer  # noqa: E402

CASES = REPO / "evals" / "queries.jsonl"
MAX_CALLS = 80
# Groq's published limit for openai/gpt-oss-20b is 8,000 tokens a minute (rate-limits
# page, checked 28 Oct 2026). Pace to 90% of it: Groq's own count can differ from the
# usage it reports.
TOKENS_PER_MINUTE = {"groq": 7200}
FIRST_CALL_TOKENS = 2500  # a guess for the first call, before any usage is known
RETRIED_PROVIDERS = ("groq",)
MAX_RATE_LIMIT_RETRIES = 5  # per call; more in a row stops the run
MAX_RETRY_WAIT_S = 120.0  # Groq asks for longer only when a daily limit is used up
DEFAULT_RETRY_WAIT_S = 30.0  # when a 429 has no retry-after
RADIUS_TOLERANCE_KM = 0.8  # about half a mile (seed doc)
FIELDS = ("date", "area", "budget", "category", "radius", "vibe", "who")
REASONS = {
    "date": {"date", "event_date"},
    "area": {"location"},
    "budget": {"budget", "price_basis"},
    "category": {"category"},
    "radius": set(),
    "vibe": set(),
    "who": set(),
}
SCORES_PATH = "/api/public/scores"


# Loading --------------------------------------------------------------------------


def load_cases(path: Path = CASES) -> list[dict[str, Any]]:
    cases = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    for c in cases:
        _model(c).model_validate(c["reply"])  # a bad hand-written reply is a bug
        unknown = set(c["scored"]) - set(FIELDS)
        if unknown:
            raise ValueError(f"{c['id']}: unknown scored fields {unknown}")
    return cases


# Scoring --------------------------------------------------------------------------


def _moment(text: str) -> datetime:
    return datetime.fromisoformat(text)


def _reasons(missing, scored_field: str) -> set[str]:
    return set(missing) & REASONS[scored_field]


def score_end_to_end(case: dict, result: UnderstandResult, city: City) -> dict[str, bool]:
    """Each scored field: did the product do what `expected` says?"""
    if result.status == "ai_resting" or result.filters is None:
        return {f: False for f in case["scored"]}
    exp, f, missing = case["expected"], result.filters, result.missing
    window = (f.date_from, f.date_to) if f.date_from is not None else None
    out = {}
    for name in case["scored"]:
        match name:
            case "date":
                out[name] = any(
                    "unsupported" not in opt
                    and _reasons(missing, "date") == set(opt["missing"])
                    and window
                    == (None if opt["window"] is None else tuple(map(_moment, opt["window"])))
                    for opt in exp["date"]
                )
            case "area":
                ok = _reasons(missing, "area") == _reasons(exp["missing"], "area")
                out[name] = ok and _area_matches(exp["area"], result, case, city)
            case "budget":
                b = exp["budget"]
                out[name] = _reasons(missing, "budget") == _reasons(exp["missing"], "budget") and (
                    (f.price_min, f.price_max, f.include_unpriced)
                    == (b["price_min"], b["price_max"], b["include_unpriced"])
                )
            case "category":
                out[name] = _reasons(missing, "category") == _reasons(
                    exp["missing"], "category"
                ) and set(f.categories or []) == set(exp["categories"] or [])
            case "radius":
                out[name] = (
                    f.radius_km is not None
                    and abs(f.radius_km - exp["radius_km"]) <= RADIUS_TOLERANCE_KM
                )
            case "vibe" | "who":
                # Beside the filters, as the parse route returns them; any listed value.
                out[name] = getattr(result.understood, name) in exp[name]
    return out


def _area_matches(expected: dict | None, result: UnderstandResult, case: dict, city: City) -> bool:
    f = result.filters
    if expected is None:
        return f.lat is None
    if "unseeded" in expected:
        return False  # the product should know this place; the parser doesn't yet
    if "point" in expected:
        loc = case["location"]
        # The point must also be marked as its source: device for "near me" (0017).
        return (
            result.used_location
            and (f.lat, f.lon) == (loc["lat"], loc["lon"])
            and result.location_source == expected.get("source")
        )
    area = next(a for a in city.areas if a.id == expected["id"])
    return not result.used_location and (f.lat, f.lon) == (area.lat, area.lon)


def score_model(case: dict, got: UnderstoodQuery | None, city: City) -> dict[str, bool]:
    """Each scored field: does the model's reply match the hand-written one (or an
    accepted variant)?"""
    if got is None:
        return {f: False for f in case["scored"]}
    variants = [
        _model(case).model_validate(case["reply"] | extra)
        for extra in [{}, *case.get("also_accept", [])]
    ]
    now = _moment(case["now"])
    return {
        name: any(_same(name, got, want, now, city) for want in variants) for name in case["scored"]
    }


def _same(name: str, a: UnderstoodQuery, b: UnderstoodQuery, now: datetime, city: City) -> bool:
    match name:
        case "date":
            unclear = ("date" in a.unclear, "date" in b.unclear)
            if unclear[0] != unclear[1] or (a.event is None) != (b.event is None):
                return False
            return unclear[0] or _when(a.when, now, city) == _when(b.when, now, city)
        case "area":
            return (a.near_me, _place(a.place, city)) == (b.near_me, _place(b.place, city))
        case "budget":
            return _budget(a) == _budget(b) and _change(a, "price") == _change(b, "price")
        case "category":
            if a.categories or b.categories:
                return set(a.categories or []) == set(b.categories or [])
            return ("category" in a.unclear) == ("category" in b.unclear)
        case "radius":
            if _change(a, "distance") != _change(b, "distance"):
                return False
            ka, kb = _km(a), _km(b)
            if ka is None or kb is None:
                return ka is kb
            return abs(ka - kb) <= RADIUS_TOLERANCE_KM
        case "vibe" | "who":
            return getattr(a, name) == getattr(b, name)
    raise ValueError(name)


def _when(when: When | None, now: datetime, city: City):
    """Two whens match if they mean the same window ("Saturday" and "31 October")."""
    if when is None:
        return None
    try:
        return date_window(when, now, city.tz)
    except AmbiguousDate:
        return "ambiguous"


def _place(place: str | None, city: City) -> str | None:
    if place is None or normalise(place) == normalise(city.name):
        return None
    area = city.find_area(place)
    return area.id if area else normalise(place)


def _model(case: dict) -> type[UnderstoodQuery]:
    """A refine row's reply is a RefineReply: the follow-up's changes (decision 0018)."""
    return RefineReply if case.get("prior") else UnderstoodQuery


def _change(q: UnderstoodQuery, kind: str) -> str | None:
    """A refine reply's relative change ("cheaper", "closer"); None for a search."""
    return getattr(q, f"{kind}_change", None)


def _budget(q: UnderstoodQuery):
    if q.free:
        return ("free",)
    return (q.price_min, q.price_max, q.price_scope == "total", "budget" in q.unclear)


def _km(q: UnderstoodQuery) -> float | None:
    if q.distance is None:
        return None
    return q.distance.value * (KM_PER_MILE if q.distance.unit == "mi" else 1.0)


# Running --------------------------------------------------------------------------


class CountingLLM:
    """Counts provider calls, and refuses any past the cap (a guard: the runner checks
    the budget before starting each case)."""

    def __init__(self, inner, max_calls: int):
        self.inner = inner
        self.max_calls = max_calls
        self.calls = 0

    async def complete(self, config, system, user, schema) -> Completion:
        if self.calls >= self.max_calls:
            raise RuntimeError("eval call cap reached")
        self.calls += 1
        return await self.inner.complete(config, system, user, schema)


class TokenPacer:
    """Waits before a call to a paced provider until the last minute's tokens, plus the
    largest call so far, fit under its limit. Counts every provider's tokens."""

    WINDOW_S = 60.0

    def __init__(self, inner, limits: dict[str, int], sleep=asyncio.sleep, monotonic=None):
        self.inner = inner
        self.limits = limits
        self.sleep = sleep
        self.monotonic = monotonic or time.monotonic
        self.spent: dict[str, list[tuple[float, int]]] = {}
        self.largest: dict[str, int] = {}
        self.tokens: Counter[str] = Counter()
        self.waited_s = 0.0

    async def complete(self, config, system, user, schema) -> Completion:
        provider = config.provider
        if provider in self.limits:
            await self._wait(provider)
        try:
            completion = await self.inner.complete(config, system, user, schema)
        except LLMError as exc:
            self._record(provider, exc.usage)  # a cut-off reply still used tokens
            raise
        self._record(provider, completion.usage)
        return completion

    async def _wait(self, provider: str) -> None:
        need = self.largest.get(provider, FIRST_CALL_TOKENS)
        while True:
            now = self.monotonic()
            window = [(t, n) for t, n in self.spent.get(provider, []) if now - t < self.WINDOW_S]
            self.spent[provider] = window
            # An empty window always goes ahead, so a call larger than the limit can't stall.
            if not window or sum(n for _, n in window) + need <= self.limits[provider]:
                return
            delay = window[0][0] + self.WINDOW_S - now
            self.waited_s += delay
            await self.sleep(delay)

    def _record(self, provider: str, usage: Usage) -> None:
        used = (usage.input_tokens or 0) + (usage.output_tokens or 0)
        if not used:
            return  # a refused call (429, timeout) reports none
        self.spent.setdefault(provider, []).append((self.monotonic(), used))
        self.largest[provider] = max(self.largest.get(provider, 0), used)
        self.tokens[provider] += used


class RateLimitStop(Exception):
    """Groq keeps refusing, or asks for a wait that means its daily limit is used up."""


class RateLimitRetry:
    """Retries a rate-limited Groq call after the wait Groq asks for, instead of letting
    the node fall back to Cloudflare. Other errors pass straight through."""

    def __init__(self, inner, sleep=asyncio.sleep):
        self.inner = inner
        self.sleep = sleep
        self.retries = 0
        self.waited_s = 0.0
        self.stopped: str | None = None  # why the run stopped, if it did

    async def complete(self, config, system, user, schema) -> Completion:
        try:
            return await self._complete(config, system, user, schema)
        except RateLimitStop as exc:
            self.stopped = str(exc)
            raise

    async def _complete(self, config, system, user, schema) -> Completion:
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                return await self.inner.complete(config, system, user, schema)
            except LLMError as exc:
                if config.provider not in RETRIED_PROVIDERS or exc.outcome != "http_429":
                    raise
                wait = exc.retry_after_s if exc.retry_after_s is not None else DEFAULT_RETRY_WAIT_S
                if wait > MAX_RETRY_WAIT_S:
                    raise RateLimitStop(
                        f"{config.provider} asks to wait {wait:.0f} s: its daily limit is used up"
                    ) from exc
                if attempt == MAX_RATE_LIMIT_RETRIES:
                    raise RateLimitStop(
                        f"{config.provider} refused {attempt + 1} times in a row"
                    ) from exc
                self.retries += 1
                self.waited_s += wait
                await self.sleep(wait)
        raise AssertionError("unreachable")


class Clock:
    def __init__(self):
        self.now: datetime | None = None

    def __call__(self) -> datetime:
        return self.now


@dataclass
class CaseResult:
    id: str
    row: int
    query: str
    status: str  # ok, needs_clarification, ai_resting, skipped:<why>, not_run
    end_to_end: dict[str, bool] = field(default_factory=dict)
    model_only: dict[str, bool] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)
    filters: dict[str, Any] | None = None
    reply: dict[str, Any] | None = None
    provider: str | None = None
    attempts: list[str] = field(default_factory=list)
    config_versions: dict[str, int] = field(default_factory=dict)
    trace_id: str | None = None

    @property
    def scored(self) -> bool:
        """Counted in the accuracies: it ran and a provider answered."""
        return bool(self.end_to_end) and self.status != "ai_resting"

    @property
    def passed(self) -> tuple[bool, bool]:
        return all(self.end_to_end.values()), all(self.model_only.values())


async def run_cases(
    cases: list[dict],
    understander: Understander,
    llm: CountingLLM,
    clock: Clock,
    city: City,
    providers: int,
) -> list[CaseResult]:
    results = []
    stopped: str | None = None
    for case in cases:
        base = CaseResult(case["id"], case["row"], case["query"], "not_run")
        if case.get("skip"):
            base.status = f"skipped:{case['skip']}"
            results.append(base)
            continue
        if stopped or llm.calls + providers > llm.max_calls:
            results.append(base)  # could pass the cap, or the run stopped: not started
            continue
        clock.now = _moment(case["now"])
        loc = case.get("location")
        point = Point(**loc) if loc else None
        try:
            if case.get("prior"):  # a refine row: the earlier search, changed (0018)
                result = await understander.refine(case["query"], Prior(**case["prior"]), point)
            else:
                result = await understander.run(case["query"], point)
        except RateLimitStop as exc:
            stopped = str(exc)
            print(f"Stopped at r{case['row']:02d}: {stopped}", file=sys.stderr)
            results.append(base)
            continue
        results.append(
            CaseResult(
                case["id"],
                case["row"],
                case["query"],
                result.status,
                end_to_end=score_end_to_end(case, result, city),
                model_only=score_model(case, result.understood, city),
                missing=list(result.missing),
                filters=result.filters.to_json() if result.filters else None,
                reply=result.understood.model_dump(mode="json") if result.understood else None,
                provider=result.provider,
                attempts=[f"{a.provider}:{a.outcome}" for a in result.attempts],
                config_versions=result.config_versions,
                trace_id=result.trace_id,
            )
        )
    return results


def summarise(results: list[CaseResult]) -> dict[str, Any]:
    scored = [r for r in results if r.scored]

    def accuracy(kind: str) -> dict[str, Any]:
        per_field = {}
        for name in FIELDS:
            marks = [getattr(r, kind)[name] for r in scored if name in getattr(r, kind)]
            if marks:
                per_field[name] = {"correct": sum(marks), "of": len(marks)}
        index = 0 if kind == "end_to_end" else 1
        passed = sum(r.passed[index] for r in scored)
        return {
            "cases_passed": passed,
            "of": len(scored),
            "accuracy": round(passed / len(scored), 4) if scored else None,
            "fields": per_field,
        }

    failures = Counter(name for r in scored for name, ok in r.end_to_end.items() if not ok)
    ran = [r for r in results if r.status != "not_run" and not r.status.startswith("skipped")]
    return {
        "cases": len(results),
        "ran": len(ran),
        "scored": len(scored),
        "skipped": sum(r.status.startswith("skipped") for r in results),
        "not_run": sum(r.status == "not_run" for r in results),
        "resting": sum(r.status == "ai_resting" for r in results),
        # Answered, but not by the first provider: scored, under the fallback model.
        "fallbacks": sum(not r.attempts[0].endswith(":ok") for r in scored),
        "attempts": dict(Counter(a for r in ran for a in r.attempts).most_common()),
        "end_to_end": accuracy("end_to_end"),
        "model_only": accuracy("model_only"),
        "failed_fields_end_to_end": dict(failures.most_common()),
        "providers": dict(Counter(r.provider for r in scored if r.provider)),
        "config_versions": next((r.config_versions for r in ran if r.config_versions), {}),
    }


def report(summary: dict, results: list[CaseResult]) -> str:
    def line(label, acc):
        pct = "n/a" if acc["accuracy"] is None else f"{acc['accuracy']:.1%}"
        fields = ", ".join(f"{k} {v['correct']}/{v['of']}" for k, v in acc["fields"].items())
        return f"{label}: {pct} ({acc['cases_passed']}/{acc['of']} cases)  [{fields}]"

    out = [
        f"Parse accuracy over {summary['scored']} cases "
        f"({summary['skipped']} skipped, {summary['not_run']} not run, "
        f"{summary['resting']} provider failures)",
        line("  end to end", summary["end_to_end"]),
        line("  model only", summary["model_only"]),
        f"  providers {summary['providers']} ({summary['fallbacks']} fallbacks), "
        f"config {summary['config_versions']}",
        f"  attempts {summary['attempts']}",
    ]
    if "tokens" in summary:
        out.append(
            f"  tokens {summary['tokens']}, waited {summary['paced_wait_s']:.0f} s for Groq's limit"
            f", {summary['rate_limit_retries']} rate-limit retries"
        )
    if summary.get("stopped"):
        out.append(f"  STOPPED EARLY: {summary['stopped']}")
    out += ["", "Failures (end to end | model only):"]
    for r in results:
        if not r.scored or all(r.passed):
            continue
        e2e = [k for k, ok in r.end_to_end.items() if not ok]
        model = [k for k, ok in r.model_only.items() if not ok]
        out.append(
            f"  r{r.row:02d} {r.query!r}: {','.join(e2e) or '-'} | {','.join(model) or '-'}"
            f"  [{r.status}{' ' + str(r.missing) if r.missing else ''}]"
        )
    resting = [r for r in results if r.status == "ai_resting"]
    if resting:
        out += ["", "Provider failures (not scored):"]
        out += [f"  r{r.row:02d} {r.query!r}: {', '.join(r.attempts)}" for r in resting]
    return "\n".join(out)


# Langfuse scores ------------------------------------------------------------------


async def send_scores(
    http: httpx.AsyncClient,
    host: str,
    public_key: str,
    secret_key: str,
    results: list[CaseResult],
    run_id: str,
) -> int:
    """Two scores per traced case: parse_end_to_end and parse_model_only (1 or 0), with
    the failed fields as the comment and the prompt and model versions as metadata."""
    token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
    headers = {"Authorization": f"Basic {token}"}
    sent = 0
    for r in results:
        if not r.scored or r.trace_id is None:
            continue
        metadata = {
            "run_id": run_id,
            "case": r.id,
            "provider": r.provider,
            "attempts": r.attempts,
            **{f"{k}_version": v for k, v in r.config_versions.items()},
        }
        for name, marks, ok in (
            ("parse_end_to_end", r.end_to_end, r.passed[0]),
            ("parse_model_only", r.model_only, r.passed[1]),
        ):
            failed = [k for k, v in marks.items() if not v]
            body = {
                "id": f"{run_id}-{r.id}-{name}",
                "traceId": r.trace_id,
                "name": name,
                "value": 1 if ok else 0,
                "dataType": "NUMERIC",
                "comment": "failed: " + ", ".join(failed) if failed else "all fields correct",
                "metadata": metadata,
            }
            try:
                response = await http.post(
                    host.rstrip("/") + SCORES_PATH, json=body, headers=headers, timeout=10
                )
                response.raise_for_status()
                sent += 1
            except httpx.HTTPError as exc:
                print(f"score for {r.id} not sent: {type(exc).__name__}", file=sys.stderr)
    return sent


# Main -----------------------------------------------------------------------------


def _env(name: str) -> str | None:
    return os.environ.get(name) or None


async def main_async(args) -> int:
    cases = load_cases(args.cases)
    async with httpx.AsyncClient() as http:
        if args.config == "repo":
            # What ships: llm_understand v2 from 014, understand_prompt v4 from 017, the
            # seeded areas.
            from tests.understand_support import FakeStore, entries

            store = FakeStore(entries(versions={"llm_understand": 2, "understand_prompt": 4}))
        else:
            if not (_env("SUPABASE_URL") and _env("SUPABASE_SERVICE_KEY")):
                print(
                    "--config supabase needs SUPABASE_URL and SUPABASE_SERVICE_KEY", file=sys.stderr
                )
                return 2
            store = SupabaseStore(
                http, _env("SUPABASE_URL").rstrip("/") + "/rest/v1", _env("SUPABASE_SERVICE_KEY")
            )
        lf = (_env("LANGFUSE_PUBLIC_KEY"), _env("LANGFUSE_SECRET_KEY"))
        lf_host = _env("LANGFUSE_HOST") or "https://cloud.langfuse.com"
        release = _env("CONFIG_VERSION") or "eval"
        tracer = (
            LangfuseTracer(http, lf_host, lf[0], lf[1], release=release)
            if all(lf)
            else NullTracer(release=release)
        )
        pacer = TokenPacer(
            OpenAICompatibleClient(
                http,
                _env("GROQ_API_KEY"),
                _env("CLOUDFLARE_API_TOKEN"),
                _env("CLOUDFLARE_ACCOUNT_ID"),
            ),
            TOKENS_PER_MINUTE,
        )
        # Retries sit inside the counter: a refused call spends nothing, so it doesn't
        # count toward the cap. They go through the pacer like any call.
        retry = RateLimitRetry(pacer)
        llm = CountingLLM(retry, args.max_calls)
        clock = Clock()
        understander = Understander(store, llm, tracer, clock=clock)
        city = await store.city("london")
        providers = len(
            (await store.config(("llm_understand",)))["llm_understand"].value["providers"]
        )

        results = await run_cases(cases, understander, llm, clock, city, providers)
        summary = summarise(results) | {
            "calls": llm.calls,
            "max_calls": args.max_calls,
            "tokens": dict(pacer.tokens),
            "paced_wait_s": round(pacer.waited_s, 1),
            "rate_limit_retries": retry.retries,
            "rate_limit_wait_s": round(retry.waited_s, 1),
            "stopped": retry.stopped,
        }
        run_id = f"eval-{datetime.now().strftime('%Y%m%dT%H%M%S')}-{uuid.uuid4().hex[:6]}"
        summary["run_id"] = run_id
        if all(lf):
            summary["scores_sent"] = await send_scores(http, lf_host, *lf, results, run_id)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(
            {"summary": summary, "results": [r.__dict__ for r in results]},
            indent=1,
            ensure_ascii=False,
        )
        + "\n"
    )
    print(report(summary, results))
    print(f"\n{llm.calls} provider calls (cap {args.max_calls}). Results: {args.out}")
    return 0 if summary["ran"] > summary["resting"] else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--config", choices=("supabase", "repo"), default="supabase")
    parser.add_argument("--cases", type=Path, default=CASES)
    parser.add_argument("--out", type=Path, default=REPO / "evals" / "results" / "latest.json")
    parser.add_argument("--max-calls", type=int, default=MAX_CALLS)
    args = parser.parse_args(argv)
    if not 1 <= args.max_calls <= MAX_CALLS:
        parser.error(f"--max-calls must be 1 to {MAX_CALLS}")
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    sys.exit(main())
