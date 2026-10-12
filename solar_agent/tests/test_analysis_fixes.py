"""
test_analysis_fixes.py – Unit tests for analysis fixes:
  1. Period analysed deterministic line
  2. Undated query default to last 30 days
  3. Relative dates against MAX(log_date)
  4. all_plants flag and unscoped warning
  5. Flagged table grounding
  6. Outlier mention in soiling analysis
  7. Fault anomaly sentence
  8. Sources footer and inline tag stripping
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from langchain_core.messages import AIMessage

from solar_agent.agents.fault_agent import FaultAgent
from solar_agent.graph.data_store import DataStore
from solar_agent.graph.orchestrator import _normalize_scope_and_tasks
from solar_agent.graph.synthesizer import Synthesizer
from solar_agent.graph.validator import _is_grounded, _numbers_from_results
from solar_agent.state import AgentState, IntentType, Plan, Scope, Task, TaskResult, TaskStatus
from solar_agent.tools.data_tools import fetch_inverter_daily_tool, resolve_date_range
from solar_agent.tools.environment_tools import estimate_soiling_loss


def _make_state(query: str = "Test query", plan: Plan | None = None, results: dict | None = None, assumptions: list | None = None) -> AgentState:
    return AgentState(
        user_query=query,
        messages=[],
        plan=plan,
        results=results or {},
        validation=None,
        replan_count=0,
        final_answer=None,
        assumptions=assumptions or [],
        is_out_of_scope=False,
        llm_call_count=0,
        run_id="test-run",
    )


class TestAnalysisFixes:

    def test_period_line_from_datastore_data(self):
        """Synthesizer computes Period analysed line directly from DataStore DataFrame dates."""
        df = pd.DataFrame({
            "inverter_id": ["INV_01", "INV_02"],
            "log_date": ["2026-09-12", "2026-09-26"],
            "performance_ratio": [0.85, 0.86],
        })
        DataStore.store(df, key="df_telemetry_test")

        plan = Plan(
            intent=IntentType.PERFORMANCE_RANKING,
            scope=Scope(plant_ids=[1], start_date=date(2026, 9, 12), end_date=date(2026, 9, 26)),
            tasks=[
                Task(id="t1", agent="data", description="Fetch telemetry"),
                Task(id="t2", agent="performance", description="Compute PR", depends_on=["t1"]),
            ],
            success_criteria="Compute PR",
        )
        results = {
            "t1": TaskResult(task_id="t1", status=TaskStatus.DONE, data_ref="df_telemetry_test"),
            "t2": TaskResult(task_id="t2", status=TaskStatus.DONE, metrics={"performance_ratio": 0.855}),
        }
        state = _make_state(plan=plan, results=results)

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="Plant 1 PR was 0.855 (t2).")
        synth = Synthesizer(llm=mock_llm)
        out = synth.synthesize(state)

        ans = out["final_answer"]
        lines = [line.strip() for line in ans.split("\n") if line.strip()]
        last_line = lines[-1]
        assert last_line == "Period analysed: 2026-09-12 to 2026-09-26 of the data used (15 days)"

    def test_period_line_from_scope_fallback(self):
        """When DataStore has no date columns, Synthesizer falls back to plan.scope dates."""
        plan = Plan(
            intent=IntentType.PERFORMANCE_RANKING,
            scope=Scope(plant_ids=[1], start_date=date(2026, 9, 1), end_date=date(2026, 9, 10)),
            tasks=[Task(id="t1", agent="performance", description="Compute PR")],
            success_criteria="Compute PR",
        )
        results = {
            "t1": TaskResult(task_id="t1", status=TaskStatus.DONE, metrics={"performance_ratio": 0.85}),
        }
        state = _make_state(plan=plan, results=results)

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="Plant 1 PR was 0.85.")
        synth = Synthesizer(llm=mock_llm)
        out = synth.synthesize(state)

        ans = out["final_answer"]
        assert "Period analysed: 2026-09-01 to 2026-09-10 of the data used (10 days)" in ans

    def test_undated_default_uses_last_30_days(self):
        """Undated queries default to the last 30 days of available data."""
        payload = {
            "scope": {"plant_ids": [1]},
            "tasks": [{"id": "t1", "agent": "data", "inputs": {}}],
        }
        _normalize_scope_and_tasks(payload, "Find faulty inverters in Plant 1")

        scope = payload["scope"]
        start_d = date.fromisoformat(scope["start_date"])
        end_d = date.fromisoformat(scope["end_date"])
        assert (end_d - start_d).days == 29  # 30 calendar days inclusive
        assert end_d == date(2026, 9, 26)
        assert start_d == date(2026, 8, 28)

        # Stated in assumptions
        assumptions = payload["assumptions"]
        assert any("Undated query; defaulted to last 30 days" in a for a in assumptions)

    def test_undated_assumption_stated_in_answer(self):
        """The undated default assumption appears in the synthesized answer."""
        plan = Plan(
            intent=IntentType.FAULT_DIAGNOSIS,
            scope=Scope(plant_ids=[1], start_date=date(2026, 8, 28), end_date=date(2026, 9, 26)),
            tasks=[Task(id="t1", agent="fault", description="Fault task")],
            success_criteria="Find faults",
        )
        results = {
            "t1": TaskResult(task_id="t1", status=TaskStatus.DONE, summary="No faults found."),
        }
        undated_line = "Undated query; defaulted to last 30 days of available data (2026-08-28 to 2026-09-26)"
        state = _make_state(plan=plan, results=results, assumptions=[undated_line])

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="No faults detected.")
        synth = Synthesizer(llm=mock_llm)
        out = synth.synthesize(state)

        ans = out["final_answer"]
        assert "Undated query; defaulted to last 30 days of available data" in ans
        assert "Data available" in ans

    def test_keep_data_available_line_in_assumptions(self):
        """'Data available ...' line is preserved deterministically in Assumptions."""
        plan = Plan(
            intent=IntentType.PERFORMANCE_RANKING,
            scope=Scope(plant_ids=[1], start_date=date(2026, 9, 1), end_date=date(2026, 9, 30)),
            tasks=[Task(id="t1", agent="performance", description="Compute plant PR performance")],
            success_criteria="Compute plant PR accurately",
        )
        results = {"t1": TaskResult(task_id="t1", status=TaskStatus.DONE)}
        state = _make_state(plan=plan, results=results, assumptions=[])

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="Plant 1 PR.")
        synth = Synthesizer(llm=mock_llm)
        out = synth.synthesize(state)

        assert "Data available 2026-08-01 to 2026-09-26" in out["final_answer"]

    def test_relative_date_last_month_against_max_log_date(self):
        """'last month' resolves relative to MAX(log_date) from DB (2026-09-26 -> August 2026)."""
        res = resolve_date_range.invoke({"text": "What was the PR last month?"})
        assert res["start_date"] == "2026-08-01"
        assert res["end_date"] == "2026-08-31"

    def test_relative_date_this_week_against_max_log_date(self):
        """'this week' resolves relative to MAX(log_date) from DB (2026-09-26)."""
        res = resolve_date_range.invoke({"text": "Show generation this week"})
        assert res["end_date"] == "2026-09-26"
        # 2026-09-26 is a Saturday (weekday 5); Monday of that week was 2026-09-21
        assert res["start_date"] == "2026-09-21"

    def test_all_plants_flag_true_suppresses_warning(self):
        """When all_plants=True, fetch_inverter_daily_tool does NOT emit unscoped query warning."""
        res = fetch_inverter_daily_tool.invoke({
            "all_plants": True,
            "start_date": "2026-09-01",
            "end_date": "2026-09-05",
        })
        warnings = res.get("warnings") or []
        assert not any("No plant_ids filter applied" in w for w in warnings)

    def test_all_plants_flag_false_emits_warning(self):
        """When all_plants=False and no plant/inverter is specified, unscoped warning is emitted."""
        res = fetch_inverter_daily_tool.invoke({
            "all_plants": False,
            "start_date": "2026-09-01",
            "end_date": "2026-09-05",
        })
        warnings = res.get("warnings") or []
        assert any("No plant_ids filter applied" in w for w in warnings)

    def test_flagged_table_grounding(self):
        """Flagged items table values (PR, status code, peer metrics) are collected for grounding."""
        flagged_items = [
            {
                "inverter_id": "P1_Block_A_INV_02",
                "block": "Block A",
                "date": "2026-09-16",
                "status_code": 2,
                "status_meaning": "Warning: non-critical",
                "pr": 0.4502,
                "peer_pr": 0.8506,
                "peer_irradiance": 6.66,
                "flagging_tool": "find_underperforming_inverters_tool",
            }
        ]
        results = {
            "t1": TaskResult(
                task_id="t1",
                status=TaskStatus.DONE,
                metrics={"flagged_items": flagged_items},
            )
        }
        nums = _numbers_from_results(results)
        assert 2.0 in nums
        assert _is_grounded(0.4502, nums)
        assert _is_grounded(0.8506, nums)
        assert _is_grounded(6.66, nums)

    def test_outlier_mention_in_soiling_analysis(self):
        """estimate_soiling_loss includes min/avg soiling ratio and flags outliers (>2x median)."""
        df = pd.DataFrame({
            "inverter_id": ["INV_01", "INV_02", "INV_03"],
            "plant_id": [1, 2, 3],
            "log_date": ["2026-09-10", "2026-09-10", "2026-09-10"],
            "total_daily_yield_kwh": [1000.0, 1000.0, 1000.0],
            "soiling_ratio": [0.95, 0.90, 0.50],
        })
        DataStore.store(df, key="df_soiling_test")

        res = estimate_soiling_loss("df_soiling_test")
        metrics = res.get("metrics", {})
        assert "plant_1_soiling_ratio_min" in metrics
        assert "plant_1_soiling_ratio_avg" in metrics
        assert "median_soiling_loss_pct" in metrics
        assert "outlier_plants" in metrics

    def test_sources_footer_replaces_inline_tags(self):
        """Synthesizer strips inline (t1), (t2) tags and replaces with a single Sources footer."""
        plan = Plan(
            intent=IntentType.PERFORMANCE_RANKING,
            scope=Scope(plant_ids=[1], start_date=date(2026, 9, 1), end_date=date(2026, 9, 30)),
            tasks=[
                Task(id="t1", agent="data", description="Fetch data"),
                Task(id="t2", agent="performance", description="Compute PR", depends_on=["t1"]),
            ],
            success_criteria="Compute plant PR accurately",
        )
        results = {
            "t1": TaskResult(task_id="t1", status=TaskStatus.DONE),
            "t2": TaskResult(task_id="t2", status=TaskStatus.DONE, metrics={"performance_ratio": 0.8705}),
        }
        state = _make_state(plan=plan, results=results)

        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(
            content="The PR of Plant 1 was 0.8705 (t2). Telemetry was loaded (t1)."
        )
        synth = Synthesizer(llm=mock_llm)
        out = synth.synthesize(state)

        ans = out["final_answer"]
        assert "(t2)" not in ans
        assert "(t1)" not in ans
        assert "Sources: t1, t2" in ans

    def test_fault_anomaly_sentence_in_metrics(self):
        """Fault agent captures anomaly sentence in TaskResult metrics."""
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        agent = FaultAgent(llm=mock_llm)
        res = agent._parse_result(
            task_id="t1",
            messages=[],
            tool_calls_made=[],
            anomaly_sentence="Statistical anomalies found: 2 on P1_Block_A_INV_02.",
        )
        assert res.metrics["anomaly_sentence"] == "Statistical anomalies found: 2 on P1_Block_A_INV_02."
