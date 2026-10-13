"""
tests/test_eval_fixes.py - Tests for number normalization, tool totals, and planner KPI scope.
"""

import pytest
import pandas as pd
from unittest.mock import MagicMock
from langchain_core.messages import AIMessage

from solar_agent.graph.validator import normalize_number_text, _extract_numbers
from solar_agent.graph.data_store import DataStore
from solar_agent.tools.performance_tools import compute_kpis
from solar_agent.tools.environment_tools import estimate_soiling_loss
from solar_agent.graph.orchestrator import Orchestrator, _ORCHESTRATOR_SYSTEM_PROMPT
from solar_agent.state import AgentState
from scripts.run_eval import _score_numbers, normalize_number_text as run_eval_norm


def test_number_normalization_thin_spaces_and_commas():
    """Test stripping U+202F, U+00A0, thin spaces, commas, and parsing '250 610' as one number."""
    # Validator normalization
    assert normalize_number_text("250 610") == "250610"
    assert normalize_number_text("250\u202f610") == "250610"
    assert normalize_number_text("250\u00a0610") == "250610"
    assert normalize_number_text("250,610") == "250610"
    assert normalize_number_text("250,610.87") == "250610.87"

    # run_eval normalization
    assert run_eval_norm("250 610") == "250610"
    assert run_eval_norm("250\u202f610") == "250610"
    assert run_eval_norm("250\u00a0610") == "250610"

    # Extraction parses 250 610 as one number
    nums = _extract_numbers("Plant 3 incurred a soiling loss of 250 610 kWh.")
    assert 250610.0 in nums
    assert len(nums) == 1

    # Multiple normalized numbers
    nums_multi = _extract_numbers("Loss was 250\u202f610 kWh and total yield was 4,135,384.29 kWh.")
    assert 250610.0 in nums_multi
    assert 4135384.29 in nums_multi


def test_fabricated_number_flagged_as_unexpected():
    """Test that an invented/fabricated number in the answer is flagged as unexpected."""
    expected = [50000.0]
    ground = [50000.0]
    # Answer mentions expected number 50,000 plus an ungrounded fabricated number 999999.0
    answer = "The total yield was 50,000 kWh and unexpected metric was 999,999 kWh."

    grounded_frac, unexpected_count = _score_numbers(expected, answer, ground)
    assert grounded_frac == 1.0
    assert unexpected_count >= 1


def test_q17_tools_return_all_plants_total_kpi():
    """Test that compute_kpis returns an all-plants total yield for multi-plant data."""
    df = pd.DataFrame({
        "plant_id": [1, 1, 2, 2],
        "block_id": [1, 1, 1, 1],
        "inverter_id": ["INV1", "INV2", "INV3", "INV4"],
        "log_date": ["2026-09-01", "2026-09-01", "2026-09-01", "2026-09-01"],
        "total_daily_yield_kwh": [1000.0, 1500.0, 2000.0, 2500.0],
        "rated_dc_kw": [500.0, 500.0, 500.0, 500.0],
        "rated_ac_kw": [450.0, 450.0, 450.0, 450.0],
        "total_solar_radiation_kwh_m2": [5.0, 5.0, 5.0, 5.0],
        "peak_dc_power_kw": [400.0, 400.0, 400.0, 400.0],
        "peak_ac_power_kw": [380.0, 380.0, 380.0, 380.0],
        "avg_dc_voltage_v": [600.0, 600.0, 600.0, 600.0],
        "avg_dc_current_a": [20.0, 20.0, 20.0, 20.0],
        "soiling_ratio": [1.0, 1.0, 1.0, 1.0],
    })
    ref = DataStore.store(df)
    res = compute_kpis(ref, level="plant", period="total")

    metrics = res.get("metrics", {})
    assert "all_plants_total_yield_kwh" in metrics
    assert metrics["all_plants_total_yield_kwh"] == 7000.0
    assert metrics["total_yield_kwh"] == 7000.0
    assert metrics.get("plant_1_total_yield_kwh") == 2500.0
    assert metrics.get("plant_2_total_yield_kwh") == 4500.0


def test_q25_tools_return_clean_yield_totals():
    """Test that estimate_soiling_loss returns clean yield and yield without soiling."""
    df = pd.DataFrame({
        "plant_id": [1, 1],
        "inverter_id": ["INV1", "INV2"],
        "log_date": ["2026-09-01", "2026-09-02"],
        "total_daily_yield_kwh": [4000.0, 6000.0],
        "soiling_ratio": [0.95, 0.95],
    })
    ref = DataStore.store(df)
    res = estimate_soiling_loss(ref)

    metrics = res.get("metrics", {})
    assert "clean_yield_kwh" in metrics
    assert "actual_yield_kwh" in metrics
    assert metrics["actual_yield_kwh"] == 10000.0
    assert metrics["clean_yield_kwh"] > metrics["actual_yield_kwh"]
    assert metrics["clean_yield_kwh"] == metrics["yield_without_soiling_kwh"]
    assert "all_plants_clean_yield_kwh" in metrics


def test_q21_planner_expected_yield_in_scope():
    """Test that expected yield queries are recognized as in-scope KPI calculations."""
    # Verify system prompt has expected yield and forecast clarification
    assert "expected yield" in _ORCHESTRATOR_SYSTEM_PROMPT.lower()
    assert "forecast" in _ORCHESTRATOR_SYSTEM_PROMPT.lower()

    # Verify Orchestrator produces a valid plan for q21 style query
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content="""{
      "intent": "summary",
      "scope": {"plant_ids": [1], "start_date": "2026-09-01", "end_date": "2026-09-26"},
      "tasks": [
        {"id": "t1", "agent": "data", "description": "Fetch telemetry", "inputs": {"plant_ids": [1]}, "depends_on": []},
        {"id": "t2", "agent": "performance", "description": "Compute KPIs", "inputs": {"data_ref": "$t1.data_ref"}, "depends_on": ["t1"]}
      ],
      "success_criteria": "Report expected yield."
    }""")

    orchestrator = Orchestrator(llm=mock_llm)
    state: AgentState = {
        "user_query": "What is the expected yield for Plant 1 in September 2026?",
        "chat_history": [],
        "replan_count": 0,
    }
    result = orchestrator.plan(state)

    assert result.get("is_out_of_scope") is False
    assert result.get("plan") is not None
    tasks = result["plan"].tasks
    assert len(tasks) == 2
    assert tasks[1].agent == "performance"


# ---------------------------------------------------------------------------
# Cache key tests: model name, tool schemas, tool results must affect hash
# ---------------------------------------------------------------------------

def test_cache_key_changes_on_system_prompt():
    """Changing the system prompt must cause a cache miss (different hash)."""
    from solar_agent.graph.llm_recorder import _hash_request
    from langchain_core.messages import SystemMessage, HumanMessage

    msgs1 = [SystemMessage(content="You are agent A."), HumanMessage(content="Hello")]
    msgs2 = [SystemMessage(content="You are agent B."), HumanMessage(content="Hello")]
    assert _hash_request(msgs1) != _hash_request(msgs2), \
        "Different system prompts must produce different cache keys"


def test_cache_key_changes_on_tool_result():
    """Changing a tool result in the message list must cause a cache miss."""
    from solar_agent.graph.llm_recorder import _hash_request
    from langchain_core.messages import SystemMessage, HumanMessage, ToolMessage

    base = [SystemMessage(content="SYS"), HumanMessage(content="Query")]
    msgs_a = base + [ToolMessage(content='{"soiling_loss": 1000}', tool_call_id="tc1")]
    msgs_b = base + [ToolMessage(content='{"soiling_loss": 9999}', tool_call_id="tc1")]
    assert _hash_request(msgs_a) != _hash_request(msgs_b), \
        "Different tool results must produce different cache keys"


def test_cache_key_changes_on_model_name():
    """Changing the model name must cause a cache miss."""
    from solar_agent.graph.llm_recorder import _hash_request
    from langchain_core.messages import HumanMessage

    msgs = [HumanMessage(content="What is the PR?")]
    hash_70b = _hash_request(msgs, model_name="llama-3.3-70b-versatile")
    hash_8b  = _hash_request(msgs, model_name="llama-3.1-8b-instant")
    assert hash_70b != hash_8b, \
        "Different model names must produce different cache keys"


def test_cache_replay_raises_on_miss():
    """ReplayLLM must raise CacheMissError on miss (never fall through to live)."""
    import tempfile, os
    from solar_agent.graph.llm_recorder import ReplayLLM, CacheMissError, _SHARED_CACHES
    from langchain_core.messages import HumanMessage

    # Use an isolated empty cache file
    with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as tmp:
        tmp.write("{}")
        tmp_path = tmp.name

    try:
        # Clear shared caches so our empty file is loaded fresh
        _SHARED_CACHES.clear()
        replay = ReplayLLM(fallback_llm=None, mode="replay",
                           cache_name=os.path.basename(tmp_path))
        # Force the cache file path
        replay.cache_file = type(replay.cache_file)(tmp_path)
        replay.cache = {}

        with pytest.raises(CacheMissError):
            replay.invoke([HumanMessage(content="no-entry-exists")])
    finally:
        os.unlink(tmp_path)
