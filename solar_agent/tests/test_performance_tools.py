"""Tests for Phase 2 Performance Tools."""
import pandas as pd
import pytest

from solar_agent.graph.executor import DataStore
from solar_agent.tools.performance_tools import compute_kpis, rank_entities, compare_periods, trend_analysis


@pytest.fixture(autouse=True)
def clean_datastore():
    """Clear the DataStore before each test."""
    DataStore.clear()
    yield
    DataStore.clear()


def _make_df():
    return pd.DataFrame({
        "log_date": ["2023-01-01", "2023-01-01", "2023-01-02", "2023-01-02"],
        "plant_id": [1, 1, 1, 1],
        "block_id": [1, 2, 1, 2],
        "inverter_id": [101, 201, 101, 201],
        "total_daily_yield_kwh": [100.0, 90.0, 110.0, 95.0],
        "rated_dc_kw": [20.0, 20.0, 20.0, 20.0],
        "rated_ac_kw": [18.0, 18.0, 18.0, 18.0],
        "total_solar_radiation_kwh_m2": [6.0, 6.0, 6.5, 6.5],
        "peak_dc_power_kw": [15.0, 14.0, 16.0, 15.0],
        "peak_ac_power_kw": [14.0, 13.0, 15.0, 14.0],
        "avg_dc_voltage_v": [600.0, 600.0, 600.0, 600.0],
        "avg_dc_current_a": [20.0, 20.0, 20.0, 20.0],
        "soiling_ratio": [1.0, 1.0, 1.0, 1.0],
    })


def test_compute_kpis_plant_total():
    ref = DataStore.store(_make_df())
    res = compute_kpis(ref, level="plant", period="total")
    
    assert "metrics" in res
    assert "data_ref" in res
    
    # Expected yield = 20 * 6 (twice) + 20 * 6.5 (twice) = 120+120 + 130+130 = 500
    # Total yield = 100+90 + 110+95 = 395
    # PR = 395 / 500 = 0.79
    
    assert res["metrics"]["total_yield_kwh"] == 395.0
    assert res["metrics"]["expected_yield_kwh"] == 500.0
    assert abs(res["metrics"]["performance_ratio"] - 0.79) < 0.001
    
    # Check stored DF
    df_res = DataStore.get(res["data_ref"])
    assert len(df_res) == 1  # aggregated to 1 row for the whole plant total


def test_compute_kpis_inverter_daily():
    ref = DataStore.store(_make_df())
    res = compute_kpis(ref, level="inverter", period="daily")
    
    df_res = DataStore.get(res["data_ref"])
    assert len(df_res) == 4  # 2 inverters * 2 days
    assert "performance_ratio" in df_res.columns


def test_rank_entities():
    df = pd.DataFrame({
        "inverter_id": [1, 2, 3],
        "performance_ratio": [0.85, 0.70, 0.90]
    })
    ref = DataStore.store(df)
    
    # Worst first
    res = rank_entities(ref, metric="performance_ratio", ascending=True)
    df_res = DataStore.get(res["data_ref"])
    
    assert df_res.iloc[0]["inverter_id"] == 2  # 0.70 is the lowest


def test_compare_periods():
    df_curr = pd.DataFrame({
        "inverter_id": [1, 2],
        "performance_ratio": [0.80, 0.75]
    })
    df_base = pd.DataFrame({
        "inverter_id": [1, 2],
        "performance_ratio": [0.85, 0.70]
    })
    
    ref_curr = DataStore.store(df_curr)
    ref_base = DataStore.store(df_base)
    
    res = compare_periods(ref_curr, ref_base, join_cols=["inverter_id"], metrics_to_compare=["performance_ratio"])
    df_res = DataStore.get(res["data_ref"])
    
    # Inverter 1 diff: 0.80 - 0.85 = -0.05
    diff1 = df_res[df_res["inverter_id"] == 1].iloc[0]
    assert abs(diff1["performance_ratio_diff_abs"] - (-0.05)) < 0.001


def test_trend_analysis():
    df = pd.DataFrame({
        "log_date": pd.date_range("2023-01-01", periods=10),
        "performance_ratio": [0.8] * 10
    })
    ref = DataStore.store(df)
    
    res = trend_analysis(ref, window_days=3)
    df_res = DataStore.get(res["data_ref"])
    
    assert "performance_ratio_rolling_3d" in df_res.columns
    # Rolling mean of constant 0.8 is 0.8
    assert abs(df_res["performance_ratio_rolling_3d"].iloc[-1] - 0.8) < 0.001
