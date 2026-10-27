"""The agent's LangGraph graph.

Compiled at startup so the memory check covers LangGraph. Understand is the first node
(step 1.5); Retrieve, Rank and plan follow in later steps.
"""

from dataclasses import asdict
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from app.understand.node import Understander


class PlanState(TypedDict, total=False):
    query: str
    status: str  # "ok" or "ai_resting"
    filters: dict[str, Any] | None  # search_items' jsonb argument
    query_text: str | None  # what Retrieve embeds
    notes: list[dict[str, str]]
    message: str | None


def build_graph(understander: Understander) -> CompiledStateGraph:
    async def understand(state: PlanState) -> PlanState:
        result = await understander.run(state["query"])
        return {
            "status": result.status,
            "filters": result.filters.to_json() if result.filters else None,
            "query_text": result.query_text,
            "notes": [asdict(n) for n in result.notes],
            "message": result.message,
        }

    graph = StateGraph(PlanState)
    graph.add_node("understand", understand)
    graph.add_edge(START, "understand")
    graph.add_edge("understand", END)
    return graph.compile()
