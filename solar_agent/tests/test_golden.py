"""
test_golden.py — DB-backed golden tests.

Design:
  - Every expected value is derived by running hand-written SQL against the real DB.
  - Fault codes are loaded from status_codes.yaml (no hard-coded >= 2 threshold).
  - Tests run tools, then assert tool output matches SQL-derived ground truth.
  - Ranking tests assert the numeric value of the top entity, not just the name.
"""
from __future__ import annotations

import logging
import math
import yaml

import pandas as pd
import pytest

from solar_agent.db.connection import get_sync_engine
from solar_agent.graph.data_store import DataStore
from solar_agent.tools.data_tools import fetch_inverter_daily_tool
from solar_agent.agents.environment_agent import estimate_soiling_loss_tool
from solar_agent.agents.fault_agent import detect_status_faults_tool
from solar_agent.agents.performance_agent import compute_kpis_tool, rank_entities_tool
from solar_agent.config.settings import STATUS_CODES_PATH

logging.basicConfig(level=logging.WARNING)

engine = get_sync_engine()

# Load fault/warning codes from YAML — no magic numbers here
with open(STATUS_CODES_PATH, "r") as f:
    _SC = yaml.safe_load(f)["status_codes"]
FAULT_CODES = [k for k, v in _SC.items() if v.get("is_fault")]
WARNING_CODES = [k for k, v in _SC.items() if not v.get("is_fault") and k not in (0, 1)]
FAULT_CODES_SQL = ",".join(map(str, FAULT_CODES))
WARNING_CODES_SQL = ",".join(map(str, WARNING_CODES))

TOLERANCE = 0.01  # 1% relative tolerance for numeric assertions


def rel_close(a: float, b: float, tol: float = TOLERANCE) -> bool:
    if b == 0:
        return abs(a) < 1e-6
    return abs((a - b) / b) <= tol


@pytest.fixture(autouse=True)
def clean_datastore():
    DataStore.clear()
    yield
    DataStore.clear()


def _run_sql(query: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(query, conn)


# ---------------------------------------------------------------------------
# Test 1: Plant 1 – yield and PR
# ---------------------------------------------------------------------------
def test_golden_plant_1_yield_and_pr():
    """Tool yield and PR must match hand-written SQL aggregation."""
    sql = """
        SELECT
            SUM(dit.total_daily_yield_kwh) as total_yield,
            SUM(dit.total_daily_yield_kwh) / SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2) as pr
        FROM daily_inverter_telemetry dit
        JOIN inverters inv ON dit.inverter_id = inv.inverter_id
        LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
        WHERE dit.plant_id = 1
          AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-30'
          AND dwt.total_solar_radiation_kwh_m2 > 0.5
    """
    df_sql = _run_sql(sql)
    expected_yield = float(df_sql["total_yield"].iloc[0])
    expected_pr = float(df_sql["pr"].iloc[0])

    # Tool pipeline
    res_fetch = fetch_inverter_daily_tool.invoke({
        "plant_ids": [1],
        "start_date": "2026-09-01",
        "end_date": "2026-09-30"
    })
    res_kpi = compute_kpis_tool.invoke({
        "data_ref": res_fetch["data_ref"],
        "level": "plant",
        "period": "total"
    })
    df_kpi = DataStore.get(res_kpi["data_ref"])

    tool_yield = float(df_kpi["total_daily_yield_kwh"].sum())
    tool_pr = float(df_kpi["performance_ratio"].iloc[0])

    assert rel_close(tool_yield, expected_yield), (
        f"Yield mismatch: tool={tool_yield:.2f}, sql={expected_yield:.2f}"
    )
    assert rel_close(tool_pr, expected_pr), (
        f"PR mismatch: tool={tool_pr:.4f}, sql={expected_pr:.4f}"
    )


# ---------------------------------------------------------------------------
# Test 2: Plant 2 – soiling loss
# ---------------------------------------------------------------------------
def test_golden_plant_2_soiling_loss():
    """Tool soiling loss (kWh) must match SQL formula yield*(1/soiling_ratio - 1)."""
    sql = f"""
        SELECT
            SUM(dit.total_daily_yield_kwh) as total_yield,
            SUM(dit.total_daily_yield_kwh * (1.0/dwt.soiling_ratio - 1)) as soiling_loss_kwh
        FROM daily_inverter_telemetry dit
        LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
        WHERE dit.plant_id = 2
          AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-30'
          AND dwt.total_solar_radiation_kwh_m2 > 0.5
          AND dwt.soiling_ratio > 0
    """
    df_sql = _run_sql(sql)
    expected_soiling_kwh = float(df_sql["soiling_loss_kwh"].iloc[0])

    # Tool pipeline
    res_fetch = fetch_inverter_daily_tool.invoke({
        "plant_ids": [2],
        "start_date": "2026-09-01",
        "end_date": "2026-09-30"
    })
    res_soiling = estimate_soiling_loss_tool.invoke({
        "data_ref": res_fetch["data_ref"]
    })

    tool_soiling = float(res_soiling["metrics"]["total_loss_kwh"])

    assert rel_close(tool_soiling, expected_soiling_kwh), (
        f"Soiling loss mismatch: tool={tool_soiling:.2f}, sql={expected_soiling_kwh:.2f}"
    )


# ---------------------------------------------------------------------------
# Test 3: Plant 2 – fault day count (fault codes from status_codes.yaml)
# ---------------------------------------------------------------------------
def test_golden_fault_days_plant_2():
    """Fault count must match SQL using is_fault codes from status_codes.yaml, not >= 2."""
    sql = f"""
        SELECT COUNT(*) as fault_count
        FROM daily_inverter_telemetry
        WHERE plant_id = 2
          AND log_date BETWEEN '2026-09-01' AND '2026-09-30'
          AND inverter_status_code IN ({FAULT_CODES_SQL})
    """
    df_sql = _run_sql(sql)
    expected_faults = int(df_sql["fault_count"].iloc[0])

    # Tool pipeline
    res_fetch = fetch_inverter_daily_tool.invoke({
        "plant_ids": [2],
        "start_date": "2026-09-01",
        "end_date": "2026-09-30"
    })
    res_fault = detect_status_faults_tool.invoke({
        "data_ref": res_fetch["data_ref"]
    })

    assert res_fault["metrics"]["fault_count"] == expected_faults


# ---------------------------------------------------------------------------
# Test 4: Plant 1 – warning code NOT counted as fault
# ---------------------------------------------------------------------------
def test_golden_warning_not_counted_as_fault_plant_1():
    """
    Plant 1 has one warning-code (code=2) day and zero fault-code (code=3/4) days.
    The tool must count 0 faults — the warning row must be excluded.
    """
    sql_warn = f"""
        SELECT COUNT(*) as warning_count
        FROM daily_inverter_telemetry
        WHERE plant_id = 1
          AND log_date BETWEEN '2026-09-01' AND '2026-09-30'
          AND inverter_status_code IN ({WARNING_CODES_SQL})
    """
    sql_fault = f"""
        SELECT COUNT(*) as fault_count
        FROM daily_inverter_telemetry
        WHERE plant_id = 1
          AND log_date BETWEEN '2026-09-01' AND '2026-09-30'
          AND inverter_status_code IN ({FAULT_CODES_SQL})
    """
    warning_count = int(_run_sql(sql_warn)["warning_count"].iloc[0])
    fault_count = int(_run_sql(sql_fault)["fault_count"].iloc[0])

    # Sanity: confirm the DB fixture has at least one warning row and zero fault rows
    assert warning_count >= 1, "Fixture issue: expected at least 1 warning row in plant 1"
    assert fault_count == 0, "Fixture issue: expected 0 fault rows in plant 1"

    # Tool pipeline
    res_fetch = fetch_inverter_daily_tool.invoke({
        "plant_ids": [1],
        "start_date": "2026-09-01",
        "end_date": "2026-09-30"
    })
    res_fault = detect_status_faults_tool.invoke({
        "data_ref": res_fetch["data_ref"]
    })

    assert res_fault["metrics"]["fault_count"] == 0, (
        f"Warning code was incorrectly counted as fault. tool={res_fault['metrics']['fault_count']}"
    )


# ---------------------------------------------------------------------------
# Test 5: Plant 1 – block ranking (assert numeric PR value of top block)
# ---------------------------------------------------------------------------
def test_golden_block_ranking_plant_1():
    """Top block PR from rank_entities must match SQL-derived max block PR numerically."""
    sql = """
        SELECT inv.block_id,
               SUM(dit.total_daily_yield_kwh) / SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2) as pr
        FROM daily_inverter_telemetry dit
        JOIN inverters inv ON dit.inverter_id = inv.inverter_id
        LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
        WHERE dit.plant_id = 1
          AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-30'
          AND dwt.total_solar_radiation_kwh_m2 > 0.5
        GROUP BY inv.block_id
        ORDER BY pr DESC
        LIMIT 1
    """
    df_sql = _run_sql(sql)
    expected_pr = float(df_sql["pr"].iloc[0])

    # Tool pipeline
    res_fetch = fetch_inverter_daily_tool.invoke({
        "plant_ids": [1],
        "start_date": "2026-09-01",
        "end_date": "2026-09-30"
    })
    res_kpi = compute_kpis_tool.invoke({
        "data_ref": res_fetch["data_ref"],
        "level": "block",
        "period": "total"
    })
    res_rank = rank_entities_tool.invoke({
        "data_ref": res_kpi["data_ref"],
        "metric": "performance_ratio",
        "ascending": False,
        "top_n": 1
    })

    # Assert the top PR value matches SQL — handles ties correctly (checks the number, not name)
    df_ranked = DataStore.get(res_rank["data_ref"])
    tool_top_pr = float(df_ranked.iloc[0]["performance_ratio"])

    assert rel_close(tool_top_pr, expected_pr), (
        f"Top block PR mismatch: tool={tool_top_pr:.5f}, sql={expected_pr:.5f}"
    )
