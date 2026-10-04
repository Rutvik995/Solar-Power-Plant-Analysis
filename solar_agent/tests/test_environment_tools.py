"""Tests for Phase 2 Environment Tools."""
import numpy as np
import pandas as pd
import pytest

from solar_agent.graph.data_store import DataStore
from solar_agent.tools.environment_tools import estimate_soiling_loss, analyze_weather_correlation


@pytest.fixture(autouse=True)
def clean_datastore():
    DataStore.clear()
    yield
    DataStore.clear()


# --- estimate_soiling_loss ---

def test_estimate_soiling_loss_empty_df():
    ref = DataStore.store(pd.DataFrame())
    res = estimate_soiling_loss(ref)
    assert "warnings" in res
    assert "Empty/missing data" in res["warnings"][0]

def test_estimate_soiling_loss_missing_cols():
    df = pd.DataFrame({"log_date": ["2023-01-01"]})
    ref = DataStore.store(df)
    res = estimate_soiling_loss(ref)
    assert "warnings" in res
    assert "Need soiling_ratio and yield" in res["warnings"][0]

def test_estimate_soiling_loss_basic():
    df = pd.DataFrame({
        "log_date": ["2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04"],
        "soiling_ratio": [0.95, 0.93, 0.91, 0.98],
        "total_daily_yield_kwh": [100.0, 100.0, 100.0, 100.0]
    })
    ref = DataStore.store(df)
    res = estimate_soiling_loss(ref)
    
    assert res["metrics"]["cleaning_events_detected"] == 1
    assert abs(res["metrics"]["total_loss_kwh"] - 24.72) < 0.1

def test_estimate_soiling_loss_noise_vs_cleaning():
    # Noise below the 0.02 threshold shouldn't trigger cleaning
    df = pd.DataFrame({
        "log_date": ["2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04", "2023-01-05"],
        "soiling_ratio": [0.95, 0.96, 0.94, 0.99, 1.0], 
        # Jumps: +0.01 (noise), -0.02, +0.05 (cleaning), +0.01 (noise)
        "total_daily_yield_kwh": [100.0] * 5
    })
    ref = DataStore.store(df)
    res = estimate_soiling_loss(ref)
    
    assert res["metrics"]["cleaning_events_detected"] == 1  # only the +0.05 jump


def test_estimate_soiling_loss_all_nulls():
    df = pd.DataFrame({
        "log_date": ["2023-01-01", "2023-01-02"],
        "soiling_ratio": [np.nan, np.nan],
        "total_daily_yield_kwh": [100.0, 100.0]
    })
    ref = DataStore.store(df)
    res = estimate_soiling_loss(ref)
    assert res["metrics"]["cleaning_events_detected"] == 0

def test_estimate_soiling_loss_hand_computed():
    df = pd.DataFrame({
        "log_date": ["2025-01-01", "2025-01-02", "2025-01-03", "2025-01-01", "2025-01-02", "2025-01-03"],
        "plant_id": [1, 1, 1, 1, 1, 1],
        "inverter_id": ["INV1", "INV1", "INV1", "INV2", "INV2", "INV2"],
        "soiling_ratio": [0.95] * 6,
        "total_daily_yield_kwh": [1000] * 6
    })
    ref = DataStore.store(df)
    res = estimate_soiling_loss(ref)
    # 6000 * (1/0.95 - 1) = 315.79
    assert abs(res["metrics"]["total_loss_kwh"] - 315.79) < 0.1
    assert not any(">15% of yield" in w for w in res.get("warnings", []))

def test_estimate_soiling_loss_high_warning():
    df = pd.DataFrame({
        "log_date": ["2025-01-01"],
        "plant_id": [1],
        "inverter_id": ["INV1"],
        "soiling_ratio": [0.80],
        "total_daily_yield_kwh": [1000]
    })
    ref = DataStore.store(df)
    res = estimate_soiling_loss(ref)
    assert any(">15% of yield" in w for w in res["warnings"])


# --- analyze_weather_correlation ---

def test_analyze_weather_correlation_empty():
    ref = DataStore.store(pd.DataFrame())
    res = analyze_weather_correlation(ref)
    assert "Empty/missing data" in res["warnings"][0]

def test_analyze_weather_correlation_missing_cols():
    df = pd.DataFrame({"log_date": ["2023-01-01"]})
    ref = DataStore.store(df)
    res = analyze_weather_correlation(ref)
    assert "warnings" in res
    assert "Missing max_module_temp_c column." in res["warnings"]

def test_analyze_weather_correlation_small_sample():
    # Only 6 rows (trigger the < 14 warning, but > 5 so it computes)
    df = pd.DataFrame({
        "max_module_temp_c": [20, 30, 40, 50, 60, 70],
        "total_solar_radiation_kwh_m2": [1, 2, 3, 4, 5, 6],
        "rated_dc_kw": [10, 10, 10, 10, 10, 10],
    })
    df["total_daily_yield_kwh"] = df["total_solar_radiation_kwh_m2"] * 10
    df["performance_ratio"] = 1.0 - (0.004 * df["max_module_temp_c"])
    
    ref = DataStore.store(df)
    res = analyze_weather_correlation(ref)
    
    assert "temp_pr_slope" in res["metrics"]
    assert any("Small sample size" in w for w in res["warnings"])

def test_analyze_weather_correlation_perfect_fit():
    # 15 rows to avoid the sample size warning
    df = pd.DataFrame({
        "max_module_temp_c": list(range(20, 35)),
        "total_solar_radiation_kwh_m2": list(range(1, 16)),
        "rated_dc_kw": [10] * 15,
    })
    df["total_daily_yield_kwh"] = df["total_solar_radiation_kwh_m2"] * 10
    df["performance_ratio"] = 1.0 - (0.004 * df["max_module_temp_c"])
    
    ref = DataStore.store(df)
    res = analyze_weather_correlation(ref)
    
    assert not res["warnings"]  # no warnings
    assert abs(res["metrics"]["irradiance_yield_r2"] - 1.0) < 0.001
    assert abs(res["metrics"]["temp_pr_r2"] - 1.0) < 0.001

def test_analyze_weather_correlation_all_nulls():
    df = pd.DataFrame({
        "max_module_temp_c": [np.nan]*15,
        "total_solar_radiation_kwh_m2": [np.nan]*15,
        "rated_dc_kw": [10]*15,
        "total_daily_yield_kwh": [10]*15,
        "performance_ratio": [0.8]*15,
    })
    ref = DataStore.store(df)
    res = analyze_weather_correlation(ref)
    
    assert "Not enough data points for Temperature vs PR regression." in res["warnings"]
    assert "Not enough data points for Irradiance vs Yield regression." in res["warnings"]
