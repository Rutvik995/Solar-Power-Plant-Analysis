"""
performance_agent.py – Performance Specialist Agent.

Responsible for:
  - Computing KPIs at inverter / block / plant level
  - Ranking entities by performance
  - Comparing two time periods (requires two pre-fetched data_refs)
  - Trend analysis over a period

Allowed tools: compute_kpis_tool, rank_entities_tool, compare_periods_tool, trend_analysis_tool
"""

from __future__ import annotations

from langchain_core.tools import tool

from solar_agent.agents.base_agent import BaseSpecialistAgent
from solar_agent.prompts.agent_prompts import PERFORMANCE_AGENT_PROMPT
from solar_agent.tools.performance_tools import (
    compare_periods,
    compute_kpis,
    rank_entities,
    trend_analysis,
)


@tool
def compute_kpis_tool(data_ref: str, level: str = "plant", period: str = "total") -> dict:
    """
    Compute solar KPIs (PR, yield, capacity factor, efficiency) aggregated to
    inverter / block / plant level over daily or total period.

    Args:
        data_ref: DataStore key from a prior fetch step.
        level:    'inverter', 'block', or 'plant'.
        period:   'daily' or 'total'.
    """
    return compute_kpis(data_ref, level=level, period=period)


@tool
def rank_entities_tool(
    data_ref: str,
    metric: str = "performance_ratio",
    ascending: bool = False,
    top_n: int = 10,
) -> dict:
    """
    Rank inverters / blocks / plants by a KPI column.
    Call compute_kpis_tool first to get an aggregated data_ref.

    Args:
        data_ref:  DataStore key from a prior compute_kpis_tool step.
        metric:    Column to rank by (e.g. 'performance_ratio', 'total_daily_yield_kwh').
        ascending: True = worst first. False = best first (default).
        top_n:     Number of results to return (default 10).
    """
    return rank_entities(data_ref, metric=metric, ascending=ascending, top_n=top_n)


@tool
def compare_periods_tool(
    ref_current: str,
    ref_baseline: str,
    join_cols: list[str],
    metrics_to_compare: list[str] = None,  # type: ignore[assignment]
) -> dict:
    """
    Compare KPIs between two time periods for the same entities.
    Both data_refs must come from prior compute_kpis_tool calls covering different date ranges.

    Args:
        ref_current:        DataStore key for the current period aggregated DF.
        ref_baseline:       DataStore key for the baseline period aggregated DF.
        join_cols:          Columns to join on (e.g. ['inverter_id'] or ['plant_id']).
        metrics_to_compare: Metrics to diff (default: ['performance_ratio', 'total_daily_yield_kwh']).
    """
    default_metrics = ["performance_ratio", "total_daily_yield_kwh"]
    return compare_periods(
        ref_current,
        ref_baseline,
        join_cols=join_cols,
        metrics_to_compare=metrics_to_compare or default_metrics,
    )


@tool
def trend_analysis_tool(
    data_ref: str,
    metric: str = "performance_ratio",
    window_days: int = 7,
) -> dict:
    """
    Compute a rolling-average trend of a KPI metric over time.
    Requires a daily-level data_ref (level='plant'/'block'/'inverter', period='daily').

    Args:
        data_ref:    DataStore key to a daily-aggregated DataFrame containing 'log_date'.
        metric:      Column to trend (e.g. 'performance_ratio', 'total_daily_yield_kwh').
        window_days: Rolling window in days (default 7).
    """
    return trend_analysis(data_ref, metric=metric, window_days=window_days)


class PerformanceAgent(BaseSpecialistAgent):
    name = "performance"
    system_prompt = PERFORMANCE_AGENT_PROMPT
    tools = [
        compute_kpis_tool,
        rank_entities_tool,
        compare_periods_tool,
        trend_analysis_tool,
    ]
