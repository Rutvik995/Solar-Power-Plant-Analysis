"""
solar_agent/tools/charts.py – Deterministic chart generation using Plotly.

Returns Plotly figure JSON:
  - 'bar': rankings or per-plant soiling
  - 'line': trends over time
No LLM calls are made.
"""
from __future__ import annotations

import json
from typing import Any

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

from solar_agent.graph.data_store import DataStore


def make_chart(data_ref: str, kind: str = "bar") -> str:
    """
    Generate a Plotly figure from data_ref and return its JSON representation.

    Args:
        data_ref: Key into DataStore for DataFrame.
        kind: 'bar' for rankings or per-plant soiling, 'line' for trends.

    Returns:
        Valid Plotly figure JSON string.
    """
    df = DataStore.get_or_load(data_ref)
    if df is None or df.empty:
        fig = go.Figure()
        fig.update_layout(title="No data available for chart")
        return fig.to_json()

    df = df.copy()

    if kind == "line":
        # Trends over time
        x_col = None
        for candidate in ["log_date", "date", "timestamp"]:
            if candidate in df.columns:
                x_col = candidate
                break
        if not x_col:
            x_col = df.columns[0]

        # Numeric y-axis
        y_col = None
        for candidate in ["performance_ratio", "daily_yield_kwh", "total_daily_yield_kwh", "soiling_ratio", "yield"]:
            if candidate in df.columns:
                y_col = candidate
                break
        if not y_col:
            numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
            y_col = numeric_cols[0] if numeric_cols else df.columns[-1]

        color_col = None
        for candidate in ["inverter_id", "plant_id", "block_name", "block_id"]:
            if candidate in df.columns and df[candidate].nunique() > 1:
                color_col = candidate
                break

        fig = px.line(
            df,
            x=x_col,
            y=y_col,
            color=color_col,
            title=f"Trend: {y_col} vs {x_col}",
            markers=True,
        )
    else:
        # kind == "bar" (rankings or per-plant soiling)
        soiling_cols = [c for c in df.columns if "soiling" in c.lower() or "loss" in c.lower()]
        plant_cols = [c for c in ["plant_id", "Plant", "plant"] if c in df.columns]

        if soiling_cols and plant_cols:
            x_col = plant_cols[0]
            y_col = soiling_cols[0]
            fig = px.bar(
                df,
                x=x_col,
                y=y_col,
                title=f"Per-Plant Soiling: {y_col}",
                text_auto=True,
            )
        else:
            # Rankings
            x_col = None
            for candidate in ["inverter_id", "plant_id", "block_id", "block_name"]:
                if candidate in df.columns:
                    x_col = candidate
                    break
            if not x_col:
                x_col = df.columns[0]

            y_col = None
            for candidate in ["performance_ratio", "pr", "daily_yield_kwh", "total_yield_kwh", "total_daily_yield_kwh"]:
                if candidate in df.columns:
                    y_col = candidate
                    break
            if not y_col:
                numeric_cols = df.select_dtypes(include=["number"]).columns.tolist()
                y_col = numeric_cols[0] if numeric_cols else df.columns[-1]

            if y_col in df.columns and pd.api.types.is_numeric_dtype(df[y_col]):
                df = df.sort_values(by=y_col, ascending=False)

            fig = px.bar(
                df.head(20),
                x=x_col,
                y=y_col,
                title=f"Ranking: {y_col}",
                text_auto=".3f" if "ratio" in str(y_col) else True,
            )

    fig.update_layout(template="plotly_white")
    return fig.to_json()
