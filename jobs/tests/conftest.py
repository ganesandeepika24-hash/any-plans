import json
import math
import os
from datetime import UTC, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import psycopg
import pytest

from jobs import embeddings
from jobs.db import City, Database
from jobs.http import CallBudget, RateLimitedClient

FIXTURES = Path(__file__).parent / "fixtures"
REPO = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 1, tzinfo=UTC)
LONDON = City("london", ZoneInfo("Europe/London"), 51.507333, -0.127654)


def fixture(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text())


@pytest.fixture(autouse=True)
def no_model_download(monkeypatch):
    """Tests use fake embedders. Loading the real model would download it, so it fails."""

    def refuse():
        raise AssertionError("a test tried to load the embedding model; pass a fake embedder")

    monkeypatch.setattr(embeddings, "get_model", refuse)


class FakeEmbedder:
    """Records every text it's asked to embed and returns constant vectors. With
    fail_after=n, the first n calls succeed and the next one raises."""

    def __init__(self, fail_after=None, dimensions=384):
        self.calls: list[list[str]] = []
        self.fail_after = fail_after
        self.dimensions = dimensions

    @property
    def texts(self) -> list[str]:
        return [t for call in self.calls for t in call]

    def __call__(self, texts):
        if self.fail_after is not None and len(self.calls) >= self.fail_after:
            raise RuntimeError("embedding failed")
        self.calls.append(list(texts))
        return [[0.1] * self.dimensions for _ in texts]


class FakeClock:
    """Time moves only when the code sleeps."""

    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def client_for(handler, per_run=10_000, daily=10_000, used_today=0, min_interval=0.0, clock=None):
    clock = clock or FakeClock()
    return RateLimitedClient(
        httpx.Client(transport=httpx.MockTransport(handler)),
        min_interval,
        CallBudget(per_run, daily, used_today),
        clock=clock,
        sleep=clock.sleep,
    )


class FakeTicketmaster:
    """Serves synthetic events. `totals(start, end)` gives each window's item count, and
    `applied_size` is the page size the server uses whatever the request asks for."""

    def __init__(self, totals, applied_size=200):
        self.totals = totals
        self.applied_size = applied_size
        self.requests: list[dict[str, str]] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        self.requests.append(params)
        start = datetime.fromisoformat(params["startDateTime"].replace("Z", "+00:00"))
        end = datetime.fromisoformat(params["endDateTime"].replace("Z", "+00:00"))
        total = self.totals(start, end)
        size, page = self.applied_size, int(params["page"])
        count = max(0, min(size, total - page * size))
        events = [
            {"id": f"{params['startDateTime']}-{page}-{i}", "name": "Synthetic"}
            for i in range(count)
        ]
        body = {
            "page": {
                "size": size,
                "totalElements": total,
                "totalPages": math.ceil(total / size),
                "number": page,
            }
        }
        if events:
            body["_embedded"] = {"events": events}
        return httpx.Response(200, json=body)


# Database fixtures (integration tests) ------------------------------------------


@pytest.fixture
def db_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        if os.environ.get("CI"):
            pytest.fail("TEST_DATABASE_URL must be set in CI")
        pytest.skip("TEST_DATABASE_URL is not set")
    return url


@pytest.fixture
def db(db_url):
    with psycopg.connect(db_url, autocommit=True) as conn:
        for table in (
            "price_snapshots",
            "curated_pages",
            "events",
            "venues",
            "job_runs",
            "usage_daily",
        ):
            conn.execute(f"delete from public.{table}")  # noqa: S608 (fixed table names)
    database = Database.connect(db_url, read_only=False)
    yield database
    database.close()


@pytest.fixture
def sql(db_url):
    """Run a query on a separate autocommit connection and return all rows."""
    conn = psycopg.connect(db_url, autocommit=True)

    def query(text: str, params=None) -> list[tuple]:
        cur = conn.execute(text, params)
        return cur.fetchall() if cur.description else []

    yield query
    conn.close()
