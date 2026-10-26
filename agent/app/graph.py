"""Placeholder LangGraph graph.

Compiled at startup so the memory check covers LangGraph. The real nodes
(Understand, Retrieve, Rank and plan) replace this in stage 1.
"""

from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph


class PlanState(TypedDict, total=False):
    query: str
    status: str


def _placeholder(state: PlanState) -> PlanState:
    return {"status": "not_implemented"}


def build_graph() -> CompiledStateGraph:
    graph = StateGraph(PlanState)
    graph.add_node("placeholder", _placeholder)
    graph.add_edge(START, "placeholder")
    graph.add_edge("placeholder", END)
    return graph.compile()
