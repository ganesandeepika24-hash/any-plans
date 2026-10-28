"""The Understand node: free text in, search_items filters out (step 1.5, decision 0012).

1. Read the active llm_understand and understand_prompt config, and the city context.
2. Ask each configured provider in order. A reply counts only if it's a JSON object
   that validates as an UnderstoodQuery. Anything else (timeout, HTTP error, empty or
   cut-off reply, bad JSON, schema failure) moves on to the next provider. Nothing is
   repaired or guessed.
3. Resolve the UnderstoodQuery into SearchFilters in code. If something has to be asked
   first, the status is "needs_clarification", with the missing reasons and a question
   for each; the filters hold what did resolve.
4. If no provider gives a valid reply, return status "ai_resting": the caller falls
   back to filter-only search (decision 0002).

Every call is traced, with the config versions it used.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.understand.clarify import Question, questions
from app.understand.llm import LLMClient, LLMConfig, LLMError, schema_instruction
from app.understand.resolve import LocationSource, Missing, Note, Point, resolve
from app.understand.schema import SearchFilters, UnderstoodQuery, strict_json_schema
from app.understand.store import Store, StoreError
from app.understand.tracing import Tracer, now_ns

CONFIG_KEYS = ("llm_understand", "understand_prompt")
MAX_QUERY_CHARS = 500
AI_RESTING = "AI search is resting; filters still work"
# One surrounding Markdown code fence is unwrapped; any other text around the JSON
# makes the reply invalid.
_FENCED = re.compile(r"```(?:json)?\s*\n(?P<body>.*)\n\s*```", re.S)
_SCHEMA = strict_json_schema(UnderstoodQuery)


class PromptConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system: str = Field(min_length=1)


@dataclass(frozen=True)
class Attempt:
    provider: str
    model: str
    outcome: str
    message: str | None = None


@dataclass
class UnderstandResult:
    status: Literal["ok", "needs_clarification", "ai_resting"]
    filters: SearchFilters | None = None
    query_text: str | None = None
    notes: tuple[Note, ...] = ()
    missing: tuple[Missing, ...] = ()
    questions: tuple[Question, ...] = ()
    used_location: bool = False  # filters' lat and lon are the user's own point
    location_source: LocationSource | None = None  # device or pin, when that point was used
    understood: UnderstoodQuery | None = None
    provider: str | None = None
    attempts: list[Attempt] = field(default_factory=list)
    config_versions: dict[str, int] = field(default_factory=dict)
    message: str | None = None
    trace_id: str | None = None  # the Langfuse trace, for queries.trace_id


def parse_reply(text: str) -> UnderstoodQuery:
    """Strict: a JSON object (optionally in one code fence) that validates."""
    body = text.strip()
    fenced = _FENCED.fullmatch(body)
    if fenced:
        body = fenced.group("body").strip()
    try:
        json.loads(body)
    except ValueError as exc:
        raise LLMError("invalid_json", "reply isn't JSON") from exc
    try:
        return UnderstoodQuery.model_validate_json(body)
    except ValidationError as exc:
        fields = sorted({".".join(str(p) for p in e["loc"]) or "(root)" for e in exc.errors()})
        raise LLMError("schema", f"reply fails UnderstoodQuery at {fields}") from exc


class Understander:
    def __init__(
        self,
        store: Store,
        llm: LLMClient,
        tracer: Tracer,
        city_id: str = "london",
        clock=lambda: datetime.now(UTC),
    ):
        self._store = store
        self._llm = llm
        self._tracer = tracer
        self._city_id = city_id
        self._clock = clock

    async def run(self, query: str, location: Point | None = None) -> UnderstandResult:
        query = query.strip()
        if not query or len(query) > MAX_QUERY_CHARS:
            raise ValueError(f"query must be 1 to {MAX_QUERY_CHARS} characters")
        trace = self._tracer.start("understand", {"query": query})
        result = await self._run(query, location, trace)
        result.trace_id = trace.trace_id
        trace.finish(
            output={
                "status": result.status,
                "provider": result.provider,
                "filters": stored_filters(result),
                "query_text": result.query_text,
                "notes": [n.code for n in result.notes],
                "missing": list(result.missing),
            },
            metadata={
                "config_versions": result.config_versions,
                **{f"{k}_version": v for k, v in result.config_versions.items()},
                "attempts": [a.outcome for a in result.attempts],
            },
            error=result.message if result.status == "ai_resting" else None,
        )
        await self._tracer.send(trace)
        return result

    async def _run(self, query: str, location: Point | None, trace) -> UnderstandResult:
        try:
            entries = await self._store.config(CONFIG_KEYS)
            versions = {k: entries[k].version for k in CONFIG_KEYS}
            llm_config = LLMConfig.model_validate(entries["llm_understand"].value)
            prompt = PromptConfig.model_validate(entries["understand_prompt"].value)
            city = await self._store.city(self._city_id)
        except (StoreError, ValidationError, KeyError) as exc:
            return UnderstandResult("ai_resting", message=f"{AI_RESTING} (config: {exc})")

        result = UnderstandResult("ai_resting", config_versions=versions, message=AI_RESTING)
        for provider in llm_config.providers:
            system = prompt.system
            schema = _SCHEMA if provider.structured else None
            if not provider.structured:
                system += schema_instruction(_SCHEMA)
            started = now_ns()
            text: str | None = None
            usage: dict[str, Any] = {}
            try:
                completion = await self._llm.complete(provider, system, query, schema)
                text = completion.text
                usage = {
                    "input": completion.usage.input_tokens,
                    "output": completion.usage.output_tokens,
                }
                understood = parse_reply(completion.text)
            except LLMError as exc:
                usage = usage or {
                    "input": exc.usage.input_tokens,
                    "output": exc.usage.output_tokens,
                }
                result.attempts.append(
                    Attempt(provider.provider, provider.model, exc.outcome, str(exc))
                )
                trace.generation(
                    name=f"understand.{provider.provider}",
                    model=provider.model,
                    start_ns=started,
                    input=_messages(system, query),
                    output=text,
                    usage=usage,
                    metadata=_attempt_metadata(provider.provider, exc.outcome, versions),
                    error=f"{exc.outcome}: {exc}",
                )
                continue

            result.attempts.append(Attempt(provider.provider, provider.model, "ok"))
            trace.generation(
                name=f"understand.{provider.provider}",
                model=provider.model,
                start_ns=started,
                input=_messages(system, query),
                output=text,
                usage=usage,
                metadata=_attempt_metadata(provider.provider, "ok", versions),
            )
            resolution = resolve(understood, self._clock(), city, location)
            result.status = "needs_clarification" if resolution.missing else "ok"
            result.message = None
            result.provider = provider.provider
            result.understood = understood
            result.filters = resolution.filters
            result.query_text = resolution.query_text
            result.notes = resolution.notes
            result.missing = resolution.missing
            result.questions = questions(resolution.missing, understood, city.name)
            result.used_location = resolution.used_location
            result.location_source = resolution.location_source
            break
        return result


def stored_filters(result: UnderstandResult) -> dict[str, Any] | None:
    """The filters as logged and traced. The user's own point is used for that search
    only, never stored (location rules, step 1.5), so lat and lon are left out."""
    if result.filters is None:
        return None
    out = result.filters.to_json()
    if result.used_location:
        out.pop("lat", None)
        out.pop("lon", None)
    return out


def _messages(system: str, user: str) -> list[dict[str, str]]:
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def _attempt_metadata(provider: str, outcome: str, versions: dict[str, int]) -> dict[str, Any]:
    return {
        "provider": provider,
        "outcome": outcome,
        "config_versions": versions,
        **{f"{k}_version": v for k, v in versions.items()},
    }
