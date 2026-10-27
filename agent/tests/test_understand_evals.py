"""evals/understand/cases.jsonl: each correct model reply resolves to the hand-written
filters. No model is involved; live scoring reuses these cases later."""

import json
from datetime import datetime

import pytest

from app.understand.resolve import resolve
from app.understand.schema import UnderstoodQuery
from tests.understand_support import REPO, london

CASES = [
    json.loads(line)
    for line in (REPO / "evals" / "understand" / "cases.jsonl").read_text().splitlines()
    if line.strip()
]
CITY = london()
AREAS = {a.id: a for a in CITY.areas}


def test_there_are_enough_cases_and_ids_are_unique():
    assert len(CASES) >= 20
    assert len({c["id"] for c in CASES}) == len(CASES)


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_case(case):
    understood = UnderstoodQuery.model_validate_json(json.dumps(case["reply"]))
    result = resolve(understood, datetime.fromisoformat(case["now"]), CITY)

    expected = dict(case["expected"])
    if "area" in expected:
        area = AREAS[expected.pop("area")]
        expected["lat"], expected["lon"] = area.lat, area.lon
    got = result.filters.to_json()
    assert got.keys() == expected.keys()
    for key, value in expected.items():
        assert got[key] == (pytest.approx(value) if isinstance(value, float) else value), key
    assert [n.code for n in result.notes] == case["notes"]
    assert result.query_text == case["reply"]["keywords"]
