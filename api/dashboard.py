"""
api/dashboard.py — Dashboard REST endpoints (no LLM calls).

Endpoints:
  GET /api/dashboard/overview          Fleet KPIs, plant table, daily, data quality
  GET /api/dashboard/plant/{plant_id}  Block PR, best/worst inverters, weather, treemap
  GET /api/dashboard/plant/{plant_id}/heatmap  PR heatmap (matrix)
  GET /api/dashboard/inverter/{inverter_id}    Per-inverter detail

Query params on all:
  plant_id  (optional, default = all)
  start     YYYY-MM-DD (clamped to DB range)
  end       YYYY-MM-DD (clamped to DB range)
  agg       day | week | month (default day)

Rules:
  - No LLM calls, no DataStore
  - All formulas via solar_agent/tools/metrics.py (sum nums/denoms before dividing)
  - 60-second in-memory cache per (endpoint, params) key
  - NaN -> null via CleanJSONResponse (parent app default_response_class)
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import yaml
from fastapi import APIRouter, HTTPException, Query

from solar_agent.config.settings import settings, STATUS_CODES_PATH
from solar_agent.db import queries as q
from solar_agent.tools import metrics as m
from solar_agent.tools.fault_tools import _FAULT_CODES, _EXCLUDE_YIELD_CODES

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Load config/dashboard.yaml
# ---------------------------------------------------------------------------
_DASH_YAML = Path(__file__).resolve().parent.parent / "config" / "dashboard.yaml"
with open(_DASH_YAML, "r", encoding="utf-8") as _fh:
    _DCFG: dict = yaml.safe_load(_fh)

HEALTH_AMBER_PP: float = float(_DCFG["health"]["amber_threshold_pp"])
HEALTH_RED_PP: float = float(_DCFG["health"]["red_threshold_pp"])
CACHE_TTL: int = int(_DCFG["cache_ttl_seconds"])
HEATMAP_MAX_DAYS: int = int(_DCFG["heatmap"]["max_days"])
SPARKLINE_MAX: int = int(_DCFG["sparkline"]["max_points"])
MIN_H: float = float(settings.min_irradiance_kwh_m2)

# Load status code labels for the inverter detail endpoint
with open(STATUS_CODES_PATH, "r", encoding="utf-8") as _sfh:
    _SC_RAW = yaml.safe_load(_sfh)
_SC_LABELS: dict[int, str] = {
    k: v["label"] for k, v in _SC_RAW.get("status_codes", _SC_RAW).items()
}

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])

# ---------------------------------------------------------------------------
# Simple in-memory cache (thread-safe)
# ---------------------------------------------------------------------------
_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, Any]] = {}  # key -> (expires_at, payload)


def _cache_key(*args: Any) -> str:
    raw = json.dumps(args, sort_keys=True, default=str)
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_get(key: str) -> Any:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and time.time() < entry[0]:
            return entry[1]
        return None


def _cache_set(key: str, value: Any) -> None:
    with _cache_lock:
        _cache[key] = (time.time() + CACHE_TTL, value)


# ---------------------------------------------------------------------------
# Shared param parsing (with clamping and clamped_note)
# ---------------------------------------------------------------------------

def _parse_params(
    plant_id: Optional[str],
    start: Optional[str],
    end: Optional[str],
    agg: str = "day",
) -> tuple[Optional[list[int]], date, date, Optional[str]]:
    """
    Returns (plant_ids, start_date, end_date, clamped_note).
    Dates are clamped to [min_log_date, max_log_date].
    """
    plant_ids: Optional[list[int]] = None
    if plant_id and str(plant_id).lower() not in ("all", "", "none"):
        try:
            plant_ids = [int(plant_id)]
        except ValueError:
            pass

    db_max = q.get_max_log_date()
    db_min = q.get_min_log_date()

    raw_end: Optional[date] = None
    raw_start: Optional[date] = None

    if end:
        try:
            raw_end = datetime.strptime(end, "%Y-%m-%d").date()
        except ValueError:
            pass
    if start:
        try:
            raw_start = datetime.strptime(start, "%Y-%m-%d").date()
        except ValueError:
            pass

    end_date = min(raw_end, db_max) if raw_end else db_max
    start_date = max(raw_start, db_min) if raw_start else db_min

    if start_date > end_date:
        start_date, end_date = end_date, start_date

    notes = []
    if raw_end and raw_end > db_max:
        notes.append(f"end clamped from {raw_end} to {db_max}")
    if raw_start and raw_start < db_min:
        notes.append(f"start clamped from {raw_start} to {db_min}")
    clamped_note: Optional[str] = "; ".join(notes) if notes else None

    return plant_ids, start_date, end_date, clamped_note


def _safe_float(val: Any, ndigits: int = 4) -> Optional[float]:
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return round(f, ndigits)
    except (TypeError, ValueError):
        return None


def _iso(d: Any) -> Optional[str]:
    if d is None:
        return None
    if hasattr(d, "isoformat"):
        return d.isoformat()
    return str(d)


# ---------------------------------------------------------------------------
# Metric helpers (all reuse metrics.py — sum numerators/denominators)
# ---------------------------------------------------------------------------

def _fleet_pr(df: pd.DataFrame) -> Optional[float]:
    """Fleet-wide PR: sum(yield) / sum(rated_dc * H) for valid irradiance days (BR-04)."""
    if df.empty:
        return None
    valid = df[df["total_solar_radiation_kwh_m2"] > MIN_H]
    num = valid["total_daily_yield_kwh"].sum()
    denom = (valid["rated_dc_kw"] * valid["total_solar_radiation_kwh_m2"]).sum()
    return _safe_float(num / denom) if denom > 0 else None


def _plant_pr(df: pd.DataFrame, plant_id: int) -> Optional[float]:
    return _fleet_pr(df[df["plant_id"] == plant_id])


def _open_alerts_count(df: pd.DataFrame) -> int:
    """Count flagged inverter-days (zero gen OR fault status)."""
    if df.empty:
        return 0
    fault_mask = df["status_code"].isin(_FAULT_CODES)
    zero_mask = (
        (df["total_solar_radiation_kwh_m2"] > MIN_H)
        & (~df["status_code"].isin(_EXCLUDE_YIELD_CODES))
        & (df["total_daily_yield_kwh"] <= settings.zero_generation_max_yield_kwh)
    )
    return int((fault_mask | zero_mask).sum())


def _availability_pct(df: pd.DataFrame) -> Optional[float]:
    """Share of inverter-days (with irr > threshold) that are non-fault and non-zero-yield."""
    if df.empty:
        return None
    irr_mask = df["total_solar_radiation_kwh_m2"] > MIN_H
    sub = df[irr_mask]
    if sub.empty:
        return None
    avail = (
        ~sub["status_code"].isin(_FAULT_CODES)
        & (sub["total_daily_yield_kwh"] > settings.zero_generation_max_yield_kwh)
    )
    return _safe_float(100.0 * avail.sum() / len(sub), 2)


def _soiling_loss_pct(df: pd.DataFrame) -> Optional[float]:
    """
    Soiling loss %: sum(soiling_energy_loss_kwh) / sum(total_daily_yield_kwh) * 100.
    Matches environment_tools.estimate_soiling_loss and metrics.compute_soiling_loss.
    """
    if df.empty or "soiling_ratio" not in df.columns or "total_daily_yield_kwh" not in df.columns:
        return None
    valid = df[(df["soiling_ratio"] > 0) & (df["soiling_ratio"] <= 1.0)].copy()
    if valid.empty:
        return None
    valid = m.compute_soiling_loss(valid)
    loss_kwh = valid["soiling_energy_loss_kwh"].sum()
    yield_kwh = valid["total_daily_yield_kwh"].sum()
    if yield_kwh <= 0:
        return None
    return _safe_float(loss_kwh / yield_kwh * 100.0, 2)


def _specific_yield(df: pd.DataFrame) -> Optional[float]:
    ok = df["rated_dc_kw"] > 0
    sub = df[ok]
    if sub.empty:
        return None
    return _safe_float((sub["total_daily_yield_kwh"] / sub["rated_dc_kw"]).mean(), 3)


def _capacity_factor(df: pd.DataFrame) -> Optional[float]:
    ok = df["rated_ac_kw"] > 0
    sub = df[ok]
    if sub.empty:
        return None
    cf = (sub["total_daily_yield_kwh"] / (sub["rated_ac_kw"] * 24)).mean()
    return _safe_float(cf, 4)


def _sparkline_pr(df: pd.DataFrame, max_pts: int = SPARKLINE_MAX) -> list[Any]:
    """Daily PR sparkline, capped at max_pts."""
    if df.empty:
        return []
    agg = m.aggregate_pr(df, ["log_date"])
    agg = agg.sort_values("log_date")
    if len(agg) > max_pts:
        step = math.ceil(len(agg) / max_pts)
        agg = agg.iloc[::step]
    return [
        {
            "date": _iso(r["log_date"]),
            "value": _safe_float(r["performance_ratio"], 4),
            "yield_mwh": _safe_float(r["total_yield_kwh"] / 1000, 3),
        }
        for _, r in agg.iterrows()
    ]


def _kpi_block(current: Any, previous: Any, sparkline: list | None = None) -> dict:
    delta = None
    if current is not None and previous is not None:
        try:
            delta = round(float(current) - float(previous), 4)
        except (TypeError, ValueError):
            pass
    out = {"value": current, "previous": previous, "delta": delta, "sparkline": sparkline or []}
    return out


def _health(pr: Optional[float], fleet_median_pr: Optional[float]) -> str:
    if pr is None or fleet_median_pr is None:
        return "amber"
    diff = fleet_median_pr - pr  # positive = below median
    if diff >= HEALTH_RED_PP / 100:
        return "red"
    if diff >= HEALTH_AMBER_PP / 100:
        return "amber"
    return "green"


def _fault_days_plant(df: pd.DataFrame, plant_id: int) -> int:
    sub = df[df["plant_id"] == plant_id]
    if sub.empty:
        return 0
    fault_mask = sub["status_code"].isin(_FAULT_CODES)
    zero_mask = (
        (sub["total_solar_radiation_kwh_m2"] > MIN_H)
        & (~sub["status_code"].isin(_EXCLUDE_YIELD_CODES))
        & (sub["total_daily_yield_kwh"] <= settings.zero_generation_max_yield_kwh)
    )
    return int(sub[fault_mask | zero_mask]["log_date"].nunique())


def _fleet_median_pr(df: pd.DataFrame) -> Optional[float]:
    """Median of per-plant PR values (for health banding)."""
    if df.empty:
        return None
    plant_prs = []
    for pid in df["plant_id"].unique():
        p = _plant_pr(df, int(pid))
        if p is not None:
            plant_prs.append(p)
    if not plant_prs:
        return None
    return float(np.median(plant_prs))


# ---------------------------------------------------------------------------
# GET /api/dashboard/overview
# ---------------------------------------------------------------------------

@router.get("/overview")
def dashboard_overview(
    plant_id: Optional[str] = Query(None),
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    agg: str = Query("day"),
):
    """
    Fleet overview: KPIs with sparklines, per-plant table, fleet_pr, daily series, data quality.
    """
    ck = _cache_key("overview", plant_id, start, end, agg)
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    plant_ids, start_date, end_date, clamped_note = _parse_params(plant_id, start, end, agg)
    df = q.fetch_inverter_daily(start_date, end_date, plant_ids=plant_ids)

    # Previous period (equal length before the range)
    n_days = (end_date - start_date).days + 1
    prev_end = start_date - timedelta(days=1)
    prev_start = prev_end - timedelta(days=n_days - 1)
    db_min = q.get_min_log_date()
    prev_df = pd.DataFrame()
    if prev_end >= db_min:
        prev_df = q.fetch_inverter_daily(max(db_min, prev_start), prev_end, plant_ids=plant_ids)

    # ---- compute current and previous scalar KPIs ----
    def _all_kpis(d: pd.DataFrame) -> dict:
        if d.empty:
            return {}
        return {
            "energy_mwh": _safe_float(d["total_daily_yield_kwh"].sum() / 1000, 3),
            "pr": _fleet_pr(d),
            "specific_yield": _specific_yield(d),
            "capacity_factor": _capacity_factor(d),
            "soiling_loss_pct": _soiling_loss_pct(d),
            "availability_pct": _availability_pct(d),
            "open_alerts": _open_alerts_count(d),
        }

    curr_kpis = _all_kpis(df)
    prev_kpis = _all_kpis(prev_df)

    # Sparkline only for PR
    pr_sparkline = _sparkline_pr(df)

    kpi_keys = ["energy_mwh", "pr", "specific_yield", "capacity_factor",
                "soiling_loss_pct", "availability_pct", "open_alerts"]
    kpis = {}
    for k in kpi_keys:
        spark = pr_sparkline if k == "pr" else []
        kpis[k] = _kpi_block(curr_kpis.get(k), prev_kpis.get(k), spark)

    # ---- per-plant table ----
    fleet_pr_val = _fleet_pr(df)
    fleet_med = _fleet_median_pr(df)
    plants_meta = q.get_all_plants()
    plants_out = []
    for _, pm in plants_meta.iterrows():
        pid = int(pm["plant_id"])
        if plant_ids and pid not in plant_ids:
            continue
        p_df = df[df["plant_id"] == pid]
        p_pr = _plant_pr(df, pid)
        plants_out.append({
            "plant_id": pid,
            "name": str(pm.get("plant_name", "")),
            "capacity_mwp": _safe_float(pm.get("capacity_mwp"), 3),
            "energy_mwh": _safe_float(p_df["total_daily_yield_kwh"].sum() / 1000, 3) if not p_df.empty else None,
            "pr": p_pr,
            "soiling_loss_pct": _soiling_loss_pct(p_df),
            "fault_days": _fault_days_plant(df, pid),
            "health": _health(p_pr, fleet_med),
        })

    # ---- daily aggregated series ----
    daily_out: list[dict] = []
    if not df.empty:
        by_date = df.groupby("log_date").agg(
            total_daily_yield_kwh=("total_daily_yield_kwh", "sum"),
            total_solar_radiation_kwh_m2=("total_solar_radiation_kwh_m2", "mean"),
        ).reset_index()
        by_date["log_date"] = pd.to_datetime(by_date["log_date"])

        freq_map = {"day": "D", "week": "W-MON", "month": "MS"}
        freq = freq_map.get(agg, "D")
        by_date_r = (
            by_date.set_index("log_date")
            .resample(freq)
            .agg({
                "total_daily_yield_kwh": "sum",
                "total_solar_radiation_kwh_m2": "mean",
            })
            .reset_index()
        )
        for _, row in by_date_r.iterrows():
            d_val = row["log_date"]
            daily_out.append({
                "date": _iso(d_val.date() if hasattr(d_val, "date") else d_val),
                "energy_mwh": _safe_float(row["total_daily_yield_kwh"] / 1000, 3),
                "irradiance_kwh_m2": _safe_float(row["total_solar_radiation_kwh_m2"], 3),
            })

    # ---- data quality ----
    dq_out: list[dict] = []
    if not df.empty:
        n_calendar = (end_date - start_date).days + 1
        by_plant = df.groupby("plant_id").agg(
            rows=("log_date", "count"),
            days_with_data=("log_date", "nunique"),
            null_yield=("total_daily_yield_kwh", lambda x: int(x.isna().sum())),
        ).reset_index()
        for _, row in by_plant.iterrows():
            dq_out.append({
                "plant_id": int(row["plant_id"]),
                "rows": int(row["rows"]),
                "missing_days": max(0, n_calendar - int(row["days_with_data"])),
                "null_counts": int(row["null_yield"]),
            })

    result = {
        "period": {"start": _iso(start_date), "end": _iso(end_date)},
        "clamped_note": clamped_note,
        "kpis": kpis,
        "fleet_pr": fleet_pr_val,
        "plants": plants_out,
        "daily": daily_out,
        "data_quality": dq_out,
    }
    _cache_set(ck, result)
    return result


# ---------------------------------------------------------------------------
# GET /api/dashboard/plant/{plant_id}
# ---------------------------------------------------------------------------

@router.get("/plant/{plant_id_path}")
def dashboard_plant(
    plant_id_path: int,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    agg: str = Query("day"),
):
    """
    Per-plant detail: blocks (PR, energy, inv_count), best/worst 5 inverters,
    weather series, treemap data.
    """
    ck = _cache_key("plant", plant_id_path, start, end, agg)
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    _, start_date, end_date, clamped_note = _parse_params(str(plant_id_path), start, end, agg)
    df = q.fetch_inverter_daily(start_date, end_date, plant_ids=[plant_id_path])

    if df.empty:
        result = {
            "period": {"start": _iso(start_date), "end": _iso(end_date)},
            "clamped_note": clamped_note,
            "blocks": [],
            "inverters_best": [],
            "inverters_worst": [],
            "weather": [],
            "treemap": [],
        }
        _cache_set(ck, result)
        return result

    # ---- blocks ----
    block_agg = m.aggregate_pr(df, ["block_id", "block_name", "plant_id"])
    inv_count = df.groupby("block_id")["inverter_id"].nunique().rename("inverter_count")
    block_energy = df.groupby("block_id")["total_daily_yield_kwh"].sum() / 1000
    block_agg = block_agg.join(inv_count, on="block_id")
    block_agg = block_agg.join(block_energy.rename("energy_mwh"), on="block_id")

    blocks_out = [
        {
            "block": str(row.get("block_name", row["block_id"])),
            "block_id": str(row["block_id"]),
            "pr": _safe_float(row["performance_ratio"], 4),
            "energy_mwh": _safe_float(row.get("energy_mwh"), 3),
            "inverter_count": int(row["inverter_count"]) if pd.notna(row.get("inverter_count")) else 0,
        }
        for _, row in block_agg.iterrows()
    ]

    # ---- best/worst inverters by PR ----
    inv_agg = m.aggregate_pr(df, ["inverter_id", "block_id", "block_name"])
    inv_clean = inv_agg.dropna(subset=["performance_ratio"]).sort_values(
        "performance_ratio", ascending=False, na_position="last"
    )

    def _inv_row(row: pd.Series) -> dict:
        return {
            "inverter_id": str(row["inverter_id"]),
            "block": str(row.get("block_name", row["block_id"])),
            "pr": _safe_float(row["performance_ratio"], 4),
        }

    inverters_best = [_inv_row(r) for _, r in inv_clean.head(5).iterrows()]
    inverters_worst = [_inv_row(r) for _, r in inv_clean.tail(5).iterrows()]

    # ---- weather series ----
    weather_df = q.fetch_weather_daily(start_date, end_date, plant_ids=[plant_id_path])
    weather_out: list[dict] = []
    if not weather_df.empty:
        for _, row in weather_df.sort_values("log_date").iterrows():
            weather_out.append({
                "date": _iso(row["log_date"]),
                "ambient_c": _safe_float(row.get("avg_ambient_temp_c"), 2),
                "module_c": _safe_float(row.get("max_module_temp_c"), 2),
                "irradiance": _safe_float(row.get("total_solar_radiation_kwh_m2"), 3),
                "soiling_ratio": _safe_float(row.get("soiling_ratio"), 4),
            })

    # ---- treemap: block × inverter × rated_dc_kw × pr ----
    df_pr = m.compute_performance_ratio(df)
    treemap_agg = (
        df_pr.groupby(["block_id", "block_name", "inverter_id"])
        .agg(
            pr=("performance_ratio", lambda x: float(x.dropna().mean()) if x.notna().any() else float("nan")),
            rated_dc_kw=("rated_dc_kw", "first"),
        )
        .reset_index()
    )
    treemap_out = [
        {
            "block": str(row.get("block_name", row["block_id"])),
            "inverter": str(row["inverter_id"]),
            "rated_dc_kw": _safe_float(row.get("rated_dc_kw"), 2),
            "pr": _safe_float(row.get("pr"), 4),
        }
        for _, row in treemap_agg.iterrows()
    ]

    result = {
        "period": {"start": _iso(start_date), "end": _iso(end_date)},
        "clamped_note": clamped_note,
        "blocks": blocks_out,
        "inverters_best": inverters_best,
        "inverters_worst": inverters_worst,
        "weather": weather_out,
        "treemap": treemap_out,
    }
    _cache_set(ck, result)
    return result


# ---------------------------------------------------------------------------
# GET /api/dashboard/plant/{plant_id}/heatmap
# ---------------------------------------------------------------------------

@router.get("/plant/{plant_id_path}/heatmap")
def dashboard_heatmap(
    plant_id_path: int,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
):
    """
    PR heatmap matrix: inverters[] x dates[] with pr[][] and status[][].
    Capped at HEATMAP_MAX_DAYS most-recent days; adds clamped_note if cut.
    """
    ck = _cache_key("heatmap", plant_id_path, start, end)
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    _, start_date, end_date, clamped_note = _parse_params(str(plant_id_path), start, end)
    df = q.fetch_inverter_daily(start_date, end_date, plant_ids=[plant_id_path])

    if df.empty:
        result = {
            "inverters": [], "dates": [], "pr": [], "status": [],
            "clamped_note": clamped_note,
        }
        _cache_set(ck, result)
        return result

    df = m.compute_performance_ratio(df)

    all_dates = sorted(df["log_date"].unique())
    note_extra: Optional[str] = None
    if len(all_dates) > HEATMAP_MAX_DAYS:
        all_dates = all_dates[-HEATMAP_MAX_DAYS:]
        note_extra = f"Capped to latest {HEATMAP_MAX_DAYS} days."
        df = df[df["log_date"].isin(all_dates)]

    inverters = sorted(df["inverter_id"].unique(), key=str)
    date_strs = [_iso(d) for d in all_dates]

    # Build lookup (inverter_id, date) -> {pr, status}
    lookup: dict[tuple[str, Any], dict] = {}
    for _, row in df.iterrows():
        key = (str(row["inverter_id"]), row["log_date"])
        lookup[key] = {
            "pr": _safe_float(row.get("performance_ratio"), 4),
            "status": int(row["status_code"]) if pd.notna(row.get("status_code")) else None,
        }

    pr_matrix: list[list] = []
    status_matrix: list[list] = []
    for inv in inverters:
        pr_row, st_row = [], []
        for d in all_dates:
            entry = lookup.get((str(inv), d), {})
            pr_row.append(entry.get("pr"))   # None = no data
            st_row.append(entry.get("status"))
        pr_matrix.append(pr_row)
        status_matrix.append(st_row)

    notes = [n for n in (clamped_note, note_extra) if n]
    result = {
        "inverters": [str(i) for i in inverters],
        "dates": date_strs,
        "pr": pr_matrix,
        "status": status_matrix,
        "clamped_note": " | ".join(notes) if notes else None,
    }
    _cache_set(ck, result)
    return result


# ---------------------------------------------------------------------------
# GET /api/dashboard/inverter/{inverter_id}
# ---------------------------------------------------------------------------

@router.get("/inverter/{inverter_id_path:path}")
def dashboard_inverter(
    inverter_id_path: str,
    start: Optional[str] = Query(None),
    end: Optional[str] = Query(None),
    agg: str = Query("day"),
):
    """
    Per-inverter detail: summary, daily KPIs, peer median PR per date.
    """
    ck = _cache_key("inverter", inverter_id_path, start, end, agg)
    cached = _cache_get(ck)
    if cached is not None:
        return cached

    _, start_date, end_date, clamped_note = _parse_params(None, start, end, agg)

    all_inv = q.get_all_inverters()
    inv_meta = all_inv[all_inv["inverter_id"].astype(str) == str(inverter_id_path)]
    if inv_meta.empty:
        raise HTTPException(status_code=404, detail=f"Inverter '{inverter_id_path}' not found.")

    inv_row = inv_meta.iloc[0]
    plant_id_val = int(inv_row["plant_id"])
    block_id_val = str(inv_row["block_id"])

    # Fetch full plant data (needed for peer median)
    df_all = q.fetch_inverter_daily(start_date, end_date, plant_ids=[plant_id_val])
    df_all = m.compute_performance_ratio(df_all)

    df_inv = df_all[df_all["inverter_id"].astype(str) == str(inverter_id_path)].copy()
    df_inv = df_inv.sort_values("log_date")

    # Flagged days
    flagged_days = 0
    if not df_inv.empty:
        fault_mask = df_inv["status_code"].isin(_FAULT_CODES)
        zero_mask = (
            (df_inv["total_solar_radiation_kwh_m2"] > MIN_H)
            & (~df_inv["status_code"].isin(_EXCLUDE_YIELD_CODES))
            & (df_inv["total_daily_yield_kwh"] <= settings.zero_generation_max_yield_kwh)
        )
        flagged_days = int((fault_mask | zero_mask).sum())

    summary = {
        "inverter_id": str(inverter_id_path),
        "block": str(inv_row.get("block_name", block_id_val)),
        "plant": str(inv_row.get("plant_name", plant_id_val)),
        "rated_dc_kw": _safe_float(inv_row.get("rated_dc_kw"), 2),
        "rated_ac_kw": _safe_float(inv_row.get("rated_ac_kw"), 2),
        "flagged_days": flagged_days,
    }

    # Daily rows
    daily_out: list[dict] = []
    if not df_inv.empty:
        for _, row in df_inv.iterrows():
            sc = row.get("status_code")
            sc_int = int(sc) if pd.notna(sc) else None
            daily_out.append({
                "date": _iso(row["log_date"]),
                "yield_kwh": _safe_float(row.get("total_daily_yield_kwh"), 3),
                "pr": _safe_float(row.get("performance_ratio"), 4),
                "peak_ac_kw": _safe_float(row.get("peak_ac_power_kw"), 3),
                "peak_dc_kw": _safe_float(row.get("peak_dc_power_kw"), 3),
                "dc_voltage": _safe_float(row.get("avg_dc_voltage_v"), 2),
                "dc_current": _safe_float(row.get("avg_dc_current_a"), 2),
                "status_code": sc_int,
                "status_label": _SC_LABELS.get(sc_int, "Unknown") if sc_int is not None else None,
            })

    # Peer median PR per date (same block, all inverters)
    df_block = df_all[df_all["block_id"].astype(str) == block_id_val]
    peer_med: list[dict] = []
    if not df_block.empty:
        by_date = df_block.groupby("log_date")["performance_ratio"].median().reset_index()
        for _, row in by_date.iterrows():
            peer_med.append({
                "date": _iso(row["log_date"]),
                "peer_median_pr": _safe_float(row["performance_ratio"], 4),
            })

    result = {
        "period": {"start": _iso(start_date), "end": _iso(end_date)},
        "clamped_note": clamped_note,
        "summary": summary,
        "daily": daily_out,
        "peer_median_pr": peer_med,
    }
    _cache_set(ck, result)
    return result
