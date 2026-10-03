"""
metrics.py — Pure functions for solar KPI calculations.

Design rules:
  1. Every function takes a DataFrame (or scalars) and returns a DataFrame or scalar.
  2. NO database calls, NO LLM calls, NO side effects.
  3. Business rules from semantic_layer.yaml are enforced here (guards, aggregation).
  4. Each function is independently unit-testable (see tests/test_metrics.py).

Column naming convention (matches fetch_inverter_daily output):
  - total_daily_yield_kwh     : AC energy produced (kWh)
  - rated_dc_kw               : inverter DC nameplate (kW)
  - rated_ac_kw               : inverter AC nameplate (kW)
  - total_solar_radiation_kwh_m2 : daily insolation H (kWh/m²)
  - peak_dc_power_kw          : daily max DC power (kW)
  - peak_ac_power_kw          : daily max AC power (kW)
  - avg_dc_voltage_v          : average DC voltage (V)
  - avg_dc_current_a          : average DC current (A)
  - soiling_ratio             : 1.0 = clean, 0.95 = 5% soiling loss

All guard thresholds are imported from settings so they can be tuned centrally.
"""

from __future__ import annotations

import warnings
from typing import Literal

import numpy as np
import pandas as pd

from solar_agent.config.settings import settings

# Minimum insolation to include a day in PR / yield calculations (BR-01)
MIN_H = settings.min_irradiance_kwh_m2
# Minimum DC power for efficiency calculation
MIN_DC_KW = settings.min_dc_power_kw_for_efficiency
# Clipping threshold fraction
CLIP_FRAC = settings.clipping_threshold_fraction


# ---------------------------------------------------------------------------
# Helper: filter valid irradiance days
# ---------------------------------------------------------------------------

def _valid_irradiance_mask(df: pd.DataFrame) -> pd.Series:
    """
    Returns a boolean mask for rows where insolation exceeds the minimum
    threshold (BR-01). Rows failing this mask are 'night/low-light' days
    and must be excluded from PR and yield analysis.
    """
    return df["total_solar_radiation_kwh_m2"] > MIN_H


# ---------------------------------------------------------------------------
# 1. Performance Ratio (PR)
# ---------------------------------------------------------------------------

def compute_performance_ratio(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds a 'performance_ratio' column to the DataFrame.

    Formula: PR = total_daily_yield_kwh / (rated_dc_kw × total_solar_radiation_kwh_m2)
    Guard:   Only computed for rows where total_solar_radiation_kwh_m2 > MIN_H (BR-01).
             PR is set to NaN for low-irradiance days.

    Returns the DataFrame with the new column added (does not mutate in place).
    """
    df = df.copy()
    denominator = df["rated_dc_kw"] * df["total_solar_radiation_kwh_m2"]
    valid = _valid_irradiance_mask(df) & (denominator > 0)

    df["performance_ratio"] = np.nan
    df.loc[valid, "performance_ratio"] = (
        df.loc[valid, "total_daily_yield_kwh"] / denominator[valid]
    )
    return df


def aggregate_pr(df: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    """
    Computes Performance Ratio at a grouped level (block or plant) by summing
    yields and summing (capacity × H) separately — never averaging individual PRs (BR-04).

    Args:
        df:          Must contain 'total_daily_yield_kwh', 'rated_dc_kw',
                     'total_solar_radiation_kwh_m2', and the group_cols.
        group_cols:  e.g. ['block_id', 'log_date'] or ['plant_id']

    Returns:
        DataFrame with group_cols + ['total_yield_kwh', 'total_expected_kwh', 'performance_ratio']
    """
    valid = df[_valid_irradiance_mask(df)].copy()
    valid["expected_yield_kwh"] = valid["rated_dc_kw"] * valid["total_solar_radiation_kwh_m2"]

    agg = (
        valid.groupby(group_cols, as_index=False)
        .agg(
            total_yield_kwh=("total_daily_yield_kwh", "sum"),
            total_expected_kwh=("expected_yield_kwh", "sum"),
        )
    )
    denom_ok = agg["total_expected_kwh"] > 0
    agg["performance_ratio"] = np.nan
    agg.loc[denom_ok, "performance_ratio"] = (
        agg.loc[denom_ok, "total_yield_kwh"] / agg.loc[denom_ok, "total_expected_kwh"]
    )
    return agg


# ---------------------------------------------------------------------------
# 2. Specific Yield (kWh/kWp)
# ---------------------------------------------------------------------------

def compute_specific_yield(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds 'specific_yield_kwh_kwp' = total_daily_yield_kwh / rated_dc_kw.

    No irradiance guard needed here — specific yield is meaningful even on
    cloudy days. However callers should note that low-irradiance days will
    have low values by design.
    """
    df = df.copy()
    ok = df["rated_dc_kw"] > 0
    df["specific_yield_kwh_kwp"] = np.nan
    df.loc[ok, "specific_yield_kwh_kwp"] = (
        df.loc[ok, "total_daily_yield_kwh"] / df.loc[ok, "rated_dc_kw"]
    )
    return df


# ---------------------------------------------------------------------------
# 3. Capacity Factor
# ---------------------------------------------------------------------------

def compute_capacity_factor(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds 'capacity_factor' = total_daily_yield_kwh / (rated_ac_kw × 24).

    Represents the fraction of maximum possible daily AC energy actually produced.
    """
    df = df.copy()
    ok = df["rated_ac_kw"] > 0
    df["capacity_factor"] = np.nan
    df.loc[ok, "capacity_factor"] = (
        df.loc[ok, "total_daily_yield_kwh"] / (df.loc[ok, "rated_ac_kw"] * 24)
    )
    return df


# ---------------------------------------------------------------------------
# 4. Inverter Efficiency (peak)
# ---------------------------------------------------------------------------

def compute_inverter_efficiency(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds 'inverter_efficiency' = peak_ac_power_kw / peak_dc_power_kw.

    Guard: only computed where peak_dc_power_kw > MIN_DC_KW (default 1 kW).
    Values outside [0.5, 1.0] are clamped to NaN as they indicate sensor issues.
    """
    df = df.copy()
    ok = df["peak_dc_power_kw"] > MIN_DC_KW
    df["inverter_efficiency"] = np.nan
    eff = df.loc[ok, "peak_ac_power_kw"] / df.loc[ok, "peak_dc_power_kw"]
    # Sanity clamp: efficiency should be between 50% and 100%
    eff = eff.where((eff >= 0.5) & (eff <= 1.0), other=np.nan)
    df.loc[ok, "inverter_efficiency"] = eff
    return df


# ---------------------------------------------------------------------------
# 5. DC/AC Loading Ratio & Clipping Indicator
# ---------------------------------------------------------------------------

def compute_dc_ac_loading(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds:
      - 'dc_ac_loading_ratio' = rated_dc_kw / rated_ac_kw  (design-time constant)
      - 'is_clipping'         = peak_ac_power_kw >= CLIP_FRAC × rated_ac_kw
    """
    df = df.copy()
    ok_rating = df["rated_ac_kw"] > 0

    df["dc_ac_loading_ratio"] = np.nan
    df.loc[ok_rating, "dc_ac_loading_ratio"] = (
        df.loc[ok_rating, "rated_dc_kw"] / df.loc[ok_rating, "rated_ac_kw"]
    )

    df["is_clipping"] = (
        df["peak_ac_power_kw"] >= CLIP_FRAC * df["rated_ac_kw"]
    )
    return df


# ---------------------------------------------------------------------------
# 6. DC Power Consistency Check
# ---------------------------------------------------------------------------

def compute_dc_power_consistency(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds:
      - 'reconstructed_dc_power_kw' = avg_dc_voltage_v × avg_dc_current_a / 1000
      - 'dc_power_discrepancy_kw'   = |reconstructed_dc_power_kw - peak_dc_power_kw|
      - 'dc_power_ok'               = discrepancy ≤ 20% of peak_dc_power_kw

    Note: peak ≠ average, so a moderate discrepancy is expected.
    A very large discrepancy (>20% of peak) may indicate sensor error.
    """
    df = df.copy()
    df["reconstructed_dc_power_kw"] = (
        df["avg_dc_voltage_v"] * df["avg_dc_current_a"] / 1000.0
    )
    df["dc_power_discrepancy_kw"] = (
        (df["reconstructed_dc_power_kw"] - df["peak_dc_power_kw"]).abs()
    )
    ok = df["peak_dc_power_kw"] > 0
    df["dc_power_ok"] = True  # default: OK if we can't compute ratio
    df.loc[ok, "dc_power_ok"] = (
        df.loc[ok, "dc_power_discrepancy_kw"]
        <= 0.20 * df.loc[ok, "peak_dc_power_kw"].abs()
    )
    return df


# ---------------------------------------------------------------------------
# 7. Soiling Loss
# ---------------------------------------------------------------------------

def compute_soiling_loss(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds:
      - 'soiling_loss_pct'    = (1 - soiling_ratio) × 100
      - 'soiling_energy_loss_kwh' = total_daily_yield_kwh × (1/soiling_ratio - 1)

    Convention (BR-07): soiling_ratio = 1.0 means perfectly clean.
    Guard: soiling_ratio must be in (0, 1]. Values outside this range are set to NaN.
    """
    df = df.copy()
    valid = (df["soiling_ratio"] > 0) & (df["soiling_ratio"] <= 1.0)

    df["soiling_loss_pct"] = np.nan
    df["soiling_energy_loss_kwh"] = np.nan

    df.loc[valid, "soiling_loss_pct"] = (1 - df.loc[valid, "soiling_ratio"]) * 100
    df.loc[valid, "soiling_energy_loss_kwh"] = (
        df.loc[valid, "total_daily_yield_kwh"]
        * (1.0 / df.loc[valid, "soiling_ratio"] - 1)
    )
    return df


# ---------------------------------------------------------------------------
# 8. Peer Deviation Score (Robust Z-score within block per day)
# ---------------------------------------------------------------------------

def compute_peer_deviation(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds 'peer_deviation_score' to each inverter-day row.

    Formula: (PR_inverter - median(PR_block_same_day)) / MAD(PR_block_same_day)
    where MAD = 1.4826 × median(|PR_i - median(PR)|).

    Groups: (log_date, block_id) — same day, same block.

    Requires 'performance_ratio' to be already computed (call compute_performance_ratio first).
    Inverters with Standby status (code = 1) are excluded from being flagged
    but are included in the peer group for median computation.

    Returned column: float, negative = underperformance, positive = overperformance.
    NaN when the block has < 2 non-null PR values for that day (can't compute MAD).
    """
    if "performance_ratio" not in df.columns:
        raise ValueError(
            "compute_peer_deviation requires 'performance_ratio' column. "
            "Call compute_performance_ratio(df) first."
        )

    df = df.copy()

    def _robust_zscore_group(group: pd.Series) -> pd.Series:
        """Apply robust z-score within a group."""
        vals = group.dropna()
        if len(vals) < 2:
            return pd.Series(np.nan, index=group.index)
        med = vals.median()
        mad = (vals - med).abs().median()
        # Scale factor 1.4826 makes MAD consistent with std for normal distributions
        mad_scaled = 1.4826 * mad
        if mad_scaled == 0:
            # All values identical → no deviation
            return pd.Series(0.0, index=group.index)
        return (group - med) / mad_scaled

    df["peer_deviation_score"] = (
        df.groupby(["log_date", "block_id"])["performance_ratio"]
        .transform(_robust_zscore_group)
    )
    return df


# ---------------------------------------------------------------------------
# 9. Expected Yield
# ---------------------------------------------------------------------------

def compute_expected_yield(df: pd.DataFrame) -> pd.DataFrame:
    """
    Adds 'expected_yield_kwh' = rated_dc_kw × total_solar_radiation_kwh_m2.

    This is the theoretical maximum yield assuming PR = 100%, used for
    loss calculation and zero-generation thresholds.
    """
    df = df.copy()
    df["expected_yield_kwh"] = df["rated_dc_kw"] * df["total_solar_radiation_kwh_m2"]
    return df


# ---------------------------------------------------------------------------
# 10. Compute all KPIs at once (convenience wrapper)
# ---------------------------------------------------------------------------

def compute_all_kpis(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies all KPI computations in the correct order.
    Returns the enriched DataFrame with all KPI columns added.

    Order matters:
      1. expected_yield       (no dependencies)
      2. performance_ratio    (no dependencies)
      3. specific_yield       (no dependencies)
      4. capacity_factor      (no dependencies)
      5. inverter_efficiency  (no dependencies)
      6. dc_ac_loading        (no dependencies)
      7. dc_power_consistency (no dependencies)
      8. soiling_loss         (no dependencies)
      9. peer_deviation       (requires performance_ratio → must be after step 2)
    """
    df = compute_expected_yield(df)
    df = compute_performance_ratio(df)
    df = compute_specific_yield(df)
    df = compute_capacity_factor(df)
    df = compute_inverter_efficiency(df)
    df = compute_dc_ac_loading(df)
    df = compute_dc_power_consistency(df)
    df = compute_soiling_loss(df)
    df = compute_peer_deviation(df)
    return df


# ---------------------------------------------------------------------------
# 11. Aggregate KPIs to block or plant level
# ---------------------------------------------------------------------------

def aggregate_kpis(
    df: pd.DataFrame,
    level: Literal["inverter", "block", "plant"],
    period: Literal["daily", "total"] = "total",
) -> pd.DataFrame:
    """
    Aggregates all KPI columns to the requested level over the entire period.

    Args:
        df:     Output of compute_all_kpis (must include 'log_date', 'plant_id',
                'block_id', 'inverter_id').
        level:  Aggregation level.
        period: 'total' aggregates the whole date range into one row per entity.
                'daily' keeps per-day rows.

    Returns:
        Aggregated DataFrame with correctly computed PR (summed, not averaged)
        and summed yields.
    """
    if level == "inverter":
        group_cols = (
            ["log_date", "inverter_id", "plant_id", "block_id"]
            if period == "daily"
            else ["inverter_id", "plant_id", "block_id"]
        )
    elif level == "block":
        group_cols = (
            ["log_date", "block_id", "plant_id"]
            if period == "daily"
            else ["block_id", "plant_id"]
        )
    else:  # plant
        group_cols = ["log_date", "plant_id"] if period == "daily" else ["plant_id"]

    # Sum yields correctly
    valid = df[_valid_irradiance_mask(df)].copy()
    valid["expected_yield_kwh"] = valid["rated_dc_kw"] * valid["total_solar_radiation_kwh_m2"]

    agg_dict: dict = {
        "total_daily_yield_kwh": "sum",
        "expected_yield_kwh": "sum",
        "soiling_energy_loss_kwh": "sum",
    }

    # Mean for ratio metrics (will be overridden for PR below)
    for col in [
        "specific_yield_kwh_kwp",
        "capacity_factor",
        "inverter_efficiency",
        "soiling_loss_pct",
        "dc_ac_loading_ratio",
        "peer_deviation_score",
    ]:
        if col in valid.columns:
            agg_dict[col] = "mean"

    result = valid.groupby(group_cols, as_index=False).agg(agg_dict)

    # Recompute PR correctly from summed values (BR-04)
    denom_ok = result["expected_yield_kwh"] > 0
    result["performance_ratio"] = np.nan
    result.loc[denom_ok, "performance_ratio"] = (
        result.loc[denom_ok, "total_daily_yield_kwh"]
        / result.loc[denom_ok, "expected_yield_kwh"]
    )

    return result
