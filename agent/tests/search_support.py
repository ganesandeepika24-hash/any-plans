"""Fakes for the Search node: search_items and the events lookup, and the embedder."""

import uuid
from typing import Any

from app.search import SearchError

VECTOR = [0.5] * 384


def event_row(n: int, **fields: Any) -> dict[str, Any]:
    """A search_items row, as PostgREST returns it."""
    return {
        "id": str(uuid.UUID(int=n)),
        "source": "ticketmaster",
        "title": f"Event {n}",
        "category": "Music",
        "genre": "Jazz",
        "start_at": "2026-10-28T20:00:00+00:00",
        "end_at": None,
        "venue_id": None,
        "venue_name": "Ronnie Scott's",
        "price_min": 20,
        "price_max": 40,
        "currency": "GBP",
        "url": f"https://example.com/{n}",
        "image_url": None,
        "last_checked_at": "2026-10-28T06:00:00+00:00",
        "confidence": "checked_today",
        "confidence_label": "Price checked today",
        "distance_m": 350.0,
        "lat": 51.5133,
        "lon": -0.1318,
        "similarity": 0.8,
        "score": 1.0 - n / 100,
    } | fields


class FakeSearch:
    def __init__(self, rows: list[dict[str, Any]] | None = None, fail: bool = False):
        self.rows = [event_row(1), event_row(2)] if rows is None else rows
        self.fail = fail
        self.calls: list[tuple[dict[str, Any], list[float] | None]] = []

    async def search(self, filters, embedding):
        self.calls.append((filters, embedding))
        if self.fail:
            raise SearchError("/rpc/search_items failed: ConnectError")
        return self.rows

    async def source_ids(self, event_ids):
        return {i: f"src-{uuid.UUID(i).int}" for i in event_ids}


class FakeEmbed:
    def __init__(self, fail: bool = False):
        self.texts: list[list[str]] = []
        self.fail = fail

    def __call__(self, texts: list[str]) -> list[list[float]]:
        self.texts.append(texts)
        if self.fail:
            raise RuntimeError("model missing")
        return [VECTOR for _ in texts]
