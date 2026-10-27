"""Langfuse traces over plain HTTP (decision 0012).

Langfuse's documented write path is OTLP/HTTP: POST {host}/api/public/otel/v1/traces
with Basic auth (public key, secret key) and `x-langfuse-ingestion-version: 4` for
real-time ingestion. This builds the OTLP JSON itself, so there's no SDK or
OpenTelemetry dependency. Attribute names are Langfuse's (langfuse.com/integrations/
native/opentelemetry, read 27 Oct 2026).

One trace per query: a root span, plus one generation per provider attempt carrying
the provider, model, outcome, token counts and the config versions in use. Sending
never raises: a tracing failure is logged and the search goes on.
"""

import base64
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

log = logging.getLogger(__name__)

OTLP_PATH = "/api/public/otel/v1/traces"
SEND_TIMEOUT_S = 3.0
SERVICE_NAME = "any-plans-agent"


def now_ns() -> int:
    return time.time_ns()


@dataclass
class Span:
    span_id: str
    name: str
    start_ns: int
    end_ns: int | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


class Trace:
    def __init__(self, name: str, input: Any, release: str, clock=now_ns):
        self.trace_id = secrets.token_hex(16)
        self._clock = clock
        self.root = Span(secrets.token_hex(8), name, clock())
        self.root.attributes.update(
            {
                "langfuse.trace.name": name,
                "langfuse.observation.type": "span",
                "langfuse.observation.input": input,
                "langfuse.release": release,
            }
        )
        self.generations: list[Span] = []

    def generation(
        self,
        *,
        name: str,
        model: str,
        start_ns: int,
        input: Any,
        output: Any,
        usage: dict[str, int | None],
        metadata: dict[str, Any],
        error: str | None = None,
    ) -> None:
        span = Span(secrets.token_hex(8), name, start_ns, self._clock(), error=error)
        span.attributes.update(
            {
                "langfuse.observation.type": "generation",
                "langfuse.observation.model.name": model,
                "langfuse.observation.input": input,
                "langfuse.observation.output": output,
                "langfuse.observation.usage_details": {
                    k: v for k, v in usage.items() if v is not None
                },
                "langfuse.observation.level": "ERROR" if error else "DEFAULT",
                **{f"langfuse.observation.metadata.{k}": v for k, v in metadata.items()},
            }
        )
        if error:
            span.attributes["langfuse.observation.status_message"] = error
        self.generations.append(span)

    def finish(self, output: Any, metadata: dict[str, Any], error: str | None = None) -> None:
        self.root.end_ns = self._clock()
        self.root.error = error
        self.root.attributes["langfuse.observation.output"] = output
        self.root.attributes["langfuse.observation.level"] = "ERROR" if error else "DEFAULT"
        if error:
            self.root.attributes["langfuse.observation.status_message"] = error
        for key, value in metadata.items():
            self.root.attributes[f"langfuse.trace.metadata.{key}"] = value
            self.root.attributes[f"langfuse.observation.metadata.{key}"] = value

    def to_otlp(self) -> dict[str, Any]:
        spans = [_otlp_span(self.trace_id, self.root, None)]
        spans += [_otlp_span(self.trace_id, g, self.root.span_id) for g in self.generations]
        return {
            "resourceSpans": [
                {
                    "resource": {"attributes": [_attr("service.name", SERVICE_NAME)]},
                    "scopeSpans": [{"scope": {"name": "any-plans.understand"}, "spans": spans}],
                }
            ]
        }


class Tracer(Protocol):
    def start(self, name: str, input: Any) -> Trace: ...

    async def send(self, trace: Trace) -> None: ...


class NullTracer:
    """Used when the Langfuse keys aren't set (tests, local runs)."""

    def __init__(self, release: str = "dev"):
        self._release = release

    def start(self, name: str, input: Any) -> Trace:
        return Trace(name, input, self._release)

    async def send(self, trace: Trace) -> None:
        return None


class LangfuseTracer:
    def __init__(
        self,
        http: httpx.AsyncClient,
        host: str,
        public_key: str,
        secret_key: str,
        release: str,
    ):
        self._http = http
        self._url = host.rstrip("/") + OTLP_PATH
        token = base64.b64encode(f"{public_key}:{secret_key}".encode()).decode()
        self._headers = {
            "Authorization": f"Basic {token}",
            "x-langfuse-ingestion-version": "4",
        }
        self._release = release

    def start(self, name: str, input: Any) -> Trace:
        return Trace(name, input, self._release)

    async def send(self, trace: Trace) -> None:
        try:
            response = await self._http.post(
                self._url, json=trace.to_otlp(), headers=self._headers, timeout=SEND_TIMEOUT_S
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            log.warning("Langfuse trace %s not sent: %s", trace.trace_id, type(exc).__name__)


def _otlp_span(trace_id: str, span: Span, parent: str | None) -> dict[str, Any]:
    out: dict[str, Any] = {
        "traceId": trace_id,
        "spanId": span.span_id,
        "name": span.name,
        "kind": 1,  # SPAN_KIND_INTERNAL
        "startTimeUnixNano": str(span.start_ns),
        "endTimeUnixNano": str(span.end_ns or span.start_ns),
        "attributes": [_attr(k, v) for k, v in span.attributes.items()],
        "status": {"code": 2, "message": span.error} if span.error else {"code": 1},
    }
    if parent:
        out["parentSpanId"] = parent
    return out


def _attr(key: str, value: Any) -> dict[str, Any]:
    if isinstance(value, bool):
        typed = {"boolValue": value}
    elif isinstance(value, int):
        typed = {"intValue": str(value)}
    elif isinstance(value, float):
        typed = {"doubleValue": value}
    elif isinstance(value, str):
        typed = {"stringValue": value}
    else:
        typed = {"stringValue": json.dumps(value, default=str, separators=(",", ":"))}
    return {"key": key, "value": typed}
