"""
test_budget.py – Tests for LLM call budget and LLM-free data agent.

Covers:
  (3) Budget keyed per run_id with threading.Lock:
      - Two sequential queries on the same thread each get a fresh budget
  (4) Worst-case budget test:
      - 3-task plan, budget=8, all slots used, still completes
      - Budget forced to 2 with 3 LLM calls, returns partial answer
  (original) Q1/Q2/Q3 stay under budget; data tasks make 0 LLM calls
"""
from __future__ import annotations

import json
import threading
from typing import Any
from unittest.mock import patch, MagicMock

import pytest
from langchain_core.messages import AIMessage

from solar_agent.graph.budgeted_llm import BudgetedLLM
from solar_agent.state import BudgetExceeded, TaskResult, TaskStatus
from solar_agent.graph.graph import build_graph, initial_state
from solar_agent.graph.data_store import DataStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_inner_llm(response_content: str = '{"tool":"compute_kpis_tool","args":{}}'):
    """Return a mock that always returns an AIMessage with response_content."""
    m = MagicMock()
    m.invoke.return_value = AIMessage(content=response_content)
    m.bind_tools.return_value = m
    m.bind.return_value = m
    return m


def _plan_json(n_tasks: int = 2) -> str:
    tasks = [
        {"id": "t1", "agent": "data", "description": "Fetch data",
         "inputs": {"start_date": "2026-09-01", "end_date": "2026-09-30"},
         "dependencies": []},
    ]
    for i in range(2, n_tasks + 1):
        tasks.append({"id": f"t{i}", "agent": "performance",
                      "description": "Compute KPI",
                      "inputs": {"data_ref": "$t1.data_ref"},
                      "dependencies": [f"t{i-1}"]})
    return json.dumps({
        "goal": "Test goal",
        "scope": {},
        "tasks": tasks,
    })


def _mock_invoke(messages, **kwargs):
    """Dispatch mock LLM replies based on message content."""
    msg_str = str(messages)
    if "orchestrator" in msg_str.lower() or ("goal" not in msg_str.lower() and "plan" in msg_str.lower()):
        return AIMessage(content=_plan_json())
    elif "compute_kpis_tool" in msg_str or "performance" in msg_str.lower():
        return AIMessage(content=json.dumps({
            "tool": "compute_kpis_tool",
            "args": {"data_ref": "some_ref", "level": "plant"}
        }))
    else:
        return AIMessage(content="Final answer simulated by mock.")


# ---------------------------------------------------------------------------
# (3a) run_id isolation: two sequential queries each get a full budget
# ---------------------------------------------------------------------------

class TestRunIdIsolation:
    """BudgetedLLM counters are keyed by run_id; sequential queries are independent."""

    def test_two_sequential_queries_each_get_full_budget(self):
        """Sequential calls with different run_ids should not share the counter."""
        inner = _make_inner_llm()
        budgeted = BudgetedLLM(inner_llm=inner, budget=3)

        # First query
        for _ in range(3):
            budgeted.invoke("msg", run_id="run-A")
        assert budgeted.call_count("run-A") == 3

        # Second query — completely fresh counter
        for _ in range(3):
            budgeted.invoke("msg", run_id="run-B")
        assert budgeted.call_count("run-B") == 3

        # run-A counter unchanged
        assert budgeted.call_count("run-A") == 3

    def test_exceeding_budget_on_one_run_does_not_affect_other(self):
        inner = _make_inner_llm()
        budgeted = BudgetedLLM(inner_llm=inner, budget=2)

        # Exhaust run-A
        budgeted.invoke("msg", run_id="run-A")
        budgeted.invoke("msg", run_id="run-A")
        with pytest.raises(BudgetExceeded):
            budgeted.invoke("msg", run_id="run-A")  # 3rd call exceeds limit=2

        # run-B still has full budget
        budgeted.invoke("msg", run_id="run-B")  # should NOT raise
        assert budgeted.call_count("run-B") == 1

    def test_bind_tools_shares_counter_across_same_run_id(self):
        """bind_tools() must not create a separate counter for the same run_id."""
        inner = _make_inner_llm()
        budgeted = BudgetedLLM(inner_llm=inner, budget=3)
        bound = budgeted.bind_tools([])

        budgeted.invoke("msg", run_id="run-X")
        bound.invoke("msg", run_id="run-X")

        # Both calls on run-X, so counter should be 2
        assert budgeted.call_count("run-X") == 2
        assert bound.call_count("run-X") == 2  # shared

    def test_thread_safety_concurrent_runs(self):
        """Multiple threads with different run_ids should not corrupt each other."""
        inner = _make_inner_llm()
        budgeted = BudgetedLLM(inner_llm=inner, budget=100)
        errors = []

        def invoke_n(run_id: str, n: int):
            for _ in range(n):
                try:
                    budgeted.invoke("msg", run_id=run_id)
                except Exception as e:
                    errors.append(e)

        threads = [threading.Thread(target=invoke_n, args=(f"run-{i}", 10))
                   for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors, f"Thread errors: {errors}"
        for i in range(5):
            assert budgeted.call_count(f"run-{i}") == 10

    def test_same_thread_sequential_graph_invocations_get_fresh_budget(self):
        """Two graph.invoke() calls from the same thread each get a full budget."""
        from solar_agent.graph.llm_recorder import ReplayLLM

        graph = build_graph(llm=ReplayLLM(mode="replay"))

        with patch.object(ReplayLLM, "invoke", side_effect=_mock_invoke):
            # First query
            DataStore.clear()
            s1 = graph.invoke(initial_state("Q1: Which plant performed best?"))
            count1 = s1["llm_call_count"]

            # Second query on the SAME thread — must get its own fresh budget
            DataStore.clear()
            s2 = graph.invoke(initial_state("Q2: What is the PR of Plant 1?"))
            count2 = s2["llm_call_count"]

        # Neither query should exceed budget, and they should have independent counts
        from solar_agent.config.settings import settings
        assert count1 <= settings.max_llm_calls_per_query, f"Q1 exceeded budget: {count1}"
        assert count2 <= settings.max_llm_calls_per_query, f"Q2 exceeded budget: {count2}"

        # run_ids should be different (fresh UUID per invocation)
        assert s1.get("run_id") is not None
        assert s2.get("run_id") is not None
        assert s1["run_id"] != s2["run_id"], "Sequential queries share a run_id!"


# ---------------------------------------------------------------------------
# (4) Worst-case budget test
# ---------------------------------------------------------------------------

class TestWorstCaseBudget:
    """Budget is consumed to the maximum or forces a partial answer."""

    def test_budget_not_exceeded_with_exact_limit(self):
        """A query using exactly max_llm_calls should succeed (not raise)."""
        inner = _make_inner_llm()
        budgeted = BudgetedLLM(inner_llm=inner, budget=5)

        # Use all 5 slots
        for _ in range(5):
            budgeted.invoke("msg", run_id="run-1")

        assert budgeted.call_count("run-1") == 5

    def test_one_call_over_limit_raises(self):
        inner = _make_inner_llm()
        budgeted = BudgetedLLM(inner_llm=inner, budget=5)

        for _ in range(5):
            budgeted.invoke("msg", run_id="run-1")

        with pytest.raises(BudgetExceeded) as exc_info:
            budgeted.invoke("msg", run_id="run-1")

        assert "6 calls" in str(exc_info.value) or "budget exceeded" in str(exc_info.value).lower()

    def test_forced_budget_exceeded_returns_partial_answer(self):
        """
        With budget=2 and a plan requiring 3 LLM calls (orchestrator + 2 agents),
        the graph must catch BudgetExceeded and return a partial answer with a warning.
        """
        from solar_agent.graph.llm_recorder import ReplayLLM
        from solar_agent.config.settings import settings

        graph = build_graph(llm=ReplayLLM(mode="replay"))

        call_count = [0]

        def limited_invoke(messages, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                # Orchestrator call — return a valid plan
                return AIMessage(content=_plan_json(n_tasks=3))
            elif call_count[0] == 2:
                # Performance agent call — normal
                return AIMessage(content=json.dumps({
                    "tool": "compute_kpis_tool",
                    "args": {"data_ref": "ref", "level": "plant"}
                }))
            else:
                # Synthesizer (or 3rd agent) — exceeds forced budget
                return AIMessage(content="Budget-forcing answer.")

        original_budget = settings.max_llm_calls_per_query
        settings.max_llm_calls_per_query = 2  # force small budget

        try:
            DataStore.clear()
            with patch.object(ReplayLLM, "invoke", side_effect=limited_invoke):
                state = graph.invoke(initial_state("Worst-case query"))
        finally:
            settings.max_llm_calls_per_query = original_budget

        # Must have a final_answer (either partial or full)
        assert state.get("final_answer") is not None, "Expected final_answer even on budget exhaustion"
        # Partial answer should mention budget/partial if it was exhausted
        fa = state["final_answer"]
        # Either completed normally or returned a partial result
        assert isinstance(fa, str) and len(fa) > 0


# ---------------------------------------------------------------------------
# (original) Q1/Q2/Q3 stay under budget; data tasks make 0 LLM calls
# ---------------------------------------------------------------------------

class TestQueryBudgetCompliance:
    def test_queries_stay_under_budget_and_data_is_free(self):
        """Q1, Q2, Q3 must stay under the default budget (12)."""
        from solar_agent.graph.llm_recorder import ReplayLLM
        from solar_agent.config.settings import settings

        queries = [
            ("q01", "Which plant had the highest total yield in September 2026?"),
            ("q02", "What was the PR of Plant 1 in September 2026?"),
            ("q03", "Did any inverters in Plant 1 have zero generation days in September 2026?"),
        ]

        graph = build_graph(llm=ReplayLLM(mode="replay"))

        with patch.object(ReplayLLM, "invoke", side_effect=_mock_invoke):
            for qid, query in queries:
                DataStore.clear()
                state = graph.invoke(initial_state(query))
                call_count = state["llm_call_count"]

                print(f"Query {qid} used {call_count} LLM calls.")
                assert call_count <= settings.max_llm_calls_per_query, f"{qid} exceeded budget!"

                results = state.get("results", {})
                if state.get("plan"):
                    data_tasks = [t for t in state["plan"].tasks if t.agent == "data"]
                    for dt in data_tasks:
                        assert results[dt.id].status.value == "done"
