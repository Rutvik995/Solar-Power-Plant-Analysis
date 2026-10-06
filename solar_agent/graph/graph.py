"""
graph/graph.py – LangGraph StateGraph wiring.

Assembles all nodes into the full multi-agent pipeline:

  [orchestrator] → [executor] (loop until all done) → [validator]
       ↑                                                     |
       └─────────────── REPLAN ──────────────────────────────┤
                                                             |
                                                   OK / FAIL ↓
                                               [synthesizer] → END

LLM call budget
───────────────
Every invocation wraps the LLM in a BudgetedLLM (settings.max_llm_calls_per_query,
default 12). If budget is exceeded during any node the graph catches BudgetExceeded,
writes a partial answer from completed tasks, and ends immediately with a warning.

Call count is reported in the final state as `llm_call_count`.

Persistence:
  build_graph() optionally accepts a PostgresSaver checkpointer.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from typing import Any

from langgraph.graph import END, StateGraph

from solar_agent.config.settings import settings
from solar_agent.graph.budgeted_llm import BudgetedLLM
from solar_agent.graph.executor import Executor
from solar_agent.graph.orchestrator import Orchestrator
from solar_agent.graph.synthesizer import Synthesizer
from solar_agent.graph.validator import Validator
from solar_agent.state import AgentState, BudgetExceeded, ValidationStatus

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Postgres checkpointer factory
# ---------------------------------------------------------------------------

@contextmanager
def get_checkpointer():
    try:
        from langgraph.checkpoint.postgres import PostgresSaver  # type: ignore[import]
        db_url = settings.database_url.replace("+psycopg", "")
        with PostgresSaver.from_conn_string(db_url) as saver:
            saver.setup()
            logger.info("PostgresSaver checkpointer initialised.")
            yield saver
    except ImportError:
        logger.warning("langgraph-checkpoint-postgres not installed. Falling back to MemorySaver.")
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import]
        yield MemorySaver()
    except Exception as exc:
        logger.error("PostgresSaver failed (%s). Falling back to MemorySaver.", exc)
        from langgraph.checkpoint.memory import MemorySaver  # type: ignore[import]
        yield MemorySaver()


# ---------------------------------------------------------------------------
# Budget-exceeded partial answer helper
# ---------------------------------------------------------------------------

def _partial_answer(state: AgentState, exc: BudgetExceeded) -> dict:
    """Build a partial final_answer from whichever tasks completed before budget ran out."""
    results = getattr(exc, "partial_results", state.get("results") or {})
    lines = [
        f"⚠ LLM call budget exceeded ({exc}). Partial results from completed tasks:"
    ]
    for task_id, tr in results.items():
        if tr and tr.status.value == "done":
            lines.append(f"\n**[{task_id}]** {tr.summary or '(no summary)'}")
            if tr.metrics:
                for k, v in tr.metrics.items():
                    lines.append(f"  - {k}: {v}")
    if len(lines) == 1:
        lines.append("  No tasks completed before the budget was exhausted.")
    return {
        "final_answer": "\n".join(lines),
        "results": results,
        "llm_call_count": state.get("llm_call_count", 0),
    }


# ---------------------------------------------------------------------------
# Routing helpers
# ---------------------------------------------------------------------------

def _route_after_orchestrator(state: AgentState) -> str:
    if state.get("is_out_of_scope") or state.get("plan") is None:
        return END
    return "executor"


def _route_after_executor(state: AgentState) -> str:
    if state.get("final_answer"):
        return END
    if Executor.all_done(state):
        return "validator"
    return "executor"


def _route_after_validator(state: AgentState) -> str:
    return Validator.route(state)


# ---------------------------------------------------------------------------
# Graph builder
# ---------------------------------------------------------------------------

def build_graph(llm: Any = None, checkpointer=None, mode: str = "live", cache_name: str = "llm_cache.json"):
    """
    Construct and compile the full LangGraph StateGraph.

    Each call to graph.invoke() gets a fresh run_id (UUID) stored in state.
    BudgetedLLM counters are keyed by run_id, so sequential or concurrent
    queries never share a budget counter.

    Args:
        llm:          Optional override. If provided, used for all roles (for tests).
        checkpointer: Optional LangGraph checkpointer (e.g. PostgresSaver).
        mode:         'live' or 'replay' or 'record'.
        cache_name:   Filename for the replay cache.

    Returns:
        A compiled LangGraph runnable.
    """
    import uuid as _uuid
    import threading
    from solar_agent.config.llm import get_llm
    from solar_agent.graph.llm_recorder import ReplayLLM

    # We share ONE budget counter across all agents
    counters: dict[str, int] = {}
    lock = threading.Lock()

    def _build_role(role: str) -> BudgetedLLM:
        raw_llm = llm if llm is not None else get_llm(role)
        # Only wrap in ReplayLLM if it's not already a ReplayLLM (some tests pass one)
        if not isinstance(raw_llm, ReplayLLM):
            recorded = ReplayLLM(fallback_llm=raw_llm, mode=mode, cache_name=cache_name)
        else:
            recorded = raw_llm
            
        return BudgetedLLM(
            inner_llm=recorded,
            budget=settings.max_llm_calls_per_query,
            _counters=counters,
            _lock=lock,
        )

    planner_llm = _build_role("planner")
    agent_llm = _build_role("agent")
    synth_llm = _build_role("synthesizer")

    orchestrator = Orchestrator(llm=planner_llm)
    executor = Executor(llm=agent_llm)
    validator = Validator()
    synthesizer = Synthesizer(llm=synth_llm)

    def _run_id(state: AgentState) -> str:
        """Return the per-invocation run_id stored in state."""
        return state.get("run_id") or "__default__"

    def _get_call_count(state: AgentState) -> int:
        return planner_llm.call_count(_run_id(state))

    def orchestrator_node(state: AgentState) -> dict:
        try:
            patch = orchestrator.plan(state, run_id=_run_id(state))
            if state.get("plan") is not None:
                patch["replan_count"] = state.get("replan_count", 0) + 1
            patch["llm_call_count"] = _get_call_count(state)
            return patch
        except BudgetExceeded as exc:
            logger.error("Budget exceeded in orchestrator: %s", exc)
            return _partial_answer(state, exc)

    def executor_node(state: AgentState) -> dict:
        try:
            patch = executor.run_next_layer(state, run_id=_run_id(state))
            patch["llm_call_count"] = _get_call_count(state)
            return patch
        except BudgetExceeded as exc:
            logger.error("Budget exceeded in executor: %s", exc)
            return _partial_answer(state, exc)

    def validator_node(state: AgentState) -> dict:
        patch = validator.validate(state)
        patch["llm_call_count"] = _get_call_count(state)
        return patch

    def synthesizer_node(state: AgentState) -> dict:
        try:
            patch = synthesizer.synthesize(state, run_id=_run_id(state))
            patch["llm_call_count"] = _get_call_count(state)
            return patch
        except BudgetExceeded as exc:
            logger.error("Budget exceeded in synthesizer: %s", exc)
            return _partial_answer(state, exc)

    def start_node(state: AgentState) -> dict:
        """Assign a fresh run_id at the start of each invocation."""
        if not state.get("run_id"):
            return {"run_id": str(_uuid.uuid4())}
        return {}

    g = StateGraph(AgentState)

    g.add_node("start", start_node)
    g.add_node("orchestrator", orchestrator_node)
    g.add_node("executor", executor_node)
    g.add_node("validator", validator_node)
    g.add_node("synthesizer", synthesizer_node)

    g.set_entry_point("start")
    g.add_edge("start", "orchestrator")

    g.add_conditional_edges("orchestrator", _route_after_orchestrator, {
        "executor": "executor",
        END: END,
    })

    g.add_conditional_edges("executor", _route_after_executor, {
        "executor": "executor",
        "validator": "validator",
        END: END,
    })

    g.add_conditional_edges("validator", _route_after_validator, {
        "orchestrator": "orchestrator",
        "synthesizer": "synthesizer",
    })

    g.add_edge("synthesizer", END)

    compile_kwargs = {}
    if checkpointer is not None:
        compile_kwargs["checkpointer"] = checkpointer

    return g.compile(**compile_kwargs)


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
        llm_call_count=0,
        run_id=None,  # set by start_node at invocation time
    )
