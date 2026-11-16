"""
test_date_resolver.py — Table-driven unit tests for deterministic resolve_date_range,
tie rule, none-found period naming, and orchestrator date handling.
"""

import pytest
from datetime import date, timedelta
from solar_agent.tools.data_tools import resolve_date_range
from solar_agent.tools.performance_tools import rank_entities
from solar_agent.graph.data_store import DataStore
import pandas as pd


# ─────────────────────────────────────────────────────────────────────────────
# 1. Table-driven resolver tests (25+ phrases)
# ─────────────────────────────────────────────────────────────────────────────

RESOLVER_TEST_CASES = [
    # 1. "1 to 15 September"
    ("1 to 15 September", "2026-09-01", "2026-09-15", {"swapped": False, "has_period_phrase": True}),
    # 2. "16 September" (single day, missing year inferred)
    ("16 September", "2026-09-16", "2026-09-16", {"swapped": False, "has_period_phrase": True}),
    # 3. "Sept 2026"
    ("Sept 2026", "2026-09-01", "2026-09-30", {"has_period_phrase": True}),
    # 4. "sept" (abbreviated month, year inferred)
    ("sept", "2026-09-01", "2026-09-30", {"has_period_phrase": True}),
    # 5. "20 Sept to 10 Sept" (reversed dates swapped, with note)
    ("20 Sept to 10 Sept", "2026-09-10", "2026-09-20", {"swapped": True, "has_period_phrase": True}),
    # 6. "whole dataset"
    ("whole dataset", "2026-07-01", "2026-10-21", {"has_period_phrase": True}),
    # 7. "overall"
    ("overall", "2026-07-01", "2026-10-21", {"has_period_phrase": True}),
    # 8. "last month" (previous full calendar month before MAX(log_date) 2026-10-21 -> Sep 2026)
    ("last month", "2026-09-01", "2026-09-30", {"has_period_phrase": True}),
    # 9. "this month" (Oct 1 to Oct 21)
    ("this month", "2026-10-01", "2026-10-21", {"has_period_phrase": True}),
    # 10. "last 7 days" (Oct 15 to Oct 21)
    ("last 7 days", "2026-10-15", "2026-10-21", {"has_period_phrase": True}),
    # 11. "in August" (bare month, year inferred)
    ("in August", "2026-08-01", "2026-08-31", {"has_period_phrase": True}),
    # 12. "December 2026" (outside data, clear message)
    ("December 2026", "2026-12-01", "2026-12-31", {"out_of_range": True, "has_period_phrase": True}),
    # 13. "yesterday" (Oct 20)
    ("yesterday", "2026-10-20", "2026-10-20", {"has_period_phrase": True}),
    # 14. "on 16 Sept 2026"
    ("on 16 Sept 2026", "2026-09-16", "2026-09-16", {"has_period_phrase": True}),
    # 15. "September 2026"
    ("September 2026", "2026-09-01", "2026-09-30", {"has_period_phrase": True}),
    # 16. "sep"
    ("sep", "2026-09-01", "2026-09-30", {"has_period_phrase": True}),
    # 17. "all time"
    ("all time", "2026-07-01", "2026-10-21", {"has_period_phrase": True}),
    # 18. "ever"
    ("ever", "2026-07-01", "2026-10-21", {"has_period_phrase": True}),
    # 19. "in total"
    ("in total", "2026-07-01", "2026-10-21", {"has_period_phrase": True}),
    # 20. "so far"
    ("so far", "2026-07-01", "2026-10-21", {"has_period_phrase": True}),
    # 21. "last 2 weeks" (14 days: Oct 8 to Oct 21)
    ("last 2 weeks", "2026-10-08", "2026-10-21", {"has_period_phrase": True}),
    # 22. "last 30 days"
    ("last 30 days", "2026-09-22", "2026-10-21", {"has_period_phrase": True}),
    # 23. "Q3 2026" (Jul 1 to Sep 30)
    ("Q3 2026", "2026-07-01", "2026-09-30", {"has_period_phrase": True}),
    # 24. "this week"
    ("this week", None, "2026-10-21", {"has_period_phrase": True}),
    # 25. "last week"
    ("last week", None, None, {"has_period_phrase": True}),
    # 26. "2026-08-01 to 2026-08-31" (ISO explicit)
    ("2026-08-01 to 2026-08-31", "2026-08-01", "2026-08-31", {"has_period_phrase": True}),
    # 27. "from 15 Aug to 1 Aug" (swap)
    ("from 15 Aug to 1 Aug", "2026-08-01", "2026-08-15", {"swapped": True, "has_period_phrase": True}),
    # 28. Undated query (default last 30 days fallback)
    ("How many plants are there?", "2026-09-22", "2026-10-21", {"has_period_phrase": False}),
]


@pytest.mark.parametrize("query,expected_start,expected_end,flags", RESOLVER_TEST_CASES)
def test_resolver_phrases(query, expected_start, expected_end, flags):
    res = resolve_date_range.invoke({"text": query})

    if expected_start is not None:
        assert res["start_date"] == expected_start, f"Expected start {expected_start}, got {res['start_date']} for '{query}'"
    if expected_end is not None:
        assert res["end_date"] == expected_end, f"Expected end {expected_end}, got {res['end_date']} for '{query}'"

    for flag_name, flag_val in flags.items():
        assert res.get(flag_name) == flag_val, f"Flag {flag_name} expected {flag_val}, got {res.get(flag_name)} for '{query}'"


def test_resolver_undated_default_message():
    """Default last 30 days assumption states exact phrasing: 'No period given, so I used the last 30 days, 22 Sep to 21 Oct'."""
    res = resolve_date_range.invoke({"text": "Show me total capacity"})
    assert res["has_period_phrase"] is False
    assert "No period given, so I used the last 30 days, 22 Sep to 21 Oct" in res["assumption"]


def test_resolver_last_month_explanation():
    """'last month' states the previous full calendar month in its assumption."""
    res = resolve_date_range.invoke({"text": "What was the PR last month?"})
    assert res["start_date"] == "2026-09-01"
    assert res["end_date"] == "2026-09-30"
    assert "September 2026" in res["assumption"]


def test_resolver_reversed_swap_note():
    """Reversed dates are swapped and a note is added."""
    res = resolve_date_range.invoke({"text": "from 20 Sept to 10 Sept"})
    assert res["start_date"] == "2026-09-10"
    assert res["end_date"] == "2026-09-20"
    assert res["swapped"] is True
    assert "reversed dates swapped, with a note" in res["assumption"]


def test_resolver_out_of_range_message():
    """December 2026 produces out_of_range=True with clear data availability message."""
    res = resolve_date_range.invoke({"text": "What was the yield in December 2026?"})
    assert res["out_of_range"] is True
    assert "outside available data range" in res["assumption"]


# ─────────────────────────────────────────────────────────────────────────────
# 2. Tie rule tests
# ─────────────────────────────────────────────────────────────────────────────

def test_tie_rule_detected_when_values_within_0_1_point():
    """If top N values are within 0.1 point of each other, rank_entities marks tie."""
    df = pd.DataFrame([
        {"inverter_id": "INV_01", "performance_ratio": 0.8732},
        {"inverter_id": "INV_02", "performance_ratio": 0.8730},
        {"inverter_id": "INV_03", "performance_ratio": 0.8731},
        {"inverter_id": "INV_04", "performance_ratio": 0.8950},
    ])
    ref = DataStore.store(df)
    res = rank_entities(ref, metric="performance_ratio", ascending=True, top_n=3)

    assert res["metrics"]["is_tie"] is True
    assert "essentially equal" in res["summary"]
    assert "within 0.1 point of each other" in res["metrics"]["tie_note"]


def test_tie_rule_not_triggered_when_values_differ_significantly():
    """If top values differ by > 0.1 point (> 0.001 in ratio), no tie is flagged."""
    df = pd.DataFrame([
        {"inverter_id": "INV_01", "performance_ratio": 0.8100},
        {"inverter_id": "INV_02", "performance_ratio": 0.8400},
        {"inverter_id": "INV_03", "performance_ratio": 0.8900},
    ])
    ref = DataStore.store(df)
    res = rank_entities(ref, metric="performance_ratio", ascending=True, top_n=3)

    assert res["metrics"].get("is_tie", False) is False
    assert "Ranked top 3 by performance_ratio" in res["summary"]


# ─────────────────────────────────────────────────────────────────────────────
# 3. None found naming period tests
# ─────────────────────────────────────────────────────────────────────────────

def test_none_found_names_period_in_fault_tools():
    """detect_zero_generation names the exact searched period when 0 faults are found."""
    from solar_agent.tools.fault_tools import detect_zero_generation
    df = pd.DataFrame([
        {
            "inverter_id": "INV_01",
            "log_date": "2026-07-01",
            "total_daily_yield_kwh": 500.0,
            "total_solar_radiation_kwh_m2": 5.0,
            "status_code": 0,
        },
        {
            "inverter_id": "INV_01",
            "log_date": "2026-10-21",
            "total_daily_yield_kwh": 520.0,
            "total_solar_radiation_kwh_m2": 5.1,
            "status_code": 0,
        },
    ])
    ref = DataStore.store(df)
    res = detect_zero_generation(ref)
    assert res["metrics"]["fault_count"] == 0
    assert "No zero-generation events between 1 Jul and 21 Oct 2026" in res["summary"]


def test_followup_inherits_entity_overrides_period():
    """Follow-up query 'and September?' inherits previous plant_ids but uses September date range."""
    from solar_agent.graph.orchestrator import _normalize_scope_and_tasks

    prev_scope = {
        "plant_ids": [1],
        "block_ids": [],
        "inverter_ids": [],
        "start_date": "2026-08-01",
        "end_date": "2026-08-31",
        "all_plants": False,
    }

    payload = {
        "intent": "performance_ranking",
        "scope": {},
        "tasks": [
            {"id": "t1", "agent": "data", "inputs": {}},
            {"id": "t2", "agent": "performance", "inputs": {"data_ref": "$t1.data_ref"}},
        ],
    }

    updated = _normalize_scope_and_tasks(payload, "and September?", prev_scope=prev_scope)
    assert updated["scope"]["plant_ids"] == [1]
    assert updated["scope"]["start_date"] == "2026-09-01"
    assert updated["scope"]["end_date"] == "2026-09-30"

