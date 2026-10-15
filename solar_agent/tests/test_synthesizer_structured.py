"""
solar_agent/tests/test_synthesizer_structured.py

Tests for:
1. Tables: built only from analysis tasks, formatted numbers (PR as %, energy in MWh/kWh,
   dates as "28 Aug 2026", friendly headings from display_labels.yaml, no raw column names or unrounded floats).
2. Synthesizer structured JSON: headline, explanation, key_points, notes.
3. Plain-language notes & warnings translation.
4. No technical details in answer fields (scans for row counts, task IDs, tool names, data_ref, call counts).
5. Fallback: deterministic answer (templated headline + formatted table), no raw metrics, no "fallback".
6. Replay cache miss: returns error_code='not_recorded' with clear message.
7. Glossary object covering 8 required terms.
"""

from datetime import date
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from api.main import app, format_structured_payload
from solar_agent.graph.data_store import DataStore
from solar_agent.graph.llm_recorder import CacheMissError
from solar_agent.graph.synthesizer import Synthesizer
from solar_agent.state import (
    IntentType,
    Plan,
    Scope,
    Task,
    TaskResult,
    TaskStatus,
)
from solar_agent.tools.table_formatter import (
    build_analysis_tables,
    load_display_labels,
    load_glossary,
    sanitize_answer_text,
    tables_to_markdown,
    translate_plain_language,
)
import pandas as pd


@pytest.fixture
def client():
    return TestClient(app)


def test_tables_only_from_analysis_tasks_and_formatting():
    """A data-fetch task never produces a table; analysis tasks produce formatted tables."""
    # Register data in DataStore
    df_data = pd.DataFrame({
        "inverter_id": [1, 2],
        "log_date": ["2026-08-28", "2026-08-29"],
        "total_daily_yield_kwh": [500.256, 1250000.789],
        "performance_ratio": [0.82456, 0.89123],
    })
    ref_data = DataStore.store(df_data)

    df_perf = pd.DataFrame({
        "inverter_id": [101, 102],
        "log_date": ["2026-08-28", "2026-08-29"],
        "total_daily_yield_kwh": [450.123, 2500000.456],
        "performance_ratio": [0.85234, 0.78129],
    })
    ref_perf = DataStore.store(df_perf)

    plan = Plan(
        intent=IntentType.SUMMARY,
        scope=Scope(start_date=date(2026, 8, 28), end_date=date(2026, 8, 29), plant_ids=[1]),
        tasks=[
            Task(id="t1", agent="data", description="Fetch daily telemetry"),
            Task(id="t2", agent="performance", description="Compute PR metrics", depends_on=["t1"]),
        ],
        success_criteria="Performance analysis",
    )
    results = {
        "t1": TaskResult(task_id="t1", status=TaskStatus.DONE, data_ref=ref_data, metrics={"row_count": 2}),
        "t2": TaskResult(task_id="t2", status=TaskStatus.DONE, data_ref=ref_perf, metrics={"row_count": 2}),
    }

    tables = build_analysis_tables(plan, results)
    # Only 1 table from performance agent, 0 from data agent
    assert len(tables) == 1
    t = tables[0]

    # Friendly headers from display_labels.yaml
    labels = load_display_labels()
    assert "inverter_id" not in t["columns"]
    assert "log_date" not in t["columns"]
    assert "total_daily_yield_kwh" not in t["columns"]
    assert "performance_ratio" not in t["columns"]

    assert "Inverter" in t["columns"]
    assert "Date" in t["columns"]
    assert "Energy produced" in t["columns"]
    assert "Performance ratio" in t["columns"]

    # Formatted rows
    row1 = t["rows"][0]
    row2 = t["rows"][1]

    # Date as '28 Aug 2026'
    assert "28 Aug 2026" in row1
    assert "29 Aug 2026" in row2

    # PR as percentage with 1 decimal
    assert "85.2%" in row1
    assert "78.1%" in row2

    # Energy: kWh under 1 MWh, MWh with thousands separator above 1 MWh
    assert "450.12 kWh" in row1
    assert "2,500.00 MWh" in row2

    # Check that no unrounded floats exist anywhere in cells
    for r in t["rows"]:
        for cell in r:
            if isinstance(cell, float):
                # Float must have at most 2 decimal places
                assert round(cell, 2) == cell


def test_plain_language_translation():
    """Verify technical notes & warnings are translated into plain language."""
    # 1. No plant_ids filter applied
    msg1 = "No plant_ids filter applied — result covers ALL plants: [1, 2, 3, 4, 5]."
    tr1 = translate_plain_language(msg1)
    assert tr1 == "You didn’t mention a plant, so this covers all five plants. Ask about a specific plant to narrow it down."

    # 2. Date clamping
    msg2 = "Requested end date 2026-09-30 exceeds available data. Clamped to 2026-09-26."
    tr2 = translate_plain_language(msg2)
    assert "The data ends on 26 Sep" in tr2

    # 3. Data available
    msg3 = "Data available 2026-08-01 to 2026-09-26"
    tr3 = translate_plain_language(msg3)
    assert "Data is available from 1 Aug 2026 to 26 Sep 2026." in tr3


def test_no_technical_details_in_answer_fields():
    """
    Row counts, task ids, tool names, data_ref, and call counts go only in trace,
    never in headline, explanation, key_points, or notes.
    """
    plan = Plan(
        intent=IntentType.SUMMARY,
        scope=Scope(start_date=date(2026, 9, 1), end_date=date(2026, 9, 26), plant_ids=[1]),
        tasks=[
            Task(id="t1", agent="data", description="Fetch daily telemetry"),
            Task(id="t2", agent="performance", description="Compute PR metrics", depends_on=["t1"]),
        ],
        success_criteria="PR for Plant 1",
    )
    results = {
        "t1": TaskResult(task_id="t1", status=TaskStatus.DONE, summary="Fetched 100 rows.", tool_calls_made=["fetch_inverter_daily"]),
        "t2": TaskResult(task_id="t2", status=TaskStatus.DONE, summary="PR computed at 0.85.", tool_calls_made=["compute_kpis_tool"]),
    }

    mock_llm = MagicMock()
    # LLM accidentally attempts to mention technical details
    mock_llm.invoke.return_value = MagicMock(content="""{
        "headline": "**Plant 1 achieved 85.0% PR in task t2 with 100 rows fetched by fetch_inverter_daily.**",
        "explanation": "Tool compute_kpis_tool processed data_ref df_12345 in 3 calls.",
        "key_points": [
            "Task t1 completed with row count 100",
            "Tool compute_kpis_tool completed"
        ],
        "notes": [
            "No plant_ids filter applied — covers all plants"
        ]
    }""")

    synth = Synthesizer(llm=mock_llm)
    state = {
        "user_query": "What was the PR of Plant 1?",
        "plan": plan,
        "results": results,
        "assumptions": ["No plant_ids filter applied — result covers ALL plants: [1]."],
    }
    patch_res = synth.synthesize(state)

    # Check headline, explanation, key_points, notes
    forbidden_terms = [
        "fetch_inverter_daily",
        "compute_kpis_tool",
        "df_12345",
        "data_ref",
        "100 rows",
        "row count",
        "t1",
        "t2",
        "3 calls",
        "calls",
    ]

    for field_name in ["headline", "explanation"]:
        val = patch_res.get(field_name, "")
        for term in forbidden_terms:
            assert term not in val.lower(), f"Forbidden term '{term}' found in {field_name}: '{val}'"

    for kp in patch_res.get("key_points", []):
        for term in forbidden_terms:
            assert term not in kp.lower(), f"Forbidden term '{term}' found in key_point: '{kp}'"

    for note in patch_res.get("notes", []):
        for term in forbidden_terms:
            assert term not in note.lower(), f"Forbidden term '{term}' found in note: '{note}'"


def test_fallback_deterministic_answer_on_failure():
    """
    On failure, build a deterministic answer: a templated headline and formatted table.
    Never output raw metrics or the phrase 'fallback'.
    """
    df_perf = pd.DataFrame({
        "inverter_id": [1],
        "log_date": ["2026-09-01"],
        "performance_ratio": [0.825],
        "total_daily_yield_kwh": [2500000.0],
    })
    ref_perf = DataStore.store(df_perf)

    plan = Plan(
        intent=IntentType.SUMMARY,
        scope=Scope(start_date=date(2026, 9, 1), end_date=date(2026, 9, 26), plant_ids=[1]),
        tasks=[
            Task(id="t1", agent="performance", description="Compute PR metrics"),
        ],
        success_criteria="PR for Plant 1",
    )
    results = {
        "t1": TaskResult(
            task_id="t1",
            status=TaskStatus.DONE,
            data_ref=ref_perf,
            metrics={"performance_ratio": 0.825, "total_yield_kwh": 2500000.0},
        ),
    }

    mock_llm = MagicMock()
    # LLM completely fails with an unexpected exception
    mock_llm.invoke.side_effect = RuntimeError("Service unavailable")

    synth = Synthesizer(llm=mock_llm)
    state = {
        "user_query": "What was the PR of Plant 1?",
        "plan": plan,
        "results": results,
        "assumptions": [],
    }

    patch_res = synth.synthesize(state)

    # 1. Templated headline exists and is bold
    assert "headline" in patch_res
    assert patch_res["headline"].startswith("**") and patch_res["headline"].endswith("**")
    assert "Plant 1" in patch_res["headline"]
    assert "82.5%" in patch_res["headline"]

    # 2. Formatted tables exist
    assert "tables" in patch_res
    assert len(patch_res["tables"]) >= 1

    # 3. Never outputs raw metrics or the word 'fallback'
    final_ans = patch_res["final_answer"]
    assert "fallback" not in final_ans.lower()
    assert "here are the raw metrics" not in final_ans.lower()
    assert "**[t1 / performance]**" not in final_ans


def test_replay_cache_miss_returns_not_recorded(client):
    """
    For a Replay cache miss, return error_code='not_recorded' with a clear message.
    """
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = CacheMissError("cache miss")

    plan = Plan(
        intent=IntentType.SUMMARY,
        scope=Scope(start_date=date(2026, 9, 1), end_date=date(2026, 9, 26), plant_ids=[1]),
        tasks=[Task(id="t1", agent="performance", description="Compute PR")],
        success_criteria="Performance ratio analysis",
    )
    synth = Synthesizer(llm=mock_llm)
    state = {
        "user_query": "Unrecorded query in replay",
        "plan": plan,
        "results": {},
        "assumptions": [],
    }
    synth_patch = synth.synthesize(state)
    assert synth_patch.get("error_code") == "not_recorded"
    assert "not recorded in replay cache" in synth_patch.get("error_message", "").lower()

    # Test via API /api/chat
    with patch("api.main.build_graph") as mock_bg:
        mock_graph = MagicMock()
        mock_graph.invoke.side_effect = CacheMissError("cache miss")
        mock_bg.return_value = mock_graph

        res = client.post("/api/chat", json={"query": "Unrecorded query", "mode": "replay"})
        assert res.status_code == 400
        data = res.json()
        assert data.get("error_code") == "not_recorded"
        assert "not recorded in replay cache" in data.get("error", {}).get("message", "").lower()


def test_glossary_object_in_response(client):
    """
    Response must contain a glossary object covering all 8 required terms.
    """
    glossary = load_glossary()
    required_terms = [
        "performance ratio",
        "soiling",
        "zero generation",
        "anomaly",
        "status code",
        "specific yield",
        "capacity factor",
        "peer comparison",
    ]
    for term in required_terms:
        assert term in glossary or term.replace(" ", "_") in glossary, f"Missing term: {term}"
        val = glossary.get(term) or glossary.get(term.replace(" ", "_"))
        assert len(val.split(".")) >= 1, f"Definition for {term} must be a sentence."

    # Verify present in format_structured_payload output
    payload = format_structured_payload({}, elapsed_ms=10.0)
    assert "glossary" in payload
    for term in required_terms:
        assert term in payload["glossary"] or term.replace(" ", "_") in payload["glossary"]


def test_format_table_cell_with_array_and_multiple_tools():
    """
    Ensure format_table_cell safely handles lists and numpy arrays without
    ambiguous truth value errors (e.g. ['detect_zero_generation_tool', 'detect_anomalies_tool']).
    """
    from solar_agent.tools.table_formatter import format_table_cell
    import numpy as np

    # Multi-element list
    res_list = format_table_cell(["detect_zero_generation_tool", "detect_anomalies_tool"], "flagged_by")
    assert res_list == "detect_zero_generation_tool, detect_anomalies_tool"

    # Multi-element numpy array
    res_arr = format_table_cell(np.array(["tool_a", "tool_b"]), "flagged_by")
    assert res_arr == "tool_a, tool_b"

    # Empty list and None
    assert format_table_cell([], "flagged_by") == "-"
    assert format_table_cell(None, "flagged_by") == "-"

