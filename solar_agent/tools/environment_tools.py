"""
Environmental Analysis Tools.

Pure Python tools operating on DataStore references.
Focuses on soiling estimation, cleaning event detection, and weather correlation.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import linregress

from solar_agent.graph.executor import DataStore
from solar_agent.tools import metrics as m

logger = logging.getLogger(__name__)


def estimate_soiling_loss(data_ref: str) -> dict[str, Any]:
    """
    Computes soiling energy loss and percentage.
    Detects cleaning events (sudden positive jumps in soiling ratio).
    
    Args:
        data_ref: Key to a daily DataFrame containing 'soiling_ratio' and 'total_daily_yield_kwh'.
    """
    df = DataStore.get_or_load(data_ref)
    if df is None or df.empty:
        return {"summary": "No data for soiling estimation.", "warnings": ["Empty/missing data"]}

    if "soiling_ratio" not in df.columns or "total_daily_yield_kwh" not in df.columns:
        return {"summary": "Missing required columns.", "warnings": ["Need soiling_ratio and yield"]}

    # Compute base metrics
    df = m.compute_soiling_loss(df)
    
    # Sort for time-series analysis
    # Assuming plant-level data, or we group by inverter/block/plant
    group_cols = [c for c in ["plant_id", "block_id", "inverter_id"] if c in df.columns]
    df = df.sort_values(by=group_cols + ["log_date"])
    
    # Detect cleaning events: day-over-day increase in soiling ratio > 2%
    # (e.g. going from 0.95 to 0.98)
    JUMP_THRESHOLD = 0.02
    
    if group_cols:
        df["prev_soiling_ratio"] = df.groupby(group_cols)["soiling_ratio"].shift(1)
    else:
        df["prev_soiling_ratio"] = df["soiling_ratio"].shift(1)
        
    df["soiling_ratio_jump"] = df["soiling_ratio"] - df["prev_soiling_ratio"]
    df["is_cleaning_event"] = df["soiling_ratio_jump"] > JUMP_THRESHOLD
    
    cleaning_events = int(df["is_cleaning_event"].sum())
    total_loss_kwh = float(df["soiling_energy_loss_kwh"].sum())

    res_ref = DataStore.store(df)
    
    return {
        "summary": f"Estimated {total_loss_kwh:.1f} kWh total soiling loss. Detected {cleaning_events} cleaning events.",
        "metrics": {
            "total_loss_kwh": round(total_loss_kwh, 2),
            "cleaning_events_detected": cleaning_events
        },
        "data_ref": res_ref,
        "warnings": [],
    }


def analyze_weather_correlation(data_ref: str) -> dict[str, Any]:
    """
    Calculates the statistical correlation (and linear regression slope) between:
      1. module_temperature_c and performance_ratio (expected negative slope)
      2. total_solar_radiation_kwh_m2 and total_daily_yield_kwh (expected strong positive)
    
    Args:
        data_ref: Key to a daily DataFrame containing PR, yield, temp, and irradiance.
    """
    df = DataStore.get_or_load(data_ref)
    if df is None or df.empty:
        return {"summary": "No data for weather correlation.", "warnings": ["Empty/missing data"]}

    # Ensure PR is computed
    if "performance_ratio" not in df.columns:
        df = m.compute_performance_ratio(df)

    warnings = []
    metrics = {}
    
    # 1. Temperature vs PR (Temperature Coefficient)
    if "module_temperature_c" in df.columns:
        # Drop NaNs for regression
        temp_df = df.dropna(subset=["module_temperature_c", "performance_ratio"])
        if len(temp_df) > 5:
            slope, intercept, r_value, p_value, std_err = linregress(
                temp_df["module_temperature_c"], temp_df["performance_ratio"]
            )
            metrics["temp_pr_slope"] = slope
            metrics["temp_pr_r2"] = r_value ** 2
        else:
            warnings.append("Not enough data points for Temperature vs PR regression.")
    else:
        warnings.append("Missing module_temperature_c column.")

    # 2. Irradiance vs Yield
    if "total_solar_radiation_kwh_m2" in df.columns and "total_daily_yield_kwh" in df.columns:
        irr_df = df.dropna(subset=["total_solar_radiation_kwh_m2", "total_daily_yield_kwh"])
        if len(irr_df) > 5:
            slope, intercept, r_value, p_value, std_err = linregress(
                irr_df["total_solar_radiation_kwh_m2"], irr_df["total_daily_yield_kwh"]
            )
            metrics["irradiance_yield_r2"] = r_value ** 2
        else:
            warnings.append("Not enough data points for Irradiance vs Yield regression.")
    
    res_ref = DataStore.store(df)
    
    summary = "Weather correlation analysis complete."
    if "temp_pr_slope" in metrics:
        # Convert slope to %/°C for readability (e.g. -0.004 -> -0.4 %/°C)
        slope_pct = metrics['temp_pr_slope'] * 100
        summary += f" Temp vs PR slope: {slope_pct:.2f}%/°C."

    return {
        "summary": summary,
        "metrics": metrics,
        "data_ref": res_ref,
        "warnings": warnings,
    }
