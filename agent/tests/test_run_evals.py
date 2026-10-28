"""The eval set and its runner (step 1.6, decision 0014). The fake LLM stands in for
the providers; the Understand node, resolver and scorer are the real ones."""

import asyncio
import importlib.util
import json
from datetime import datetime
from typing import get_args

import httpx
import pytest

from app.understand.llm import LLMError
from app.understand.node import Understander
from app.understand.schema import Vibe, Who
from tests.understand_support import (
    REPO,
    FakeLLM,
    FakeStore,
    RecordingTracer,
    entries,
    london,
    seed_areas,
)

_spec = importlib.util.spec_from_file_location("run_evals", REPO / "evals" / "run_evals.py")
run_evals = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(run_evals)

CASES = run_evals.load_cases()
BY_ROW = {c["row"]: c for c in CASES}
CITY = london()
REASONS = {"location", "date", "budget", "price_basis", "event_date", "category"}

# What a perfect model still fails end to end today: resolver and calendar gaps, each
# named in decision 0014. When the resolver learns one, move it out of this list.
CEILING_FAILURES = {
    12: ["area"],  # Bloomsbury isn't a seeded area
    15: ["date"],  # "any Saturday" isn't a filter
    16: ["date"],  # "any weekend"
    21: ["date"],  # months
    22: ["area"],  # Kentish Town
    23: ["date"],  # "Friday night" starts at 17:00; a weekday is the whole day
    28: ["area"],  # Hampstead
    33: ["area"],  # Wembley
    34: ["date", "area"],  # Remembrance Sunday (no calendar); central London
    35: ["area"],  # Regent Street
    36: ["date", "area"],  # "this winter"; Somerset House
    42: ["date", "area"],  # Hanukkah; Golders Green
    44: ["area"],  # Richmond Park isn't an area name
    46: ["date", "area"],  # Chinese New Year; Chinatown
    47: ["date"],  # Pancake Day
    48: ["date"],  # half-term
    49: ["date"],  # Mother's Day
    52: ["date", "area"],  # Eid; Whitechapel
    53: ["date", "area"],  # Vaisakhi; Southall
    55: ["date", "area"],  # Chelsea Flower Show dates and venue
    56: ["area"],  # central London
    57: ["date"],  # an event with a weekday resolves to the weekday instead of asking
    58: ["area"],  # Liverpool Street
    68: ["date"],  # date ranges
}


def run(cases, script, max_calls=80, providers=1, tracer=None):
    llm = run_evals.CountingLLM(FakeLLM(**script), max_calls)
    clock = run_evals.Clock()
    store = FakeStore(entries(versions={"llm_understand": 2, "understand_prompt": 3}))
    if providers == 1:
        store.entries["llm_understand"].value["providers"] = store.entries["llm_understand"].value[
            "providers"
        ][:1]
    understander = Understander(store, llm, tracer or RecordingTracer(), clock=clock)
    results = asyncio.run(run_evals.run_cases(cases, understander, llm, clock, CITY, providers))
    return results, llm


def perfect(cases):
    return {"groq": [json.dumps(c["reply"]) for c in cases if not c["skip"]], "cloudflare": []}


def answer(row, **changes) -> str:
    """The model's reply for `row`: the hand-written one with `changes`."""
    return json.dumps(BY_ROW[row]["reply"] | changes)


# The set ---------------------------------------------------------------------------


def test_the_set_has_73_synthetic_rows_in_order():
    assert [c["row"] for c in CASES] == list(range(1, 74))
    assert len({c["id"] for c in CASES}) == 73
    assert {c["tag"] for c in CASES} == {"synthetic"}
    assert {c["now"] for c in CASES} == {"2026-10-27T12:00:00+00:00"}


def test_only_the_refine_rows_are_skipped():
    skipped = {c["row"]: c["skip"] for c in CASES if c["skip"]}
    assert skipped == {25: "needs_prior_filters", 26: "needs_prior_filters"}


def test_pin_rows_score_date_and_radius_only():
    for row in (23, 24):
        assert BY_ROW[row]["scored"] == ["date", "radius"]
        assert BY_ROW[row]["location"] is not None


def test_radius_is_scored_only_where_the_user_states_one():
    for c in CASES:
        stated = c["reply"]["distance"] is not None
        assert ("radius" in c["scored"]) == stated, c["id"]
        assert (c["expected"]["radius_km"] is not None) == stated, c["id"]


def test_expectations_are_well_formed():
    area_ids = {a.id for a in seed_areas()}
    for c in CASES:
        exp = c["expected"]
        assert set(exp["missing"]) <= REASONS, c["id"]
        assert (exp["status"] == "needs_clarification") == bool(exp["missing"]), c["id"]
        assert exp["date"], c["id"]
        for opt in exp["date"]:
            assert set(opt) in ({"window", "missing"}, {"unsupported"}), c["id"]
            if opt.get("window"):
                start, end = map(datetime.fromisoformat, opt["window"])
                assert start.utcoffset() is not None and start < end, c["id"]
        if exp["area"] and "id" in exp["area"]:
            assert exp["area"]["id"] in area_ids, c["id"]
        for name, allowed in (("vibe", get_args(Vibe)), ("who", get_args(Who))):
            # Scored exactly where expected lists the right values, the reply's among them.
            assert (name in c["scored"]) == (name in exp), c["id"]
            if name in exp:
                assert exp[name] and set(exp[name]) <= {*allowed, None}, c["id"]
                assert c["reply"][name] in exp[name], c["id"]
        assert c["uc"] is None or c["uc"].startswith("UC"), c["id"]


@pytest.mark.parametrize(
    ("row", "missing"),
    [
        (1, ["category"]),
        (2, ["category"]),
        (5, ["date"]),
        (7, ["date"]),
        (27, ["budget"]),
        (32, ["date"]),
        (39, ["location"]),  # round 2: "nearby" with no location; Saturday resolves
        (56, ["event_date"]),
        (57, ["event_date"]),
        (60, ["price_basis"]),
        (61, ["location"]),
        (62, ["location", "date"]),
        (71, ["category"]),
    ],
)
def test_the_docs_clarify_rows(row, missing):
    assert BY_ROW[row]["expected"]["missing"] == missing


def test_the_docs_clear_rows_resolve():
    for row in (22, 63, 64, 65, 66, 67, 69, 70, 72, 73):
        assert BY_ROW[row]["expected"]["status"] == "ok", row


# Scoring with a perfect model -------------------------------------------------------


def test_a_perfect_model_scores_100_percent_model_only():
    results, _ = run(CASES, perfect(CASES))
    summary = run_evals.summarise(results)
    assert summary["model_only"]["accuracy"] == 1.0
    assert summary["ran"] == 71 and summary["skipped"] == 2


def test_a_perfect_model_fails_end_to_end_only_on_known_resolver_gaps():
    results, _ = run(CASES, perfect(CASES))
    failed = {
        r.row: [k for k, ok in r.end_to_end.items() if not ok]
        for r in results
        if r.scored and not r.passed[0]
    }
    assert failed == CEILING_FAILURES
    summary = run_evals.summarise(results)
    assert (summary["end_to_end"]["cases_passed"], summary["end_to_end"]["of"]) == (47, 71)


# Scoring catches mistakes ----------------------------------------------------------


WHEN = {r: BY_ROW[r]["reply"]["when"] for r in (6, 31)}


@pytest.mark.parametrize(
    ("row", "changes", "field"),
    [
        (6, {"when": {**WHEN[6], "weekday": "fri"}}, "date"),
        (6, {"place": "Soho"}, "area"),
        (11, {"price_max": 30}, "budget"),
        (8, {"categories": ["Comedy"]}, "category"),
        (24, {"distance": {"value": 3, "unit": "km"}}, "radius"),
        (71, {"unclear": []}, "category"),
        (27, {"unclear": []}, "budget"),
        (60, {"price_scope": "each"}, "budget"),
        (61, {"near_me": False}, "area"),
        (5, {"unclear": []}, "date"),
        (19, {"free": False, "price_max": 0}, "budget"),  # "under £0" isn't "free"
        (14, {"vibe": None}, "vibe"),
        (3, {"vibe": "Lively"}, "vibe"),
        (4, {"who": "Friends"}, "who"),
        (22, {"who": "Friends"}, "who"),  # no group in the phrase: none to invent
    ],
    ids=[
        "wrong weekday",
        "wrong place",
        "wrong price",
        "wrong category",
        "wrong radius",
        "no category question",
        "no budget question",
        "total as each",
        "no location question",
        "vague date not flagged",
        "zero price, not free",
        "vibe missed",
        "wrong vibe",
        "wrong who",
        "invented who",
    ],
)
def test_a_wrong_reply_fails_that_field_in_both_scores(row, changes, field):
    results, _ = run([BY_ROW[row]], {"groq": [answer(row, **changes)], "cloudflare": []})
    (r,) = results
    assert r.end_to_end[field] is False and r.model_only[field] is False
    assert all(ok for k, ok in r.model_only.items() if k != field)


def test_an_equivalent_when_scores_as_the_same():
    # "Saturday" given as 31 October is the same window.
    reply = answer(31, when={**WHEN[31], "kind": "date", "weekday": None, "month": 10, "day": 31})
    results, _ = run([BY_ROW[31]], {"groq": [reply], "cloudflare": []})
    assert all(results[0].model_only.values()) and all(results[0].end_to_end.values())


def test_another_spelling_of_the_same_area_scores_as_the_same():
    results, _ = run([BY_ROW[38]], {"groq": [answer(38, place="South Bank")], "cloudflare": []})
    assert all(results[0].model_only.values()) and all(results[0].end_to_end.values())


def test_also_accept_variants_count_for_model_only():
    results, _ = run([BY_ROW[60]], {"groq": [answer(60, unclear=[])], "cloudflare": []})
    assert all(results[0].model_only.values()) and all(results[0].end_to_end.values())


def test_the_round_2_rows_score_vibe_and_who():
    rows = {c["row"] for c in CASES if {"vibe", "who"} & set(c["scored"])}
    assert rows == {3, 4, 12, 13, 14, 22, 50}


def test_a_vibe_or_group_without_a_category_question_passes_but_asking_fails():
    # "Something chilled tonight in Peckham": the model gives the vibe and doesn't ask.
    results, _ = run([BY_ROW[3]], perfect([BY_ROW[3]]))
    assert all(results[0].end_to_end.values()) and results[0].status == "ok"
    # The model flags category as unclear as well: code still doesn't ask.
    unclear = answer(3, unclear=["category"])
    results, _ = run([BY_ROW[3]], {"groq": [unclear], "cloudflare": []})
    assert results[0].end_to_end["category"] and results[0].missing == []
    # No vibe and a category flag: it asks, and both category and vibe fail.
    asks = answer(3, vibe=None, unclear=["category"])
    results, _ = run([BY_ROW[3]], {"groq": [asks], "cloudflare": []})
    (r,) = results
    assert r.missing == ["category"]
    assert not r.end_to_end["category"] and not r.end_to_end["vibe"]


def test_no_vibe_is_also_right_where_the_doc_inferred_one():
    for row in (12, 13):
        results, _ = run([BY_ROW[row]], {"groq": [answer(row, vibe=None)], "cloudflare": []})
        (r,) = results
        assert all(r.model_only.values()) and r.end_to_end["vibe"], row


def test_a_resting_case_is_a_provider_failure_left_out_of_both_accuracies():
    err = LLMError("http_429", "rate limited")
    script = perfect([BY_ROW[72]]) | {"groq": [err, json.dumps(BY_ROW[72]["reply"])]}
    script["cloudflare"] = [LLMError("truncated", "cut off")]
    results, _ = run([BY_ROW[64], BY_ROW[72]], script, providers=2)
    resting, answered = results
    assert resting.status == "ai_resting" and not resting.scored
    assert not any(resting.end_to_end.values())  # still marked, for the results file
    summary = run_evals.summarise(results)
    assert (summary["ran"], summary["scored"], summary["resting"]) == (2, 1, 1)
    assert summary["end_to_end"]["of"] == summary["model_only"]["of"] == 1
    assert summary["end_to_end"]["accuracy"] == 1.0
    assert summary["attempts"] == {"groq:http_429": 1, "cloudflare:truncated": 1, "groq:ok": 1}
    text = run_evals.report(summary, results)
    assert "over 1 cases (0 skipped, 0 not run, 1 provider failures)" in text
    assert (
        "Provider failures (not scored):\n  r64 'Things to do in Soho this weekend': "
        "groq:http_429, cloudflare:truncated" in text
    )


def test_a_fallback_answer_is_scored_and_counted():
    err = LLMError("http_429", "rate limited")
    script = {"groq": [err], "cloudflare": [json.dumps(BY_ROW[64]["reply"])]}
    results, _ = run([BY_ROW[64]], script, providers=2)
    summary = run_evals.summarise(results)
    assert (summary["scored"], summary["fallbacks"]) == (1, 1)
    assert summary["providers"] == {"cloudflare": 1}


def test_resting_cases_send_no_langfuse_scores():
    err = LLMError("timeout", "slow")
    results, _ = run([BY_ROW[64]], {"groq": [err], "cloudflare": [err]}, providers=2)
    results[0].trace_id = "t1"
    seen = []

    async def go():
        transport = httpx.MockTransport(lambda r: seen.append(r) or httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as http:
            return await run_evals.send_scores(http, "https://lf.example", "pk", "sk", results, "r")

    assert asyncio.run(go()) == 0 and seen == []


# Rate-limit retries (decision 0016) ---------------------------------------------------


def retrying(cases, script, max_calls=80):
    """run(), with the runner's retry between the counter and the providers."""
    waits = []

    async def sleep(s):
        waits.append(s)

    retry = run_evals.RateLimitRetry(FakeLLM(**script), sleep=sleep)
    llm = run_evals.CountingLLM(retry, max_calls)
    clock = run_evals.Clock()
    store = FakeStore(entries(versions={"llm_understand": 2, "understand_prompt": 3}))
    understander = Understander(store, llm, RecordingTracer(), clock=clock)
    results = asyncio.run(run_evals.run_cases(cases, understander, llm, clock, CITY, 2))
    return results, llm, retry, waits


def limited(seconds=7.0):
    return LLMError("http_429", "rate limited", retry_after_s=seconds)


def test_a_groq_rate_limit_waits_and_retries_groq_instead_of_falling_back():
    reply = json.dumps(BY_ROW[64]["reply"])
    results, llm, retry, waits = retrying(
        [BY_ROW[64]], {"groq": [limited(7), limited(12), reply], "cloudflare": [reply]}
    )
    (r,) = results
    assert (r.status, r.provider, r.attempts) == ("ok", "groq", ["groq:ok"])
    assert waits == [7, 12] and (retry.retries, retry.waited_s) == (2, 19)
    assert llm.calls == 1  # refused calls spend nothing, so they don't count toward the cap
    assert llm.inner.inner.script["cloudflare"] == [reply]  # Cloudflare never called


def test_a_429_without_retry_after_waits_the_default():
    reply = json.dumps(BY_ROW[64]["reply"])
    _, _, _, waits = retrying([BY_ROW[64]], {"groq": [limited(None), reply], "cloudflare": []})
    assert waits == [run_evals.DEFAULT_RETRY_WAIT_S]


def test_a_long_wait_means_the_daily_limit_and_stops_the_run():
    reply = json.dumps(BY_ROW[64]["reply"])
    cases = [BY_ROW[64], BY_ROW[72], BY_ROW[73]]
    results, llm, retry, waits = retrying(
        cases, {"groq": [reply, limited(3600)], "cloudflare": [reply, reply]}
    )
    assert [r.status for r in results] == ["ok", "not_run", "not_run"]
    assert waits == [] and "daily limit" in retry.stopped
    assert run_evals.summarise(results)["not_run"] == 2


def test_too_many_rate_limits_in_a_row_stop_the_run():
    n = run_evals.MAX_RATE_LIMIT_RETRIES + 1
    results, _, retry, waits = retrying([BY_ROW[64]], {"groq": [limited(1)] * n, "cloudflare": []})
    assert results[0].status == "not_run" and len(waits) == n - 1
    assert "in a row" in retry.stopped


def test_other_errors_still_fall_back_and_cloudflare_isnt_retried():
    reply = json.dumps(BY_ROW[64]["reply"])
    slow = LLMError("timeout", "slow")
    results, _, _, waits = retrying([BY_ROW[64]], {"groq": [slow], "cloudflare": [reply]})
    assert results[0].provider == "cloudflare" and waits == []
    results, _, _, waits = retrying([BY_ROW[64]], {"groq": [slow], "cloudflare": [limited(5)]})
    assert results[0].status == "ai_resting" and waits == []


# Pacing ----------------------------------------------------------------------------


class _Provider:
    def __init__(self, name):
        self.provider = name


class _Usage:
    """A fake provider that reports fixed usage, and a clock that sleeping moves on."""

    def __init__(self, used=(1500, 500), fail=None):
        self.used, self.fail, self.now, self.sleeps = used, fail, 0.0, []

    async def complete(self, config, system, user, schema):
        if self.fail:
            raise self.fail
        return run_evals.Completion("{}", run_evals.Usage(*self.used), 1)

    async def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def pacer(fake, limit=7200):
    return run_evals.TokenPacer(fake, {"groq": limit}, sleep=fake.sleep, monotonic=lambda: fake.now)


def calls(p, provider, n):
    async def go():
        for _ in range(n):
            await p.complete(_Provider(provider), "", "", None)

    asyncio.run(go())


def test_groq_calls_wait_once_a_minute_of_tokens_is_spent():
    fake = _Usage()  # 2,000 tokens a call: three fit in 7,200, the fourth waits
    p = pacer(fake)
    calls(p, "groq", 4)
    assert fake.sleeps == [60.0]
    assert p.tokens["groq"] == 8000 and p.waited_s == 60.0


def test_the_wait_is_until_the_oldest_call_leaves_the_window():
    fake = _Usage()
    p = pacer(fake)
    calls(p, "groq", 1)
    fake.now = 25.0
    calls(p, "groq", 2)
    fake.now = 40.0
    calls(p, "groq", 1)  # 6,000 spent since 0 s; the call at 0 s leaves at 60 s
    assert fake.sleeps == [20.0]


def test_unpaced_providers_never_wait_but_are_counted():
    fake = _Usage()
    p = pacer(fake)
    calls(p, "cloudflare", 10)
    assert fake.sleeps == [] and p.tokens == {"cloudflare": 20000}


def test_a_call_larger_than_the_limit_still_goes_ahead_after_a_full_window():
    fake = _Usage(used=(9000, 0))
    p = pacer(fake)
    calls(p, "groq", 2)
    assert fake.sleeps == [60.0]  # it waits for an empty window rather than forever


def test_refused_calls_spend_nothing_and_cut_off_replies_count():
    refused = _Usage(fail=LLMError("http_429", "rate limited"))
    p = pacer(refused)
    with pytest.raises(LLMError):
        calls(p, "groq", 1)
    assert p.tokens == {} and p.spent == {"groq": []}
    cut = _Usage(fail=LLMError("truncated", "cut off", run_evals.Usage(1500, 2048)))
    p = pacer(cut)
    with pytest.raises(LLMError):
        calls(p, "cloudflare", 1)
    assert p.tokens == {"cloudflare": 3548}


def test_the_groq_pace_is_under_its_published_limit():
    assert run_evals.TOKENS_PER_MINUTE == {"groq": 7200}  # 90% of 8,000


# The call cap ----------------------------------------------------------------------


def test_the_run_stops_before_passing_the_cap():
    results, llm = run(CASES, perfect(CASES), max_calls=5)
    assert llm.calls == 5
    assert sum(r.scored for r in results) == 5
    assert run_evals.summarise(results)["not_run"] == 73 - 2 - 5


def test_fallbacks_count_and_a_case_never_starts_without_room_for_them():
    err = LLMError("http_5xx", "down")
    script = {"groq": [err, err], "cloudflare": [json.dumps(BY_ROW[64]["reply"])] * 2}
    results, llm = run([BY_ROW[64], BY_ROW[72], BY_ROW[73]], script, max_calls=5, providers=2)
    assert llm.calls == 4  # two cases at two calls; the third would need up to two more
    assert [r.status for r in results] == ["ok", "ok", "not_run"]


def test_the_cap_is_never_passed_even_if_the_check_is_skipped():
    llm = run_evals.CountingLLM(FakeLLM(groq=["{}"]), 0)
    with pytest.raises(RuntimeError):
        asyncio.run(llm.complete(None, "", "", None))


KEYS = (
    "GROQ_API_KEY",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "LANGFUSE_PUBLIC_KEY",
    "LANGFUSE_SECRET_KEY",
    "SUPABASE_SERVICE_KEY",
)


def test_a_run_where_every_case_rests_exits_1_and_still_writes_results(monkeypatch, tmp_path):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    out = tmp_path / "r.json"
    assert run_evals.main(["--config", "repo", "--max-calls", "4", "--out", str(out)]) == 1
    summary = json.loads(out.read_text())["summary"]
    assert (summary["ran"], summary["resting"], summary["calls"]) == (2, 2, 4)


def test_a_real_run_paces_groq_and_reports_tokens(monkeypatch, tmp_path):
    for key in KEYS:
        monkeypatch.delenv(key, raising=False)
    made = []

    class Recording(run_evals.TokenPacer):
        def __init__(self, inner, limits, **kw):
            super().__init__(inner, limits, **kw)
            made.append(limits)

    monkeypatch.setattr(run_evals, "TokenPacer", Recording)
    retries = []

    class RecordingRetry(run_evals.RateLimitRetry):
        def __init__(self, inner, **kw):
            super().__init__(inner, **kw)
            retries.append(inner)

    monkeypatch.setattr(run_evals, "RateLimitRetry", RecordingRetry)
    counted = []

    class RecordingCount(run_evals.CountingLLM):
        def __init__(self, inner, max_calls):
            super().__init__(inner, max_calls)
            counted.append(inner)

    monkeypatch.setattr(run_evals, "CountingLLM", RecordingCount)
    out = tmp_path / "r.json"
    run_evals.main(["--config", "repo", "--max-calls", "2", "--out", str(out)])
    assert made == [{"groq": 7200}]
    assert len(retries) == 1 and isinstance(retries[0], Recording)  # retries go through pacing
    # The counter wraps the retry, so refused calls don't count toward the cap.
    assert len(counted) == 1 and isinstance(counted[0], RecordingRetry)
    summary = json.loads(out.read_text())["summary"]
    assert (summary["tokens"], summary["paced_wait_s"]) == ({}, 0.0)
    assert (summary["rate_limit_retries"], summary["stopped"]) == (0, None)
    assert summary["config_versions"] == {"llm_understand": 2, "understand_prompt": 3}


def test_supabase_config_needs_its_keys(monkeypatch, tmp_path):
    monkeypatch.delenv("SUPABASE_SERVICE_KEY", raising=False)
    assert run_evals.main(["--out", str(tmp_path / "r.json")]) == 2


def test_max_calls_above_80_is_refused():
    with pytest.raises(SystemExit):
        run_evals.main(["--max-calls", "81"])
    assert run_evals.MAX_CALLS == 80


# Report and Langfuse scores ---------------------------------------------------------


def test_the_report_gives_both_numbers_and_the_failures():
    results, _ = run(CASES, perfect(CASES))
    text = run_evals.report(run_evals.summarise(results), results)
    assert "end to end: 66.2% (47/71 cases)" in text
    assert "model only: 100.0% (71/71 cases)" in text
    assert "r68 '14 to 15 November, Shoreditch': date | -" in text


def test_scores_go_to_langfuse_with_versions_and_failed_fields():
    tracer = RecordingTracer()
    results, _ = run([BY_ROW[64], BY_ROW[68]], perfect([BY_ROW[64], BY_ROW[68]]), tracer=tracer)
    seen = []

    def handler(request):
        seen.append(
            (request.url.path, request.headers["authorization"], json.loads(request.content))
        )
        return httpx.Response(200, json={"id": "x"})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            return await run_evals.send_scores(
                http, "https://lf.example", "pk", "sk", results, "run-1"
            )

    assert asyncio.run(go()) == 4
    assert {p for p, _, _ in seen} == {"/api/public/scores"}
    assert all(auth.startswith("Basic ") for _, auth, _ in seen)
    bodies = {(b["metadata"]["case"], b["name"]): b for _, _, b in seen}
    e2e = bodies[("r68", "parse_end_to_end")]
    assert (e2e["value"], e2e["comment"]) == (0, "failed: date")
    assert e2e["traceId"] == results[1].trace_id
    assert e2e["metadata"]["understand_prompt_version"] == 3
    assert e2e["metadata"]["llm_understand_version"] == 2
    assert bodies[("r64", "parse_model_only")]["value"] == 1


def test_no_keys_in_the_scores_payload():
    results, _ = run([BY_ROW[64]], perfect([BY_ROW[64]]))
    seen = []

    async def go():
        transport = httpx.MockTransport(lambda r: seen.append(r.content) or httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as http:
            await run_evals.send_scores(
                http, "https://lf.example", "pk-x", "sk-secret", results, "r"
            )

    asyncio.run(go())
    assert seen and all(b"sk-secret" not in body for body in seen)
