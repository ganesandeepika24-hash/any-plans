"""HTTP client with a minimum gap between requests, retries and a call budget.

API keys travel in query strings, so errors are re-raised without the URL's query.
"""

import re
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import httpx

RETRY_STATUSES = {429, 500, 502, 503, 504}
ERROR_TEXT_LIMIT = 300
_KEY_IN_TEXT = re.compile(r"(api_?key=)[^&\s\"']+", re.IGNORECASE)


class CapReached(Exception):
    """The run's call budget is spent. The caller stops cleanly and records it."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class RequestFailed(Exception):
    """A request failed after retries. Neither the status nor the message carries an API key."""

    def __init__(self, status: int | None, message: str):
        super().__init__(f"HTTP {status}: {message}" if status is not None else message)
        self.status = status
        self.message = message


@dataclass(frozen=True)
class Failure:
    """One failed window, for the run summary."""

    window: str
    status: int | None
    message: str
    page: int | None = None  # Ticketmaster page number or Skiddle offset


class CallBudget:
    """Calls allowed in this run: the lower of the per-run cap and what's left of the day."""

    def __init__(self, per_run: int, daily_limit: int, used_today: int):
        self.per_run = per_run
        self.daily_limit = daily_limit
        self.used_today = used_today
        self.used = 0

    def take(self) -> None:
        if self.used >= self.per_run:
            raise CapReached(f"per-run cap of {self.per_run} calls")
        if self.used_today + self.used >= self.daily_limit:
            raise CapReached(f"daily stop of {self.daily_limit} calls")
        self.used += 1


class RateLimitedClient:
    def __init__(
        self,
        client: httpx.Client,
        min_interval: float,
        budget: CallBudget,
        max_retries: int = 3,
        backoff: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        self._client = client
        self._min_interval = min_interval
        self.budget = budget
        self._max_retries = max_retries
        self._backoff = backoff
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def get_json(self, url: str, params: dict[str, Any]) -> Any:
        for attempt in range(self._max_retries + 1):
            self.budget.take()
            self._wait()
            try:
                response = self._client.get(url, params=params)
            except httpx.TransportError as exc:
                if attempt == self._max_retries:
                    raise RequestFailed(
                        None, f"GET {_redact(url)}: {type(exc).__name__}: {_scrub(str(exc))}"
                    ) from None
                self._sleep(self._backoff * 2**attempt)
                continue
            if response.status_code in RETRY_STATUSES and attempt < self._max_retries:
                self._sleep(_retry_after(response) or self._backoff * 2**attempt)
                continue
            if response.is_error:
                raise RequestFailed(response.status_code, f"GET {_redact(url)}: {_text(response)}")
            try:
                return response.json()
            except ValueError:
                raise RequestFailed(
                    response.status_code, f"GET {_redact(url)}: not JSON: {_text(response)}"
                ) from None
        raise AssertionError("unreachable")

    def _wait(self) -> None:
        now = self._clock()
        if self._last is not None and now - self._last < self._min_interval:
            self._sleep(self._min_interval - (now - self._last))
        self._last = self._clock()


def _retry_after(response: httpx.Response) -> float | None:
    try:
        return max(0.0, float(response.headers["Retry-After"]))
    except (KeyError, ValueError):
        return None


def _redact(url: str) -> str:
    return url.split("?", 1)[0]


def _scrub(text: str) -> str:
    return _KEY_IN_TEXT.sub(r"\1<redacted>", text)


def _text(response: httpx.Response) -> str:
    body = " ".join(response.text.split())[:ERROR_TEXT_LIMIT]
    return _scrub(f"{response.reason_phrase} {body}".strip())
