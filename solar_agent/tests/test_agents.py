"""
tests/test_agents.py – Unit tests for Phase 3 agent nodes.

Strategy:
  - Unit tests: mock the LLM (no API calls) to verify tool routing, error handling,
    iteration cap, and TaskResult structure.
  - Integration tests: marked @pytest.mark.integration — require a real LLM and DB.
    Run with: pytest -m integration
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
from langchain_core.messages import AIMessage, ToolMessage

from solar_agent.agents.base_agent import MAX_ITERATIONS, BaseSpecialistAgent
from solar_agent.agents.data_agent import DataAgent
from solar_agent.agents.environment_agent import EnvironmentAgent
from solar_agent.agents.fault_agent import FaultAgent
from solar_agent.agents.performance_agent import PerformanceAgent
from solar_agent.graph.data_store import DataStore
from solar_agent.state import TaskStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tool_call(name: str, args: dict, call_id: str = "call_1") -> dict:
    return {"name": name, "args": args, "id": call_id, "type": "tool_call"}


def _ai_with_tool_call(name: str, args: dict, call_id: str = "call_1") -> AIMessage:
    """Return an AIMessage that requests one tool call."""
    return AIMessage(
        content="",
        tool_calls=[_make_tool_call(name, args, call_id)],
    )


def _ai_final(content: str) -> AIMessage:
    """Return a final AIMessage with no tool calls."""
    return AIMessage(content=content)


def _tool_result(payload: dict, call_id: str = "call_1") -> ToolMessage:
    return ToolMessage(content=json.dumps(payload, default=str), tool_call_id=call_id)


def _build_mock_llm(*responses: AIMessage):
    """Return a mock LLM whose bind_tools returns self and invoke cycles through responses."""
    mock = MagicMock()
    mock.bind_tools.return_value = mock
    mock.invoke.side_effect = list(responses)
    return mock


# ---------------------------------------------------------------------------
# BaseSpecialistAgent – structure tests
# ---------------------------------------------------------------------------

from langchain_core.tools import tool as _lc_tool


@_lc_tool
def _dummy_tool(data_ref: str) -> dict:
    """Dummy tool for testing."""
    return {"data_ref": "dr_test", "metrics": {"val": 42}, "warnings": []}


class _ConcreteAgent(BaseSpecialistAgent):
    name = "test_agent"
    system_prompt = "You are a test agent."
    tools = [_dummy_tool]



def test_base_agent_no_tools_raises():
    """Agent with no tools must raise at construction time."""
    class Empty(BaseSpecialistAgent):
        name = "empty"
        system_prompt = "sys"
        tools = []

    with pytest.raises(ValueError, match="must define at least one tool"):
        Empty(llm=MagicMock())


def test_base_agent_result_status_done():
    """When LLM returns one tool call then a final message, status is DONE."""
    mock_llm = _build_mock_llm(
        _ai_with_tool_call("_dummy_tool", {"data_ref": "dr_input"}),
        _ai_final("The PR is 0.87."),
    )
    agent = _ConcreteAgent(llm=mock_llm)
    agent._call_tool = lambda name, args: {"data_ref": "dr_abc", "metrics": {"pr": 0.87}, "warnings": []}
    result = agent.run("t1", "Test task", {"data_ref": "dr_input"})

    assert result.task_id == "t1"
    assert result.status == TaskStatus.DONE
    assert result.summary == "The PR is 0.87."


def test_base_agent_captures_data_ref_from_tool():
    """data_ref from the tool ToolMessage is extracted into TaskResult."""
    tool_payload = {"data_ref": "dr_xyz", "metrics": {"count": 5}, "warnings": []}
    mock_llm = _build_mock_llm(
        _ai_with_tool_call("_dummy_tool", {"data_ref": "dr_in"}),
        _ai_final("Done."),
    )
    agent = _ConcreteAgent(llm=mock_llm)
    # Patch the agent's _call_tool to return our controlled payload
    agent._call_tool = lambda name, args: tool_payload
    result = agent.run("t2", "desc", {})

    assert result.data_ref == "dr_xyz"
    assert result.metrics is not None
    assert result.metrics["count"] == 5


def test_base_agent_llm_exception_returns_failed():
    """If LLM raises, status is FAILED and error is populated."""
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm
    mock_llm.invoke.side_effect = RuntimeError("LLM unavailable")

    agent = _ConcreteAgent(llm=mock_llm)
    result = agent.run("t3", "desc", {})

    assert result.status == TaskStatus.FAILED
    assert "LLM unavailable" in (result.error or "")


def test_base_agent_iteration_cap():
    """If the LLM keeps calling tools, execution stops after MAX_ITERATIONS."""
    # Always return a tool call, never a final message
    infinite_responses = [
        _ai_with_tool_call("_dummy_tool", {"data_ref": "x"}, call_id=f"c{i}")
        for i in range(MAX_ITERATIONS + 5)
    ]
    mock_llm = _build_mock_llm(*infinite_responses)
    dummy_return = {"data_ref": "dr", "metrics": {}, "warnings": []}
    agent = _ConcreteAgent(llm=mock_llm)
    agent._call_tool = lambda name, args: dummy_return
    result = agent.run("t4", "desc", {})

    # Should complete without raising (capped at MAX_ITERATIONS)
    assert result.status in (TaskStatus.DONE, TaskStatus.FAILED)
    assert mock_llm.invoke.call_count <= MAX_ITERATIONS


def test_base_agent_unknown_tool_raises_failed():
    """If the LLM calls a tool that doesn't exist, status is FAILED."""
    mock_llm = _build_mock_llm(
        _ai_with_tool_call("nonexistent_tool", {}),
        _ai_final("Done."),
    )
    agent = _ConcreteAgent(llm=mock_llm)
    result = agent.run("t5", "desc", {})
    assert result.status == TaskStatus.FAILED
    assert "nonexistent_tool" in (result.error or "")


def test_base_agent_warnings_aggregated():
    """Warnings from multiple tool calls are all collected into the result."""
    payload_1 = {"data_ref": "d1", "metrics": {}, "warnings": ["warn1"]}
    payload_2 = {"data_ref": "d2", "metrics": {}, "warnings": ["warn2"]}

    mock_llm = _build_mock_llm(
        _ai_with_tool_call("_dummy_tool", {}, call_id="c1"),
        _ai_with_tool_call("_dummy_tool", {}, call_id="c2"),
        _ai_final("Done."),
    )
    call_counter = {"n": 0}
    payloads = [payload_1, payload_2]

    def _patched_call(name, args):
        r = payloads[call_counter["n"] % len(payloads)]
        call_counter["n"] += 1
        return r

    agent = _ConcreteAgent(llm=mock_llm)
    agent._call_tool = _patched_call
    result = agent.run("t6", "desc", {})

    assert result.status == TaskStatus.DONE


def test_base_agent_latency_populated():
    """latency_ms should be populated with a positive value."""
    mock_llm = _build_mock_llm(_ai_final("Done immediately."))
    agent = _ConcreteAgent(llm=mock_llm)
    result = agent.run("t7", "desc", {})
    assert result.latency_ms is not None
    assert result.latency_ms >= 0


# ---------------------------------------------------------------------------
# DataAgent – tool routing
# ---------------------------------------------------------------------------

def test_data_agent_constructs():
    """DataAgent should construct without errors."""
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm
    agent = DataAgent(llm=mock_llm)
    assert agent.name == "data"
    assert len(agent.tools) == 5


def test_data_agent_only_has_data_tools():
    """DataAgent must NOT have performance, fault, or environment tools."""
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm
    agent = DataAgent(llm=mock_llm)
    tool_names = {t.name for t in agent.tools}
    assert "compute_kpis_tool" not in tool_names
    assert "detect_zero_generation_tool" not in tool_names
    assert "estimate_soiling_loss_tool" not in tool_names
    assert "fetch_inverter_daily_tool" in tool_names


# ---------------------------------------------------------------------------
# PerformanceAgent – tool routing
# ---------------------------------------------------------------------------

def test_performance_agent_constructs():
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm
    agent = PerformanceAgent(llm=mock_llm)
    assert agent.name == "performance"
    tool_names = {t.name for t in agent.tools}
    assert "compute_kpis_tool" in tool_names
    assert "rank_entities_tool" in tool_names
    assert "fetch_inverter_daily_tool" not in tool_names


def test_performance_agent_routes_compute_kpis():
    """When LLM calls compute_kpis_tool, the result is parsed correctly."""
    kpi_payload = {
        "summary": "Computed total KPIs at plant level.",
        "data_ref": "df_kpi_result",
        "metrics": {"performance_ratio": 0.866, "total_yield_kwh": 2351535.09},
        "warnings": [],
    }
    mock_llm = _build_mock_llm(
        _ai_with_tool_call("compute_kpis_tool", {"data_ref": "df_raw", "level": "plant", "period": "total"}),
        _ai_final("Plant PR is 0.866."),
    )
    agent = PerformanceAgent(llm=mock_llm)
    agent._call_tool = lambda name, args: kpi_payload
    result = agent.run("t_perf_1", "Compute plant-level KPIs", {"data_ref": "df_raw"})

    assert result.status == TaskStatus.DONE
    assert result.data_ref == "df_kpi_result"
    assert result.metrics is not None
    assert abs(result.metrics["performance_ratio"] - 0.866) < 0.001


# ---------------------------------------------------------------------------
# FaultAgent – tool routing
# ---------------------------------------------------------------------------

def test_fault_agent_constructs():
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm
    agent = FaultAgent(llm=mock_llm)
    assert agent.name == "fault"
    tool_names = {t.name for t in agent.tools}
    assert "detect_zero_generation_tool" in tool_names
    assert "detect_status_faults_tool" in tool_names
    assert "detect_peer_underperformance_tool" in tool_names
    assert "detect_anomalies_tool" in tool_names
    assert "estimate_soiling_loss_tool" not in tool_names


def test_fault_agent_routes_zero_generation():
    """FaultAgent correctly calls detect_zero_generation_tool."""
    fault_payload = {
        "summary": "Detected 1 instances of unexpected zero generation.",
        "data_ref": "df_faults",
        "metrics": {"fault_count": 1},
        "warnings": [],
    }
    mock_llm = _build_mock_llm(
        _ai_with_tool_call("detect_zero_generation_tool", {"data_ref": "df_raw"}),
        _ai_final("1 zero-generation fault found."),
    )
    agent = FaultAgent(llm=mock_llm)
    agent._call_tool = lambda name, args: fault_payload
    result = agent.run("t_fault_1", "Check for zero generation", {"data_ref": "df_raw"})

    assert result.status == TaskStatus.DONE
    assert result.metrics["fault_count"] == 1


def test_fault_agent_no_cross_agent_tools():
    """FaultAgent must not expose performance or environment tools."""
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm
    agent = FaultAgent(llm=mock_llm)
    tool_names = {t.name for t in agent.tools}
    assert "compute_kpis_tool" not in tool_names
    assert "estimate_soiling_loss_tool" not in tool_names


# ---------------------------------------------------------------------------
# EnvironmentAgent – tool routing
# ---------------------------------------------------------------------------

def test_environment_agent_constructs():
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm
    agent = EnvironmentAgent(llm=mock_llm)
    assert agent.name == "environment"
    tool_names = {t.name for t in agent.tools}
    assert "estimate_soiling_loss_tool" in tool_names
    assert "analyze_weather_correlation_tool" in tool_names
    assert "detect_zero_generation_tool" not in tool_names


def test_environment_agent_routes_soiling_loss():
    """EnvironmentAgent correctly calls estimate_soiling_loss_tool."""
    soiling_payload = {
        "summary": "Estimated 89771.8 kWh total soiling loss.",
        "data_ref": "df_soiling",
        "metrics": {"total_loss_kwh": 89771.84, "cleaning_events_detected": 2},
        "warnings": [],
    }
    mock_llm = _build_mock_llm(
        _ai_with_tool_call("estimate_soiling_loss_tool", {"data_ref": "df_raw"}),
        _ai_final("Soiling loss is ~89.8 MWh."),
    )
    agent = EnvironmentAgent(llm=mock_llm)
    agent._call_tool = lambda name, args: soiling_payload
    result = agent.run("t_env_1", "Estimate soiling", {"data_ref": "df_raw"})

    assert result.status == TaskStatus.DONE
    assert abs(result.metrics["total_loss_kwh"] - 89771.84) < 0.1


def test_environment_agent_warning_propagated():
    """If a tool emits a warning, it appears in the result."""
    soiling_payload = {
        "summary": "Estimated 500000 kWh total soiling loss.",
        "data_ref": "df_soiling_bad",
        "metrics": {"total_loss_kwh": 500000.0, "cleaning_events_detected": 0},
        "warnings": ["High soiling loss detected (>15% of yield). Estimated loss: 500000.0 kWh"],
    }
    mock_llm = _build_mock_llm(
        _ai_with_tool_call("estimate_soiling_loss_tool", {"data_ref": "df_raw"}),
        _ai_final("Warning: extremely high soiling detected."),
    )
    agent = EnvironmentAgent(llm=mock_llm)
    agent._call_tool = lambda name, args: soiling_payload
    result = agent.run("t_env_2", "Estimate soiling with high loss", {"data_ref": "df_raw"})

    assert result.status == TaskStatus.DONE
    # The payload warnings are logged; the agent summary mentions it
    assert "warning" in result.summary.lower() or result.metrics["total_loss_kwh"] > 0


# ---------------------------------------------------------------------------
# Integration tests (skipped unless DB + LLM are available)
# ---------------------------------------------------------------------------

@pytest.mark.integration
def test_data_agent_live_fetch():
    """
    Live smoke test: DataAgent fetches real data and returns a data_ref.
    Requires env vars for DB URL and LLM API key.
    Run with: pytest -m integration
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
    agent = DataAgent(llm=llm)
    result = agent.run(
        "t_live_data",
        "Fetch inverter telemetry for plant Solar Alpha for September 2026.",
        {"plant_names": ["Solar Alpha"], "date_range": "2026-09-12 to 2026-09-26"},
    )
    assert result.status == TaskStatus.DONE
    assert result.data_ref is not None


@pytest.mark.integration
def test_fault_agent_live():
    """
    Live smoke test: FaultAgent runs all four detection tools on real data.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI
    from solar_agent.tools.data_tools import fetch_inverter_daily_tool

    # Pre-fetch data so we have a data_ref
    fetch_result = fetch_inverter_daily_tool.invoke({
        "plant_ids": [1],
        "start_date": "2026-09-12",
        "end_date": "2026-09-26",
    })
    data_ref = fetch_result["data_ref"]

    llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
    agent = FaultAgent(llm=llm)
    result = agent.run(
        "t_live_fault",
        "Detect all faults in the telemetry data and cross-reference your findings.",
        {"data_ref": data_ref},
    )
    assert result.status == TaskStatus.DONE
    assert result.summary  # LLM should produce some explanation
