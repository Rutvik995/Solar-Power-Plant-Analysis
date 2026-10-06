"""
Performance Analysis Tools.

Pure Python tools operating on DataStore references.
These tools use the base KPIs from metrics.py and provide aggregated analysis,
ranking, period comparisons, and trending.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

import pandas as pd

from solar_agent.graph.data_store import DataStore
from solar_agent.tools import metrics as m

logger = logging.getLogger(__name__)


def compute_kpis(
    data_ref: str,
    level: Literal["inverter", "block", "plant"] = "plant",
    period: Literal["daily", "total"] = "total",
) -> dict[str, Any]:
    """
    Compute all KPIs and aggregate to the requested level/period.

    Args:
        data_ref: Key in DataStore pointing to the raw fetched telemetry DataFrame.
        level:    Aggregation level (inverter, block, or plant).
        period:   Aggregation period (daily or total).

    Returns:
        Structured dict with summary, data_ref (to the new result DF), and warnings.
    """
    df = DataStore.get_or_load(data_ref)
    if df is None:
        return {"summary": "Error: data not found in store.", "metrics": {}, "warnings": ["Data missing"]}
    if df.empty:
        return {"summary": "No data available to compute KPIs.", "metrics": {}, "warnings": ["Empty DataFrame"]}

    # Compute all row-level KPIs first
    df_kpi = m.compute_all_kpis(df)
    
    # Aggregate
    df_agg = m.aggregate_kpis(df_kpi, level=level, period=period)

    # Store result and return ref
    res_ref = DataStore.store(df_agg)
    
    # Generate a lightweight summary dict for the LLM
    metrics_summary = {}
    if period == "total" and level == "plant":
        if not df_agg.empty:
            if len(df_agg) == 1:
                row = df_agg.iloc[0]
                metrics_summary = {
                    "performance_ratio": round(row.get("performance_ratio", float("nan")), 4),
                    "total_yield_kwh": round(row.get("total_daily_yield_kwh", 0), 2),
                    "expected_yield_kwh": round(row.get("expected_yield_kwh", 0), 2),
                }
            else:
                for _, row in df_agg.iterrows():
                    pid = row.get("plant_id")
                    if pd.notna(pid):
                        pid = int(pid)
                    else:
                        pid = "unknown"
                    metrics_summary[f"plant_{pid}_performance_ratio"] = round(row.get("performance_ratio", float("nan")), 4)
                    metrics_summary[f"plant_{pid}_total_yield_kwh"] = round(row.get("total_daily_yield_kwh", 0), 2)

    return {
        "summary": f"Computed {period} KPIs at {level} level.",
        "metrics": metrics_summary,
        "data_ref": res_ref,
        "warnings": [],
    }


def rank_entities(
    data_ref: str,
    metric: str = "performance_ratio",
    ascending: bool = False,
    top_n: int = 10,
) -> dict[str, Any]:
    """
    Rank entities (rows) in the provided DataFrame based on a specific metric.

    Args:
        data_ref: Key in DataStore to the aggregated DataFrame (e.g. from compute_kpis).
        metric:   Column name to rank by.
        ascending: True for worst-first, False for best-first.
        top_n:    Number of top (or bottom) results to return.
    """
    df = DataStore.get_or_load(data_ref)
    if df is None or df.empty:
        return {"summary": "No data to rank.", "warnings": ["Empty/missing data"]}

    if metric not in df.columns:
        return {"summary": f"Metric '{metric}' not found.", "warnings": [f"Missing column: {metric}"]}

    df_ranked = df.sort_values(by=metric, ascending=ascending).head(top_n)
    res_ref = DataStore.store(df_ranked)

    return {
        "summary": f"Ranked top {top_n} by {metric} ({'ascending' if ascending else 'descending'}).",
        "data_ref": res_ref,
        "warnings": [],
    }


def compare_periods(
    ref_current: str,
    ref_baseline: str,
    join_cols: list[str],
    metrics_to_compare: list[str] = ["performance_ratio", "total_daily_yield_kwh"],
) -> dict[str, Any]:
    """
    Compare metrics between two periods (e.g., this month vs last month) for the same entities.
    
    Args:
        ref_current:  DataStore key for the current period aggregated DF.
        ref_baseline: DataStore key for the baseline period aggregated DF.
        join_cols:    Columns to join on (e.g. ['inverter_id']).
        metrics_to_compare: Metrics to calculate absolute and % differences for.
    """
    df_curr = DataStore.get_or_load(ref_current)
    df_base = DataStore.get_or_load(ref_baseline)

    if df_curr is None or df_base is None or df_curr.empty or df_base.empty:
        return {"summary": "Missing data for comparison.", "warnings": ["Data missing"]}
        
    df_merged = pd.merge(
        df_curr, df_base, on=join_cols, suffixes=("_curr", "_base")
    )
    
    if df_merged.empty:
        return {"summary": "No overlapping entities to compare.", "warnings": ["Empty intersection"]}

    for m in metrics_to_compare:
        c_curr, c_base = f"{m}_curr", f"{m}_base"
        if c_curr in df_merged.columns and c_base in df_merged.columns:
            df_merged[f"{m}_diff_abs"] = df_merged[c_curr] - df_merged[c_base]
            # Avoid division by zero
            base_safe = df_merged[c_base].replace(0, pd.NA)
            df_merged[f"{m}_diff_pct"] = (df_merged[f"{m}_diff_abs"] / base_safe) * 100

    res_ref = DataStore.store(df_merged)
    return {
        "summary": f"Compared {len(df_merged)} entities across periods.",
        "data_ref": res_ref,
        "warnings": [],
    }


def trend_analysis(
    data_ref: str,
    metric: str = "performance_ratio",
    window_days: int = 7,
) -> dict[str, Any]:
    """
    Calculate rolling averages to identify trends over time.

    Args:
        data_ref: Key to a daily aggregated DataFrame. Must contain 'log_date'.
        metric:   Metric to calculate trend for.
        window_days: Size of the rolling window.
    """
    df = DataStore.get_or_load(data_ref)
    if df is None or df.empty:
        return {"summary": "No data for trend analysis.", "warnings": ["Empty/missing data"]}
        
    if "log_date" not in df.columns or metric not in df.columns:
        return {"summary": "Missing log_date or metric column.", "warnings": ["Missing columns"]}

    # Sort by date
    df = df.sort_values(by="log_date")
    
    # Calculate rolling mean (assuming one row per date; if multiple groups, need to groupby)
    # Check if there are other grouping columns (like plant_id, block_id, inverter_id)
    group_cols = [c for c in ["plant_id", "block_id", "inverter_id"] if c in df.columns]
    
    if group_cols:
        df[f"{metric}_rolling_{window_days}d"] = (
            df.groupby(group_cols)[metric]
            .transform(lambda x: x.rolling(window_days, min_periods=1).mean())
        )
    else:
        df[f"{metric}_rolling_{window_days}d"] = df[metric].rolling(window_days, min_periods=1).mean()

    res_ref = DataStore.store(df)
    return {
        "summary": f"Calculated {window_days}-day rolling average for {metric}.",
        "data_ref": res_ref,
        "warnings": [],
    }
