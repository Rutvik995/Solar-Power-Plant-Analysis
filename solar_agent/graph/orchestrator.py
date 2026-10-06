"""
graph/orchestrator.py – Orchestrator node.

The orchestrator is the system's planner. It receives a natural-language user query
and produces a structured Plan (a DAG of Tasks) that the Executor will run.

Design constraints:
  - The LLM plans and classifies intent; it never computes numbers.
  - All entity resolution and date parsing happen in the Data Agent tasks, not here.
  - The plan is validated as a Pydantic model (DAG cycle check included).
  - If the query is out-of-scope, the orchestrator sets is_out_of_scope=True
    and returns without a plan.
  - On re-plan (validation → REPLAN), the orchestrator receives the replan_hint
    and produces a revised plan.
"""

from __future__ import annotations

import json
import logging
from datetime import date, timedelta
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from solar_agent.state import AgentState, IntentType, Plan, Scope, Task, TaskStatus

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# System prompt
# ---------------------------------------------------------------------------

_ORCHESTRATOR_SYSTEM_PROMPT = """You are the Orchestrator for a Solar Power Plant Analysis multi-agent system.
Your job is to read the user's question and produce a structured JSON execution plan.

AGENTS AVAILABLE:
- "data"        : Resolves entity names/dates, fetches telemetry from the DB.
- "performance" : Computes KPIs, ranks inverters/blocks, compares periods, analyses trends.
- "fault"       : Detects zero-generation, status faults, peer underperformance, anomalies.
- "environment" : Estimates soiling losses, analyses weather correlations.

RULES:
1. Every plan MUST start with a "data" task (t1) that fetches the telemetry.
2. All later tasks depend_on the data task (at minimum).
3. Tasks that can run in parallel (e.g. fault + environment both analysing the same data)
   should share the same dependency, NOT depend on each other.
4. Task IDs must be "t1", "t2", "t3", etc.
5. inputs may reference a previous task's output with the string "$t1.data_ref".
6. If the query is out-of-scope (e.g. weather forecasting, financial modelling, topics
   unrelated to solar plant operations), return {"out_of_scope": true, "reason": "..."}.
7. NEVER include numeric calculations, formulas, or data values in the plan.

INTENT TYPES: performance_ranking, fault_diagnosis, comparison, trend, environmental_impact,
              root_cause, summary, out_of_scope.

OUTPUT FORMAT (JSON only, no markdown code fences):
{
  "intent": "<intent_type>",
  "scope": {
    "plant_ids": [<int>, ...],
    "block_ids": [],
    "inverter_ids": [],
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD"
  },
  "tasks": [
    {
      "id": "t1",
      "agent": "data",
      "description": "Fetch inverter daily telemetry for Plant 1 from 2026-09-12 to 2026-09-26.",
      "inputs": {"plant_ids": [1], "start_date": "2026-09-12", "end_date": "2026-09-26"},
      "depends_on": []
    },
    {
      "id": "t2",
      "agent": "performance",
      "description": "Compute plant-level KPIs and rank inverters by PR.",
      "inputs": {"data_ref": "$t1.data_ref"},
      "depends_on": ["t1"]
    }
  ],
  "success_criteria": "Return the plant PR, a ranked list of inverters, and any warnings."
}
"""

_OUT_OF_SCOPE_TOPICS = [
    "weather forecast", "stock", "financial", "investment", "crypto",
    "recipe", "sport", "news", "politic",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_scope() -> dict:
    """Fallback scope when the LLM omits it: last available 30 days."""
    today = date.today()
    return {
        "plant_ids": [],
        "block_ids": [],
        "inverter_ids": [],
        "start_date": (today - timedelta(days=30)).isoformat(),
        "end_date": today.isoformat(),
    }


def _resolve_input_refs(inputs: dict[str, Any], results: dict) -> dict[str, Any]:
    """
    Replace "$t1.data_ref" style references with actual values from completed task results.
    """
    resolved: dict[str, Any] = {}
    for key, val in inputs.items():
        if isinstance(val, str) and val.startswith("$"):
            # Format: "$t1.data_ref" or "$t2.metrics.performance_ratio"
            parts = val[1:].split(".")  # ["t1", "data_ref"] or ["t2", "metrics", "performance_ratio"]
            task_id = parts[0]
            attr_chain = parts[1:]
            tr = results.get(task_id)
            if tr is not None:
                obj: Any = tr
                for attr in attr_chain:
                    if isinstance(obj, dict):
                        obj = obj.get(attr)
                    else:
                        obj = getattr(obj, attr, None)
                resolved[key] = obj
            else:
                resolved[key] = None  # dependency not yet resolved
        else:
            resolved[key] = val
    return resolved


# ---------------------------------------------------------------------------
# Orchestrator node
# ---------------------------------------------------------------------------

class Orchestrator:
    """
    LangGraph node that plans a DAG of Tasks from the user's query.

    The node is called at the start of each graph invocation and on REPLAN.
    """

    def __init__(self, llm):
        self._llm = llm

    def plan(self, state: AgentState, run_id: str = "__default__") -> dict:
        """
        Produce (or revise) a Plan and return a state patch.
        Called as a LangGraph node: receives full AgentState, returns partial update.
        """
        query = state["user_query"]
        replan_count = state.get("replan_count", 0)
        prev_validation = state.get("validation")

        # Quick heuristic out-of-scope guard before calling the LLM
        query_lower = query.lower()
        if any(kw in query_lower for kw in _OUT_OF_SCOPE_TOPICS):
            logger.info("Orchestrator: query appears out-of-scope (heuristic).")
            return {
                "is_out_of_scope": True,
                "final_answer": (
                    "This system analyses solar power plant telemetry. "
                    "Your question appears to be outside that scope. "
                    "Please ask about plant performance, faults, soiling losses, or weather effects."
                ),
            }

        # Build the human message
        human_parts = [f"User query: {query}"]
        if replan_count > 0 and prev_validation:
            issues_text = "\n".join(
                f"  [{i.severity.upper()}] {i.description} (hint: {i.replan_hint})"
                for i in prev_validation.issues
            )
            human_parts.append(
                f"\nThis is re-plan attempt #{replan_count}. "
                f"The previous plan had these validation issues:\n{issues_text}\n"
                "Please produce a revised plan that addresses these issues."
            )

        human_msg = HumanMessage(content="\n".join(human_parts))
        messages = [SystemMessage(content=_ORCHESTRATOR_SYSTEM_PROMPT), human_msg]

        try:
            response = self._llm.invoke(messages, run_id=run_id)
            raw = response.content.strip()

            # Strip markdown fences if the LLM wraps the JSON
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            payload = json.loads(raw)
        except Exception as exc:
            logger.error("Orchestrator failed to parse LLM response: %s", exc)
            return {
                "is_out_of_scope": True,
                "final_answer": f"Planning failed: {exc}. Please rephrase your question.",
            }

        # Handle explicit out-of-scope response
        if payload.get("out_of_scope"):
            return {
                "is_out_of_scope": True,
                "final_answer": payload.get("reason", "Query is out of scope."),
            }

        # Build and validate the Plan Pydantic model
        try:
            scope_data = payload.get("scope") or _default_scope()
            scope = Scope(**scope_data)

            tasks = [Task(**t) for t in payload.get("tasks", [])]

            plan = Plan(
                intent=IntentType(payload.get("intent", "summary")),
                scope=scope,
                tasks=tasks,
                success_criteria=payload.get("success_criteria", "Answer the user's question."),
            )
        except (ValidationError, ValueError, KeyError) as exc:
            logger.error("Orchestrator Plan validation failed: %s", exc)
            return {
                "is_out_of_scope": True,
                "final_answer": f"Could not construct a valid plan: {exc}",
            }

        logger.info(
            "Orchestrator produced plan with %d tasks (intent=%s, replan=%d).",
            len(plan.tasks), plan.intent, replan_count,
        )
        return {
            "plan": plan,
            "results": {},
            "replan_count": replan_count,
            "is_out_of_scope": False,
        }
