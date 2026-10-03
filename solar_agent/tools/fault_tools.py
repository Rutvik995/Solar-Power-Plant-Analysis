"""
Fault Analysis Tools.

Pure Python tools operating on DataStore references.
Focuses on fault detection: zero generation, status codes, peer underperformance,
and statistical anomalies (rolling z-score).
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd
import yaml

from solar_agent.config.settings import STATUS_CODES_PATH
from solar_agent.graph.executor import DataStore
from solar_agent.tools import metrics as m
from solar_agent.config.settings import settings

logger = logging.getLogger(__name__)

# Load status codes mapping to identify which codes are faults and which bypass yield checks
with open(STATUS_CODES_PATH, "r", encoding="utf-8") as f:
    _STATUS_CONFIG = yaml.safe_load(f)
    _CODES_DICT = _STATUS_CONFIG.get("status_codes", _STATUS_CONFIG)
    # Build quick lookup sets
    _FAULT_CODES = {k for k, v in _CODES_DICT.items() if v.get("is_fault", False)}
    _EXCLUDE_YIELD_CODES = {
        k for k, v in _CODES_DICT.items() if v.get("exclude_from_yield_check", False)
    }


def detect_zero_generation(data_ref: str) -> dict[str, Any]:
    """
    Detects inverters reporting ~0 yield on days with sufficient irradiance.
    Skips rows with status codes explicitly flagged to bypass yield checks (e.g. Standby).
    
    Rule: Yield <= zero_generation_max_yield_kwh AND irradiance > MIN_H.
    """
    df = DataStore.get_or_load(data_ref)
    if df is None or df.empty:
        return {"summary": "No data for zero-generation check.", "warnings": ["Empty/missing data"]}

    # Apply irradiance guard (BR-01)
    mask_h = df["total_solar_radiation_kwh_m2"] > settings.min_irradiance_kwh_m2
    
    # Exclude specific statuses (like Standby = 1) from zero-generation check
    mask_status = ~df["status_code"].isin(_EXCLUDE_YIELD_CODES)
    
    # Check yield threshold
    mask_yield = df["total_daily_yield_kwh"] <= settings.zero_generation_max_yield_kwh
    
    # Identify faults
    faults_df = df[mask_h & mask_status & mask_yield].copy()
    
    res_ref = DataStore.store(faults_df)
    
    return {
        "summary": f"Detected {len(faults_df)} instances of unexpected zero generation.",
        "metrics": {"fault_count": len(faults_df)},
        "data_ref": res_ref,
        "warnings": [],
    }


def detect_status_faults(data_ref: str) -> dict[str, Any]:
    """
    Detects explicitly flagged fault statuses (e.g. Fault=3, Offline=4) and counts consecutive days.
    """
    df = DataStore.get_or_load(data_ref)
    if df is None or df.empty:
        return {"summary": "No data for status fault check.", "warnings": ["Empty/missing data"]}

    # Filter to only known fault codes
    faults = df[df["status_code"].isin(_FAULT_CODES)].copy()
    
    if faults.empty:
        return {
            "summary": "No explicit status faults detected.",
            "metrics": {"fault_count": 0},
            "data_ref": DataStore.store(faults),
            "warnings": [],
        }

    # Sort by inverter and date to count streaks
    faults = faults.sort_values(by=["inverter_id", "log_date"])
    
    # Calculate consecutive days using date diffs
    faults["prev_date"] = faults.groupby("inverter_id")["log_date"].shift(1)
    faults["is_consecutive"] = (
        pd.to_datetime(faults["log_date"]) - pd.to_datetime(faults["prev_date"])
    ).dt.days == 1
    
    # Assign a streak ID (increments when NOT consecutive)
    faults["streak_id"] = (~faults["is_consecutive"]).groupby(faults["inverter_id"]).cumsum()
    
    # Calculate streak length
    faults["streak_length_days"] = faults.groupby(["inverter_id", "streak_id"])["log_date"].transform("count")

    res_ref = DataStore.store(faults)
    
    return {
        "summary": f"Detected {len(faults)} inverter-days with fault statuses.",
        "metrics": {"fault_count": len(faults), "max_streak": int(faults["streak_length_days"].max())},
        "data_ref": res_ref,
        "warnings": [],
    }


def detect_peer_underperformance(data_ref: str, min_consecutive_days: int = 1) -> dict[str, Any]:
    """
    Identifies inverters significantly underperforming their block peers.
    Uses MAD-based z-score (from metrics.py). Flags if score < -threshold for N days.
    """
    df = DataStore.get_or_load(data_ref)
    if df is None or df.empty:
        return {"summary": "No data for peer underperformance check.", "warnings": ["Empty/missing data"]}

    # We need PR to compute peer deviation
    if "performance_ratio" not in df.columns:
        df = m.compute_performance_ratio(df)
        
    # Compute the score
    df = m.compute_peer_deviation(df)
    
    # Filter to severe underperformers
    threshold = -settings.peer_underperformance_z_threshold
    bad = df[df["peer_deviation_score"] < threshold].copy()
    
    if bad.empty:
        return {
            "summary": "No peer underperformance detected.",
            "metrics": {"underperformer_count": 0},
            "data_ref": DataStore.store(bad),
            "warnings": [],
        }
        
    if min_consecutive_days > 1:
        # Sort and count streaks
        bad = bad.sort_values(["inverter_id", "log_date"])
        bad["prev_date"] = bad.groupby("inverter_id")["log_date"].shift(1)
        bad["is_consecutive"] = (pd.to_datetime(bad["log_date"]) - pd.to_datetime(bad["prev_date"])).dt.days == 1
        bad["streak_id"] = (~bad["is_consecutive"]).groupby(bad["inverter_id"]).cumsum()
        bad["streak_len"] = bad.groupby(["inverter_id", "streak_id"])["log_date"].transform("count")
        
        # Keep only those meeting the consecutive days requirement
        bad = bad[bad["streak_len"] >= min_consecutive_days]

    res_ref = DataStore.store(bad)
    
    return {
        "summary": f"Detected {len(bad)} instances of severe peer underperformance.",
        "metrics": {"underperformer_count": len(bad)},
        "data_ref": res_ref,
        "warnings": [],
    }


def detect_anomalies(
    data_ref: str,
    columns: list[str] = ["performance_ratio", "avg_dc_voltage_v", "avg_dc_current_a"],
    window_days: int = 14,
    z_threshold: float = 3.0,
) -> dict[str, Any]:
    """
    Detect statistical anomalies (sudden spikes/drops) for a single inverter over time,
    using a rolling z-score.
    """
    df = DataStore.get_or_load(data_ref)
    if df is None or df.empty:
        return {"summary": "No data for anomaly check.", "warnings": ["Empty/missing data"]}

    # Ensure PR exists if requested
    if "performance_ratio" in columns and "performance_ratio" not in df.columns:
        df = m.compute_performance_ratio(df)

    df = df.sort_values(by=["inverter_id", "log_date"]).copy()
    anomalies_found = 0
    
    for col in columns:
        if col not in df.columns:
            continue
            
        # Rolling mean and std dev per inverter
        roll = df.groupby("inverter_id")[col].rolling(window_days, min_periods=window_days//2)
        mean_col = f"{col}_roll_mean"
        std_col = f"{col}_roll_std"
        
        # Using .values to assign back cleanly bypassing the multi-index from rolling
        df[mean_col] = roll.mean().values
        df[std_col] = roll.std().values
        
        # Calculate z-score
        z_col = f"{col}_zscore"
        # Avoid division by zero
        safe_std = df[std_col].replace(0, np.nan)
        df[z_col] = (df[col] - df[mean_col]) / safe_std
        
        # Flag anomalies
        flag_col = f"{col}_is_anomaly"
        df[flag_col] = df[z_col].abs() > z_threshold
        anomalies_found += df[flag_col].sum()

    res_ref = DataStore.store(df)
    
    return {
        "summary": f"Anomaly scan complete. Flagged {anomalies_found} datapoints.",
        "metrics": {"total_anomalies": int(anomalies_found)},
        "data_ref": res_ref,
        "warnings": [],
    }
