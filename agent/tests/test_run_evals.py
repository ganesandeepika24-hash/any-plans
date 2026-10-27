"""The eval set and its runner (step 1.6, decision 0014). The fake LLM stands in for
the providers; the Understand node, resolver and scorer are the real ones."""

import asyncio
import importlib.util
import json
from datetime import datetime

import httpx
import pytest

from app.understand.llm import LLMError
from app.understand.node import Understander
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
    store = FakeStore(entries(versions={"understand_prompt": 2}))
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


def test_a_resting_reply_fails_every_field():
    err = LLMError("http_429", "rate limited")
    results, _ = run([BY_ROW[64]], {"groq": [err], "cloudflare": []})
    (r,) = results
    assert r.status == "ai_resting"
    assert not any(r.end_to_end.values()) and not any(r.model_only.values())
    assert run_evals.summarise(results)["resting"] == 1


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
    assert e2e["metadata"]["understand_prompt_version"] == 2
    assert e2e["metadata"]["llm_understand_version"] == 1
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
