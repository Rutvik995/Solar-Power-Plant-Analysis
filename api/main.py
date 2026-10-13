"""
api/main.py — FastAPI service for Solar Power Plant Analysis.

Features:
  - CORS configured for http://localhost:5173
  - All responses return strict JSON (dates as ISO strings, NaN/Inf as null)
  - GET /api/meta: plants, blocks, min/max log_date, LLM provider, mode
  - GET /api/dashboard/*: KPI metrics, daily breakdown, block PR, inverter heatmap, soiling, flagged
  - POST /api/chat: runs the LangGraph pipeline with error mapping for HTTP 429 and BudgetExceeded
"""

from __future__ import annotations

import json
import logging
import math
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from solar_agent.config.settings import settings
from solar_agent.db import queries as q
from solar_agent.graph.data_store import DataStore
from solar_agent.graph.graph import build_graph, initial_state
from solar_agent.state import BudgetExceeded
from solar_agent.tools import metrics as m
from solar_agent.tools.fault_tools import _EXCLUDE_YIELD_CODES, _FAULT_CODES

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON Sanitization Helpers (NaN -> null, date -> ISO string)
# ---------------------------------------------------------------------------

def clean_for_json(obj: Any) -> Any:
    """Recursively clean data structures so NaNs become None and dates become ISO."""
    if obj is None:
        return None
    if isinstance(obj, (float, np.floating)):
        if math.isnan(obj) or math.isinf(obj):
            return None
        return float(obj)
    if isinstance(obj, (int, np.integer)):
        return int(obj)
    if isinstance(obj, (date, datetime)):
        return obj.isoformat()
    if isinstance(obj, (pd.Timestamp, np.datetime64)):
        return pd.to_datetime(obj).isoformat()
    if isinstance(obj, (list, tuple, set)):
        return [clean_for_json(x) for x in obj]
    if isinstance(obj, np.ndarray):
        return [clean_for_json(x) for x in obj.tolist()]
    try:
        if pd.isna(obj):
            return None
    except Exception:
        pass
    if isinstance(obj, dict):
        return {str(k): clean_for_json(v) for k, v in obj.items()}
    return obj


class CleanJSONResponse(JSONResponse):
    """Custom JSONResponse that converts NaNs to null and dates to ISO strings."""

    def render(self, content: Any) -> bytes:
        cleaned = clean_for_json(content)
        return json.dumps(
            cleaned,
            ensure_ascii=False,
            allow_nan=False,
            default=str,
        ).encode("utf-8")


# ---------------------------------------------------------------------------
# FastAPI Application & CORS
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Solar Power Plant Analysis API",
    description="Operational telemetry analytics and multi-agent assistant for solar plants",
    version="1.0.0",
    default_response_class=CleanJSONResponse,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# In-memory checkpointer registry for multi-turn chats
# ---------------------------------------------------------------------------

_checkpointers: dict[str, MemorySaver] = {}


def get_memory_saver(thread_id: str) -> MemorySaver:
    if thread_id not in _checkpointers:
        _checkpointers[thread_id] = MemorySaver()
    return _checkpointers[thread_id]


def is_rate_limit_error(exc: Exception) -> bool:
    if getattr(exc, "status_code", None) == 429:
        return True
    resp = getattr(exc, "response", None)
    if resp and getattr(resp, "status_code", None) == 429:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "rate_limit" in msg


def parse_dashboard_params(
    plant_id: Optional[str] = None,
    start: Optional[str] = None,
    end: Optional[str] = None,
) -> tuple[Optional[list[int]], date, date]:
    """Parse and validate dashboard filter parameters."""
    plant_ids: Optional[list[int]] = None
    if plant_id is not None and str(plant_id).lower() not in ("all", "", "none"):
        try:
            plant_ids = [int(plant_id)]
        except ValueError:
            pass

    max_d = q.get_max_log_date()
    min_d = q.get_min_log_date()

    if end:
        try:
            end_date = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            end_date = max_d
    else:
        end_date = max_d

    if start:
        try:
            start_date = datetime.strptime(start, "%Y-%m-%d").date()
        except ValueError:
            start_date = min_d
    else:
        start_date = min_d

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    return plant_ids, start_date, end_date


# ---------------------------------------------------------------------------
# Metadata Endpoint
# ---------------------------------------------------------------------------

@app.get("/api/meta")
def get_metadata():
    """
    Returns plants (id, name, capacity_mwp), blocks, min/max log_date,
    LLM provider and mode.
    """
    plants_df = q.get_all_plants()
    blocks_df = q.get_all_blocks()
    min_date = q.get_min_log_date()
    max_date = q.get_max_log_date()

    plants = [
        {
            "id": int(r["plant_id"]),
            "name": str(r["plant_name"]),
            "capacity_mwp": float(r["capacity_mwp"]) if pd.notna(r["capacity_mwp"]) else None,
        }
        for _, r in plants_df.iterrows()
    ]

    blocks = [
        {
            "id": int(r["block_id"]) if str(r["block_id"]).isdigit() else str(r["block_id"]),
            "name": str(r["block_name"]),
            "plant_id": int(r["plant_id"]),
            "plant_name": str(r["plant_name"]),
        }
        for _, r in blocks_df.iterrows()
    ]

    return {
        "plants": plants,
        "blocks": blocks,
        "min_log_date": min_date.isoformat(),
        "max_log_date": max_date.isoformat(),
        "llm_provider": settings.llm_provider,
        "mode": getattr(settings, "llm_mode", "replay"),
    }


# ---------------------------------------------------------------------------
# Dashboard Endpoints (No LLM calls)
# ---------------------------------------------------------------------------

@app.get("/api/dashboard/kpis")
def get_dashboard_kpis(
    plant_id: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    """
    Returns yield, PR, soiling loss %, fault days, previous-period values,
    and a daily sparkline.
    """
    plant_ids, start_date, end_date = parse_dashboard_params(plant_id, start, end)
    df = q.fetch_inverter_daily(start_date, end_date, plant_ids=plant_ids)

    if df.empty:
        return {
            "yield": 0.0,
            "pr": None,
            "soiling_loss_pct": None,
            "fault_days": 0,
            "previous_period": {
                "yield": None,
                "pr": None,
                "soiling_loss_pct": None,
                "fault_days": None,
            },
            "sparkline": [],
            "daily_sparkline": [],
        }

    # 1. Yield
    curr_yield = float(round(df["total_daily_yield_kwh"].sum(), 2))

    # 2. Performance Ratio (BR-01, BR-04)
    valid_mask = df["total_solar_radiation_kwh_m2"] > settings.min_irradiance_kwh_m2
    valid_df = df[valid_mask]
    tot_yield_valid = valid_df["total_daily_yield_kwh"].sum()
    tot_exp_valid = (valid_df["rated_dc_kw"] * valid_df["total_solar_radiation_kwh_m2"]).sum()
    curr_pr = float(round(tot_yield_valid / tot_exp_valid, 4)) if tot_exp_valid > 0 else None

    # 3. Soiling loss %
    df_soiling = m.compute_soiling_loss(df)
    s_mean = df_soiling["soiling_loss_pct"].mean() if "soiling_loss_pct" in df_soiling else None
    curr_soiling = float(round(s_mean, 2)) if pd.notna(s_mean) else None

    # 4. Fault days
    fault_mask = df["status_code"].isin(_FAULT_CODES) | (
        (df["total_solar_radiation_kwh_m2"] > settings.min_irradiance_kwh_m2)
        & (~df["status_code"].isin(_EXCLUDE_YIELD_CODES))
        & (df["total_daily_yield_kwh"] <= settings.zero_generation_max_yield_kwh)
    )
    curr_fault_days = int(df[fault_mask]["log_date"].nunique())

    # 5. Sparkline
    daily_agg = m.aggregate_pr(df, ["log_date"])
    sparkline = [
        {
            "date": r["log_date"].isoformat(),
            "yield": float(round(r["total_yield_kwh"], 2)),
            "pr": float(round(r["performance_ratio"], 4)) if pd.notna(r["performance_ratio"]) else None,
        }
        for _, r in daily_agg.iterrows()
    ]

    # 6. Previous Period
    duration = (end_date - start_date).days + 1
    prev_end_date = start_date - timedelta(days=1)
    prev_start_date = prev_end_date - timedelta(days=duration - 1)
    min_system_date = q.get_min_log_date()

    if prev_end_date >= min_system_date:
        prev_df = q.fetch_inverter_daily(max(min_system_date, prev_start_date), prev_end_date, plant_ids=plant_ids)
    else:
        prev_df = pd.DataFrame()

    if not prev_df.empty:
        prev_yield = float(round(prev_df["total_daily_yield_kwh"].sum(), 2))
        pv_mask = prev_df["total_solar_radiation_kwh_m2"] > settings.min_irradiance_kwh_m2
        pv_valid = prev_df[pv_mask]
        py_valid = pv_valid["total_daily_yield_kwh"].sum()
        pe_valid = (pv_valid["rated_dc_kw"] * pv_valid["total_solar_radiation_kwh_m2"]).sum()
        prev_pr = float(round(py_valid / pe_valid, 4)) if pe_valid > 0 else None

        prev_soiling_df = m.compute_soiling_loss(prev_df)
        ps_mean = prev_soiling_df["soiling_loss_pct"].mean() if "soiling_loss_pct" in prev_soiling_df else None
        prev_soiling = float(round(ps_mean, 2)) if pd.notna(ps_mean) else None

        prev_f_mask = prev_df["status_code"].isin(_FAULT_CODES) | (
            (prev_df["total_solar_radiation_kwh_m2"] > settings.min_irradiance_kwh_m2)
            & (~prev_df["status_code"].isin(_EXCLUDE_YIELD_CODES))
            & (prev_df["total_daily_yield_kwh"] <= settings.zero_generation_max_yield_kwh)
        )
        prev_fault_days = int(prev_df[prev_f_mask]["log_date"].nunique())
    else:
        prev_yield = None
        prev_pr = None
        prev_soiling = None
        prev_fault_days = None

    return {
        "yield": curr_yield,
        "pr": curr_pr,
        "soiling_loss_pct": curr_soiling,
        "fault_days": curr_fault_days,
        "previous_period": {
            "yield": prev_yield,
            "pr": prev_pr,
            "soiling_loss_pct": prev_soiling,
            "fault_days": prev_fault_days,
        },
        "sparkline": sparkline,
        "daily_sparkline": sparkline,
    }


@app.get("/api/dashboard/daily")
def get_dashboard_daily(
    plant_id: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    """Returns daily date, yield, irradiance."""
    plant_ids, start_date, end_date = parse_dashboard_params(plant_id, start, end)
    df = q.fetch_inverter_daily(start_date, end_date, plant_ids=plant_ids)
    if df.empty:
        return []

    daily = df.groupby("log_date").agg(
        yield_kwh=("total_daily_yield_kwh", "sum"),
        irradiance=("total_solar_radiation_kwh_m2", "mean"),
    ).reset_index().sort_values("log_date")

    return [
        {
            "date": r["log_date"].isoformat(),
            "yield": float(round(r["yield_kwh"], 2)),
            "irradiance": float(round(r["irradiance"], 2)) if pd.notna(r["irradiance"]) else None,
        }
        for _, r in daily.iterrows()
    ]


@app.get("/api/dashboard/blocks")
def get_dashboard_blocks(
    plant_id: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    """Returns block PR for each block."""
    plant_ids, start_date, end_date = parse_dashboard_params(plant_id, start, end)
    df = q.fetch_inverter_daily(start_date, end_date, plant_ids=plant_ids)
    if df.empty:
        return []

    agg_blocks = m.aggregate_pr(df, ["block_id", "block_name", "plant_id"])
    return [
        {
            "block_id": str(r["block_id"]),
            "block_name": str(r["block_name"]),
            "plant_id": int(r["plant_id"]),
            "pr": float(round(r["performance_ratio"], 4)) if pd.notna(r["performance_ratio"]) else None,
            "performance_ratio": float(round(r["performance_ratio"], 4)) if pd.notna(r["performance_ratio"]) else None,
        }
        for _, r in agg_blocks.iterrows()
    ]


@app.get("/api/dashboard/heatmap")
def get_dashboard_heatmap(
    plant_id: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    """Returns inverter, date, PR."""
    plant_ids, start_date, end_date = parse_dashboard_params(plant_id, start, end)
    df = q.fetch_inverter_daily(start_date, end_date, plant_ids=plant_ids)
    if df.empty:
        return []

    df_pr = m.compute_performance_ratio(df)
    return [
        {
            "inverter": str(r["inverter_id"]),
            "inverter_id": str(r["inverter_id"]),
            "date": r["log_date"].isoformat(),
            "pr": float(round(r["performance_ratio"], 4)) if pd.notna(r["performance_ratio"]) else None,
            "performance_ratio": float(round(r["performance_ratio"], 4)) if pd.notna(r["performance_ratio"]) else None,
        }
        for _, r in df_pr.iterrows()
    ]


@app.get("/api/dashboard/soiling")
def get_dashboard_soiling(
    plant_id: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    """Returns plant, date, ratio."""
    plant_ids, start_date, end_date = parse_dashboard_params(plant_id, start, end)
    df_w = q.fetch_weather_daily(start_date, end_date, plant_ids=plant_ids)
    if df_w.empty:
        return []

    return [
        {
            "plant": str(r["plant_name"]),
            "plant_id": int(r["plant_id"]),
            "plant_name": str(r["plant_name"]),
            "date": r["log_date"].isoformat(),
            "ratio": float(round(r["soiling_ratio"], 4)) if pd.notna(r["soiling_ratio"]) else None,
            "soiling_ratio": float(round(r["soiling_ratio"], 4)) if pd.notna(r["soiling_ratio"]) else None,
        }
        for _, r in df_w.iterrows()
    ]


@app.get("/api/dashboard/flagged")
def get_dashboard_flagged(
    plant_id: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    """Returns flagged inverters: inverter, block, date, type, status code, PR."""
    plant_ids, start_date, end_date = parse_dashboard_params(plant_id, start, end)
    df = q.fetch_inverter_daily(start_date, end_date, plant_ids=plant_ids)
    if df.empty:
        return []

    df = m.compute_performance_ratio(df)
    try:
        df = m.compute_peer_deviation(df)
    except Exception:
        df["peer_deviation_score"] = np.nan

    flagged = []
    for _, r in df.iterrows():
        ftype = None
        sc = r.get("status_code")
        if sc in _FAULT_CODES:
            ftype = "Fault" if sc == 3 else "Offline"
        elif (
            r.get("total_solar_radiation_kwh_m2", 0) > settings.min_irradiance_kwh_m2
            and r.get("total_daily_yield_kwh", 999) <= settings.zero_generation_max_yield_kwh
            and sc not in _EXCLUDE_YIELD_CODES
        ):
            ftype = "Zero Generation"
        elif sc == 2:
            ftype = "Warning"
        elif pd.notna(r.get("peer_deviation_score")) and r.get("peer_deviation_score") < -settings.peer_underperformance_z_threshold:
            ftype = "Underperformance"

        if ftype:
            pr_val = float(round(r["performance_ratio"], 4)) if pd.notna(r.get("performance_ratio")) else None
            flagged.append({
                "inverter": str(r["inverter_id"]),
                "inverter_id": str(r["inverter_id"]),
                "block": str(r.get("block_name") or r["block_id"]),
                "block_id": str(r["block_id"]),
                "date": r["log_date"].isoformat(),
                "type": ftype,
                "status_code": int(sc) if pd.notna(sc) else None,
                "pr": pr_val,
                "performance_ratio": pr_val,
            })

    return flagged


# ---------------------------------------------------------------------------
# Multi-Agent Chat Endpoint
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    query: str
    thread_id: Optional[str] = None
    mode: Optional[str] = "replay"


@app.post("/api/chat")
def chat_endpoint(request: ChatRequest, req: Request):
    """
    Runs the multi-agent graph and returns:
    {answer_markdown, tables, charts, plan, warnings, period, sources, llm_calls, elapsed_ms, error}
    Maps HTTP 429 and BudgetExceeded to a clear error object.
    """
    start_time = time.time()
    thread_id = request.thread_id or str(uuid.uuid4())
    mode = request.mode or "replay"

    # Allow test injection of mocked LLM via app.state
    llm_override = getattr(req.app.state, "llm_override", None)

    try:
        checkpointer = get_memory_saver(thread_id)
        graph = build_graph(llm=llm_override, mode=mode, checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        state_input = initial_state(request.query.strip())
        result = graph.invoke(state_input, config=config)
    except BudgetExceeded as exc:
        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        return CleanJSONResponse(
            status_code=429,
            content={
                "answer_markdown": f"LLM call budget exceeded: {exc}",
                "tables": [],
                "charts": [],
                "plan": [],
                "warnings": [str(exc)],
                "period": None,
                "sources": [],
                "llm_calls": {
                    "planner": 0,
                    "agent": 0,
                    "synthesizer": 0,
                    "total": settings.max_llm_calls_per_query,
                    "budget": settings.max_llm_calls_per_query,
                },
                "elapsed_ms": elapsed_ms,
                "error": {
                    "code": 429,
                    "type": "BudgetExceeded",
                    "message": str(exc),
                },
            },
        )
    except Exception as exc:
        elapsed_ms = round((time.time() - start_time) * 1000, 2)
        if is_rate_limit_error(exc):
            return CleanJSONResponse(
                status_code=429,
                content={
                    "answer_markdown": "Rate limit exceeded (HTTP 429). Please wait a moment and retry.",
                    "tables": [],
                    "charts": [],
                    "plan": [],
                    "warnings": [str(exc)],
                    "period": None,
                    "sources": [],
                    "llm_calls": {
                        "planner": 0,
                        "agent": 0,
                        "synthesizer": 0,
                        "total": 0,
                        "budget": settings.max_llm_calls_per_query,
                    },
                    "elapsed_ms": elapsed_ms,
                    "error": {
                        "code": 429,
                        "type": "RateLimitError",
                        "message": "Rate limit exceeded (HTTP 429). Please wait and try again.",
                    },
                },
            )
        return CleanJSONResponse(
            status_code=400,
            content={
                "answer_markdown": f"Execution error: {exc}",
                "tables": [],
                "charts": [],
                "plan": [],
                "warnings": [str(exc)],
                "period": None,
                "sources": [],
                "llm_calls": {
                    "planner": 0,
                    "agent": 0,
                    "synthesizer": 0,
                    "total": 0,
                    "budget": settings.max_llm_calls_per_query,
                },
                "elapsed_ms": elapsed_ms,
                "error": {
                    "code": 400,
                    "type": type(exc).__name__,
                    "message": str(exc),
                },
            },
        )

    elapsed_ms = round((time.time() - start_time) * 1000, 2)
    results_dict = result.get("results") or {}

    # 1. Tables: [{title, columns, rows}]
    tables = []
    for tid, tr in results_dict.items():
        if tr and tr.data_ref:
            df_table = DataStore.get(tr.data_ref)
            if df_table is not None and not df_table.empty:
                df_head = df_table.head(100).copy()
                for c in df_head.columns:
                    if pd.api.types.is_datetime64_any_dtype(df_head[c]) or any(
                        isinstance(v, (date, datetime)) for v in df_head[c].dropna()
                    ):
                        df_head[c] = df_head[c].apply(
                            lambda x: x.isoformat() if hasattr(x, "isoformat") else str(x) if pd.notna(x) else None
                        )
                cols = [str(c) for c in df_head.columns]
                rows = df_head.values.tolist()
                tables.append({
                    "title": f"Task {tid}: {tr.summary or 'Data Table'}",
                    "columns": cols,
                    "rows": rows,
                })

    # 2. Charts: [plotly_json]
    charts = []
    for tid, tr in results_dict.items():
        if tr and tr.metrics and "chart_json" in tr.metrics:
            cj = tr.metrics["chart_json"]
            if isinstance(cj, str):
                try:
                    charts.append(json.loads(cj))
                except Exception:
                    charts.append(cj)
            else:
                charts.append(cj)

    # 3. Plan: [{id, agent, tool, depends_on, status}]
    plan_data = []
    plan_obj = result.get("plan")
    if plan_obj and hasattr(plan_obj, "tasks"):
        for t in plan_obj.tasks:
            tr = results_dict.get(t.id)
            st_val = tr.status.value if tr and hasattr(tr, "status") and hasattr(tr.status, "value") else "pending"
            plan_data.append({
                "id": t.id,
                "agent": t.agent,
                "tool": t.tool_hint or getattr(t, "tool", None),
                "depends_on": t.depends_on,
                "status": st_val,
            })

    # 4. Period: {start, end}
    period = None
    if plan_obj and hasattr(plan_obj, "scope") and plan_obj.scope:
        s = plan_obj.scope
        period = {
            "start": s.start_date.isoformat(),
            "end": s.end_date.isoformat(),
        }

    # 5. Sources
    sources = []
    for tr in results_dict.values():
        if tr and getattr(tr, "tool_calls_made", None):
            for tc in tr.tool_calls_made:
                if tc not in sources:
                    sources.append(tc)
    if not sources:
        sources = ["daily_inverter_telemetry", "daily_weather_telemetry"]

    # 6. LLM calls: {planner, agent, synthesizer, total, budget}
    total_calls = result.get("llm_call_count", 0)
    budget = settings.max_llm_calls_per_query
    replan_count = result.get("replan_count", 0)
    planner_calls = (1 + replan_count) if total_calls > 0 else 0
    synth_calls = 1 if (result.get("final_answer") and total_calls >= (planner_calls + 1)) else 0
    agent_calls = max(0, total_calls - planner_calls - synth_calls)
    llm_calls = {
        "planner": planner_calls,
        "agent": agent_calls,
        "synthesizer": synth_calls,
        "total": total_calls,
        "budget": budget,
    }

    warnings = list(result.get("warnings") or [])

    # Check for budget warning in final answer
    err_obj = None
    ans = result.get("final_answer") or ""
    if "⚠ LLM call budget exceeded" in ans:
        err_obj = {
            "code": 429,
            "type": "BudgetExceeded",
            "message": "LLM call budget exceeded during pipeline execution.",
        }

    return {
        "answer_markdown": ans,
        "tables": tables,
        "charts": charts,
        "plan": plan_data,
        "warnings": warnings,
        "period": period,
        "sources": sources,
        "llm_calls": llm_calls,
        "elapsed_ms": elapsed_ms,
        "error": err_obj,
    }
