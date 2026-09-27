# ============================================================
# agents/supervisor.py — Routes the user query to the right agent
# ============================================================

from langchain_openai import ChatOpenAI
from backend.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, SUPERVISOR_MODEL
from backend.schemas import AgentState, SupervisorRoute

_llm = ChatOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=OPENROUTER_BASE_URL,
    model=SUPERVISOR_MODEL,
    temperature=0,
)

_router_llm = _llm.with_structured_output(SupervisorRoute)

SYSTEM_PROMPT = """\
You are the Supervisor of a Solar Power Plant AI analytics system.
Your ONLY job is to read the user's question and decide which specialist agent to call next.

Available agents:
- sql_orchestrator    → General data queries: totals, averages, rankings, historical lookup
- diagnostic_agent    → Fault detection, anomaly analysis, health checks, inverter status
- predictive_agent    → Yield forecasts, future predictions, power generation estimates

Return JSON that strictly matches the SupervisorRoute schema.
"""

def supervisor_node(state: AgentState) -> AgentState:
    """Inspect the user query and set next_node."""
    route: SupervisorRoute = _router_llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": state["user_query"]},
    ])
    return {
        **state,
        "next_node": route.next_node,
    }
