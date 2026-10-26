"""
Tests for fault agent data_ref re-routing and graceful missing-column handling.
Verifies that:
1. detect_zero_generation does not raise KeyError when total_solar_radiation_kwh_m2 is missing.
2. detect_status_faults, detect_peer_underperformance, and detect_anomalies handle missing columns gracefully.
3. Orchestrator normalizes fault/environment task data_ref to the data task ($t1.data_ref).
4. Executor re-routes fault/environment task data_ref to raw telemetry if an aggregate frame is received.
"""

from datetime import date
import pandas as pd
import pytest

from solar_agent.graph.data_store import DataStore
from solar_agent.tools.fault_tools import (
    detect_zero_generation,
    detect_status_faults,
    detect_peer_underperformance,
    detect_anomalies,
)
from solar_agent.graph.orchestrator import _normalize_scope_and_tasks


def test_detect_zero_generation_missing_radiation_column():
    """BR-01 fallback: Missing radiation column should not crash with KeyError."""
    df = pd.DataFrame([
        {"inverter_id": "P3_Block_A_INV_01", "total_daily_yield_kwh": 0.0, "status_code": 0},
        {"inverter_id": "P3_Block_A_INV_02", "total_daily_yield_kwh": 100.0, "status_code": 0},
    ])
    ref = DataStore.store(df)
    res = detect_zero_generation(ref)
    assert isinstance(res, dict)
    assert "metrics" in res
    assert res["metrics"]["fault_count"] == 1
    assert any("not present" in w.lower() for w in res.get("warnings", []))


def test_detect_status_faults_missing_status_code():
    """Missing status_code should return safe empty dict with warning instead of crashing."""
    df = pd.DataFrame([
        {"inverter_id": "P3_Block_A_INV_01", "total_daily_yield_kwh": 50.0},
    ])
    ref = DataStore.store(df)
    res = detect_status_faults(ref)
    assert isinstance(res, dict)
    assert res["metrics"]["fault_count"] == 0
    assert any("status_code" in w.lower() for w in res.get("warnings", []))


def test_detect_anomalies_missing_log_date():
    """Missing log_date should return safe empty dict with warning instead of crashing."""
    df = pd.DataFrame([
        {"inverter_id": "P3_Block_A_INV_01", "performance_ratio": 0.82},
    ])
    ref = DataStore.store(df)
    res = detect_anomalies(ref)
    assert isinstance(res, dict)
    assert res["metrics"]["total_anomalies"] == 0
    assert any("log_date" in w.lower() for w in res.get("warnings", []))


def test_orchestrator_normalizes_fault_data_ref_to_data_task():
    """Orchestrator must replace $t2.data_ref with $t1.data_ref for fault tasks."""
    plan_dict = {
        "intent": "root_cause",
        "scope": {"plant_ids": [3], "start_date": "2026-08-30", "end_date": "2026-09-26"},
        "tasks": [
            {
                "id": "t1",
                "agent": "data",
                "inputs": {"plant_ids": [3], "start_date": "2026-08-30", "end_date": "2026-09-26"},
                "depends_on": [],
            },
            {
                "id": "t2",
                "agent": "performance",
                "inputs": {"data_ref": "$t1.data_ref"},
                "depends_on": ["t1"],
            },
            {
                "id": "t3",
                "agent": "fault",
                "inputs": {"data_ref": "$t2.data_ref", "target_inverter": "P3_Block_A_INV_05"},
                "depends_on": ["t1", "t2"],
            },
            {
                "id": "t4",
                "agent": "environment",
                "inputs": {"data_ref": "$t2.data_ref"},
                "depends_on": ["t1", "t2"],
            },
        ],
    }
    _normalize_scope_and_tasks(
        plan_dict,
        "Explain why inverter P3_Block_A_INV_05 in Block_A performance ratio deviated from its block peers during 2026-08-30 to 2026-09-26.",
    )
    tasks = plan_dict["tasks"]
    t3 = [t for t in tasks if t["id"] == "t3"][0]
    t4 = [t for t in tasks if t["id"] == "t4"][0]
    assert t3["inputs"]["data_ref"] == "$t1.data_ref"
    assert t4["inputs"]["data_ref"] == "$t1.data_ref"


def test_executor_reroutes_fault_data_ref_when_aggregate_frame_passed():
    """Executor must re-route data_ref if fault task receives an aggregated frame missing telemetry columns."""
    from solar_agent.graph.executor import Executor
    from solar_agent.state import Task, TaskResult, TaskStatus
    from unittest.mock import MagicMock

    raw_df = pd.DataFrame([
        {
            "inverter_id": "P3_Block_A_INV_01",
            "plant_id": 3,
            "block_id": 1,
            "log_date": "2026-09-01",
            "total_daily_yield_kwh": 100.0,
            "status_code": 0,
            "total_solar_radiation_kwh_m2": 5.2,
        }
    ])
    raw_ref = DataStore.store(raw_df)

    agg_df = pd.DataFrame([
        {
            "inverter_id": "P3_Block_A_INV_01",
            "performance_ratio": 0.85,
        }
    ])
    agg_ref = DataStore.store(agg_df)

    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content="Fault check passed", tool_calls=[])
    executor = Executor(llm=mock_llm)

    prev_results = {
        "t1": TaskResult(task_id="t1", status=TaskStatus.DONE, data_ref=raw_ref),
        "t2": TaskResult(task_id="t2", status=TaskStatus.DONE, data_ref=agg_ref),
    }

    t3 = Task(
        id="t3",
        agent="fault",
        description="Run fault detection",
        inputs={"data_ref": "$t2.data_ref"},
        depends_on=["t1", "t2"],
    )

    layer_results = executor._run_layer([t3], prev_results)
    assert "t3" in layer_results
    assert layer_results["t3"].status == TaskStatus.DONE

