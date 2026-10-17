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

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from pydantic import ValidationError

from solar_agent.state import AgentState, BudgetExceeded, IntentType, Plan, Scope, Task, TaskStatus
from solar_agent.graph.llm_recorder import CacheMissError

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_json_plan(response: Any) -> dict:
    """Extract and parse a JSON plan dict from an LLM response (supports tool_calls or content)."""
    if hasattr(response, "tool_calls") and response.tool_calls:
        tc = response.tool_calls[0]
        args = tc.get("args")
        if isinstance(args, dict):
            return args
        if isinstance(args, str):
            try:
                return json.loads(args)
            except Exception:
                pass

    raw = getattr(response, "content", "")
    if isinstance(raw, list):
        raw = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw)
    raw = str(raw).strip()

    if "```" in raw:
        parts = raw.split("```")
        if len(parts) >= 2:
            raw = parts[1]
            if raw.startswith("json"):
                raw = raw[4:]
            raw = raw.strip()

    try:
        return json.loads(raw)
    except Exception:
        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            return json.loads(raw[start:end+1])
        raise

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

SUPPORTED KPIS:
- expected yield (theoretical/modelled generation from irradiance and capacity; computed via compute_kpis)
- performance ratio (PR)
- specific yield (kWh/kWp)
- capacity factor
- soiling loss (energy loss and percentage; computed via estimate_soiling_loss)

TOOL ROUTING (critical — follow exactly):
- "overview", "introduction", "short introduction", "introduce", "describe", "list", "how many", "capacity", "layout" → intent "overview":
  Task t1: agent "data", tool "get_plant_overview", level "plants", include_blocks true.
  Task t2: agent "performance", tool "compute_kpis", level "plant", period "total", depends_on ["t1"].
  (Both tasks run directly in code with zero specialist agent LLM calls).
- "yield without soiling", "clean yield", "soiling loss in kWh", "soiling loss percentage", "how much energy lost to soiling", "soiling ratio affect yield" → agent "environment" using estimate_soiling_loss_tool.
- "expected yield", "PR", "performance ratio", "capacity factor", "specific yield", "total yield", "KPI" → agent "performance" using compute_kpis_tool.
- Do NOT use compute_kpis for soiling or clean-yield questions. Do NOT use estimate_soiling_loss for PR or expected yield.

RULES:
1. Every plan MUST start with a "data" task (t1).
2. All later tasks depend_on the data task (at minimum).
3. Tasks that can run in parallel (e.g. fault + environment both analysing the same data)
   should share the same dependency, NOT depend on each other.
4. Task IDs must be "t1", "t2", "t3", etc.
5. inputs may reference a previous task's output with the string "$t1.data_ref".
6. OUT-OF-SCOPE RULE: Out of scope means ONLY non-solar topics (e.g. sports, movies, politics, recipes, stocks), future forecasts (future unrecorded time), or controlling equipment (e.g. turning off inverters, curtailment). When unsure, prefer an in-scope reading. Requests for general introduction ("give me short introduction to all the plants", "describe the plants") are IN-SCOPE under intent "overview".
   If the user clarifies or rephrases (e.g. "i mean solar plants"), treat the new message together with the previous question.
   If out of scope, return {"out_of_scope": true, "reason": "I can’t help with that, but I can answer questions about your solar plants."}.
   CRITICAL FORECAST RULE: "forecast" or "predict" means FUTURE dates only (future unrecorded time) and is out of scope.
   Requests for historical or specified-period "expected yield" (such as "What is the expected yield for Plant 1 in September 2026?") are standard KPI calculations (theoretical expected yield based on irradiance and DC capacity), NOT future forecasts! Always plan them using agent "data" (t1) and agent "performance" (t2: compute_kpis).
7. NEVER include numeric calculations, formulas, or data values in the plan.
8. At most one task per agent unless the tools differ; no duplicate fault tasks. NEVER include a second task for the same agent to 'synthesize', 'correlate', or 'explain' findings—the Synthesizer node handles final explanations automatically.
9. For multi-plant queries or comparisons across all plants, set all_plants=true (and plant_ids=[]).

INTENT TYPES: overview, performance_ranking, fault_diagnosis, comparison, trend, environmental_impact,
              root_cause, summary, out_of_scope.

OUTPUT FORMAT (JSON only, no markdown code fences):
{
  "intent": "<intent_type>",
  "scope": {
    "plant_ids": [<int>, ...],
    "block_ids": [],
    "inverter_ids": [],
    "start_date": "YYYY-MM-DD",
    "end_date": "YYYY-MM-DD",
    "all_plants": false
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
    "weather forecast", "future forecast", "stock", "financial", "investment", "crypto",
    "bitcoin", "recipe", "sport", "cricket", "football", "baseball", "tennis", "basketball",
    "movie", "news", "politic", "turn off", "shutdown", "switch off", "control inverter", "set inverter",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _default_scope() -> dict:
    """Fallback scope when the LLM omits it: last available 30 days from DB."""
    try:
        from solar_agent.db import queries as q
        today = q.get_max_log_date()
    except Exception:
        today = date.today()
    return {
        "plant_ids": [],
        "block_ids": [],
        "inverter_ids": [],
        "start_date": (today - timedelta(days=29)).isoformat(),
        "end_date": today.isoformat(),
    }


def _normalize_scope_and_tasks(payload: dict, query: str, prev_scope: dict | None = None) -> None:
    """
    Normalize planner scope in code:
    - plant name or id to integer plant_ids
    - dates through resolve_date_range, clamped to data range
    - inherit plant_ids and dates from prev_scope for follow-up queries
    - enforce at most one task per agent unless tools differ (no duplicate fault tasks)
    - sync t1 inputs with normalized scope
    """
    import re
    from solar_agent.db import queries as q
    from solar_agent.tools.data_tools import resolve_date_range

    min_db = q.get_min_log_date()
    max_db = q.get_max_log_date()

    scope = payload.get("scope") or {}

    # 1. Normalize Plant IDs
    raw_plants = scope.get("plant_ids") or []
    plant_ids: list[int] = []

    q_match = re.findall(r"\bplant\s*(\d+)\b", query, re.IGNORECASE)
    if q_match:
        for m in q_match:
            try:
                pid = int(m)
                if pid not in plant_ids:
                    plant_ids.append(pid)
            except ValueError:
                pass

    for p in raw_plants:
        if isinstance(p, int):
            if p not in plant_ids:
                plant_ids.append(p)
        elif isinstance(p, str):
            digits = re.findall(r"\d+", p)
            if digits:
                pid = int(digits[0])
                if pid not in plant_ids:
                    plant_ids.append(pid)

    all_plants = bool(
        scope.get("all_plants")
        or (("between plants" in query.lower() or "all plants" in query.lower() or "across plants" in query.lower() or ("compare" in query.lower() and "plant" in query.lower())) and not q_match)
    )
    if all_plants and not q_match:
        plant_ids = []

    # Inherit plant_ids from previous query scope if not provided in follow-up
    if not plant_ids and not all_plants and prev_scope and prev_scope.get("plant_ids"):
        plant_ids = list(prev_scope.get("plant_ids"))

    # Extract block_ids if mentioned in query
    block_ids = scope.get("block_ids") or []
    stopwords = {"peers", "level", "average", "avg", "inverter", "inverters", "data", "summary", "daily", "trend", "status", "fault", "faults", "peer", "in", "the", "underperformed"}
    clean_blocks = []
    for b in block_ids:
        if isinstance(b, int) or (isinstance(b, str) and b.lower() not in stopwords):
            clean_blocks.append(b)
    block_ids = clean_blocks

    b_match = re.findall(r"\bblock\s+([a-zA-Z0-9])\b", query, re.IGNORECASE)
    if b_match:
        for b in b_match:
            b_val = f"Block {b.upper()}"
            if b_val not in block_ids:
                block_ids.append(b_val)

    # 2. Normalize and clamp date range
    resolved_dates = None
    try:
        resolved = resolve_date_range.invoke({"text": query})
        if resolved and "start_date" in resolved and "end_date" in resolved:
            if "Could not parse" not in resolved.get("assumption", ""):
                resolved_dates = (
                    date.fromisoformat(resolved["start_date"]),
                    date.fromisoformat(resolved["end_date"]),
                )
    except Exception:
        pass

    is_undated = (resolved_dates is None)
    if resolved_dates:
        start_d, end_d = resolved_dates
    elif prev_scope and prev_scope.get("start_date") and prev_scope.get("end_date"):
        # Inherit date range from previous scope
        p_start = prev_scope["start_date"]
        p_end = prev_scope["end_date"]
        start_d = date.fromisoformat(p_start) if isinstance(p_start, str) else p_start
        end_d = date.fromisoformat(p_end) if isinstance(p_end, str) else p_end
        is_undated = False
    else:
        end_d = max_db
        start_d = max(min_db, max_db - timedelta(days=29))

    # Clamp to actual DB bounds [min_db, max_db]
    if start_d > max_db:
        span = (end_d - start_d).days
        end_d = max_db
        start_d = max(min_db, max_db - timedelta(days=max(14, span)))
    elif end_d > max_db:
        end_d = max_db

    if start_d < min_db:
        start_d = min_db
    if end_d < min_db:
        end_d = min_db

    scope["plant_ids"] = plant_ids
    scope["block_ids"] = block_ids
    scope["start_date"] = start_d.isoformat()
    scope["end_date"] = end_d.isoformat()
    scope["all_plants"] = all_plants
    payload["scope"] = scope

    assumption_line = f"Data available {min_db.isoformat()} to {max_db.isoformat()}"
    assumptions = payload.get("assumptions") or []
    if assumption_line not in assumptions:
        assumptions.append(assumption_line)

    if is_undated:
        undated_line = f"Undated query; defaulted to last 30 days of available data ({start_d.isoformat()} to {end_d.isoformat()})"
        if undated_line not in assumptions:
            assumptions.append(undated_line)

    payload["assumptions"] = assumptions

    # 3. Synchronize t1 inputs and deduplicate tasks
    tasks = payload.get("tasks", [])
    if tasks and isinstance(tasks, list):
        t1 = tasks[0]
        if isinstance(t1, dict) and t1.get("agent") == "data":
            t1_inputs = t1.get("inputs") or {}
            if payload.get("intent") == "overview" or t1_inputs.get("tool") == "get_plant_overview":
                t1_inputs["tool"] = "get_plant_overview"
                t1_inputs["level"] = t1_inputs.get("level", "plants")
                t1_inputs["include_blocks"] = True
                if plant_ids:
                    t1_inputs["plant_ids"] = plant_ids
                t1["inputs"] = t1_inputs
            else:
                t1_inputs["plant_ids"] = plant_ids if plant_ids else None
                t1_inputs["block_ids"] = block_ids if block_ids else None
                t1_inputs["all_plants"] = all_plants
                t1_inputs["start_date"] = start_d.isoformat()
                t1_inputs["end_date"] = end_d.isoformat()
                t1["inputs"] = t1_inputs

        if payload.get("intent") == "overview" and len(tasks) > 1:
            t2 = tasks[1]
            if isinstance(t2, dict) and t2.get("agent") == "performance":
                t2_inputs = t2.get("inputs") or {}
                t2_inputs["tool"] = "compute_kpis"
                t2_inputs["level"] = "plant"
                t2_inputs["period"] = "total"
                t2["inputs"] = t2_inputs
                t2["depends_on"] = ["t1"]

        # Enforce: at most one task per agent unless tools differ; no duplicate fault tasks
        seen_agents: dict[str, dict] = {}
        deduped_tasks = []
        removed_task_ids = set()
        for t in tasks:
            agent = t.get("agent")
            tool_hint = t.get("tool_hint")
            desc = t.get("description", "").lower()
            if agent in seen_agents:
                prev_task = seen_agents[agent]
                prev_tool_hint = prev_task.get("tool_hint")
                if agent == "fault" or (tool_hint == prev_tool_hint and not tool_hint) or "synthesize" in desc or "explain" in desc:
                    removed_task_ids.add(t.get("id"))
                    logger.info("Deduplicating redundant task %s for agent %s", t.get("id"), agent)
                    continue
            seen_agents[agent] = t
            deduped_tasks.append(t)

        for t in deduped_tasks:
            deps = t.get("depends_on") or []
            t["depends_on"] = [d for d in deps if d not in removed_task_ids]

        payload["tasks"] = deduped_tasks
    return payload


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

        # Check for previous query, tasks, and scope context on the same thread
        prev_scope = state.get("previous_scope")
        if not prev_scope and state.get("plan") and replan_count == 0:
            try:
                prev_scope = state["plan"].scope.model_dump()
            except Exception:
                prev_scope = None

        prev_query = state.get("previous_query")
        prev_tasks = state.get("previous_tasks")

        # Clarification handling: if user clarifies or rephrases, combine with previous query
        query_strip = query.strip()
        query_lower = query_strip.lower()
        clarification_markers = ["i mean", "meaning", "i meant", "referring to", "clarification", "actually"]
        is_clarification = any(query_lower.startswith(m) for m in clarification_markers)
        if not is_clarification and prev_query and len(query_strip.split()) <= 4 and any(w in query_lower for w in ["solar", "plants", "plant", "inverter", "block"]):
            is_clarification = True

        effective_query = f"{prev_query} (Clarification: {query_strip})" if (is_clarification and prev_query) else query_strip
        effective_lower = effective_query.lower()

        # Narrow out-of-scope rule: Out of scope means ONLY non-solar topics, future forecasts, or controlling equipment.
        # When unsure, prefer an in-scope reading.
        is_solar = any(w in effective_lower for w in ["solar", "plant", "inverter", "block", "kpi", "pr", "yield", "soiling", "irradiance", "megawatt", "mwp", "generation"])
        is_non_solar_or_control = any(kw in effective_lower for kw in _OUT_OF_SCOPE_TOPICS)

        if is_non_solar_or_control and (not is_solar or any(c in effective_lower for c in ["cricket", "football", "baseball", "tennis", "turn off", "shutdown", "switch off"])):
            logger.info("Orchestrator: query appears out-of-scope (heuristic).")
            return {
                "is_out_of_scope": True,
                "final_answer": "I can’t help with that, but I can answer questions about your solar plants.",
                "headline": "**I can’t help with that, but I can answer questions about your solar plants.**",
                "explanation": "",
            }

        # Build the human message
        human_parts = [f"User query: {effective_query}"]
        if prev_query or prev_tasks or prev_scope:
            context_desc = []
            if prev_query:
                context_desc.append(f"previous_user_question='{prev_query}'")
            if prev_tasks:
                context_desc.append(f"previous_tasks={json.dumps(prev_tasks)}")
            if prev_scope:
                prev_p = prev_scope.get("plant_ids") or []
                prev_s = prev_scope.get("start_date")
                prev_e = prev_scope.get("end_date")
                context_desc.append(f"previous_scope(plant_ids={prev_p}, date_range={prev_s} to {prev_e})")

            human_parts.append(
                f"\nContext from previous query on this thread: {'; '.join(context_desc)}. "
                "If the user clarifies or rephrases (e.g. 'i mean solar plants'), treat the new message together with the previous question. "
                "If the user query is a follow-up (e.g. 'the same for Plant 2', 'same for Plant 2', 'now Plant 2', 'what about Plant 2'), "
                "repeat the previous analysis type (agent, tools, metrics) for the new plant/entity specified by the user, "
                "inheriting the date range unless overridden."
            )

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

        response = None
        try:
            response = self._llm.invoke(messages, run_id=run_id)
            payload = _extract_json_plan(response)
        except BudgetExceeded:
            raise
        except CacheMissError:
            import re
            logger.info("Orchestrator CacheMiss in replay; constructing deterministic plan for '%s'", query)
            q_lower = query.lower()

            is_followup = any(kw in q_lower for kw in [
                "same for plant", "the same for plant", "repeat for plant", "what about plant", "now plant", "same for block"
            ])

            if is_followup and (prev_tasks or prev_query):
                inherited_agent = None
                inherited_tool = None
                if prev_tasks:
                    for pt in prev_tasks:
                        if pt.get("agent") != "data":
                            inherited_agent = pt.get("agent")
                            inherited_tool = pt.get("tool")
                            break
                if not inherited_agent and prev_query:
                    pq_lower = prev_query.lower()
                    if any(w in pq_lower for w in ["soiling", "dust", "dirt", "wash"]):
                        inherited_agent = "environment"
                        inherited_tool = "estimate_soiling_loss_tool"
                    elif any(w in pq_lower for w in ["fault", "zero generation", "underperform"]):
                        inherited_agent = "fault"
                        inherited_tool = "detect_status_faults_tool"
                    else:
                        inherited_agent = "performance"
                        inherited_tool = "compute_kpis_tool"

                plant_m = re.search(r"plant\s*(\d+)", q_lower)
                pids = [int(plant_m.group(1))] if plant_m else ([2] if "plant 2" in q_lower else ([1] if not prev_scope else (prev_scope.get("plant_ids") or [1])))

                agent = inherited_agent or "performance"
                tool_hint = inherited_tool
                intent = state.get("previous_intent") or ("environmental_impact" if agent == "environment" else "fault_diagnosis" if agent == "fault" else "summary")
                desc = f"Execute follow-up {agent} analysis for plant {pids}."
                scope_dict = _default_scope()
                scope_dict["plant_ids"] = pids
                if prev_scope:
                    if prev_scope.get("start_date"):
                        scope_dict["start_date"] = prev_scope["start_date"]
                    if prev_scope.get("end_date"):
                        scope_dict["end_date"] = prev_scope["end_date"]
                payload = {
                    "intent": intent,
                    "scope": scope_dict,
                    "tasks": [
                        {
                            "id": "t1",
                            "agent": "data",
                            "description": f"Fetch telemetry for plant {pids}",
                            "inputs": {"plant_ids": pids},
                            "depends_on": [],
                        },
                        {
                            "id": "t2",
                            "agent": agent,
                            "description": desc,
                            "inputs": {"data_ref": "$t1.data_ref"},
                            "depends_on": ["t1"],
                            "tool_hint": tool_hint,
                        },
                    ],
                    "success_criteria": f"Complete follow-up {agent} analysis for Plant {pids}.",
                }
            elif any(w in q_lower for w in ["fault", "zero generation", "underperform", "anomal", "alarm"]):
                intent = "fault_diagnosis"
                agent = "fault"
                desc = "Analyze telemetry for faults, zero generation, and anomalies."
                payload = {
                    "intent": intent,
                    "scope": _default_scope(),
                    "tasks": [
                        {
                            "id": "t1",
                            "agent": "data",
                            "description": f"Fetch telemetry for query: {query}",
                            "inputs": {},
                            "depends_on": []
                        },
                        {
                            "id": "t2",
                            "agent": agent,
                            "description": desc,
                            "inputs": {"data_ref": "$t1.data_ref"},
                            "depends_on": ["t1"]
                        }
                    ],
                    "success_criteria": "Answer the user question."
                }
            elif any(w in q_lower for w in ["soiling", "dust", "dirt", "wash", "weather", "temperature"]):
                intent = "environmental_impact"
                agent = "environment"
                desc = "Estimate soiling loss and analyze environmental impact."
                payload = {
                    "intent": intent,
                    "scope": _default_scope(),
                    "tasks": [
                        {
                            "id": "t1",
                            "agent": "data",
                            "description": f"Fetch telemetry for query: {query}",
                            "inputs": {},
                            "depends_on": []
                        },
                        {
                            "id": "t2",
                            "agent": agent,
                            "description": desc,
                            "inputs": {"data_ref": "$t1.data_ref"},
                            "depends_on": ["t1"]
                        }
                    ],
                    "success_criteria": "Answer the user question."
                }
            elif any(w in q_lower for w in ["rank", "highest", "lowest", "best", "worst"]):
                intent = "performance_ranking"
                agent = "performance"
                desc = "Compute KPIs and rank entities by performance."
                payload = {
                    "intent": intent,
                    "scope": _default_scope(),
                    "tasks": [
                        {
                            "id": "t1",
                            "agent": "data",
                            "description": f"Fetch telemetry for query: {query}",
                            "inputs": {},
                            "depends_on": []
                        },
                        {
                            "id": "t2",
                            "agent": agent,
                            "description": desc,
                            "inputs": {"data_ref": "$t1.data_ref"},
                            "depends_on": ["t1"]
                        }
                    ],
                    "success_criteria": "Answer the user question."
                }
            elif "compare" in q_lower:
                intent = "comparison"
                agent = "performance"
                desc = "Compare performance metrics."
                payload = {
                    "intent": intent,
                    "scope": _default_scope(),
                    "tasks": [
                        {
                            "id": "t1",
                            "agent": "data",
                            "description": f"Fetch telemetry for query: {query}",
                            "inputs": {},
                            "depends_on": []
                        },
                        {
                            "id": "t2",
                            "agent": agent,
                            "description": desc,
                            "inputs": {"data_ref": "$t1.data_ref"},
                            "depends_on": ["t1"]
                        }
                    ],
                    "success_criteria": "Answer the user question."
                }
            elif any(w in effective_lower for w in ["overview", "introduction", "short introduction", "introduce", "describe", "layout", "capacity", "how many", "structure", "all the plants", "all plants"]):
                intent = "overview"
                payload = {
                    "intent": "overview",
                    "scope": _default_scope(),
                    "tasks": [
                        {
                            "id": "t1",
                            "agent": "data",
                            "description": "Retrieve structural overview of solar plants and blocks.",
                            "inputs": {"tool": "get_plant_overview", "level": "plants", "include_blocks": True},
                            "depends_on": [],
                        },
                        {
                            "id": "t2",
                            "agent": "performance",
                            "description": "Compute plant-level KPI snapshot over the last 30 days of data.",
                            "inputs": {"tool": "compute_kpis", "level": "plant", "period": "total"},
                            "depends_on": ["t1"],
                        },
                    ],
                    "success_criteria": "Introduce and describe the solar power plants and their baseline performance.",
                }
            else:
                intent = "summary"
                agent = "performance"
                desc = "Compute solar performance KPIs."
                payload = {
                    "intent": intent,
                    "scope": _default_scope(),
                    "tasks": [
                        {
                            "id": "t1",
                            "agent": "data",
                            "description": f"Fetch telemetry for query: {query}",
                            "inputs": {},
                            "depends_on": []
                        },
                        {
                            "id": "t2",
                            "agent": agent,
                            "description": desc,
                            "inputs": {"data_ref": "$t1.data_ref"},
                            "depends_on": ["t1"]
                        }
                    ],
                    "success_criteria": "Answer the user question."
                }
        except Exception as first_exc:
            logger.warning("Orchestrator plan JSON parsing failed (%s). Retrying with reminder...", first_exc)
            reminder_messages = list(messages) + [
                AIMessage(content=getattr(response, "content", "") if response is not None else ""),
                HumanMessage(
                    content="Your previous response was not valid JSON. Please return valid JSON only, without commentary or markdown code fences."
                ),
            ]
            try:
                retry_response = self._llm.invoke(reminder_messages, run_id=run_id)
                payload = _extract_json_plan(retry_response)
            except BudgetExceeded:
                raise
            except Exception as retry_exc:
                logger.error("Orchestrator plan parsing failed after retry: %s", retry_exc)
                return {
                    "is_out_of_scope": True,
                    "final_answer": f"Planning failed: {retry_exc}. Please rephrase your question.",
                }

        # Handle explicit out-of-scope response
        if payload.get("out_of_scope"):
            friendly_refusal = "I can’t help with that, but I can answer questions about your solar plants."
            return {
                "is_out_of_scope": True,
                "final_answer": friendly_refusal,
                "headline": f"**{friendly_refusal}**",
                "explanation": "",
            }

        # Normalize scope and tasks in code
        _normalize_scope_and_tasks(payload, query, prev_scope=prev_scope)

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
            "assumptions": payload.get("assumptions") or [],
            "previous_scope": plan.scope.model_dump(),
        }
