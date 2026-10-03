"""Tests for Phase 2 Fault Tools."""
import numpy as np
import pandas as pd
import pytest

from solar_agent.graph.data_store import DataStore
from solar_agent.tools.fault_tools import (
    detect_zero_generation,
    detect_status_faults,
    detect_peer_underperformance,
    detect_anomalies,
)
from solar_agent.config.settings import settings


@pytest.fixture(autouse=True)
def clean_datastore():
    DataStore.clear()
    yield
    DataStore.clear()


# --- detect_zero_generation ---

def test_detect_zero_generation_empty():
    ref = DataStore.store(pd.DataFrame())
    res = detect_zero_generation(ref)
    assert "Empty/missing data" in res["warnings"][0]

def test_detect_zero_generation_standby_vs_fault():
    df = pd.DataFrame({
        "inverter_id": [1, 2, 3, 4],
        # Inv 1: normal yield -> OK
        # Inv 2: zero yield + Status 0 -> FAULT
        # Inv 3: zero yield + low irradiance -> OK (night)
        # Inv 4: zero yield + Status 1 (Standby) -> OK (excluded)
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


def test_detect_zero_generation_all_nulls():
    df = pd.DataFrame({
        "inverter_id": [1, 2],
        "total_daily_yield_kwh": [np.nan, np.nan],
        "total_solar_radiation_kwh_m2": [np.nan, np.nan],
        "status_code": [0, 0]
    })
    ref = DataStore.store(df)
    res = detect_zero_generation(ref)
    assert res["metrics"]["fault_count"] == 0


# --- detect_status_faults ---

def test_detect_status_faults_empty():
    ref = DataStore.store(pd.DataFrame())
    res = detect_status_faults(ref)
    assert "Empty/missing data" in res["warnings"][0]

def test_detect_status_faults_gap_in_dates():
    # Streak should be broken if dates have a gap
    df = pd.DataFrame({
        "log_date": ["2023-01-01", "2023-01-02", "2023-01-04", "2023-01-05"],
        "inverter_id": [1, 1, 1, 1],
        "status_code": [3, 3, 3, 3] # All faults
    })
    ref = DataStore.store(df)
    res = detect_status_faults(ref)
    
    assert res["metrics"]["fault_count"] == 4
    # Max streak is 2 because of the gap between 02 and 04
    assert res["metrics"]["max_streak"] == 2

def test_detect_status_faults_normal_only():
    df = pd.DataFrame({
        "log_date": ["2023-01-01", "2023-01-02"],
        "inverter_id": [1, 1],
        "status_code": [0, 0]
    })
    ref = DataStore.store(df)
    res = detect_status_faults(ref)
    assert res["metrics"]["fault_count"] == 0


# --- detect_peer_underperformance ---

def test_detect_peer_underperformance_empty():
    ref = DataStore.store(pd.DataFrame())
    res = detect_peer_underperformance(ref)
    assert "Empty/missing data" in res["warnings"][0]

def test_detect_peer_underperformance_single_inverter():
    # MAD is meaningless for 1 inverter in a block
    df = pd.DataFrame({
        "log_date": ["2023-01-01"],
        "block_id": [1],
        "inverter_id": [1],
        "performance_ratio": [0.20],
        "status_code": [0],
    })
    ref = DataStore.store(df)
    res = detect_peer_underperformance(ref, min_consecutive_days=1)
    # peer_deviation_score will be NaN, so it won't be < threshold
    assert res["metrics"]["underperformer_count"] == 0

def test_detect_peer_underperformance_min_days():
    # Inverter 5 is a severe outlier for 3 days
    dates = ["2023-01-01", "2023-01-02", "2023-01-03"]
    df_list = []
    for d in dates:
        df_list.append(pd.DataFrame({
            "log_date": [d] * 5,
            "block_id": [1] * 5,
            "inverter_id": [1, 2, 3, 4, 5],
            "performance_ratio": [0.82, 0.80, 0.78, 0.81, 0.20],
            "status_code": [0] * 5,
        }))
    df = pd.concat(df_list)
    ref = DataStore.store(df)
    
    # Needs 3 days
    res = detect_peer_underperformance(ref, min_consecutive_days=3)
    assert res["metrics"]["underperformer_count"] == 3  # 3 inverter-days flagged
    
    df_faults = DataStore.get(res["data_ref"])
    assert all(df_faults["inverter_id"] == 5)
    
    # Gap in dates -> streak broken
    df_gap = df[df["log_date"] != "2023-01-02"].copy()
    ref_gap = DataStore.store(df_gap)
    res_gap = detect_peer_underperformance(ref_gap, min_consecutive_days=3)
    assert res_gap["metrics"]["underperformer_count"] == 0  # no 3-day streak


# --- detect_anomalies ---

def test_detect_anomalies_empty():
    ref = DataStore.store(pd.DataFrame())
    res = detect_anomalies(ref)
    assert "Empty/missing data" in res["warnings"][0]

def test_detect_anomalies_spike():
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
    assert df_res.iloc[-1]["performance_ratio_is_anomaly"] == True

def test_detect_anomalies_all_nulls():
    dates = pd.date_range("2023-01-01", periods=21)
    df = pd.DataFrame({
        "log_date": dates,
        "inverter_id": [1] * 21,
        "performance_ratio": [np.nan] * 21
    })
    ref = DataStore.store(df)
    res = detect_anomalies(ref)
    assert res["metrics"]["total_anomalies"] == 0
