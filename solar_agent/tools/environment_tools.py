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

from solar_agent.graph.data_store import DataStore
from solar_agent.tools import metrics as m
from solar_agent.config.settings import settings

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
    # Detect cleaning events: day-over-day increase in soiling ratio > threshold
    JUMP_THRESHOLD = settings.soiling_cleaning_jump_threshold
    
    # Aggregate to plant-day level for cleaning event detection
    if "plant_id" in df.columns:
        plant_daily = df.groupby(["plant_id", "log_date"], as_index=False)["soiling_ratio"].mean()
        plant_daily = plant_daily.sort_values(["plant_id", "log_date"])
        plant_daily["prev_soiling_ratio"] = plant_daily.groupby("plant_id")["soiling_ratio"].shift(1)
    else:
        plant_daily = df.groupby(["log_date"], as_index=False)["soiling_ratio"].mean()
        plant_daily = plant_daily.sort_values("log_date")
        plant_daily["prev_soiling_ratio"] = plant_daily["soiling_ratio"].shift(1)
        
    plant_daily["soiling_ratio_jump"] = plant_daily["soiling_ratio"] - plant_daily["prev_soiling_ratio"]
    plant_daily["is_cleaning_event"] = plant_daily["soiling_ratio_jump"] > JUMP_THRESHOLD
    
    cleaning_events = int(plant_daily["is_cleaning_event"].sum())
    total_loss_kwh = float(df["soiling_energy_loss_kwh"].sum())
    total_yield = float(df["total_daily_yield_kwh"].sum())

    warnings = []
    if total_yield > 0 and total_loss_kwh > 0.15 * total_yield:
        warnings.append(f"High soiling loss detected (>15% of yield). Estimated loss: {total_loss_kwh:.1f} kWh")

    res_ref = DataStore.store(df)
    
    return {
        "summary": f"Estimated {total_loss_kwh:.1f} kWh total soiling loss. Detected {cleaning_events} cleaning events.",
        "metrics": {
            "total_loss_kwh": round(total_loss_kwh, 2),
            "cleaning_events_detected": cleaning_events
        },
        "data_ref": res_ref,
        "warnings": warnings,
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

    warnings = []
    metrics = {}
    
    # Ensure PR is computed
    if "performance_ratio" not in df.columns:
        if "rated_dc_kw" in df.columns and "total_solar_radiation_kwh_m2" in df.columns:
            df = m.compute_performance_ratio(df)
        else:
            warnings.append("Missing columns to compute performance ratio.")
    
    # 1. Temperature vs PR (Temperature Coefficient)
    if "max_module_temp_c" in df.columns:
        # Drop NaNs for regression
        temp_df = df.dropna(subset=["max_module_temp_c", "performance_ratio"])
        if len(temp_df) > 5:
            if len(temp_df) < 14:
                warnings.append(f"Small sample size for Temperature vs PR regression: {len(temp_df)} valid days.")
            slope, intercept, r_value, p_value, std_err = linregress(
                temp_df["max_module_temp_c"], temp_df["performance_ratio"]
            )
            metrics["temp_pr_slope"] = slope
            metrics["temp_pr_r2"] = r_value ** 2
        else:
            warnings.append("Not enough data points for Temperature vs PR regression.")
    else:
        warnings.append("Missing max_module_temp_c column.")

    # 2. Irradiance vs Specific Yield
    if "total_solar_radiation_kwh_m2" in df.columns and "total_daily_yield_kwh" in df.columns and "rated_dc_kw" in df.columns:
        df["specific_yield"] = df["total_daily_yield_kwh"] / df["rated_dc_kw"]
        irr_df = df.dropna(subset=["total_solar_radiation_kwh_m2", "specific_yield"])
        if len(irr_df) > 5:
            if len(irr_df) < 14:
                warnings.append(f"Small sample size for Irradiance vs Yield regression: {len(irr_df)} valid days.")
            slope, intercept, r_value, p_value, std_err = linregress(
                irr_df["total_solar_radiation_kwh_m2"], irr_df["specific_yield"]
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
