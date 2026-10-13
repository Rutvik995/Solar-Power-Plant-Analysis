"""
test_features_phase3.py – Tests for:
  1. Fault agent always calling all four fault tools.
  2. tools/charts.py make_chart specs (bar rankings, bar soiling, line trends, zero LLM calls).
  3. Chart task handled by Executor with no LLM calls.
  4. Follow-up queries inheriting scope (plant_ids, dates) across the same thread_id.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest
from langchain_core.messages import AIMessage
from langgraph.checkpoint.memory import MemorySaver

from solar_agent.agents.fault_agent import FaultAgent
from solar_agent.graph.data_store import DataStore
from solar_agent.graph.executor import Executor
from solar_agent.graph.graph import build_graph
from solar_agent.graph.orchestrator import _normalize_scope_and_tasks
from solar_agent.state import AgentState, IntentType, Plan, Scope, Task, TaskStatus
from solar_agent.tools.charts import make_chart


class TestFaultAgentAllTools:

    def test_fault_agent_always_calls_all_four_tools(self):
        """When the LLM makes 0 tool calls, FaultAgent automatically invokes all 4 fault tools."""
        df = pd.DataFrame({
            "inverter_id": ["P1_Block_A_INV_01", "P1_Block_A_INV_02"],
            "plant_id": [1, 1],
            "block": ["Block A", "Block A"],
            "log_date": ["2026-09-01", "2026-09-01"],
            "total_daily_yield_kwh": [0.0, 100.0],
            "total_solar_radiation_kwh_m2": [5.0, 5.0],
            "daily_irradiance_kwh_m2": [5.0, 5.0],
            "status_code": [0, 2],
            "performance_ratio": [0.0, 0.45],
            "sub_hourly_zero_share": [1.0, 0.0],
        })
        DataStore.store(df, key="df_fault_test_all")

        # Mock LLM that returns a pure text response with NO tool calls
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.return_value = AIMessage(content="Inspection finished.")

        agent = FaultAgent(llm=mock_llm)
        res = agent.run(task_id="t1", description="Fault check", inputs={"data_ref": "df_fault_test_all"})

        assert res.status == TaskStatus.DONE
        tools_called = res.tool_calls_made
        expected_four = {
            "detect_zero_generation_tool",
            "detect_status_faults_tool",
            "detect_peer_underperformance_tool",
            "detect_anomalies_tool",
        }
        for tool_name in expected_four:
            assert tool_name in tools_called, f"Expected {tool_name} to be called"

        assert "anomaly_sentence" in res.metrics
        assert "total_anomalies" in res.metrics


class TestCharts:

    def test_make_chart_bar_rankings(self):
        """make_chart produces a valid Plotly JSON bar chart for ranking data."""
        df = pd.DataFrame({
            "inverter_id": ["INV_01", "INV_02", "INV_03"],
            "performance_ratio": [0.85, 0.72, 0.88],
        })
        DataStore.store(df, key="df_rank_chart")

        chart_json = make_chart("df_rank_chart", kind="bar")
        fig_dict = json.loads(chart_json)
        assert "data" in fig_dict
        assert len(fig_dict["data"]) > 0
        assert fig_dict["data"][0]["type"] == "bar"
        assert "layout" in fig_dict

    def test_make_chart_bar_soiling(self):
        """make_chart produces a valid Plotly JSON bar chart for soiling loss per plant."""
        df = pd.DataFrame({
            "plant_id": [1, 2, 3],
            "soiling_loss_pct": [3.2, 8.5, 2.1],
        })
        DataStore.store(df, key="df_soiling_chart")

        chart_json = make_chart("df_soiling_chart", kind="bar")
        fig_dict = json.loads(chart_json)
        assert fig_dict["data"][0]["type"] == "bar"

    def test_make_chart_line_trends(self):
        """make_chart produces a valid Plotly JSON line chart for time-series trends."""
        df = pd.DataFrame({
            "log_date": ["2026-09-01", "2026-09-02", "2026-09-03"],
            "total_daily_yield_kwh": [5000.0, 5200.0, 4800.0],
        })
        DataStore.store(df, key="df_trend_chart")

        chart_json = make_chart("df_trend_chart", kind="line")
        fig_dict = json.loads(chart_json)
        assert fig_dict["data"][0]["type"] == "scatter"
        assert fig_dict["data"][0]["mode"] == "lines+markers"

    def test_chart_task_execution_no_llm(self):
        """Executor runs chart task without invoking the LLM."""
        df = pd.DataFrame({
            "plant_id": [1, 2],
            "yield": [1000, 2000],
        })
        DataStore.store(df, key="df_chart_exec")

        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = AssertionError("LLM should not be called for chart tasks!")

        executor = Executor(llm=mock_llm)
        task = Task(id="t1", agent="chart", description="Plot bar chart", inputs={"data_ref": "df_chart_exec", "kind": "bar"})
        state = AgentState(
            user_query="Plot yield",
            messages=[],
            plan=Plan(
                intent=IntentType.PERFORMANCE_RANKING,
                scope=Scope(plant_ids=[1, 2], start_date=date(2026, 9, 1), end_date=date(2026, 9, 26)),
                tasks=[task],
                success_criteria="Plot chart",
            ),
            results={},
            validation=None,
            replan_count=0,
            final_answer=None,
            assumptions=[],
            warnings=[],
            is_out_of_scope=False,
            llm_call_count=0,
            run_id="test",
        )
        patch = executor.run_next_layer(state)
        res = patch["results"]["t1"]
        assert res.status == TaskStatus.DONE
        assert "chart_json" in res.metrics
        assert mock_llm.invoke.call_count == 0


class TestFollowUpScopeInheritance:

    def test_scope_normalization_inherits_previous_scope(self):
        """_normalize_scope_and_tasks inherits plant_ids and dates when not specified in follow-up."""
        prev_scope = {
            "plant_ids": [1],
            "start_date": "2026-09-01",
            "end_date": "2026-09-26",
        }

        # Follow-up 1: "now show Block B" -> should inherit plant 1 and date range, and extract Block B
        payload1 = {
            "intent": "fault_diagnosis",
            "scope": {},
            "tasks": [{"id": "t1", "agent": "data", "description": "Fetch data", "inputs": {}}],
        }
        _normalize_scope_and_tasks(payload1, "now show Block B", prev_scope=prev_scope)
        assert payload1["scope"]["plant_ids"] == [1]
        assert payload1["scope"]["start_date"] == "2026-09-01"
        assert payload1["scope"]["end_date"] == "2026-09-26"
        assert payload1["scope"]["block_ids"] == ["Block B"]

        # Follow-up 2: "same for Plant 2" -> should inherit date range, but set Plant 2
        payload2 = {
            "intent": "fault_diagnosis",
            "scope": {"plant_ids": [2]},
            "tasks": [{"id": "t1", "agent": "data", "description": "Fetch data", "inputs": {}}],
        }
        _normalize_scope_and_tasks(payload2, "same for Plant 2", prev_scope=prev_scope)
        assert payload2["scope"]["plant_ids"] == [2]
        assert payload2["scope"]["start_date"] == "2026-09-01"
        assert payload2["scope"]["end_date"] == "2026-09-26"

    def test_graph_inherits_scope_across_thread_id(self):
        """Full graph execution on same thread_id retains previous scope in orchestrator."""
        checkpointer = MemorySaver()

        df = pd.DataFrame({
            "inverter_id": ["P1_Block_A_INV_01"],
            "plant_id": [1],
            "block": ["Block A"],
            "log_date": ["2026-09-01"],
            "total_daily_yield_kwh": [1000.0],
            "daily_irradiance_kwh_m2": [5.0],
            "status_code": [0],
            "performance_ratio": [0.86],
        })
        DataStore.store(df, key="df_thread_test")

        def _mock_llm_call(messages, **kwargs):
            msg_str = str(messages)
            if "orchestrator" in msg_str.lower() or "planner" in msg_str.lower():
                if "Block B" in msg_str:
                    return AIMessage(content=json.dumps({
                        "intent": "fault_diagnosis",
                        "scope": {},
                        "tasks": [{"id": "t1", "agent": "chart", "description": "Plot chart", "inputs": {"data_ref": "df_thread_test", "kind": "bar"}}],
                        "success_criteria": "Block B analysed",
                    }))
                return AIMessage(content=json.dumps({
                    "intent": "performance_ranking",
                    "scope": {"plant_ids": [1], "start_date": "2026-09-01", "end_date": "2026-09-26"},
                    "tasks": [{"id": "t1", "agent": "chart", "description": "Plot chart", "inputs": {"data_ref": "df_thread_test", "kind": "bar"}}],
                    "success_criteria": "PR computed",
                }))
            elif "synthesizer" in msg_str.lower():
                return AIMessage(content="Final synthesized answer.")
            return AIMessage(content=json.dumps({"metrics": {"ok": 1}}))

        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        mock_llm.invoke.side_effect = _mock_llm_call

        graph = build_graph(llm=mock_llm, checkpointer=checkpointer, mode="passthrough")
        config = {"configurable": {"thread_id": "thread-solar-followup"}}

        # Query 1
        s1 = graph.invoke({"user_query": "What was the PR of Plant 1 in September 2026?"}, config=config)
        assert s1["plan"].scope.plant_ids == [1]
        assert s1["plan"].scope.start_date == date(2026, 9, 1)
        assert s1["plan"].scope.end_date == date(2026, 9, 26)

        # Query 2 (Follow-up on same thread_id: "now show Block B")
        s2 = graph.invoke({"user_query": "now show Block B"}, config=config)
        assert s2["plan"].scope.plant_ids == [1]
        assert s2["plan"].scope.start_date == date(2026, 9, 1)
        assert s2["plan"].scope.end_date == date(2026, 9, 26)
        assert s2["plan"].scope.block_ids == ["Block B"]
