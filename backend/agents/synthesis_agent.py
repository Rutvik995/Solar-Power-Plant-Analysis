# ============================================================
# agents/synthesis_agent.py — Converts raw data into a human narrative
# ============================================================

from __future__ import annotations
import json
from langchain_openai import ChatOpenAI
from backend.config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, SYNTHESIS_MODEL
from backend.schemas import AgentState

_llm = ChatOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=OPENROUTER_BASE_URL,
    model=SYNTHESIS_MODEL,
    temperature=0.3,
)

SYSTEM_PROMPT = """\
You are the Solar Power Plant Analytics AI — a friendly, expert system that explains
technical solar plant data to engineers and plant managers.

Given the analysis context below, write a clear, helpful Markdown-formatted response:
- Use bullet points, headers (##, ###), and bold text where appropriate.
- Quantify findings with actual numbers from the data.
- Suggest actionable next steps where relevant.
- Keep the tone professional but approachable.
- Do NOT make up numbers — only use what is in the provided context.
"""

def synthesis_agent_node(state: AgentState) -> AgentState:
    """Assemble findings from state and generate the final markdown response."""
    context_parts: list[str] = [f"**User Question:** {state['user_query']}\n"]

    if state.get("sql_query"):
        context_parts.append(f"**SQL Executed:**\n```sql\n{state['sql_query']}\n```")

    if state.get("query_results"):
        # Limit to first 50 rows to avoid token overflow
        rows = state["query_results"][:50]
        context_parts.append(
            f"**Query Results ({len(rows)} rows shown):**\n```json\n"
            + json.dumps(rows, indent=2, default=str)
            + "\n```"
        )

    if state.get("diagnostic_report"):
        report = state["diagnostic_report"]
        fault_lines = []
        for f in report.faults_detected[:30]:   # cap at 30 for prompt size
            fault_lines.append(
                f"  - [{f.severity}] {f.inverter_id} → {f.fault_category}: {f.description}"
            )
        faults_str = "\n".join(fault_lines) if fault_lines else "  No faults."
        context_parts.append(
            f"**Diagnostic Summary:** {report.summary}\n\n"
            f"**Fault Details (up to 30 shown):**\n{faults_str}"
        )

    if state.get("forecast_report"):
        r = state["forecast_report"]
        context_parts.append(
            f"**Forecast:** Plant {r.plant_id} on {r.target_date} → "
            f"{r.total_predicted_yield_kwh:,.2f} kWh predicted.\n"
            f"Note: {r.confidence_note}"
        )

    user_prompt = "\n\n".join(context_parts)

    response = _llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_prompt},
    ])

    return {**state, "final_response": response.content}
