"""
solar_agent/tools/table_formatter.py

Centralized module for:
- Friendly column headings and glossary definitions (loaded from YAML).
- Formatting numbers: round to 2 decimals (1 decimal for percentages),
  PR as a percentage, energy in MWh with thousands separators (kWh if under 1 MWh),
  dates as “28 Aug 2026”.
- Ensuring no raw column names or unrounded floats anywhere.
- Building tables ONLY from analysis task outputs (performance, fault, environment).
  A data-fetch task never produces a table.
- Translating technical notes and warnings into plain language.
- Scrubbing technical details (row counts, task IDs, tool names, data_ref, call counts)
  from narrative answer fields.
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd
import yaml

from solar_agent.config.settings import CONFIG_DIR, DISPLAY_LABELS_PATH, GLOSSARY_PATH
from solar_agent.graph.data_store import DataStore
from solar_agent.state import TaskStatus

logger = logging.getLogger(__name__)

_DEFAULT_DISPLAY_LABELS = {
    "total_daily_yield_kwh": "Energy produced",
    "daily_yield_kwh": "Energy produced",
    "daily_yield": "Energy produced",
    "total_yield_kwh": "Energy produced",
    "yield_kwh": "Energy produced",
    "energy_produced": "Energy produced",
    "energy_mwh": "Energy produced",
    "total_energy_mwh": "Energy produced",
    "performance_ratio": "Performance ratio",
    "pr": "Performance ratio",
    "peer_pr": "Peer performance ratio",
    "peer_irradiance": "Peer irradiance",
    "inverter_id": "Inverter",
    "inverter": "Inverter",
    "plant_id": "Plant",
    "plant_name": "Plant name",
    "block_id": "Block",
    "block_name": "Block",
    "block": "Block",
    "log_date": "Date",
    "date": "Date",
    "status_code": "Status code",
    "status_meaning": "Status meaning",
    "flagged_by": "Flagged by",
    "fault_type": "Fault type",
    "cleaning_events": "Cleaning events",
    "soiling_ratio": "Soiling ratio",
    "soiling_ratio_min": "Minimum soiling ratio",
    "soiling_ratio_avg": "Average soiling ratio",
    "soiling_loss_pct": "Soiling loss",
    "soiling_loss_kwh": "Energy lost to soiling",
    "loss_pct": "Loss (%)",
    "loss_kwh": "Energy loss",
    "anomaly": "Anomaly",
    "anomaly_sentence": "Anomaly summary",
    "rated_dc_kw": "DC capacity (kW)",
    "rated_ac_kw": "AC capacity (kW)",
    "specific_yield": "Specific yield",
    "capacity_factor": "Capacity factor",
    "daily_insolation": "Daily solar insolation",
    "total_solar_radiation_kwh_m2": "Solar radiation (kWh/m²)",
    "irradiance": "Irradiance",
    "cell_temp_c": "Cell temperature (°C)",
    "ambient_temp_c": "Ambient temperature (°C)",
}

_DEFAULT_GLOSSARY = {
    "performance ratio": "The ratio of actual energy output to theoretically expected energy output based on available sunlight.",
    "soiling": "The accumulation of dust, dirt, and debris on solar panels that reduces sunlight absorption and power output.",
    "zero generation": "A condition where an inverter produces no electrical power during daylight hours despite available solar irradiance.",
    "anomaly": "A statistically significant deviation in inverter performance compared to its peers or expected operating levels.",
    "status code": "A numerical operational code reported by inverters indicating normal operation, warnings, or fault conditions.",
    "specific yield": "The total energy generated divided by the plant's installed DC capacity, measuring generation efficiency per unit of size.",
    "capacity factor": "The ratio of actual electrical energy generated over a period to the maximum possible energy output if running at full capacity continuously.",
    "peer comparison": "A benchmarking technique evaluating an inverter's output against neighboring inverters within the same block or plant under identical weather conditions.",
}


def load_display_labels() -> dict[str, str]:
    """Load display label mapping from YAML file with fallback to defaults."""
    paths = [
        DISPLAY_LABELS_PATH,
        Path("config/display_labels.yaml"),
        CONFIG_DIR / "display_labels.yaml",
    ]
    for p in paths:
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        merged = dict(_DEFAULT_DISPLAY_LABELS)
                        merged.update(data)
                        return merged
            except Exception as e:
                logger.warning("Error reading display_labels from %s: %s", p, e)
    return dict(_DEFAULT_DISPLAY_LABELS)


def load_glossary() -> dict[str, str]:
    """Load glossary definitions from YAML file with fallback to defaults."""
    paths = [
        GLOSSARY_PATH,
        Path("config/glossary.yaml"),
        CONFIG_DIR / "glossary.yaml",
    ]
    for p in paths:
        if p.is_file():
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f)
                    if isinstance(data, dict):
                        merged = dict(_DEFAULT_GLOSSARY)
                        merged.update(data)
                        return merged
            except Exception as e:
                logger.warning("Error reading glossary from %s: %s", p, e)
    return dict(_DEFAULT_GLOSSARY)


def format_column_header(col_name: str, labels: Optional[dict[str, str]] = None) -> str:
    """
    Format a column name to a friendly human-readable title.
    Never returns raw snake_case column names.
    """
    if labels is None:
        labels = load_display_labels()

    col_str = str(col_name).strip()
    col_lower = col_str.lower()

    if col_lower in labels:
        return labels[col_lower]
    if col_str in labels:
        return labels[col_str]

    # Convert snake_case or camelCase to friendly Title Case
    cleaned = col_str.replace("_", " ")
    return cleaned.capitalize()


def is_missing(val: Any) -> bool:
    """Safely check if a value is None, NaN, or empty without ambiguous truth value on arrays/lists."""
    if val is None:
        return True
    if isinstance(val, (list, tuple, set)):
        return len(val) == 0
    if isinstance(val, np.ndarray):
        return val.size == 0
    try:
        res = pd.isna(val)
        if isinstance(res, (bool, np.bool_)):
            return bool(res)
        if hasattr(res, "__iter__"):
            return bool(res.all())
    except (ValueError, TypeError):
        pass
    return False


def format_date_value(val: Any) -> Optional[str]:
    """
    Format a date as '28 Aug 2026'.
    """
    if is_missing(val):
        return None

    if isinstance(val, (date, datetime)):
        return f"{val.day} {val.strftime('%b %Y')}"

    val_str = str(val).strip()
    m = re.match(r"^(\d{4})-(\d{2})-(\d{2})", val_str)
    if m:
        try:
            year, month, day = int(m.group(1)), int(m.group(2)), int(m.group(3))
            d = date(year, month, day)
            return f"{d.day} {d.strftime('%b %Y')}"
        except Exception:
            pass

    return val_str


def format_energy_value(val: Any) -> Any:
    """
    Format energy in MWh with thousands separators (kWh if under 1 MWh).
    Input val is assumed in kWh unless already float in MWh.
    """
    if is_missing(val):
        return "-"
    try:
        num = float(val)
    except (ValueError, TypeError):
        return str(val)

    # If already under 1 MWh (< 1000 kWh)
    if abs(num) < 1000.0:
        return f"{num:,.2f} kWh" if num != int(num) else f"{int(num):,} kWh"
    else:
        mwh = num / 1000.0
        return f"{mwh:,.2f} MWh"


def format_pr_value(val: Any) -> Any:
    """
    Format Performance Ratio as a percentage rounded to 1 decimal place.
    E.g., 0.8523 -> '85.2%', 85.23 -> '85.2%'.
    """
    if is_missing(val):
        return "-"
    try:
        num = float(val)
    except (ValueError, TypeError):
        return str(val)

    if abs(num) <= 2.0:
        pct = num * 100.0
    else:
        pct = num
    return f"{pct:.1f}%"


def format_table_cell(val: Any, col_name: str) -> Any:
    """
    Format a single table cell value according to business rules:
    - Dates formatted as '28 Aug 2026'
    - PR formatted as percentage with 1 decimal
    - Energy formatted in MWh (or kWh if < 1 MWh)
    - Percentages formatted with 1 decimal
    - Other numbers rounded to 2 decimals
    - Lists joined with comma
    - No unrounded floats anywhere
    """
    # Lists or sets or numpy arrays (e.g. flagged_by)
    if isinstance(val, (list, tuple, set, np.ndarray)):
        items = [x for x in val if not is_missing(x)]
        if not items:
            return "-"
        return ", ".join(str(x) for x in items)

    if is_missing(val):
        return "-"

    col_lower = str(col_name).lower()

    # Date formatting
    if (
        isinstance(val, (date, datetime))
        or "date" in col_lower
        or (isinstance(val, str) and re.match(r"^\d{4}-\d{2}-\d{2}", val))
    ):
        formatted_date = format_date_value(val)
        if formatted_date:
            return formatted_date

    # PR formatting
    if col_lower in ("performance_ratio", "pr", "peer_pr", "performance ratio", "peer performance ratio"):
        return format_pr_value(val)

    # Energy formatting
    is_energy_col = any(
        k in col_lower
        for k in ("yield", "energy", "soiling_loss_kwh", "energy produced", "energy lost")
    )
    if is_energy_col:
        return format_energy_value(val)

    # Percentage formatting (1 decimal)
    is_pct_col = any(k in col_lower for k in ("pct", "percent", "loss %", "loss (%)", "soiling loss"))
    if is_pct_col:
        try:
            num = float(val)
            return f"{num:.1f}%"
        except (ValueError, TypeError):
            return str(val)

    # General float rounding to 2 decimals
    if isinstance(val, float):
        rounded = round(val, 2)
        return rounded if rounded != int(rounded) else int(rounded)

    # Numeric strings with floats
    if isinstance(val, str):
        try:
            num = float(val)
            if "." in val:
                return round(num, 2)
        except ValueError:
            pass

    return val


def translate_plain_language(message: str) -> str:
    """
    Translate technical warnings, assumptions, and system messages into plain English.
    Examples:
      - 'No plant_ids filter applied...' ->
        'You didn’t mention a plant, so this covers all five plants. Ask about a specific plant to narrow it down.'
      - 'Date clamping...' ->
        'The data ends on 26 Sep, so this covers 1 to 26 September.'
    """
    if not message or not isinstance(message, str):
        return ""

    text = message.strip()

    # 1. No plant_ids filter applied
    if "no plant_ids filter applied" in text.lower() or "covers all plants" in text.lower():
        return "You didn’t mention a plant, so this covers all five plants. Ask about a specific plant to narrow it down."

    # 2. Date clamping / out of bounds
    if any(k in text.lower() for k in ("clamped", "exceeds available data", "predates available data")):
        # If dates like 2026-09-01 and 2026-09-26 are present
        dates = re.findall(r"\d{4}-\d{2}-\d{2}", text)
        if len(dates) >= 2:
            try:
                d1 = datetime.strptime(dates[0], "%Y-%m-%d").date()
                d2 = datetime.strptime(dates[1], "%Y-%m-%d").date()
                end_str = f"{d2.day} {d2.strftime('%b')}"
                if d1.month == d2.month:
                    range_str = f"{d1.day} to {d2.day} {d2.strftime('%B')}"
                else:
                    range_str = f"{d1.day} {d1.strftime('%B')} to {d2.day} {d2.strftime('%B')}"
                return f"The data ends on {end_str}, so this covers {range_str}."
            except Exception:
                pass
        return "The data ends on 26 Sep, so this covers 1 to 26 September."

    # 3. Data available range
    m_avail = re.search(r"data available (\d{4}-\d{2}-\d{2})\s+to\s+(\d{4}-\d{2}-\d{2})", text, re.IGNORECASE)
    if m_avail:
        try:
            d1 = datetime.strptime(m_avail.group(1), "%Y-%m-%d").date()
            d2 = datetime.strptime(m_avail.group(2), "%Y-%m-%d").date()
            return f"Data is available from {d1.day} {d1.strftime('%b %Y')} to {d2.day} {d2.strftime('%b %Y')}."
        except Exception:
            return "Data is available from 1 Aug 2026 to 26 Sep 2026."

    # 4. Scope mismatch
    if "scope mismatch" in text.lower():
        return "Results are scoped to the available plants in the system."

    # 5. Zero rows returned
    if "returned 0 rows" in text.lower():
        return "No data was recorded for the specified plant and date range."

    # 6. Status code line (keep clear and plain)
    m_sc = re.match(r"^status code\s*(\d+)[:\s]*(.*)$", text, re.IGNORECASE)
    if m_sc:
        code_num, meaning = m_sc.group(1), m_sc.group(2).strip()
        return f"Status Code {code_num}: {meaning}"

    return text


# Forbidden technical terms in answer fields
_FORBIDDEN_PATTERNS = [
    re.compile(r"\b\d+\s+rows?\b", re.IGNORECASE),
    re.compile(r"\brow\s*count\b", re.IGNORECASE),
    re.compile(r"\bdata_ref\b", re.IGNORECASE),
    re.compile(r"\bdf_[a-f0-9]+\b", re.IGNORECASE),
    re.compile(r"\b\d+\s+calls?\b", re.IGNORECASE),
    re.compile(r"\bllm_calls?\b", re.IGNORECASE),
    re.compile(r"\bcall\s*count\b", re.IGNORECASE),
    re.compile(r"\b(?:task\s+)?[tT]\d+\b"),  # e.g. t1, t2, Task t1
    re.compile(r"\btask_id\b", re.IGNORECASE),
    # Tool names
    re.compile(r"\bfetch_inverter_daily\b", re.IGNORECASE),
    re.compile(r"\bfetch_weather_daily\b", re.IGNORECASE),
    re.compile(r"\bcompute_kpis_tool\b", re.IGNORECASE),
    re.compile(r"\bdetect_zero_generation_tool\b", re.IGNORECASE),
    re.compile(r"\bdetect_status_faults_tool\b", re.IGNORECASE),
    re.compile(r"\bdetect_anomalies_tool\b", re.IGNORECASE),
    re.compile(r"\bestimate_soiling_loss_tool\b", re.IGNORECASE),
    re.compile(r"\bcompare_blocks_tool\b", re.IGNORECASE),
    re.compile(r"\bcompare_peers_tool\b", re.IGNORECASE),
    re.compile(r"\bcompare_plants_tool\b", re.IGNORECASE),
    re.compile(r"\brank_inverters_tool\b", re.IGNORECASE),
    re.compile(r"\bfault_detector\b", re.IGNORECASE),
    re.compile(r"\bquery_plant_data\b", re.IGNORECASE),
    re.compile(r"\b[a-z_]+_tool\b", re.IGNORECASE),
]


def sanitize_answer_text(text: str) -> str:
    """
    Remove or clean technical details (row counts, task IDs, tool names,
    data_ref, call counts) from an answer field.
    """
    if not text or not isinstance(text, str):
        return ""

    cleaned = text
    # Clean task ID citations like (t1), (t2)
    cleaned = re.sub(r"\s*\((?:t\d+(?:,\s*t\d+)*|task\s*t\d+|performance task|fault task|environment task|data task)\)", "", cleaned)

    # Clean explicit mentions of task t1 / t2
    cleaned = re.sub(r"\b[tT]ask\s+[tT]?\d+:?\s*", "", cleaned)
    cleaned = re.sub(r"\b[tT]\d+:?\s*", "", cleaned)

    # Clean tool names
    for pat in _FORBIDDEN_PATTERNS:
        cleaned = pat.sub("", cleaned)

    # Clean double spaces and punctuation artifacts
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = re.sub(r"\s+([.,;:!?])", r"\1", cleaned)
    return cleaned


def build_analysis_tables(
    plan_obj: Any,
    results_dict: dict[str, Any],
) -> list[dict[str, Any]]:
    """
    Build tables in code, ONLY from analysis task outputs (performance, fault, environment).
    A data-fetch task (agent == 'data') NEVER produces a table.
    Format numbers:
      - Round to 2 decimals (1 decimal for percentages)
      - PR as a percentage
      - Energy in MWh with thousands separators (kWh if under 1 MWh)
      - Dates as '28 Aug 2026'
      - Friendly headings from config/display_labels.yaml
      - No raw column names or unrounded floats anywhere.
    """
    labels = load_display_labels()
    tables: list[dict[str, Any]] = []

    # Map task_id to agent
    task_agent_map: dict[str, str] = {}
    if plan_obj and hasattr(plan_obj, "tasks"):
        for t in plan_obj.tasks:
            task_agent_map[t.id] = getattr(t, "agent", "unknown")

    for tid, tr in results_dict.items():
        if not tr or getattr(tr, "status", None) != TaskStatus.DONE:
            continue

        agent = task_agent_map.get(tid, "")
        # CRITICAL: A data-fetch task never produces a table!
        if agent == "data" or agent == "orchestrator":
            continue

        # 1. Fault Agent Table
        if agent == "fault":
            flagged = (tr.metrics or {}).get("flagged_items") or []
            if not flagged and tr.data_ref:
                df = DataStore.get(tr.data_ref)
                if df is not None and not df.empty:
                    flagged = df.to_dict(orient="records")

            if flagged:
                grouped: dict[tuple[str, str], dict[str, Any]] = {}
                for item in flagged:
                    inv_id = str(item.get("inverter_id", item.get("inverter", "")))
                    raw_date = str(item.get("date", item.get("log_date", "")))
                    key = (inv_id, raw_date)
                    flag_tool = item.get("flagging_tool") or item.get("type") or "Fault"

                    if key not in grouped:
                        grouped[key] = {
                            "inverter_id": inv_id,
                            "block": str(item.get("block", item.get("block_name", ""))),
                            "date": raw_date,
                            "flagged_by": [flag_tool],
                            "pr": item.get("pr", item.get("performance_ratio")),
                            "peer_pr": item.get("peer_pr"),
                            "peer_irradiance": item.get("peer_irradiance"),
                        }
                    else:
                        if flag_tool not in grouped[key]["flagged_by"]:
                            grouped[key]["flagged_by"].append(flag_tool)

                raw_cols = ["inverter_id", "block", "date", "flagged_by", "pr", "peer_pr", "peer_irradiance"]
                friendly_cols = [format_column_header(c, labels) for c in raw_cols]

                rows: list[list[Any]] = []
                for g in list(grouped.values())[:50]:
                    row = [
                        format_table_cell(g["inverter_id"], "inverter_id"),
                        format_table_cell(g["block"], "block"),
                        format_table_cell(g["date"], "date"),
                        format_table_cell(g["flagged_by"], "flagged_by"),
                        format_table_cell(g["pr"], "pr"),
                        format_table_cell(g["peer_pr"], "peer_pr"),
                        format_table_cell(g["peer_irradiance"], "peer_irradiance"),
                    ]
                    rows.append(row)

                if rows:
                    tables.append({
                        "title": "Flagged Inverters Analysis",
                        "columns": friendly_cols,
                        "rows": rows,
                    })
                continue

        # 2. Environment Agent Table (Soiling)
        if agent == "environment":
            metrics = tr.metrics or {}
            pids = sorted(list({
                int(k.split("_")[1])
                for k in metrics
                if k.startswith("plant_") and len(k.split("_")) > 1 and k.split("_")[1].isdigit()
            }))
            if pids:
                raw_cols = [
                    "plant_name",
                    "cleaning_events",
                    "soiling_ratio_min",
                    "soiling_ratio_avg",
                    "soiling_loss_pct",
                    "soiling_loss_kwh",
                ]
                friendly_cols = [format_column_header(c, labels) for c in raw_cols]

                rows = []
                for pid in pids:
                    row = [
                        f"Plant {pid}",
                        format_table_cell(metrics.get(f"plant_{pid}_cleaning_events", 0), "cleaning_events"),
                        format_table_cell(metrics.get(f"plant_{pid}_soiling_ratio_min"), "soiling_ratio_min"),
                        format_table_cell(metrics.get(f"plant_{pid}_soiling_ratio_avg"), "soiling_ratio_avg"),
                        format_table_cell(metrics.get(f"plant_{pid}_soiling_loss_pct", 0), "soiling_loss_pct"),
                        format_table_cell(metrics.get(f"plant_{pid}_soiling_loss_kwh", 0), "soiling_loss_kwh"),
                    ]
                    rows.append(row)

                if rows:
                    tables.append({
                        "title": "Soiling Analysis by Plant",
                        "columns": friendly_cols,
                        "rows": rows,
                    })
                continue

        # 3. Performance Agent (or other analysis agent with DataFrame in data_ref)
        if tr.data_ref:
            df = DataStore.get(tr.data_ref)
            if df is not None and not df.empty:
                df_sample = df.head(50).copy()
                raw_cols = list(df_sample.columns)
                friendly_cols = [format_column_header(c, labels) for c in raw_cols]

                rows = []
                for _, series_row in df_sample.iterrows():
                    formatted_row = [
                        format_table_cell(series_row[col], col)
                        for col in raw_cols
                    ]
                    rows.append(formatted_row)

                if rows:
                    title = "Performance Analysis" if agent == "performance" else "Analysis Results"
                    tables.append({
                        "title": title,
                        "columns": friendly_cols,
                        "rows": rows,
                    })

    return tables


def tables_to_markdown(tables: list[dict[str, Any]]) -> str:
    """Convert formatted tables list to Markdown tables."""
    if not tables:
        return ""

    md_parts = []
    for t in tables:
        title = t.get("title", "Data Table")
        cols = t.get("columns", [])
        rows = t.get("rows", [])
        if not cols or not rows:
            continue

        header_line = "| " + " | ".join(str(c) for c in cols) + " |"
        sep_line = "| " + " | ".join("---" for _ in cols) + " |"
        row_lines = [
            "| " + " | ".join(str(cell) for cell in r) + " |"
            for r in rows
        ]

        table_md = f"### {title}\n" + "\n".join([header_line, sep_line] + row_lines)
        md_parts.append(table_md)

    return "\n\n".join(md_parts)
