"""
graph/graph.py – LangGraph StateGraph wiring.

Assembles all nodes into the full multi-agent pipeline:

  [orchestrator] → [executor] (loop until all done) → [validator]
       ↑                                                     |
       └─────────────── REPLAN ──────────────────────────────┤
                                                             |
                                                   OK / FAIL ↓
                                               [synthesizer] → END

Node responsibilities:
  - orchestrator : LLM planner → emits Plan
  - executor     : runs one topological layer at a time (loops until done)
  - validator    : checks results → OK / REPLAN / FAIL
  - synthesizer  : writes the final grounded answer

Usage:
    from solar_agent.graph.graph import build_graph
    from langchain_google_genai import ChatGoogleGenerativeAI

    llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
    graph = build_graph(llm)

    result = graph.invoke({
        "user_query": "Which inverters underperformed last month in Block A?",
        "messages": [],
        "plan": None,
        "results": {},
        "validation": None,
        "replan_count": 0,
        "final_answer": None,
        "assumptions": [],
        "is_out_of_scope": False,
    })
    print(result["final_answer"])
"""

from __future__ import annotations

import logging

from langgraph.graph import END, StateGraph

from solar_agent.graph.executor import Executor
from solar_agent.graph.orchestrator import Orchestrator
from solar_agent.graph.synthesizer import Synthesizer
from solar_agent.graph.validator import Validator
from solar_agent.state import AgentState, ValidationStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Routing helpers (used as conditional edges)
# ---------------------------------------------------------------------------

def _route_after_orchestrator(state: AgentState) -> str:
    """After orchestrator: go to executor if we have a plan, else end."""
    if state.get("is_out_of_scope") or state.get("plan") is None:
        return END
    return "executor"


def _route_after_executor(state: AgentState) -> str:
    """After executor: keep looping until all tasks are terminal."""
    if Executor.all_done(state):
        return "validator"
    return "executor"


def _route_after_validator(state: AgentState) -> str:
    """After validator: synthesize, replan, or fail."""
    return Validator.route(state)


def _route_after_replan(state: AgentState) -> str:
    """After orchestrator during a replan: increment counter then go to executor."""
    # replan_count is already incremented in the orchestrator patch
    return _route_after_orchestrator(state)


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph(llm) -> StateGraph:
    """
    Construct and compile the full LangGraph StateGraph.

    Args:
        llm: Any LangChain chat model (must support tool-calling for agents).

    Returns:
        A compiled LangGraph runnable.
    """
    orchestrator = Orchestrator(llm=llm)
    executor = Executor(llm=llm)
    validator = Validator()
    synthesizer = Synthesizer(llm=llm)

    # Thin wrapper nodes that call the class methods
    def orchestrator_node(state: AgentState) -> dict:
        patch = orchestrator.plan(state)
        # Increment replan_count on subsequent calls
        if state.get("plan") is not None:  # this is a re-plan
            patch["replan_count"] = state.get("replan_count", 0) + 1
        return patch

    def executor_node(state: AgentState) -> dict:
        return executor.run_next_layer(state)

    def validator_node(state: AgentState) -> dict:
        return validator.validate(state)

    def synthesizer_node(state: AgentState) -> dict:
        return synthesizer.synthesize(state)

    # Build graph
    g = StateGraph(AgentState)

    g.add_node("orchestrator", orchestrator_node)
    g.add_node("executor", executor_node)
    g.add_node("validator", validator_node)
    g.add_node("synthesizer", synthesizer_node)

    # Entry point
    g.set_entry_point("orchestrator")

    # Edges
    g.add_conditional_edges("orchestrator", _route_after_orchestrator, {
        "executor": "executor",
        END: END,
    })

    g.add_conditional_edges("executor", _route_after_executor, {
        "executor": "executor",
        "validator": "validator",
    })

    g.add_conditional_edges("validator", _route_after_validator, {
        "orchestrator": "orchestrator",   # REPLAN
        "synthesizer": "synthesizer",     # OK or FAIL
    })

    g.add_edge("synthesizer", END)

    return g.compile()


# ---------------------------------------------------------------------------
# Convenience: initial state factory
# ---------------------------------------------------------------------------

def initial_state(user_query: str) -> AgentState:
    """Return a fresh AgentState with sensible defaults."""
    return AgentState(
        user_query=user_query,
        messages=[],
        plan=None,
        results={},
        validation=None,
        replan_count=0,
        final_answer=None,
        assumptions=[],
        is_out_of_scope=False,
    )
