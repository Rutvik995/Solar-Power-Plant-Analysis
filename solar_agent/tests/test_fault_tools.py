"""Tests for Phase 2 Fault Tools."""
import pandas as pd
import pytest

from solar_agent.graph.executor import DataStore
from solar_agent.tools.fault_tools import (
    detect_zero_generation,
    detect_status_faults,
    detect_peer_underperformance,
    detect_anomalies,
)


@pytest.fixture(autouse=True)
def clean_datastore():
    DataStore.clear()
    yield
    DataStore.clear()


def test_detect_zero_generation():
    df = pd.DataFrame({
        "inverter_id": [1, 2, 3, 4],
        # Inv 1: normal
        # Inv 2: zero yield but high irradiance -> FAULT
        # Inv 3: zero yield but low irradiance -> OK (night)
        # Inv 4: zero yield but status 1 (Standby) -> OK (excluded)
        "total_daily_yield_kwh": [100.0, 0.0, 0.0, 0.0],
        "total_solar_radiation_kwh_m2": [6.0, 6.0, 0.1, 6.0],
        "status_code": [0, 0, 0, 1],
    })
    ref = DataStore.store(df)
    
    res = detect_zero_generation(ref)
    assert res["metrics"]["fault_count"] == 1
    
    df_faults = DataStore.get(res["data_ref"])
    assert len(df_faults) == 1
    assert df_faults.iloc[0]["inverter_id"] == 2


def test_detect_status_faults():
    df = pd.DataFrame({
        "log_date": ["2023-01-01", "2023-01-02", "2023-01-04"], # 4th is not consecutive
        "inverter_id": [1, 1, 1],
        "status_code": [3, 3, 3] # All faults
    })
    ref = DataStore.store(df)
    
    res = detect_status_faults(ref)
    assert res["metrics"]["fault_count"] == 3
    # Max streak is 2 (Jan 1, Jan 2)
    assert res["metrics"]["max_streak"] == 2
    

def test_detect_peer_underperformance():
    df = pd.DataFrame({
        "log_date": ["2023-01-01"] * 5,
        "block_id": [1] * 5,
        "inverter_id": [1, 2, 3, 4, 5],
        # Inverter 5 is a severe outlier (PR 0.20 vs others ~0.80)
        "performance_ratio": [0.82, 0.80, 0.78, 0.81, 0.20],
        "status_code": [0, 0, 0, 0, 0],
    })
    ref = DataStore.store(df)
    
    # Check for 1 consecutive day
    res = detect_peer_underperformance(ref, min_consecutive_days=1)
    assert res["metrics"]["underperformer_count"] == 1
    
    df_faults = DataStore.get(res["data_ref"])
    assert df_faults.iloc[0]["inverter_id"] == 5


def test_detect_anomalies():
    # 20 days normal, 1 day spike
    dates = pd.date_range("2023-01-01", periods=21)
    pr = [0.80] * 20 + [0.20]  # Sudden drop on day 21
    
    df = pd.DataFrame({
        "log_date": dates,
        "inverter_id": [1] * 21,
        "performance_ratio": pr
    })
    ref = DataStore.store(df)
    
    res = detect_anomalies(ref, columns=["performance_ratio"], window_days=14, z_threshold=3.0)
    assert res["metrics"]["total_anomalies"] == 1
    
    df_res = DataStore.get(res["data_ref"])
    # Day 21 should be flagged
    assert df_res.iloc[-1]["performance_ratio_is_anomaly"] == True
    assert df_res.iloc[-2]["performance_ratio_is_anomaly"] == False
