"""
data_tools.py — LangChain-compatible tools for the Data Agent.

Tools provided:
  1. get_schema_info()      → semantic layer YAML as structured text
  2. resolve_entities()     → fuzzy-match names to IDs
  3. resolve_date_range()   → natural-language → (start_date, end_date)
  4. run_sql_readonly()     → guarded SQL executor
  5. fetch_inverter_daily() → thin wrapper around db/queries.py
  6. fetch_weather_daily()  → thin wrapper around db/queries.py

Each function is also wrapped as a LangChain @tool so it can be bound
to a ReAct agent in agents/data_agent.py.

Security: run_sql_readonly() has three layers of defence:
  Layer 1 — sqlglot parse: reject anything that is not a SELECT or WITH-SELECT
  Layer 2 — table allowlist: reject queries touching non-approved tables
  Layer 3 — DB engine event guard in db/connection.py (defence in depth)
"""

from __future__ import annotations

import logging
import re
from datetime import date, timedelta
from typing import Optional

import pandas as pd
import sqlglot
import yaml
from langchain_core.tools import tool
from thefuzz import process as fuzzy_process
from pydantic import BaseModel, ConfigDict, Field

from solar_agent.config.settings import settings, SEMANTIC_LAYER_PATH
from solar_agent.db import queries as q

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Internal caches (populated lazily from the DB)
# ---------------------------------------------------------------------------
_plants_cache: Optional[pd.DataFrame] = None
_blocks_cache: Optional[pd.DataFrame] = None
_inverters_cache: Optional[pd.DataFrame] = None


def _get_plants() -> pd.DataFrame:
    global _plants_cache
    if _plants_cache is None:
        _plants_cache = q.get_all_plants()
    return _plants_cache


def _get_blocks() -> pd.DataFrame:
    global _blocks_cache
    if _blocks_cache is None:
        _blocks_cache = q.get_all_blocks()
    return _blocks_cache


def _get_inverters() -> pd.DataFrame:
    global _inverters_cache
    if _inverters_cache is None:
        _inverters_cache = q.get_all_inverters()
    return _inverters_cache


def _invalidate_entity_caches() -> None:
    """Call this if DB is refreshed during a session."""
    global _plants_cache, _blocks_cache, _inverters_cache
    _plants_cache = None
    _blocks_cache = None
    _inverters_cache = None


# ---------------------------------------------------------------------------
# 1. get_schema_info
# ---------------------------------------------------------------------------

class GetSchemaInfoSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")

@tool(args_schema=GetSchemaInfoSchema)
def get_schema_info() -> str:
    """
    Returns the semantic layer YAML as a formatted string.

    This includes table/column descriptions, units, KPI formulas, join patterns,
    and business rules. Agents MUST consult this before constructing any query.
    """
    try:
        with open(SEMANTIC_LAYER_PATH, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        lines = ["# Solar Plant Analysis — Schema & Semantic Layer\n"]

        # Tables
        lines.append("## Tables\n")
        for table_name, table_info in data.get("tables", {}).items():
            lines.append(f"### {table_name}")
            lines.append(f"Description: {table_info.get('description', '').strip()}")
            lines.append(f"Primary key: {table_info.get('primary_key', 'N/A')}")
            if "foreign_keys" in table_info:
                lines.append(f"Foreign keys: {table_info['foreign_keys']}")
            lines.append("Columns:")
            for col, col_info in table_info.get("columns", {}).items():
                unit = f" [{col_info['unit']}]" if "unit" in col_info else ""
                desc = col_info.get("description", "").strip().replace("\n", " ")
                lines.append(f"  - {col}{unit}: {desc}")
            lines.append("")

        # KPI Formulas
        lines.append("## KPI Formulas\n")
        for kpi_name, kpi in data.get("kpi_formulas", {}).items():
            lines.append(f"### {kpi.get('name', kpi_name)}")
            lines.append(f"  Formula: {kpi.get('formula', '')}")
            if "guard" in kpi:
                lines.append(f"  Guard: {kpi['guard']}")
            if "unit" in kpi:
                lines.append(f"  Unit: {kpi['unit']}")
            if "aggregation_note" in kpi:
                lines.append(f"  ⚠ Aggregation: {kpi['aggregation_note'].strip()}")
            lines.append("")

        # Business Rules
        lines.append("## Business Rules\n")
        for rule in data.get("business_rules", []):
            lines.append(f"  [{rule['id']}] {rule['rule'].strip()}")
        lines.append("")

        return "\n".join(lines)

    except Exception as exc:
        logger.error("get_schema_info failed: %s", exc)
        return f"ERROR: Could not load semantic layer: {exc}"


# ---------------------------------------------------------------------------
# 2. resolve_entities
# ---------------------------------------------------------------------------

class ResolveEntitiesSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plant_names: Optional[list[str]] = None
    block_names: Optional[list[str]] = None
    inverter_ids_raw: Optional[list[str]] = None

@tool(args_schema=ResolveEntitiesSchema)
def resolve_entities(
    plant_names: Optional[list[str]] = None,
    block_names: Optional[list[str]] = None,
    inverter_ids_raw: Optional[list[str]] = None,
) -> dict:
    """
    Fuzzy-matches human-readable names to database IDs.

    Args:
        plant_names:      e.g. ["Plant 1", "Rajasthan Solar"]
        block_names:      e.g. ["Block A", "block b"] — matched within resolved plants if provided
        inverter_ids_raw: e.g. ["INV-001", "101"] — numeric strings or labels

    Returns a dict:
    {
        "plant_ids": [1, 2],
        "block_ids": [3, 4],
        "inverter_ids": [10, 11],
        "assumptions": ["Matched 'Block A' to block_id=3 in plant_id=1 (score=95)"]
    }

    The 'assumptions' list should be passed to the AgentState.assumptions field.
    If a name cannot be matched with confidence >= 70, it is reported in 'unresolved'.
    """
    FUZZY_THRESHOLD = 70  # minimum match score (0-100)
    result: dict = {
        "plant_ids": [],
        "block_ids": [],
        "inverter_ids": [],
        "assumptions": [],
        "unresolved": [],
    }

    # --- Plants ---
    if plant_names:
        plants_df = _get_plants()
        name_to_id = dict(zip(plants_df["plant_name"], plants_df["plant_id"]))
        all_names = list(name_to_id.keys())
        for query_name in plant_names:
            match, score = fuzzy_process.extractOne(query_name, all_names) if all_names else (None, 0)
            if match and score >= FUZZY_THRESHOLD:
                pid = name_to_id[match]
                result["plant_ids"].append(int(pid))
                result["assumptions"].append(
                    f"Matched '{query_name}' → plant '{match}' (plant_id={pid}, score={score})"
                )
            else:
                result["unresolved"].append({"type": "plant", "name": query_name, "best_score": score})

    # --- Blocks ---
    if block_names:
        blocks_df = _get_blocks()
        # If plant_ids already resolved, restrict block search to those plants
        if result["plant_ids"]:
            blocks_df = blocks_df[blocks_df["plant_id"].isin(result["plant_ids"])]

        name_to_row = {row["block_name"]: row for _, row in blocks_df.iterrows()}
        all_block_names = list(name_to_row.keys())
        for query_name in block_names:
            match, score = fuzzy_process.extractOne(query_name, all_block_names) if all_block_names else (None, 0)
            if match and score >= FUZZY_THRESHOLD:
                row = name_to_row[match]
                bid = int(row["block_id"])
                result["block_ids"].append(bid)
                result["assumptions"].append(
                    f"Matched '{query_name}' → block '{match}' "
                    f"(block_id={bid}, plant_id={int(row['plant_id'])}, score={score})"
                )
            else:
                result["unresolved"].append({"type": "block", "name": query_name, "best_score": score})

    # --- Inverters ---
    if inverter_ids_raw:
        inverters_df = _get_inverters()
        for raw_id in inverter_ids_raw:
            # Try direct numeric match first
            try:
                numeric_id = int(raw_id)
                if numeric_id in inverters_df["inverter_id"].values:
                    result["inverter_ids"].append(numeric_id)
                    result["assumptions"].append(f"Resolved inverter id '{raw_id}' directly.")
                    continue
            except (ValueError, TypeError):
                pass
            result["unresolved"].append({"type": "inverter", "id": raw_id})

    return result


# ---------------------------------------------------------------------------
# 3. resolve_date_range
# ---------------------------------------------------------------------------

class ResolveDateRangeSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    text: str


@tool(args_schema=ResolveDateRangeSchema)
def resolve_date_range(text: str) -> dict:
    """
    Parses natural-language date expressions into concrete start/end dates.

    Deterministic resolver in code, relative to MAX(log_date) and clamped to the
    available data range, with a note when clamped.
    """
    today = q.get_max_log_date()
    min_db = q.get_min_log_date()

    t = text.strip().lower()
    # Normalize unicode hyphens/dashes
    for ch in ("\u2011", "\u2013", "\u2014", "\u00ad"):
        t = t.replace(ch, "-")

    def _fmt(d: date) -> str:
        return d.isoformat()

    def _fmt_short(s: date, e: date) -> str:
        return f"{s.day} {s.strftime('%b')} to {e.day} {e.strftime('%b')}"

    def _first_of_month(d: date) -> date:
        return d.replace(day=1)

    def _last_of_month(d: date) -> date:
        if d.month == 12:
            return d.replace(year=d.year + 1, month=1, day=1) - timedelta(days=1)
        return d.replace(month=d.month + 1, day=1) - timedelta(days=1)

    _MONTH_MAP = {
        "jan": 1, "january": 1, "feb": 2, "february": 2, "mar": 3, "march": 3,
        "apr": 4, "april": 4, "may": 5, "jun": 6, "june": 6, "jul": 7, "july": 7,
        "aug": 8, "august": 8, "sep": 9, "sept": 9, "september": 9,
        "oct": 10, "october": 10, "nov": 11, "november": 11, "dec": 12, "december": 12,
    }
    _MONTH_PATTERN = (
        r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|"
        r"jul(?:y)?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    )

    def _infer_year(month_num: int) -> int:
        for yr in (today.year, today.year - 1, today.year + 1):
            try:
                candidate_start = date(yr, month_num, 1)
                candidate_end = _last_of_month(candidate_start)
                if not (candidate_end < min_db or candidate_start > today):
                    return yr
            except ValueError:
                pass
        return today.year

    _WHOLE_RANGE_PHRASES = [
        "whole dataset", "entire dataset", "whole data", "entire data",
        "whole period", "entire period", "all time", "all data",
        "full period", "full dataset", "overall", "so far", "in total",
    ]

    def _parse(t_in: str):
        swapped = False
        has_period_phrase = True

        # 1. Whole dataset phrases
        t_clean = re.sub(r"\b(the|for|show|me|data|in|plant|inverter|block)\b", "", t_in).strip()
        if t_clean in ("all", "all time", "overall", "ever", "so far", "in total") or any(p in t_in for p in _WHOLE_RANGE_PHRASES) or re.search(r"\b(ever|overall)\b", t_in):
            start = min_db
            end = today
            assumption = f"Interpreted '{text}' as full data range: {_fmt(start)} to {_fmt(end)}."
            return start, end, assumption, has_period_phrase, swapped

        # 2. yesterday
        if re.search(r"\byesterday\b", t_in):
            start = today - timedelta(days=1)
            end = start
            assumption = f"Interpreted 'yesterday' as {_fmt(start)}."
            return start, end, assumption, has_period_phrase, swapped

        # 3. Explicit ISO range: 2026-09-01 to 2026-09-26
        explicit = re.search(r"(\d{4}-\d{2}-\d{2})\s*(?:to|through|-)\s*(\d{4}-\d{2}-\d{2})", t_in)
        if explicit:
            start = date.fromisoformat(explicit.group(1))
            end = date.fromisoformat(explicit.group(2))
            if start > end:
                start, end = end, start
                swapped = True
            note = " (reversed dates swapped, with a note)" if swapped else ""
            assumption = f"Explicit date range: {_fmt(start)} to {_fmt(end)}{note}."
            return start, end, assumption, has_period_phrase, swapped

        # 4. "1 to 15 September" / "from 20 Sept to 10 Sept" (reversed dates swapped, with a note)
        cross_month_range = re.search(
            r"(?:from\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_PATTERN +
            r")\s+(?:to|-)\s+(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_PATTERN +
            r")(?:\s+(\d{4}))?",
            t_in,
        )
        if cross_month_range:
            d1 = int(cross_month_range.group(1))
            m1_name = cross_month_range.group(2).lower()
            d2 = int(cross_month_range.group(3))
            m2_name = cross_month_range.group(4).lower()
            yr_str = cross_month_range.group(5)
            m1_num = _MONTH_MAP.get(m1_name, 1)
            m2_num = _MONTH_MAP.get(m2_name, 1)
            year = int(yr_str) if yr_str else _infer_year(m1_num)
            start = date(year, m1_num, d1)
            end = date(year, m2_num, d2)
            if start > end:
                start, end = end, start
                swapped = True
            note = " (reversed dates swapped, with a note)" if swapped else ""
            assumption = f"Interpreted '{text}' as {_fmt(start)} to {_fmt(end)}{note}."
            return start, end, assumption, has_period_phrase, swapped

        same_month_range = re.search(
            r"(?:from\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(?:to|-)\s+(\d{1,2})(?:st|nd|rd|th)?\s+(" +
            _MONTH_PATTERN + r")(?:\s+(\d{4}))?",
            t_in,
        )
        if same_month_range:
            d1 = int(same_month_range.group(1))
            d2 = int(same_month_range.group(2))
            m_name = same_month_range.group(3).lower()
            yr_str = same_month_range.group(4)
            m_num = _MONTH_MAP.get(m_name, 9)
            year = int(yr_str) if yr_str else _infer_year(m_num)
            if d1 > d2:
                d1, d2 = d2, d1
                swapped = True
            start = date(year, m_num, d1)
            end = date(year, m_num, d2)
            note = " (reversed dates swapped, with a note)" if swapped else ""
            assumption = f"Interpreted '{text}' as {_fmt(start)} to {_fmt(end)}{note}."
            return start, end, assumption, has_period_phrase, swapped

        # 5. Single day with year: "16 September 2026", "on 16 Sept 2026", "Sept 16 2026"
        single_day_yr = re.search(
            r"(?:on\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_PATTERN + r")\s+(\d{4})",
            t_in,
        )
        if single_day_yr:
            d_num = int(single_day_yr.group(1))
            m_name = single_day_yr.group(2).lower()
            year = int(single_day_yr.group(3))
            start = date(year, _MONTH_MAP[m_name], d_num)
            end = start
            assumption = f"Interpreted '{text}' as single day {_fmt(start)}."
            return start, end, assumption, has_period_phrase, swapped

        single_day_mdy = re.search(
            r"(?:on\s+)?(" + _MONTH_PATTERN + r")\s+(\d{1,2})(?:st|nd|rd|th)?(?:,)?\s+(\d{4})",
            t_in,
        )
        if single_day_mdy:
            m_name = single_day_mdy.group(1).lower()
            d_num = int(single_day_mdy.group(2))
            year = int(single_day_mdy.group(3))
            start = date(year, _MONTH_MAP[m_name], d_num)
            end = start
            assumption = f"Interpreted '{text}' as single day {_fmt(start)}."
            return start, end, assumption, has_period_phrase, swapped

        # 6. Single day WITHOUT year: "16 September", "on 16 Sept" (missing year inferred)
        single_day_noyr = re.search(
            r"(?:on\s+)?(\d{1,2})(?:st|nd|rd|th)?\s+(" + _MONTH_PATTERN + r")\b",
            t_in,
        )
        if single_day_noyr:
            d_num = int(single_day_noyr.group(1))
            m_name = single_day_noyr.group(2).lower()
            m_num = _MONTH_MAP[m_name]
            year = _infer_year(m_num)
            start = date(year, m_num, d_num)
            end = start
            assumption = f"Interpreted '{text}' as single day {_fmt(start)} (year {year} inferred)."
            return start, end, assumption, has_period_phrase, swapped

        single_day_md_noyr = re.search(
            r"(?:on\s+)?(" + _MONTH_PATTERN + r")\s+(\d{1,2})(?:st|nd|rd|th)?\b",
            t_in,
        )
        if single_day_md_noyr:
            m_name = single_day_md_noyr.group(1).lower()
            d_num = int(single_day_md_noyr.group(2))
            m_num = _MONTH_MAP[m_name]
            year = _infer_year(m_num)
            start = date(year, m_num, d_num)
            end = start
            assumption = f"Interpreted '{text}' as single day {_fmt(start)} (year {year} inferred)."
            return start, end, assumption, has_period_phrase, swapped

        # 7. Single ISO date: "2026-09-16"
        single_iso = re.search(r"\b(\d{4}-\d{2}-\d{2})\b", t_in)
        if single_iso:
            start = date.fromisoformat(single_iso.group(1))
            end = start
            assumption = f"Interpreted '{text}' as single day {_fmt(start)}."
            return start, end, assumption, has_period_phrase, swapped

        # 8. Month + year: "September 2026", "Sept 2026", "aug 2026"
        month_yr = re.search(r"\b(" + _MONTH_PATTERN + r")\s+(\d{4})\b", t_in)
        if month_yr:
            m_name = month_yr.group(1).lower()
            year = int(month_yr.group(2))
            m_num = _MONTH_MAP[m_name]
            start = date(year, m_num, 1)
            end = _last_of_month(start)
            assumption = f"Interpreted '{text}' as {m_name.capitalize()} {year}: {_fmt(start)} to {_fmt(end)}."
            return start, end, assumption, has_period_phrase, swapped

        # 9. Quarter: "Q1 2026", "q3 2026"
        quarter_match = re.search(r"\bq([1-4])\s+(\d{4})\b", t_in)
        if quarter_match:
            q_num = int(quarter_match.group(1))
            year = int(quarter_match.group(2))
            q_start_month = (q_num - 1) * 3 + 1
            start = date(year, q_start_month, 1)
            end = _last_of_month(date(year, q_start_month + 2, 1))
            assumption = f"Interpreted '{text}' as Q{q_num} {year}: {_fmt(start)} to {_fmt(end)}."
            return start, end, assumption, has_period_phrase, swapped

        # 10. "last month" / "previous month" (previous full calendar month before MAX(log_date), stated in answer)
        if any(p in t_in for p in ("last month", "previous month")):
            first_this = _first_of_month(today)
            end = first_this - timedelta(days=1)
            start = _first_of_month(end)
            assumption = f"Interpreted 'last month' as previous calendar month {start.strftime('%B %Y')}: {_fmt(start)} to {_fmt(end)}."
            return start, end, assumption, has_period_phrase, swapped

        # 11. "this month"
        if "this month" in t_in:
            start = _first_of_month(today)
            end = today
            assumption = f"Interpreted 'this month' as {_fmt(start)} to {_fmt(end)}."
            return start, end, assumption, has_period_phrase, swapped

        # 12. "last N days" / "past N days"
        n_days = re.search(r"\b(?:last|past)\s+(\d+)\s+days?\b", t_in)
        if n_days:
            n = int(n_days.group(1))
            start = today - timedelta(days=n - 1)
            end = today
            assumption = f"Interpreted '{text}' as last {n} days: {_fmt(start)} to {_fmt(end)}."
            return start, end, assumption, has_period_phrase, swapped

        # 13. "last N weeks" / "past N weeks"
        n_weeks = re.search(r"\b(?:last|past)\s+(\d+)\s+weeks?\b", t_in)
        if n_weeks:
            n = int(n_weeks.group(1))
            start = today - timedelta(days=n * 7 - 1)
            end = today
            assumption = f"Interpreted '{text}' as last {n} weeks: {_fmt(start)} to {_fmt(end)}."
            return start, end, assumption, has_period_phrase, swapped

        # 14. "last N months"
        n_months = re.search(r"\b(?:last|past)\s+(\d+)\s+months?\b", t_in)
        if n_months:
            n = int(n_months.group(1))
            first_this = _first_of_month(today)
            mo = first_this.month - n
            yr = first_this.year + (mo - 1) // 12
            mo = ((mo - 1) % 12) + 1
            start = date(yr, mo, 1)
            end = today
            assumption = f"Interpreted '{text}' as last {n} months: {_fmt(start)} to {_fmt(end)}."
            return start, end, assumption, has_period_phrase, swapped

        # 15. "last week" / "this week"
        if "last week" in t_in or "previous week" in t_in:
            dsm = today.weekday()
            last_sun = today - timedelta(days=dsm + 1)
            last_mon = last_sun - timedelta(days=6)
            assumption = f"Interpreted 'last week' as {_fmt(last_mon)} to {_fmt(last_sun)}."
            return last_mon, last_sun, assumption, has_period_phrase, swapped

        if "this week" in t_in:
            dsm = today.weekday()
            start = today - timedelta(days=dsm)
            end = today
            assumption = f"Interpreted 'this week' as {_fmt(start)} to {_fmt(end)}."
            return start, end, assumption, has_period_phrase, swapped

        # 16. Bare month: "in August", "September", "sept", "sep" (missing year inferred)
        bare_month = re.search(r"(?:\b(?:in|for|during)\s+)?\b(" + _MONTH_PATTERN + r")\b", t_in)
        if bare_month:
            m_name = bare_month.group(1).lower()
            m_num = _MONTH_MAP[m_name]
            year = _infer_year(m_num)
            start = date(year, m_num, 1)
            end = _last_of_month(start)
            assumption = f"Interpreted '{text}' as {m_name.capitalize()} {year} (year {year} inferred): {_fmt(start)} to {_fmt(end)}."
            return start, end, assumption, has_period_phrase, swapped

        # 17. Default fallback: no period phrase found
        has_period_phrase = False
        start = today - timedelta(days=29)
        end = today
        assumption = f"No period given, so I used the last 30 days, {_fmt_short(start, end)}"
        return start, end, assumption, has_period_phrase, swapped

    raw_start, raw_end, assumption, has_period_phrase, swapped = _parse(t)

    # Clamping & out of range check
    clamped = False
    out_of_range = False
    warnings_list = []

    if raw_end < min_db or raw_start > today:
        out_of_range = True
        clamped = True
        warnings_list.append(
            f"Requested period ({_fmt(raw_start)} to {_fmt(raw_end)}) is outside available data range "
            f"({_fmt(min_db)} to {_fmt(today)}). No data available."
        )
    else:
        if raw_start < min_db:
            clamped = True
            warnings_list.append(f"Start date {_fmt(raw_start)} predates available data; clamped to {_fmt(min_db)}.")
            raw_start = min_db
        if raw_end > today:
            clamped = True
            warnings_list.append(f"End date {_fmt(raw_end)} exceeds available data; clamped to {_fmt(today)}.")
            raw_end = today

    if warnings_list:
        assumption += " | " + " ".join(warnings_list)

    return {
        "start_date": _fmt(raw_start),
        "end_date": _fmt(raw_end),
        "today": _fmt(today),
        "assumption": assumption,
        "has_period_phrase": has_period_phrase,
        "not_parsed": not has_period_phrase,
        "clamped": clamped,
        "swapped": swapped,
        "out_of_range": out_of_range,
    }


# 4. run_sql_readonly — guarded SQL executor
# ---------------------------------------------------------------------------

class SecurityError(Exception):
    """Raised when a SQL statement violates security guardrails."""
    pass


def _validate_sql_security(sql: str) -> None:
    """
    Three-layer SQL security validation.

    Layer 1: sqlglot parse — ensure statement is SELECT or WITH-SELECT.
    Layer 2: Table allowlist — ensure only approved BASE tables are referenced
             (CTE aliases/subquery aliases are excluded from the check).

    Raises SecurityError with a descriptive message if any check fails.
    """
    # --- Empty / whitespace check ---
    if not sql or not sql.strip():
        raise SecurityError("Empty or whitespace-only SQL statement.")

    # --- Layer 1: Parse and check statement type ---
    try:
        statements = sqlglot.parse(sql, dialect="postgres")
    except sqlglot.errors.ParseError as exc:
        raise SecurityError(f"SQL parse error: {exc}") from exc

    # Filter out None entries (sqlglot may emit None for trailing semicolons)
    statements = [s for s in statements if s is not None]

    if not statements:
        raise SecurityError("No parseable SQL statements found.")

    for stmt in statements:
        stmt_type = type(stmt).__name__
        if stmt_type not in ("Select", "With"):
            raise SecurityError(
                f"Rejected: only SELECT statements are allowed. "
                f"Got statement type: {stmt_type}. "
                f"SQL: {sql[:120]!r}"
            )
        # Extra check: if it's a WITH, the final query must be SELECT
        if stmt_type == "With":
            if not isinstance(stmt.this, sqlglot.expressions.Select):
                raise SecurityError(
                    "Rejected: WITH clause must terminate in a SELECT, not a DML statement."
                )

        # Deep AST scan: walk all nodes looking for any DML expression.
        # sqlglot 25.x may parse CTE-wrapped INSERT/DELETE as a Select node
        # but still embed the DML type in the AST tree.
        _DML_TYPES = (
            sqlglot.expressions.Insert,
            sqlglot.expressions.Update,
            sqlglot.expressions.Delete,
            sqlglot.expressions.Drop,
            sqlglot.expressions.Create,
            sqlglot.expressions.Alter,
            sqlglot.expressions.Command,
        )
        for node in stmt.walk():
            if isinstance(node, _DML_TYPES):
                raise SecurityError(
                    f"Rejected: DML expression '{type(node).__name__}' found in the query. "
                    f"Only pure SELECT queries are permitted."
                )

    # --- Layer 2: Table allowlist ---
    # Collect CTE alias names so we don't flag them as disallowed real tables.
    allowed = set(settings.sql_allowed_tables)
    for stmt in statements:
        # Gather CTE alias names defined in this statement
        cte_aliases: set[str] = set()
        for cte in stmt.find_all(sqlglot.expressions.CTE):
            if cte.alias:
                cte_aliases.add(cte.alias.lower())

        # Check all Table nodes that are NOT CTE aliases
        for table_node in stmt.find_all(sqlglot.expressions.Table):
            table_name = table_node.name.lower()
            if not table_name:
                continue
            if table_name in cte_aliases:
                continue  # it's a CTE alias reference, not a real table
            if table_name not in allowed:
                raise SecurityError(
                    f"Rejected: table '{table_name}' is not in the allowlist. "
                    f"Allowed tables: {sorted(allowed)}"
                )


class RunSqlReadonlySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    sql: str

@tool(args_schema=RunSqlReadonlySchema)
def run_sql_readonly(sql: str) -> dict:
    """
    Executes a validated, read-only SQL SELECT against the database.

    Security checks:
      1. Only SELECT (or WITH...SELECT) statements allowed.
      2. Only allowlisted tables may be queried.
      3. Result set is capped at sql_row_limit rows.
      4. Query times out after sql_timeout_seconds.

    Returns:
    {
        "columns": [...],
        "rows": [[...], ...],
        "row_count": N,
        "truncated": bool,   # True if row_limit was hit
        "sql_used": "...",
    }
    On error returns {"error": "..."} — the agent must handle this gracefully.
    """
    try:
        _validate_sql_security(sql)
    except SecurityError as exc:
        logger.warning("SQL security rejection: %s", exc)
        return {"error": f"SecurityError: {exc}"}

    from solar_agent.db.connection import get_sync_engine
    from sqlalchemy import text

    try:
        engine = get_sync_engine()
        # Inject statement timeout via SET LOCAL (PostgreSQL-specific)
        with engine.connect() as conn:
            conn.execute(
                text(f"SET LOCAL statement_timeout = '{settings.sql_timeout_seconds * 1000}'")
            )
            result = conn.execute(text(sql))
            columns = list(result.keys())
            rows = result.fetchmany(settings.sql_row_limit + 1)

        truncated = len(rows) > settings.sql_row_limit
        rows = rows[: settings.sql_row_limit]

        return {
            "columns": columns,
            "rows": [list(row) for row in rows],
            "row_count": len(rows),
            "truncated": truncated,
            "sql_used": sql,
        }

    except Exception as exc:
        logger.error("run_sql_readonly execution error: %s", exc)
        return {"error": str(exc), "sql_used": sql}


# ---------------------------------------------------------------------------
# 5 & 6. fetch_inverter_daily / fetch_weather_daily (tool wrappers)
# ---------------------------------------------------------------------------

class FetchInverterDailySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: str
    end_date: str
    plant_ids: Optional[list[int]] = None
    block_ids: Optional[list[str]] = None
    inverter_ids: Optional[list[str]] = None
    all_plants: bool = False

@tool(args_schema=FetchInverterDailySchema)
def fetch_inverter_daily_tool(
    start_date: str,
    end_date: str,
    plant_ids: Optional[list[int]] = None,
    block_ids: Optional[list[str]] = None,
    inverter_ids: Optional[list[str]] = None,
    all_plants: bool = False,
) -> dict:
    """
    Fetches daily inverter telemetry joined with weather and hierarchy data.

    This is the primary data-fetching tool. Use it instead of hand-writing SQL
    whenever possible (it uses trusted, pre-validated query templates).

    Args:
        start_date:   ISO date string "YYYY-MM-DD"
        end_date:     ISO date string "YYYY-MM-DD"
        plant_ids:    List of plant_id integers to filter (REQUIRED unless you want all plants).
                      Omitting this returns ALL plants — only do this for cross-plant queries.
        block_ids:    Optional list of block_id strings (e.g. ['1', '2'])
        inverter_ids: Optional list of inverter_id strings (e.g. ['P1_Block_A_INV_01'])
        all_plants:   Boolean flag indicating if all plants are intentionally fetched.

    Returns a dict with:
        "data_ref": str           — in-memory key to retrieve the DataFrame from DataStore
        "shape":    [rows, cols]
        "distinct_plant_ids": [...] — actual plant_ids in the result (scope verification)
        "date_range": {"min": ..., "max": ...}
        "warnings": [...]         — scope warnings if result is empty or scope mismatch
    """
    from solar_agent.graph.data_store import DataStore  # avoid circular at module load

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    df = q.fetch_inverter_daily(
        start_date=start,
        end_date=end,
        plant_ids=plant_ids,
        block_ids=block_ids,
        inverter_ids=inverter_ids,
    )

    # Scope guard: warn on empty result or unexpected plant_ids
    warnings: list[str] = []
    if df.empty:
        warnings.append(
            f"fetch_inverter_daily returned 0 rows for the given filters "
            f"(plant_ids={plant_ids}, dates={start_date} to {end_date}). "
            "Check that the plant IDs and date range are correct."
        )
    else:
        actual_plants = sorted(df["plant_id"].unique().tolist())
        if plant_ids and set(actual_plants) != set(plant_ids):
            warnings.append(
                f"Scope mismatch: requested plant_ids={plant_ids} but result contains "
                f"plant_ids={actual_plants}. Results may cover more or fewer plants than intended."
            )
        if (plant_ids is None or len(plant_ids) == 0) and not all_plants:
            warnings.append(
                f"No plant_ids filter applied — result covers ALL plants: {actual_plants}. "
                "Pass plant_ids=[...] to scope to a specific plant."
            )

    ref = DataStore.store(df)
    distinct_plants = sorted(df["plant_id"].unique().tolist()) if not df.empty else []
    return {
        "data_ref": ref,
        "row_count": len(df),
        "shape": list(df.shape),
        "columns": list(df.columns),
        "sample": df.head(3).to_dict(orient="records"),
        "date_range": {
            "min": str(df["log_date"].min()) if not df.empty else None,
            "max": str(df["log_date"].max()) if not df.empty else None,
        },
        "distinct_plant_ids": distinct_plants,
        "empty": df.empty,
        "warnings": warnings,
    }


class FetchWeatherDailySchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    start_date: str
    end_date: str
    plant_ids: Optional[list[int]] = None

@tool(args_schema=FetchWeatherDailySchema)
def fetch_weather_daily_tool(
    start_date: str,
    end_date: str,
    plant_ids: Optional[list[int]] = None,
) -> dict:
    """
    Fetches daily weather data (plant-level) for the specified period.

    Args:
        start_date: ISO date string "YYYY-MM-DD"
        end_date:   ISO date string "YYYY-MM-DD"
        plant_ids:  Optional list of plant_id integers

    Returns a dict with data_ref, shape, columns, sample, date_range.
    """
    from solar_agent.graph.data_store import DataStore

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    df = q.fetch_weather_daily(
        start_date=start,
        end_date=end,
        plant_ids=plant_ids,
    )

    ref = DataStore.store(df)
    return {
        "data_ref": ref,
        "shape": list(df.shape),
        "columns": list(df.columns),
        "sample": df.head(3).to_dict(orient="records"),
        "date_range": {
            "min": str(df["log_date"].min()) if not df.empty else None,
            "max": str(df["log_date"].max()) if not df.empty else None,
        },
        "empty": df.empty,
    }


# ---------------------------------------------------------------------------
# 7. get_plant_overview
# ---------------------------------------------------------------------------

from typing import Literal

class GetPlantOverviewSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plant_ids: Optional[list[int]] = None
    level: Literal["plants", "blocks", "inverters"] = "plants"


def get_plant_overview(
    plant_ids: Optional[list[int]] = None,
    level: str = "plants",
) -> dict[str, Any]:
    """
    Returns plant, block, or inverter structural overview and data coverage
    without joining telemetry.
    """
    from sqlalchemy import text
    from solar_agent.db.connection import get_sync_engine
    from solar_agent.graph.data_store import DataStore

    engine = get_sync_engine()
    with engine.connect() as conn:
        cov = conn.execute(text(
            "SELECT MIN(log_date) as min_date, MAX(log_date) as max_date FROM daily_inverter_telemetry"
        )).fetchone()
        data_coverage = {
            "min_date": str(cov.min_date) if cov and cov.min_date else None,
            "max_date": str(cov.max_date) if cov and cov.max_date else None,
            "start_date": str(cov.min_date) if cov and cov.min_date else None,
            "end_date": str(cov.max_date) if cov and cov.max_date else None,
        }

        p_filter = ""
        params: dict[str, Any] = {}
        if plant_ids:
            p_filter = "WHERE p.plant_id = ANY(:plant_ids)"
            params["plant_ids"] = list(plant_ids)

        if level == "plants":
            sql = f"""
            SELECT
                p.plant_id,
                p.plant_name,
                CAST(p.capacity_mwp AS FLOAT) AS capacity_mwp,
                CAST(p.area_acres AS FLOAT) AS area_acres,
                CAST((SELECT COUNT(*) FROM blocks b WHERE b.plant_id = p.plant_id) AS INT) AS block_count,
                CAST((SELECT COUNT(*) FROM inverters i WHERE i.plant_id = p.plant_id) AS INT) AS inverter_count,
                CAST((SELECT COALESCE(SUM(i.rated_dc_kw), 0) FROM inverters i WHERE i.plant_id = p.plant_id) AS FLOAT) AS total_rated_dc_kw,
                CAST((SELECT COALESCE(SUM(i.rated_ac_kw), 0) FROM inverters i WHERE i.plant_id = p.plant_id) AS FLOAT) AS total_rated_ac_kw
            FROM plants p
            {p_filter}
            ORDER BY p.plant_id
            """
            df = pd.read_sql_query(text(sql), conn, params=params if params else None)
            plants_list = df.to_dict(orient="records")
            data_ref = DataStore.store(df)
            metrics = {
                "total_plants": len(df),
                "total_blocks": int(df["block_count"].sum()) if not df.empty else 0,
                "total_inverters": int(df["inverter_count"].sum()) if not df.empty else 0,
                "combined_capacity_mwp": round(float(df["capacity_mwp"].sum()), 2) if not df.empty else 0.0,
                "total_rated_dc_kw": round(float(df["total_rated_dc_kw"].sum()), 2) if not df.empty else 0.0,
                "total_rated_ac_kw": round(float(df["total_rated_ac_kw"].sum()), 2) if not df.empty else 0.0,
            }
            return {
                "level": "plants",
                "plants": plants_list,
                "data": plants_list,
                "data_ref": data_ref,
                "total_count": len(df),
                "total_plants": metrics["total_plants"],
                "total_blocks": metrics["total_blocks"],
                "total_inverters": metrics["total_inverters"],
                "combined_capacity_mwp": metrics["combined_capacity_mwp"],
                "total_capacity_mwp": metrics["combined_capacity_mwp"],
                "data_coverage": data_coverage,
                "metrics": metrics,
                "summary": f"Retrieved structural overview for {len(df)} solar plants (total {metrics['combined_capacity_mwp']} MWp).",
            }

        elif level == "blocks":
            b_filter = "WHERE b.plant_id = ANY(:plant_ids)" if plant_ids else ""
            sql = f"""
            SELECT
                p.plant_name,
                b.block_name,
                CAST(COUNT(i.inverter_id) AS INT) AS inverter_count,
                CAST(COALESCE(SUM(i.rated_dc_kw), 0) AS FLOAT) AS rated_dc_kw
            FROM blocks b
            JOIN plants p ON b.plant_id = p.plant_id
            LEFT JOIN inverters i ON b.block_id = i.block_id
            {b_filter}
            GROUP BY p.plant_id, p.plant_name, b.block_id, b.block_name
            ORDER BY p.plant_id, b.block_id
            """
            df = pd.read_sql_query(text(sql), conn, params=params if params else None)
            blocks_list = df.to_dict(orient="records")
            data_ref = DataStore.store(df)
            metrics = {
                "total_blocks": len(df),
                "total_inverters": int(df["inverter_count"].sum()) if not df.empty else 0,
                "total_rated_dc_kw": round(float(df["rated_dc_kw"].sum()), 2) if not df.empty else 0.0,
            }
            return {
                "level": "blocks",
                "blocks": blocks_list,
                "data": blocks_list,
                "data_ref": data_ref,
                "total_count": len(df),
                "data_coverage": data_coverage,
                "metrics": metrics,
                "summary": f"Retrieved {len(df)} blocks across {len(plant_ids) if plant_ids else 'all'} plants.",
            }

        elif level == "inverters":
            i_filter = "WHERE i.plant_id = ANY(:plant_ids)" if plant_ids else ""
            sql = f"""
            SELECT
                i.inverter_id,
                b.block_name,
                CAST(i.rated_dc_kw AS FLOAT) AS rated_dc_kw,
                CAST(i.rated_ac_kw AS FLOAT) AS rated_ac_kw
            FROM inverters i
            JOIN blocks b ON i.block_id = b.block_id
            {i_filter}
            ORDER BY i.plant_id, i.block_id, i.inverter_id
            """
            df = pd.read_sql_query(text(sql), conn, params=params if params else None)
            total_count = len(df)
            capped_df = df.head(50)
            inverters_list = capped_df.to_dict(orient="records")
            data_ref = DataStore.store(capped_df)
            metrics = {
                "total_count": total_count,
                "returned_count": len(capped_df),
                "total_rated_dc_kw": round(float(df["rated_dc_kw"].sum()), 2) if not df.empty else 0.0,
                "total_rated_ac_kw": round(float(df["rated_ac_kw"].sum()), 2) if not df.empty else 0.0,
            }
            return {
                "level": "inverters",
                "inverters": inverters_list,
                "data": inverters_list,
                "data_ref": data_ref,
                "total_count": total_count,
                "returned_count": len(capped_df),
                "data_coverage": data_coverage,
                "metrics": metrics,
                "summary": f"Retrieved {len(capped_df)} inverters (total {total_count} matching).",
            }
        else:
            raise ValueError(f"Invalid level '{level}'. Must be 'plants', 'blocks', or 'inverters'.")


@tool(args_schema=GetPlantOverviewSchema)
def get_plant_overview_tool(
    plant_ids: Optional[list[int]] = None,
    level: str = "plants",
) -> dict:
    """
    Returns plant, block, or inverter structural overview and telemetry data coverage.

    Args:
        plant_ids: Optional list of plant_id integers to filter.
        level:     'plants', 'blocks', or 'inverters' (default: 'plants').
    """
    return get_plant_overview(plant_ids=plant_ids, level=level)

