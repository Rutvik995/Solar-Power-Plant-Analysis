# ============================================================
# graph.py — LangGraph StateGraph assembly
# ============================================================

from langgraph.graph import StateGraph, END
from backend.schemas import AgentState
from backend.agents.supervisor      import supervisor_node
from backend.agents.sql_orchestrator import sql_orchestrator_node
from backend.agents.diagnostic_agent import diagnostic_agent_node
from backend.agents.predictive_agent  import predictive_agent_node
from backend.agents.synthesis_agent   import synthesis_agent_node


def _route(state: AgentState) -> str:
    """Conditional edge: read next_node set by supervisor and branch."""
    return state.get("next_node", "sql_orchestrator")


def build_graph():
    """Build and compile the LangGraph StateGraph."""
    g = StateGraph(AgentState)

    # Register nodes
    g.add_node("supervisor",         supervisor_node)
    g.add_node("sql_orchestrator",   sql_orchestrator_node)
    g.add_node("diagnostic_agent",   diagnostic_agent_node)
    g.add_node("predictive_agent",   predictive_agent_node)
    g.add_node("synthesis_agent",    synthesis_agent_node)

    # Entry point
    g.set_entry_point("supervisor")

    # Conditional branch after supervisor
    g.add_conditional_edges(
        "supervisor",
        _route,
        {
            "sql_orchestrator": "sql_orchestrator",
            "diagnostic_agent": "diagnostic_agent",
            "predictive_agent": "predictive_agent",
        },
    )

    # All specialist nodes flow into synthesis_agent
    g.add_edge("sql_orchestrator", "synthesis_agent")
    g.add_edge("diagnostic_agent", "synthesis_agent")
    g.add_edge("predictive_agent", "synthesis_agent")

    # synthesis_agent is the terminal node
    g.add_edge("synthesis_agent", END)

    return g.compile()
