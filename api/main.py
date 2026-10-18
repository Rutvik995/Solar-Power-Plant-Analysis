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
import re
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from typing import Any, Optional

import numpy as np
import pandas as pd
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from langgraph.checkpoint.memory import MemorySaver
from pydantic import BaseModel

from solar_agent.config.settings import settings
from solar_agent.db import queries as q
from solar_agent.db.chat_storage import (
    create_message,
    create_session,
    delete_message_pair,
    delete_messages_after,
    delete_session,
    get_last_completed_assistant_context,
    get_message,
    get_message_by_run_id,
    get_session,
    init_chat_db,
    list_messages,
    list_sessions,
    update_message,
    update_session,
)
from solar_agent.graph.data_store import DataStore
from solar_agent.graph.graph import build_graph, initial_state
from solar_agent.graph.synthesizer import (
    clean_task_summary,
    extract_structured_answers,
    normalize_spaces,
)
from solar_agent.state import (
    BudgetExceeded,
    Cancelled,
    CancelToken,
    get_cancel_token,
    register_cancel_token,
    unregister_cancel_token,
)
from solar_agent.graph.llm_recorder import CacheMissError
from solar_agent.tools.table_formatter import (
    build_analysis_tables,
    load_glossary,
    sanitize_answer_text,
    translate_plain_language,
)
from solar_agent.tools import metrics as m
from solar_agent.tools.fault_tools import _EXCLUDE_YIELD_CODES, _FAULT_CODES
from api.dashboard import router as dashboard_router

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# JSON Sanitization Helpers (NaN -> null, date -> ISO string)
# ---------------------------------------------------------------------------

def clean_for_json(obj: Any) -> Any:
    """Recursively clean data structures so NaNs become None and dates become ISO."""
    if obj is None:
        return None
    if isinstance(obj, bool):
        return obj
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


@app.on_event("startup")
def on_startup():
    """Apply chat_schema.sql and mark stale running messages as stopped."""
    try:
        init_chat_db()
    except Exception as exc:
        logger.error("Failed to initialize chat database on startup: %s", exc)


app.include_router(dashboard_router)



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
    """Returns flagged inverters: one row per inverter and date with flagged_by list."""
    plant_ids, start_date, end_date = parse_dashboard_params(plant_id, start, end)
    df = q.fetch_inverter_daily(start_date, end_date, plant_ids=plant_ids)
    if df.empty:
        return []

    df = m.compute_performance_ratio(df)
    try:
        df = m.compute_peer_deviation(df)
    except Exception:
        df["peer_deviation_score"] = np.nan

    grouped: dict[tuple[str, str], dict[str, Any]] = {}
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
            inv_id = str(r["inverter_id"])
            d_str = r["log_date"].isoformat()
            key = (inv_id, d_str)
            pr_val = float(round(r["performance_ratio"], 4)) if pd.notna(r.get("performance_ratio")) else None
            blk_str = str(r.get("block_name") or r["block_id"])
            if key not in grouped:
                grouped[key] = {
                    "inverter": inv_id,
                    "inverter_id": inv_id,
                    "block": blk_str,
                    "block_id": str(r["block_id"]),
                    "date": d_str,
                    "flagged_by": [ftype],
                    "type": ftype,
                    "status_code": int(sc) if pd.notna(sc) else None,
                    "pr": pr_val,
                    "performance_ratio": pr_val,
                }
            else:
                if ftype not in grouped[key]["flagged_by"]:
                    grouped[key]["flagged_by"].append(ftype)
                    grouped[key]["type"] = ", ".join(grouped[key]["flagged_by"])

    return list(grouped.values())


# ---------------------------------------------------------------------------
# Multi-Agent Chat Endpoint
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Structured Payload Helpers
# ---------------------------------------------------------------------------

def _format_plan_steps(plan_obj: Any, results_dict: dict[str, Any]) -> list[dict[str, Any]]:
    agent_default_tools = {
        "data": "fetch_inverter_daily",
        "performance": "compute_kpis_tool",
        "fault": "detect_status_faults_tool",
        "environment": "estimate_soiling_loss_tool",
    }
    plan_data = []
    if plan_obj and hasattr(plan_obj, "tasks"):
        for t in plan_obj.tasks:
            tr = results_dict.get(t.id) if results_dict else None
            st_val = tr.status.value if tr and hasattr(tr, "status") and hasattr(tr.status, "value") else "pending"

            real_tool = None
            if tr and getattr(tr, "tool_calls_made", None) and tr.tool_calls_made:
                real_tool = tr.tool_calls_made[0]
            if not real_tool:
                hint = getattr(t, "tool_hint", None) or getattr(t, "tool", None)
                if hint and hint != "auto":
                    real_tool = hint
            if not real_tool or real_tool == "auto":
                real_tool = agent_default_tools.get(t.agent, "fetch_inverter_daily")

            summary_clean = clean_task_summary(tr.summary) if (tr and tr.summary) else f"{t.agent.capitalize()} analysis completed."
            plan_data.append({
                "id": t.id,
                "task": summary_clean,
                "agent": t.agent,
                "tool": real_tool,
                "depends_on": t.depends_on,
                "status": st_val,
            })
    return plan_data


def get_suggestions(intent: str, is_out_of_scope: bool = False, plan_obj: Any = None) -> list[str]:
    """Return up to 4 contextual suggestions chosen by answer type / intent."""
    if is_out_of_scope:
        return [
            "Give me a short introduction to all the plants",
            "What was the performance ratio of Plant 1?",
            "Were there any inverter faults in September 2026?",
            "How much energy was lost to soiling in Plant 2?",
        ]

    intent_str = str(intent).lower()
    if "overview" in intent_str:
        return [
            "Show block details for Plant 1",
            "List inverters in Block A of Plant 1",
            "What was the performance ratio of all plants?",
            "Were there any inverter faults detected?",
        ]
    elif any(k in intent_str for k in ["performance", "summary", "ranking", "trend", "comparison"]):
        return [
            "Show as chart",
            "Were there any inverter faults during this period?",
            "How much energy was lost to soiling?",
            "Compare performance across all plants",
        ]
    elif any(k in intent_str for k in ["fault", "root_cause"]):
        return [
            "Show performance ratio for flagged inverters",
            "What was the peer irradiance during these events?",
            "Show as chart",
            "Check soiling losses for this period",
        ]
    elif any(k in intent_str for k in ["environment", "soiling"]):
        return [
            "When was the last cleaning event?",
            "What was the total energy loss to soiling?",
            "Show as chart",
            "Were there any inverter faults?",
        ]
    else:
        return [
            "Give me a short introduction to all the plants",
            "Show as chart",
            "Check for faults in Plant 1",
            "What was the performance ratio?",
        ]


def format_structured_payload(
    result: dict[str, Any],
    elapsed_ms: float,
    exc: Optional[Exception] = None,
) -> dict[str, Any]:
    """Build unified structured payload dictionary according to the specifications."""
    results_dict = dict(result.get("results") or {})
    if exc and hasattr(exc, "partial_results") and exc.partial_results:
        results_dict.update(exc.partial_results)

    # 1. Error & Text outputs
    err_obj = None
    error_code = None
    is_refusal = bool(result.get("is_out_of_scope"))

    if is_refusal:
        friendly_refusal = "I can’t help with that, but I can answer questions about your solar plants."
        ans = friendly_refusal
        short_answer = friendly_refusal
        headline = f"**{friendly_refusal}**"
        explanation = ""
        key_points = []
        notes = []
        details_markdown = ""
        raw_assumptions = []
        raw_warnings = []
    elif isinstance(exc, CacheMissError) or (exc and type(exc).__name__ == "CacheMissError"):
        msg = "This query was not recorded in replay cache. Switch to live or record mode to execute."
        ans = msg
        short_answer = msg
        headline = f"**{msg}**"
        explanation = msg
        key_points = []
        notes = []
        details_markdown = ""
        error_code = "not_recorded"
        err_obj = {
            "code": 404,
            "error_code": "not_recorded",
            "type": "CacheMissError",
            "message": msg,
        }
        raw_assumptions = []
        raw_warnings = []
    elif isinstance(exc, Cancelled):
        ans = "Execution stopped by user."
        short_answer = "Execution stopped by user."
        headline = "**Execution stopped by user.**"
        explanation = "Execution was stopped before completion."
        key_points = []
        notes = []
        details_markdown = "Execution was stopped before completion."
        err_obj = {
            "code": 499,
            "type": "Cancelled",
            "message": str(exc) or "Execution stopped by user.",
        }
        raw_assumptions = []
        raw_warnings = []
    elif isinstance(exc, BudgetExceeded):
        ans = f"LLM call budget exceeded: {exc}"
        short_answer = f"LLM call budget exceeded: {exc}"
        headline = f"**LLM call budget exceeded: {exc}**"
        explanation = "The pipeline exceeded its allotted LLM call budget."
        key_points = []
        notes = []
        details_markdown = ""
        err_obj = {
            "code": 429,
            "type": "BudgetExceeded",
            "message": str(exc),
        }
        raw_assumptions = []
        raw_warnings = []
    elif exc is not None:
        ans = f"Execution error: {exc}"
        short_answer = f"Execution error: {exc}"
        headline = f"**Execution error: {exc}**"
        explanation = str(exc)
        key_points = []
        notes = []
        details_markdown = ""
        err_obj = {
            "code": 400,
            "type": type(exc).__name__,
            "message": str(exc),
        }
        raw_assumptions = []
        raw_warnings = []
    else:
        # Check if result was flagged as not_recorded
        if result.get("error_code") == "not_recorded":
            msg = result.get("error_message") or "This query was not recorded in replay cache. Switch to live or record mode to execute."
            ans = msg
            short_answer = msg
            headline = f"**{msg}**"
            explanation = msg
            key_points = []
            notes = []
            details_markdown = ""
            error_code = "not_recorded"
            err_obj = {
                "code": 404,
                "error_code": "not_recorded",
                "type": "CacheMissError",
                "message": msg,
            }
            raw_assumptions = []
            raw_warnings = []
        else:
            ans = normalize_spaces(result.get("final_answer") or "")
            headline = result.get("headline")
            explanation = result.get("explanation")
            key_points = list(result.get("key_points") or [])
            notes = list(result.get("notes") or [])

            short_answer = result.get("short_answer")
            details_markdown = result.get("details_markdown")
            if not short_answer or not details_markdown:
                sa_ext, dm_ext = extract_structured_answers(ans)
                if not short_answer:
                    short_answer = sa_ext
                if not details_markdown:
                    details_markdown = dm_ext

            if not headline:
                headline = f"**{short_answer.strip('*')}**" if short_answer else "**Analysis complete.**"
                if not (headline.startswith("**") and headline.endswith("**")):
                    headline = f"**{headline.strip('*')}**"
            if not short_answer:
                short_answer = headline.strip("*").strip()
            if not explanation:
                explanation = details_markdown or ""

            short_answer = normalize_spaces(short_answer)
            details_markdown = normalize_spaces(details_markdown)
            if "⚠ LLM call budget exceeded" in ans:
                err_obj = {
                    "code": 429,
                    "type": "BudgetExceeded",
                    "message": "LLM call budget exceeded during pipeline execution.",
                }
            raw_assumptions = list(result.get("assumptions") or [])
            raw_warnings = list(result.get("warnings") or [])

    if isinstance(exc, Cancelled) and "Execution stopped by user." not in raw_warnings:
        raw_warnings.append("Execution stopped by user.")
    elif isinstance(exc, BudgetExceeded) and str(exc) not in raw_warnings:
        raw_warnings.append(str(exc))

    # Translate assumptions and warnings into plain language
    assumptions = [translate_plain_language(a) for a in raw_assumptions if a]
    warnings = [translate_plain_language(w) for w in raw_warnings if w]
    if not notes and assumptions:
        notes = list(assumptions)

    # Sanitize answer fields (no technical details in answer fields)
    headline = sanitize_answer_text(headline or "")
    explanation = sanitize_answer_text(explanation or "")
    key_points = [sanitize_answer_text(kp) for kp in key_points]
    notes = [sanitize_answer_text(n) for n in notes]

    # 2. Tables: ONLY from analysis task outputs (performance, fault, environment)
    if is_refusal:
        tables = []
    elif "tables" in result and isinstance(result["tables"], list) and result["tables"]:
        tables = result["tables"]
    else:
        try:
            tables = build_analysis_tables(result.get("plan"), results_dict)
        except Exception as t_err:
            logger.warning("Failed to build analysis tables: %s", t_err)
            tables = []
        except Exception as t_err:
            logger.warning("Failed to build analysis tables: %s", t_err)
            tables = []

    # 3. Charts: [plotly_json]
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

    # 4. Plan: [{id, task, agent, tool, depends_on, status}]
    plan_data = _format_plan_steps(result.get("plan"), results_dict)

    # 5. Period: {start, end}
    period = None
    plan_obj = result.get("plan")
    if plan_obj and hasattr(plan_obj, "scope") and plan_obj.scope:
        s = plan_obj.scope
        period = {
            "start": s.start_date.isoformat(),
            "end": s.end_date.isoformat(),
        }

    # 6. Sources: tool names
    sources = []
    for tr in results_dict.values():
        if tr and getattr(tr, "tool_calls_made", None):
            for tc in tr.tool_calls_made:
                if tc not in sources:
                    sources.append(tc)
    if not sources:
        sources = ["fetch_inverter_daily", "compute_kpis_tool"]

    # 7. LLM calls: computed per request
    llm_calls = result.get("llm_calls")
    if not llm_calls:
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

    # 8. Glossary
    glossary = load_glossary()

    # 9. Trace: technical details (row counts, task IDs, tool names, data_ref, call counts)
    row_counts: dict[str, int] = {}
    data_refs: dict[str, str] = {}
    for tid, tr in results_dict.items():
        if tr:
            if hasattr(tr, "metrics") and tr.metrics and "row_count" in tr.metrics:
                row_counts[tid] = tr.metrics["row_count"]
            if hasattr(tr, "data_ref") and tr.data_ref:
                data_refs[tid] = tr.data_ref
                if tid not in row_counts:
                    df = DataStore.get(tr.data_ref)
                    if df is not None:
                        row_counts[tid] = len(df)

    trace = {
        "plan": plan_data,
        "llm_calls": llm_calls,
        "tool_names": sources,
        "data_refs": data_refs,
        "row_counts": row_counts,
    }

    intent_val = None
    if plan_obj and hasattr(plan_obj, "intent"):
        intent_val = getattr(plan_obj.intent, "value", str(plan_obj.intent))
    elif result.get("intent"):
        intent_val = str(result.get("intent"))

    suggestions = get_suggestions(
        intent=intent_val or "summary",
        is_out_of_scope=is_refusal,
        plan_obj=plan_obj,
    )

    payload = {
        "headline": headline,
        "explanation": explanation,
        "key_points": key_points,
        "notes": notes,
        "short_answer": short_answer or headline,
        "details_markdown": details_markdown,
        "answer_markdown": ans,
        "tables": tables,
        "charts": charts,
        "plan": plan_data,
        "assumptions": notes if notes else assumptions,
        "warnings": warnings,
        "period": period,
        "sources": sources,
        "glossary": glossary,
        "trace": trace,
        "llm_calls": llm_calls,
        "elapsed_ms": elapsed_ms,
        "error": err_obj,
        "suggestions": suggestions[:4],
        "is_out_of_scope": is_refusal,
        "intent": intent_val,
    }
    if error_code:
        payload["error_code"] = error_code

    return payload



# ---------------------------------------------------------------------------
# Background Run Tracking & Execution
# ---------------------------------------------------------------------------

_RUN_STATES: dict[str, dict[str, Any]] = {}
_RUN_THREADS: dict[str, threading.Thread] = {}
_RUNS_LOCK = threading.Lock()


def _execute_graph_run(
    run_id: str,
    asst_msg_id: str,
    session_id: str,
    query: str,
    mode: str,
    prev_query: Optional[str],
    prev_tasks: Optional[list[dict]],
    prev_scope: Optional[dict],
    llm_override: Any = None,
) -> None:
    t_start = time.perf_counter()
    cancel_token = get_cancel_token(run_id)

    state_input = initial_state(query)
    state_input["run_id"] = run_id
    if prev_query:
        state_input["previous_query"] = prev_query
    if prev_tasks:
        state_input["previous_tasks"] = prev_tasks
    if prev_scope:
        state_input["previous_scope"] = prev_scope

    graph = build_graph(llm=llm_override, mode=mode)
    config = {"configurable": {"thread_id": f"session_{session_id}"}}
    final_state = dict(state_input)

    try:
        if cancel_token:
            cancel_token.check()

        for chunk in graph.stream(state_input, config=config, stream_mode="updates"):
            if cancel_token:
                cancel_token.check(partial_results=final_state.get("results") or {})

            for _, patch in chunk.items():
                if isinstance(patch, dict):
                    final_state.update(patch)

            with _RUNS_LOCK:
                if run_id in _RUN_STATES:
                    _RUN_STATES[run_id]["elapsed_ms"] = round((time.perf_counter() - t_start) * 1000, 1)
                    if "llm_calls" in final_state:
                        _RUN_STATES[run_id]["llm_calls"] = final_state["llm_calls"]
                    if final_state.get("plan"):
                        _RUN_STATES[run_id]["plan"] = _format_plan_steps(
                            final_state["plan"], final_state.get("results") or {}
                        )

        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 1)
        payload = format_structured_payload(final_state, elapsed_ms)
        payload["run_id"] = run_id

        with _RUNS_LOCK:
            if run_id in _RUN_STATES:
                _RUN_STATES[run_id]["status"] = "complete"
                _RUN_STATES[run_id]["payload"] = payload
                _RUN_STATES[run_id]["plan"] = payload.get("plan", [])
                _RUN_STATES[run_id]["llm_calls"] = payload.get("llm_calls", {})
                _RUN_STATES[run_id]["elapsed_ms"] = elapsed_ms

        update_message(
            message_id=asst_msg_id,
            content=payload.get("short_answer") or "",
            payload=payload,
            status="complete",
        )

    except Cancelled as exc:
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 1)
        partial_res = getattr(exc, "partial_results", None) or final_state.get("results") or {}
        final_state["results"] = partial_res
        payload = format_structured_payload(final_state, elapsed_ms, exc=exc)
        payload["run_id"] = run_id

        with _RUNS_LOCK:
            if run_id in _RUN_STATES:
                _RUN_STATES[run_id]["status"] = "stopped"
                _RUN_STATES[run_id]["payload"] = payload
                _RUN_STATES[run_id]["plan"] = payload.get("plan", [])
                _RUN_STATES[run_id]["elapsed_ms"] = elapsed_ms

        update_message(
            message_id=asst_msg_id,
            content="Execution stopped by user.",
            payload=payload,
            status="stopped",
        )

    except BudgetExceeded as exc:
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 1)
        payload = format_structured_payload(final_state, elapsed_ms, exc=exc)
        payload["run_id"] = run_id

        with _RUNS_LOCK:
            if run_id in _RUN_STATES:
                _RUN_STATES[run_id]["status"] = "complete"
                _RUN_STATES[run_id]["payload"] = payload
                _RUN_STATES[run_id]["elapsed_ms"] = elapsed_ms

        update_message(
            message_id=asst_msg_id,
            content=payload.get("short_answer") or str(exc),
            payload=payload,
            status="complete",
        )

    except CacheMissError as exc:
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 1)
        payload = format_structured_payload(final_state, elapsed_ms, exc=exc)
        payload["run_id"] = run_id
        payload["error_code"] = "not_recorded"

        with _RUNS_LOCK:
            if run_id in _RUN_STATES:
                _RUN_STATES[run_id]["status"] = "error"
                _RUN_STATES[run_id]["payload"] = payload
                _RUN_STATES[run_id]["error"] = str(exc)
                _RUN_STATES[run_id]["error_code"] = "not_recorded"
                _RUN_STATES[run_id]["elapsed_ms"] = elapsed_ms

        update_message(
            message_id=asst_msg_id,
            content=payload.get("short_answer") or str(exc),
            payload=payload,
            status="error",
        )

    except Exception as exc:
        logger.exception("Background graph execution failed: %s", exc)
        elapsed_ms = round((time.perf_counter() - t_start) * 1000, 1)
        try:
            payload = format_structured_payload(final_state, elapsed_ms, exc=exc)
        except Exception as inner_exc:
            logger.exception("Failed to build error payload: %s", inner_exc)
            payload = {
                "headline": f"**Execution error: {exc}**",
                "explanation": str(exc),
                "short_answer": str(exc),
                "error": {"code": 500, "type": type(exc).__name__, "message": str(exc)},
                "plan": _format_plan_steps(final_state.get("plan"), final_state.get("results") or {}),
                "tables": [],
            }
        payload["run_id"] = run_id

        with _RUNS_LOCK:
            if run_id in _RUN_STATES:
                _RUN_STATES[run_id]["status"] = "error"
                _RUN_STATES[run_id]["payload"] = payload
                _RUN_STATES[run_id]["error"] = str(exc)
                _RUN_STATES[run_id]["elapsed_ms"] = elapsed_ms

        update_message(
            message_id=asst_msg_id,
            content=f"Execution error: {exc}",
            payload=payload,
            status="error",
        )
    finally:
        unregister_cancel_token(run_id)


# ---------------------------------------------------------------------------
# Multi-Agent Synchronous Chat Endpoint (Legacy / Direct)
# ---------------------------------------------------------------------------

class ChatRequest(BaseModel):
    query: str
    thread_id: Optional[str] = None
    mode: Optional[str] = "replay"


@app.post("/api/chat")
def chat_endpoint(request: ChatRequest, req: Request):
    """
    Runs the multi-agent graph synchronously and returns structured fields.
    """
    start_time = time.perf_counter()
    thread_id = request.thread_id or str(uuid.uuid4())
    mode = request.mode or "replay"
    llm_override = getattr(req.app.state, "llm_override", None)

    try:
        checkpointer = get_memory_saver(thread_id)
        graph = build_graph(llm=llm_override, mode=mode, checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        state_input = initial_state(request.query.strip())
        state_input["run_id"] = str(uuid.uuid4())
        result = graph.invoke(state_input, config=config)
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
        payload = format_structured_payload(result, elapsed_ms)
        if payload.get("error_code") == "not_recorded":
            return CleanJSONResponse(status_code=400, content=payload)
        return payload
    except CacheMissError as exc:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
        payload = format_structured_payload({}, elapsed_ms, exc=exc)
        payload["error_code"] = "not_recorded"
        return CleanJSONResponse(status_code=400, content=payload)
    except BudgetExceeded as exc:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
        payload = format_structured_payload({}, elapsed_ms, exc=exc)
        return CleanJSONResponse(status_code=429, content=payload)
    except Exception as exc:
        elapsed_ms = round((time.perf_counter() - start_time) * 1000, 1)
        if is_rate_limit_error(exc):
            payload = format_structured_payload({}, elapsed_ms, exc=exc)
            payload["error"] = {"code": 429, "type": "RateLimitError", "message": "Rate limit exceeded (HTTP 429). Please wait and try again."}
            return CleanJSONResponse(status_code=429, content=payload)
        payload = format_structured_payload({}, elapsed_ms, exc=exc)
        return CleanJSONResponse(status_code=400, content=payload)



# ---------------------------------------------------------------------------
# Sessions & Async Chat Endpoints
# ---------------------------------------------------------------------------

def generate_chat_title(query: str) -> str:
    """
    Intelligently generate a concise, human-readable chat session title
    based on the question (e.g. 'Plant 1 PR (Sep 2026)', 'Block 2 Faulty Inverters').
    """
    if not query or not query.strip():
        return "New Chat"

    q = query.strip()
    q_lower = q.lower()

    if q_lower in ("hi", "hello", "hey", "help"):
        return "New Chat"

    # 1. Detect comparison
    is_compare = bool(re.search(r"\b(compare|comparison|versus|vs)\b", q_lower))

    # 2. Extract Plants
    plants = re.findall(r"\bplant\s*([0-9]+|[a-zA-Z]\b)", q_lower)
    plant_str = None
    if len(plants) >= 2:
        plant_str = f"Plant {plants[0].upper()} vs {plants[1].upper()}"
    elif len(plants) == 1:
        val = plants[0]
        plant_str = f"Plant {val.upper()}" if len(val) == 1 and val.isalpha() else f"Plant {val.title()}"
    elif re.search(r"\bplants?\b", q_lower):
        plant_str = "Plant"

    # 3. Extract Block
    block_match = re.search(r"\bblock\s*([0-9]+|[a-zA-Z]\b)", q_lower)
    block_str = f"Block {block_match.group(1).upper()}" if block_match else None

    # 4. Extract Inverter
    inv_match = re.search(r"\binverter\s*([0-9]+|[a-zA-Z0-9_-]+)?", q_lower)
    inv_str = None
    if inv_match and inv_match.group(1) and inv_match.group(1) not in ("s", "in", "of", "for", "the", "with", "had", "all"):
        inv_str = f"Inverter {inv_match.group(1).upper()}"
    elif "inverter" in q_lower:
        inv_str = "Inverters"

    # 5. Extract Date / Time
    date_str = None
    months_str = "january|february|march|april|may|june|july|august|september|october|november|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"

    m1 = re.search(r"\b(" + months_str + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,?\s*(\d{4}))?\b", q_lower)
    m2 = re.search(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+(" + months_str + r")(?:,?\s*(\d{4}))?\b", q_lower)
    m3 = re.search(r"\b(" + months_str + r")(?:\s+(\d{4}))?\b", q_lower)

    if m1:
        mon = m1.group(1)[:3].title()
        day = m1.group(2)
        yr = m1.group(3)
        date_str = f"{mon} {day}" + (f" {yr}" if yr else "")
    elif m2:
        day = m2.group(1)
        mon = m2.group(2)[:3].title()
        yr = m2.group(3)
        date_str = f"{mon} {day}" + (f" {yr}" if yr else "")
    elif m3:
        mon = m3.group(1)[:3].title()
        yr = m3.group(2)
        date_str = f"{mon} {yr}" if yr else mon
    elif "today" in q_lower:
        date_str = "Today"
    elif "yesterday" in q_lower:
        date_str = "Yesterday"
    elif "last month" in q_lower:
        date_str = "Last Month"
    elif "last week" in q_lower:
        date_str = "Last Week"

    # 6. Topic detection
    topic = None
    if re.search(r"\b(pr|performance\s*ratio)\b", q_lower):
        topic = "PR"
    elif re.search(r"\bsoiling\s*loss\b", q_lower):
        topic = "Soiling Loss"
    elif re.search(r"\b(soiling|dust|cleaning|clean)\b", q_lower):
        topic = "Soiling & Cleaning" if "clean" in q_lower else "Soiling"
    elif re.search(r"\b(zero[-\s]*gen|zero[-\s]*generation|offline|outage)\b", q_lower):
        topic = "Zero-Gen"
    elif re.search(r"\b(trip|tripped)\b", q_lower):
        topic = "Trip"
    elif re.search(r"\b(fault|faulty|alarm|breakdown|fail|failure|error)\b", q_lower):
        topic = "Faults"
    elif re.search(r"\b(underperform|underperforming|underperformance|derating|low\s+gen)\b", q_lower):
        topic = "Underperforming"
    elif re.search(r"\b(yield|generation|energy|kwh|mwh)\b", q_lower):
        topic = "Yield"
    elif re.search(r"\b(irradiance|ghi|poa|weather|temperature)\b", q_lower):
        topic = "Irradiance"
    elif re.search(r"\bstatus\b", q_lower):
        topic = "Status"

    parts = []
    if plant_str:
        parts.append(plant_str)
    if block_str:
        parts.append(block_str)
    if inv_str:
        if inv_str == "Inverters" and topic == "Faults":
            parts.append("Faulty Inverters")
            topic = None
        elif inv_str == "Inverters" and topic == "Zero-Gen":
            parts.append("Zero-Gen Inverters")
            topic = None
        else:
            parts.append(inv_str)

    if topic and topic not in parts:
        parts.append(topic)
    if is_compare and "vs" not in " ".join(parts):
        parts.append("Comparison")
    if date_str:
        parts.append(f"({date_str})")

    if parts:
        candidate = " ".join(parts)
        if len(candidate) <= 35:
            return candidate
        parts_no_date = [p for p in parts if not p.startswith("(")]
        candidate_short = " ".join(parts_no_date)
        if len(candidate_short) <= 35:
            return candidate_short

    # Fallback to cleaning conversational query phrasing
    clean = re.sub(
        r"^(what\s+(is|was|were|are|rhe|the)\s+|show\s+(me\s+)?(all\s+)?|tell\s+me\s+(about\s+)?|can\s+you\s+(explain\s+)?|explain\s+(how\s+)?|give\s+me\s+|how\s+(much|many|is|are|does\s+the\s+system\s+|do\s+we\s+)\s*|why\s+(did|is|are)\s+|please\s+|find\s+(all\s+)?|calculate\s+|check\s+|which\s+|list\s+(all\s+)?)",
        "",
        q,
        flags=re.I,
    ).strip()
    clean = re.sub(r"^(the|a|an|all|any)\s+", "", clean, flags=re.I).strip()
    clean = re.sub(r"[?!.,;:]+$", "", clean).strip()

    if clean:
        words = clean.split()[:5]
        acronyms = {"pr", "ghi", "poa", "dc", "ac", "kwh", "mwh"}
        res = " ".join(w.upper() if w.lower() in acronyms else w.capitalize() for w in words)
        return res[:35]

    return "Chat Session"


class CreateSessionRequest(BaseModel):
    title: Optional[str] = "New Chat"


class UpdateSessionRequest(BaseModel):
    title: str


class SessionChatRequest(BaseModel):
    query: str
    mode: Optional[str] = "replay"


class EditMessageRequest(BaseModel):
    query: str
    mode: Optional[str] = "replay"


@app.get("/api/sessions")
def get_sessions():
    """List all chat sessions ordered by updated_at descending."""
    return list_sessions()


@app.post("/api/sessions")
def create_session_endpoint(body: Optional[CreateSessionRequest] = None):
    """Create a new chat session."""
    title = body.title if (body and body.title) else "New Chat"
    return create_session(title=title)


@app.patch("/api/sessions/{session_id}")
def update_session_endpoint(session_id: str, body: UpdateSessionRequest):
    """Rename a chat session."""
    sess = update_session(session_id, title=body.title)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return sess


@app.delete("/api/sessions/{session_id}")
def delete_session_endpoint(session_id: str):
    """Delete a chat session and all its messages (cascade delete)."""
    success = delete_session(session_id)
    if not success:
        raise HTTPException(status_code=404, detail="Session not found")
    return {"deleted": True, "session_id": session_id}


@app.get("/api/sessions/{session_id}/messages")
def get_session_messages(session_id: str):
    """Get all messages for a session ordered chronologically."""
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")
    return list_messages(session_id)


@app.delete("/api/messages/{message_id}")
def delete_message_endpoint(message_id: str):
    """Delete a question and its answer pair."""
    success = delete_message_pair(message_id)
    if not success:
        raise HTTPException(status_code=404, detail="Message not found")
    return {"deleted": True, "message_id": message_id}


@app.post("/api/sessions/{session_id}/chat")
def session_chat_endpoint(session_id: str, request: SessionChatRequest, req: Request):
    """
    Save user message, create running assistant message, start graph in background thread,
    and return {run_id, assistant_message_id} immediately.
    """
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    q_text = request.query.strip()

    # Auto-title session from the first question based on question content
    existing_msgs = list_messages(session_id)
    if not existing_msgs or sess.get("title") in ("New Chat", "", None):
        smart_title = generate_chat_title(q_text)
        if smart_title:
            update_session(session_id, title=smart_title)

    # Follow-up context is built from the last completed assistant payload in DB, not checkpointer
    prev_query, prev_tasks, prev_scope = get_last_completed_assistant_context(session_id)

    # Save user message
    create_message(
        session_id=session_id,
        role="user",
        content=q_text,
        status="complete",
    )

    # Create running assistant message
    asst_msg_id = str(uuid.uuid4())
    create_message(
        message_id=asst_msg_id,
        session_id=session_id,
        role="assistant",
        content="",
        payload=None,
        status="running",
    )

    run_id = str(uuid.uuid4())
    register_cancel_token(run_id)

    with _RUNS_LOCK:
        _RUN_STATES[run_id] = {
            "run_id": run_id,
            "session_id": str(session_id),
            "assistant_message_id": asst_msg_id,
            "status": "running",
            "plan": [],
            "llm_calls": {"planner": 0, "agent": 0, "synthesizer": 0, "total": 0, "budget": settings.max_llm_calls_per_query},
            "payload": None,
            "error": None,
            "start_time": time.perf_counter(),
            "elapsed_ms": 0.0,
        }

    llm_override = getattr(req.app.state, "llm_override", None)
    th = threading.Thread(
        target=_execute_graph_run,
        args=(
            run_id,
            asst_msg_id,
            str(session_id),
            q_text,
            request.mode or "replay",
            prev_query,
            prev_tasks,
            prev_scope,
            llm_override,
        ),
        daemon=True,
    )
    with _RUNS_LOCK:
        _RUN_THREADS[run_id] = th
    th.start()

    return {
        "run_id": run_id,
        "assistant_message_id": asst_msg_id,
    }


@app.get("/api/runs/{run_id}")
def get_run_endpoint(run_id: str):
    """
    Returns status, plan steps with live task statuses, calls so far,
    and when finished the full payload.
    """
    with _RUNS_LOCK:
        run_state = _RUN_STATES.get(run_id)
        if run_state:
            res = {
                "run_id": run_id,
                "status": run_state["status"],
                "plan": run_state.get("plan", []),
                "llm_calls": run_state.get("llm_calls", {}),
                "payload": run_state.get("payload"),
                "elapsed_ms": run_state.get("elapsed_ms", 0.0),
            }
            if run_state.get("payload") and isinstance(run_state["payload"], dict):
                for k, v in run_state["payload"].items():
                    if k not in res:
                        res[k] = v
            return res

    # Not in memory: check database for assistant message
    msg = get_message_by_run_id(run_id)
    if msg:
        payload = msg.get("payload") or {}
        res = {
            "run_id": run_id,
            "status": msg.get("status"),
            "plan": payload.get("plan", []),
            "llm_calls": payload.get("llm_calls", {}),
            "payload": payload,
            "elapsed_ms": payload.get("elapsed_ms", 0.0),
        }
        for k, v in payload.items():
            if k not in res:
                res[k] = v
        return res

    raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found")


@app.post("/api/runs/{run_id}/stop")
def stop_run_endpoint(run_id: str):
    """Signal cancellation for an active run."""
    token = get_cancel_token(run_id)
    if token:
        token.cancel()
    with _RUNS_LOCK:
        if run_id in _RUN_STATES:
            if _RUN_STATES[run_id]["status"] == "running":
                _RUN_STATES[run_id]["status"] = "stopped"
            return {"run_id": run_id, "status": _RUN_STATES[run_id]["status"]}
    return {"run_id": run_id, "status": "stopped"}


@app.post("/api/messages/{message_id}/edit")
def edit_message_endpoint(message_id: str, request: EditMessageRequest, req: Request):
    """
    Update the user message, delete all later messages in the session,
    and start a new background run.
    """
    user_msg = get_message(message_id)
    if not user_msg:
        raise HTTPException(status_code=404, detail="Message not found")
    if user_msg["role"] != "user":
        raise HTTPException(status_code=400, detail="Only user messages can be edited")

    session_id = user_msg["session_id"]
    sess = get_session(session_id)
    if not sess:
        raise HTTPException(status_code=404, detail="Session not found")

    new_query = request.query.strip()

    # 1. Update the user message
    update_message(message_id, content=new_query)

    # 2. Delete all later messages in the session
    delete_messages_after(session_id, message_id)

    # 3. Check for auto-titling if this is the first message
    all_msgs = list_messages(session_id)
    if len(all_msgs) <= 1:
        smart_title = generate_chat_title(new_query)
        if smart_title:
            update_session(session_id, title=smart_title)

    # 4. Follow-up context from the last completed assistant message prior to this message
    prev_query, prev_tasks, prev_scope = get_last_completed_assistant_context(
        session_id, exclude_message_id=message_id
    )

    # 5. Create new running assistant message
    asst_msg_id = str(uuid.uuid4())
    create_message(
        message_id=asst_msg_id,
        session_id=session_id,
        role="assistant",
        content="",
        payload=None,
        status="running",
    )

    # 6. Start new run in background thread
    run_id = str(uuid.uuid4())
    register_cancel_token(run_id)

    with _RUNS_LOCK:
        _RUN_STATES[run_id] = {
            "run_id": run_id,
            "session_id": str(session_id),
            "assistant_message_id": asst_msg_id,
            "status": "running",
            "plan": [],
            "llm_calls": {"planner": 0, "agent": 0, "synthesizer": 0, "total": 0, "budget": settings.max_llm_calls_per_query},
            "payload": None,
            "error": None,
            "start_time": time.perf_counter(),
            "elapsed_ms": 0.0,
        }

    llm_override = getattr(req.app.state, "llm_override", None)
    th = threading.Thread(
        target=_execute_graph_run,
        args=(
            run_id,
            asst_msg_id,
            str(session_id),
            new_query,
            request.mode or "replay",
            prev_query,
            prev_tasks,
            prev_scope,
            llm_override,
        ),
        daemon=True,
    )
    with _RUNS_LOCK:
        _RUN_THREADS[run_id] = th
    th.start()

    return {
        "run_id": run_id,
        "assistant_message_id": asst_msg_id,
    }

