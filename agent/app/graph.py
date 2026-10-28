"""The agent's LangGraph graph.

Compiled at startup so the memory check covers LangGraph. Understand is the first node
(step 1.5). Search follows it only when the parse is ok (stage 1 step 1): a search that
needs clarifying, or that no provider answered, never reaches it. Rank and plan follow
in later steps.
"""

from dataclasses import asdict
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.search import SearchError, SearchNode
from app.understand.node import Understander


class PlanState(TypedDict, total=False):
    query: str
    status: str  # "ok", "needs_clarification" or "ai_resting"
    filters: dict[str, Any] | None  # search_items' jsonb argument
    query_text: str | None  # what Search embeds
    notes: list[dict[str, str]]
    missing: list[str]
    message: str | None
    trace_id: str | None  # the Langfuse trace
    results: list[dict[str, Any]] | None  # set only when Search ran


def build_graph(understander: Understander, search: SearchNode) -> CompiledStateGraph:
    async def understand(state: PlanState) -> PlanState:
        result = await understander.run(state["query"])
        return {
            "status": result.status,
            "filters": result.filters.to_json() if result.filters else None,
            "query_text": result.query_text,
            "notes": [asdict(n) for n in result.notes],
            "missing": list(result.missing),
            "message": result.message,
            "trace_id": result.trace_id,
        }

    async def search_node(state: PlanState) -> PlanState:
        try:
            hits = await search.run(state["filters"] or {}, state["query_text"])
        except SearchError as exc:
            return {"results": None, "message": str(exc)}
        return {"results": [h.result.model_dump(mode="json") for h in hits]}

    def after_understand(state: PlanState) -> str:
        return "search" if state["status"] == "ok" else END

    graph = StateGraph(PlanState)
    graph.add_node("understand", understand)
    graph.add_node("search", search_node)
    graph.add_edge(START, "understand")
    graph.add_conditional_edges("understand", after_understand, ["search", END])
    graph.add_edge("search", END)
    return graph.compile()
