"""
Templated SQL queries for the Solar Power Plant Analysis system.

Rules:
- All queries are SELECT-only.
- Parameters use SQLAlchemy :param_name syntax (never f-strings with user data).
- Every query enforces a LIMIT via the row_limit parameter.
- Table names are hardcoded — never interpolated from user input.
- The get_max_log_date() function defines 'today' for the whole system.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
from sqlalchemy import text

from solar_agent.db.connection import get_sync_engine
from solar_agent.config.settings import settings

# ---------------------------------------------------------------------------
# Utility
# ---------------------------------------------------------------------------

def _execute_to_df(sql: str, params: dict) -> pd.DataFrame:
    """Execute a parameterised SELECT and return a DataFrame."""
    engine = get_sync_engine()
    with engine.connect() as conn:
        result = conn.execute(text(sql), params)
        rows = result.fetchmany(settings.sql_row_limit)
        columns = list(result.keys())
    return pd.DataFrame(rows, columns=columns)


# ---------------------------------------------------------------------------
# System-level queries
# ---------------------------------------------------------------------------

GET_MAX_LOG_DATE_SQL = """
SELECT GREATEST(
    (SELECT MAX(log_date) FROM daily_inverter_telemetry),
    (SELECT MAX(log_date) FROM daily_weather_telemetry)
) AS max_log_date
"""

def get_max_log_date() -> date:
    """
    Returns the latest log_date present in either telemetry table.
    This is the system's definition of 'today' (BR-05).
    """
    engine = get_sync_engine()
    with engine.connect() as conn:
        result = conn.execute(text(GET_MAX_LOG_DATE_SQL)).scalar()
    if result is None:
        raise RuntimeError("No telemetry data found in the database.")
    return result  # already a datetime.date from psycopg


GET_MIN_LOG_DATE_SQL = """
SELECT LEAST(
    (SELECT MIN(log_date) FROM daily_inverter_telemetry),
    (SELECT MIN(log_date) FROM daily_weather_telemetry)
) AS min_log_date
"""

def get_min_log_date() -> date:
    """
    Returns the earliest log_date present in either telemetry table.
    """
    engine = get_sync_engine()
    with engine.connect() as conn:
        result = conn.execute(text(GET_MIN_LOG_DATE_SQL)).scalar()
    if result is None:
        return date(2000, 1, 1)
    return result


# ---------------------------------------------------------------------------
# Entity resolution queries
# ---------------------------------------------------------------------------

GET_ALL_PLANTS_SQL = """
SELECT plant_id, plant_name, capacity_mwp
FROM plants
ORDER BY plant_id
LIMIT :row_limit
"""

def get_all_plants() -> pd.DataFrame:
    return _execute_to_df(GET_ALL_PLANTS_SQL, {"row_limit": settings.sql_row_limit})


GET_ALL_BLOCKS_SQL = """
SELECT b.block_id, b.block_name, b.plant_id, p.plant_name
FROM blocks b
JOIN plants p ON b.plant_id = p.plant_id
ORDER BY b.plant_id, b.block_id
LIMIT :row_limit
"""

def get_all_blocks() -> pd.DataFrame:
    return _execute_to_df(GET_ALL_BLOCKS_SQL, {"row_limit": settings.sql_row_limit})


GET_ALL_INVERTERS_SQL = """
SELECT i.inverter_id, i.block_id, i.plant_id,
       i.rated_dc_kw, i.rated_ac_kw,
       b.block_name, p.plant_name
FROM inverters i
JOIN blocks b ON i.block_id = b.block_id
JOIN plants p ON i.plant_id = p.plant_id
ORDER BY i.plant_id, i.block_id, i.inverter_id
LIMIT :row_limit
"""

def get_all_inverters() -> pd.DataFrame:
    return _execute_to_df(GET_ALL_INVERTERS_SQL, {"row_limit": settings.sql_row_limit})


# ---------------------------------------------------------------------------
# Core workhorse: fetch_inverter_daily
# ---------------------------------------------------------------------------

FETCH_INVERTER_DAILY_BASE_SQL = """
SELECT
    dit.log_date,
    dit.inverter_id,
    dit.plant_id,
    inv.block_id,
    inv.rated_dc_kw,
    inv.rated_ac_kw,
    b.block_name,
    p.plant_name,
    dit.peak_dc_power_kw,
    dit.peak_ac_power_kw,
    dit.total_daily_yield_kwh,
    dit.avg_dc_voltage_v,
    dit.avg_dc_current_a,
    dit.inverter_status_code AS status_code,
    dwt.total_solar_radiation_kwh_m2,
    dwt.peak_poa_irradiance_w_m2,
    dwt.avg_ambient_temp_c,
    dwt.max_module_temp_c,
    dwt.soiling_ratio
FROM daily_inverter_telemetry dit
JOIN inverters inv ON dit.inverter_id = inv.inverter_id
JOIN blocks b ON inv.block_id = b.block_id
JOIN plants p ON dit.plant_id = p.plant_id
JOIN daily_weather_telemetry dwt
    ON dit.log_date = dwt.log_date
    AND dit.plant_id = dwt.plant_id
WHERE dit.log_date BETWEEN :start_date AND :end_date
{plant_filter}
{block_filter}
{inverter_filter}
ORDER BY dit.log_date, dit.plant_id, inv.block_id, dit.inverter_id
LIMIT :row_limit
"""


def fetch_inverter_daily(
    start_date: date,
    end_date: date,
    plant_ids: Optional[list[int]] = None,
    block_ids: Optional[list[int]] = None,
    inverter_ids: Optional[list[int]] = None,
) -> pd.DataFrame:
    """
    Primary data-fetching function. Returns a DataFrame with all inverter
    telemetry joined to weather and hierarchy for the specified scope and period.

    Scope filters are AND'd together (narrowing):
      - plant_ids: restrict to these plants
      - block_ids: restrict to inverters in these blocks
      - inverter_ids: restrict to specific inverters

    All three can be None (no restriction) or combined.
    """
    params: dict = {
        "start_date": start_date,
        "end_date": end_date,
        "row_limit": settings.sql_row_limit,
    }

    plant_filter = ""
    block_filter = ""
    inverter_filter = ""

    if plant_ids:
        placeholders = ", ".join(f":plant_id_{i}" for i in range(len(plant_ids)))
        plant_filter = f"AND dit.plant_id IN ({placeholders})"
        for i, pid in enumerate(plant_ids):
            params[f"plant_id_{i}"] = pid

    if block_ids:
        placeholders = ", ".join(f":block_id_{i}" for i in range(len(block_ids)))
        block_filter = f"AND inv.block_id IN ({placeholders})"
        for i, bid in enumerate(block_ids):
            params[f"block_id_{i}"] = bid

    if inverter_ids:
        placeholders = ", ".join(f":inverter_id_{i}" for i in range(len(inverter_ids)))
        inverter_filter = f"AND dit.inverter_id IN ({placeholders})"
        for i, iid in enumerate(inverter_ids):
            params[f"inverter_id_{i}"] = iid

    sql = FETCH_INVERTER_DAILY_BASE_SQL.format(
        plant_filter=plant_filter,
        block_filter=block_filter,
        inverter_filter=inverter_filter,
    )

    df = _execute_to_df(sql, params)

    # Enforce correct dtypes
    if not df.empty:
        df["log_date"] = pd.to_datetime(df["log_date"]).dt.date
        for col in ["plant_id", "status_code"]:
            if col in df.columns:
                df[col] = df[col].astype("Int64")  # nullable integer
        for col in ["inverter_id", "block_id"]:
            if col in df.columns:
                df[col] = df[col].astype("string")
        numeric_cols = [
            "rated_dc_kw", "rated_ac_kw",
            "peak_dc_power_kw", "peak_ac_power_kw", "total_daily_yield_kwh",
            "avg_dc_voltage_v", "avg_dc_current_a",
            "total_solar_radiation_kwh_m2", "peak_poa_irradiance_w_m2",
            "avg_ambient_temp_c", "max_module_temp_c", "soiling_ratio",
        ]
        for col in numeric_cols:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col], errors="coerce")

    return df


# ---------------------------------------------------------------------------
# Weather-only query
# ---------------------------------------------------------------------------

FETCH_WEATHER_DAILY_SQL = """
SELECT
    dwt.log_date,
    dwt.plant_id,
    p.plant_name,
    dwt.peak_poa_irradiance_w_m2,
    dwt.total_solar_radiation_kwh_m2,
    dwt.avg_ambient_temp_c,
    dwt.max_module_temp_c,
    dwt.soiling_ratio
FROM daily_weather_telemetry dwt
JOIN plants p ON dwt.plant_id = p.plant_id
WHERE dwt.log_date BETWEEN :start_date AND :end_date
{plant_filter}
ORDER BY dwt.log_date, dwt.plant_id
LIMIT :row_limit
"""


def fetch_weather_daily(
    start_date: date,
    end_date: date,
    plant_ids: Optional[list[int]] = None,
) -> pd.DataFrame:
    """Returns daily weather data for the specified plants and period."""
    params: dict = {
        "start_date": start_date,
        "end_date": end_date,
        "row_limit": settings.sql_row_limit,
    }

    plant_filter = ""
    if plant_ids:
        placeholders = ", ".join(f":plant_id_{i}" for i in range(len(plant_ids)))
        plant_filter = f"AND dwt.plant_id IN ({placeholders})"
        for i, pid in enumerate(plant_ids):
            params[f"plant_id_{i}"] = pid

    sql = FETCH_WEATHER_DAILY_SQL.format(plant_filter=plant_filter)
    df = _execute_to_df(sql, params)

    if not df.empty:
        df["log_date"] = pd.to_datetime(df["log_date"]).dt.date
        df["plant_id"] = df["plant_id"].astype("Int64")

    return df


# ---------------------------------------------------------------------------
# Inverter telemetry exists check (for gap detection)
# ---------------------------------------------------------------------------

INVERTER_TELEMETRY_GAPS_SQL = """
SELECT
    i.inverter_id,
    i.block_id,
    i.plant_id,
    i.rated_dc_kw,
    COUNT(dit.log_date) AS days_with_data,
    MIN(dit.log_date)   AS first_date,
    MAX(dit.log_date)   AS last_date
FROM inverters i
LEFT JOIN daily_inverter_telemetry dit
    ON i.inverter_id = dit.inverter_id
    AND dit.log_date BETWEEN :start_date AND :end_date
{plant_filter}
GROUP BY i.inverter_id, i.block_id, i.plant_id, i.rated_dc_kw
ORDER BY i.plant_id, i.block_id, i.inverter_id
LIMIT :row_limit
"""


def fetch_telemetry_coverage(
    start_date: date,
    end_date: date,
    plant_ids: Optional[list[int]] = None,
) -> pd.DataFrame:
    """
    Returns one row per inverter with the count of days that have telemetry
    in the specified period. Inverters with days_with_data = 0 are missing
    all data (gaps). Used by Validator and Fault Agent.
    """
    params: dict = {
        "start_date": start_date,
        "end_date": end_date,
        "row_limit": settings.sql_row_limit,
    }

    plant_filter = ""
    if plant_ids:
        placeholders = ", ".join(f":plant_id_{i}" for i in range(len(plant_ids)))
        plant_filter = f"AND i.plant_id IN ({placeholders})"
        for i, pid in enumerate(plant_ids):
            params[f"plant_id_{i}"] = pid

    sql = INVERTER_TELEMETRY_GAPS_SQL.format(plant_filter=plant_filter)
    return _execute_to_df(sql, params)
