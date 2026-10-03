"""Tests for Phase 2 Environment Tools."""
import pandas as pd
import pytest

from solar_agent.graph.executor import DataStore
from solar_agent.tools.environment_tools import estimate_soiling_loss, analyze_weather_correlation


@pytest.fixture(autouse=True)
def clean_datastore():
    DataStore.clear()
    yield
    DataStore.clear()


def test_estimate_soiling_loss():
    df = pd.DataFrame({
        "log_date": ["2023-01-01", "2023-01-02", "2023-01-03", "2023-01-04"],
        "soiling_ratio": [0.95, 0.93, 0.91, 0.98],  # Drops steadily, then jumps (cleaned)
        "total_daily_yield_kwh": [100.0, 100.0, 100.0, 100.0]
    })
    ref = DataStore.store(df)
    
    res = estimate_soiling_loss(ref)
    
    # Cleaning events: 1 (from 0.91 to 0.98, jump > 0.02)
    assert res["metrics"]["cleaning_events_detected"] == 1
    
    # Total loss:
    # 100 * (1/0.95 - 1) = 5.26
    # 100 * (1/0.93 - 1) = 7.53
    # 100 * (1/0.91 - 1) = 9.89
    # 100 * (1/0.98 - 1) = 2.04
    # Total approx 24.72
    assert abs(res["metrics"]["total_loss_kwh"] - 24.72) < 0.1


def test_analyze_weather_correlation():
    # Synthetic data for regression
    # Perfect linear relation for yield vs irradiance: yield = 10 * H
    # Perfect linear relation for PR vs Temp: PR = 1.0 - 0.004 * Temp
    df = pd.DataFrame({
        "module_temperature_c": [20, 30, 40, 50, 60, 70],
        "total_solar_radiation_kwh_m2": [1, 2, 3, 4, 5, 6],
    })
    df["total_daily_yield_kwh"] = df["total_solar_radiation_kwh_m2"] * 10
    df["performance_ratio"] = 1.0 - (0.004 * df["module_temperature_c"])
    
    ref = DataStore.store(df)
    
    res = analyze_weather_correlation(ref)
    
    # R^2 should be 1.0 for both
    assert abs(res["metrics"]["irradiance_yield_r2"] - 1.0) < 0.001
    assert abs(res["metrics"]["temp_pr_r2"] - 1.0) < 0.001
    
    # Slope for temp vs PR should be -0.004
    assert abs(res["metrics"]["temp_pr_slope"] - (-0.004)) < 0.001
