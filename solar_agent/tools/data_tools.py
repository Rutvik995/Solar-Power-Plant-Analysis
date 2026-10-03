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

@tool
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

@tool
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

@tool
def resolve_date_range(text: str) -> dict:
    """
    Parses natural-language date expressions into concrete start/end dates.

    'Today' is defined as MAX(log_date) from the database (BR-05).

    Supported expressions:
      - "last month", "previous month"
      - "this month"
      - "last week", "this week"
      - "last N days" / "past N days"  (e.g. "last 30 days")
      - "last N months"                 (e.g. "last 3 months")
      - "last quarter", "this quarter"
      - "last year", "this year"
      - "Q1 2025", "Q2 2024", etc.
      - Explicit dates: "2025-01-01 to 2025-03-31"
      - "all" / "all time" → full available range

    Returns:
    {
        "start_date": "YYYY-MM-DD",
        "end_date":   "YYYY-MM-DD",
        "today":      "YYYY-MM-DD",
        "assumption": "Interpreted 'last month' as 2025-07-01 to 2025-07-31"
    }
    """
    today = q.get_max_log_date()
    t = text.strip().lower()

    def _fmt(d: date) -> str:
        return d.isoformat()

    def _first_of_month(d: date) -> date:
        return d.replace(day=1)

    def _last_of_month(d: date) -> date:
        # Go to first of next month, then back 1 day
        if d.month == 12:
            return d.replace(year=d.year + 1, month=1, day=1) - timedelta(days=1)
        return d.replace(month=d.month + 1, day=1) - timedelta(days=1)

    start: date
    end: date = today
    assumption: str

    # --- Explicit YYYY-MM-DD to YYYY-MM-DD ---
    explicit = re.search(
        r"(\d{4}-\d{2}-\d{2})\s+(?:to|through|–|-)\s+(\d{4}-\d{2}-\d{2})", t
    )
    if explicit:
        start = date.fromisoformat(explicit.group(1))
        end = date.fromisoformat(explicit.group(2))
        assumption = f"Explicit date range: {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- Quarter expressions: "Q1 2025", "q2 2024" ---
    quarter_match = re.search(r"q([1-4])\s+(\d{4})", t)
    if quarter_match:
        q_num = int(quarter_match.group(1))
        year = int(quarter_match.group(2))
        q_start_month = (q_num - 1) * 3 + 1
        start = date(year, q_start_month, 1)
        end_month = q_start_month + 2
        end = _last_of_month(date(year, end_month, 1))
        assumption = f"Interpreted '{text}' as Q{q_num} {year}: {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- "last N days" / "past N days" ---
    n_days_match = re.search(r"(?:last|past)\s+(\d+)\s+days?", t)
    if n_days_match:
        n = int(n_days_match.group(1))
        start = today - timedelta(days=n - 1)
        end = today
        assumption = f"Interpreted '{text}' as last {n} days: {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- "last N months" ---
    n_months_match = re.search(r"(?:last|past)\s+(\d+)\s+months?", t)
    if n_months_match:
        n = int(n_months_match.group(1))
        # Go back n months from first of this month
        this_month_first = _first_of_month(today)
        month = this_month_first.month - n
        year = this_month_first.year + (month - 1) // 12
        month = ((month - 1) % 12) + 1
        start = date(year, month, 1)
        end = today
        assumption = f"Interpreted '{text}' as last {n} months: {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- "last month" / "previous month" ---
    if any(p in t for p in ("last month", "previous month")):
        first_this = _first_of_month(today)
        end = first_this - timedelta(days=1)
        start = _first_of_month(end)
        assumption = f"Interpreted '{text}' as: {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- "this month" ---
    if "this month" in t:
        start = _first_of_month(today)
        end = today
        assumption = f"Interpreted '{text}' as: {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- "last week" ---
    if "last week" in t or "previous week" in t:
        # ISO week: Monday–Sunday
        days_since_monday = today.weekday()  # 0=Mon
        last_sunday = today - timedelta(days=days_since_monday + 1)
        last_monday = last_sunday - timedelta(days=6)
        start = last_monday
        end = last_sunday
        assumption = f"Interpreted '{text}' as last week: {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- "this week" ---
    if "this week" in t:
        days_since_monday = today.weekday()
        start = today - timedelta(days=days_since_monday)
        end = today
        assumption = f"Interpreted '{text}' as this week: {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- "last quarter" ---
    if "last quarter" in t or "previous quarter" in t:
        current_q = (today.month - 1) // 3 + 1
        if current_q == 1:
            q_num = 4
            year = today.year - 1
        else:
            q_num = current_q - 1
            year = today.year
        q_start_month = (q_num - 1) * 3 + 1
        start = date(year, q_start_month, 1)
        end = _last_of_month(date(year, q_start_month + 2, 1))
        assumption = f"Interpreted '{text}' as Q{q_num} {year}: {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- "this quarter" ---
    if "this quarter" in t:
        current_q = (today.month - 1) // 3 + 1
        q_start_month = (current_q - 1) * 3 + 1
        start = date(today.year, q_start_month, 1)
        end = today
        assumption = f"Interpreted '{text}' as this quarter (Q{current_q}): {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- "last year" ---
    if "last year" in t or "previous year" in t:
        start = date(today.year - 1, 1, 1)
        end = date(today.year - 1, 12, 31)
        assumption = f"Interpreted '{text}' as {today.year - 1}: {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- "this year" ---
    if "this year" in t:
        start = date(today.year, 1, 1)
        end = today
        assumption = f"Interpreted '{text}' as this year: {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- "all" / "all time" ---
    if t in ("all", "all time", "all data", "full period"):
        start = date(2000, 1, 1)  # effectively open start
        end = today
        assumption = f"Interpreted '{text}' as full available range: {_fmt(start)} to {_fmt(end)}"
        return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}

    # --- Fallback: default to last 30 days ---
    start = today - timedelta(days=29)
    assumption = (
        f"Could not parse '{text}'; defaulted to last 30 days: {_fmt(start)} to {_fmt(end)}. "
        "Please re-state the date range for accuracy."
    )
    return {"start_date": _fmt(start), "end_date": _fmt(end), "today": _fmt(today), "assumption": assumption}


# ---------------------------------------------------------------------------
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


@tool
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

@tool
def fetch_inverter_daily_tool(
    start_date: str,
    end_date: str,
    plant_ids: Optional[list[int]] = None,
    block_ids: Optional[list[int]] = None,
    inverter_ids: Optional[list[int]] = None,
) -> dict:
    """
    Fetches daily inverter telemetry joined with weather and hierarchy data.

    This is the primary data-fetching tool. Use it instead of hand-writing SQL
    whenever possible (it uses trusted, pre-validated query templates).

    Args:
        start_date:   ISO date string "YYYY-MM-DD"
        end_date:     ISO date string "YYYY-MM-DD"
        plant_ids:    Optional list of plant_id integers to filter
        block_ids:    Optional list of block_id integers to filter
        inverter_ids: Optional list of inverter_id integers to filter

    Returns a dict with:
        "data_ref": str   — in-memory key to retrieve the DataFrame from DataStore
        "shape":    [rows, cols]
        "columns":  [...]
        "sample":   first 3 rows as list of dicts (for LLM inspection)
        "date_range": {"min": ..., "max": ...}
    """
    from solar_agent.graph.executor import DataStore  # avoid circular at module load

    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    df = q.fetch_inverter_daily(
        start_date=start,
        end_date=end,
        plant_ids=plant_ids,
        block_ids=block_ids,
        inverter_ids=inverter_ids,
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


@tool
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
    from solar_agent.graph.executor import DataStore

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
