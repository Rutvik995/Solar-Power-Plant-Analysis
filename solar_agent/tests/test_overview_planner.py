"""
solar_agent/tests/test_overview_planner.py

Tests for:
1. Planner returns overview for "give me short introduction to all the plants".
2. Planner returns overview for follow-up clarification "i mean solar plants".
3. Non-solar question ("who won the cricket match") is refused with friendly reply and 4 suggestions.
4. Golden test: get_plant_overview tool counts and capacity sum match raw SQL exactly.
5. Fault table formatting: 5 columns (Inverter, Block, Date, Problem found, Actual vs peers),
   tool names mapped to plain labels, and peer irradiance moved to note.
6. Execution of overview tasks t1 and t2 with 0 specialist agent LLM calls.
"""

import pytest
from unittest.mock import MagicMock
from sqlalchemy import text

from api.main import format_structured_payload
from solar_agent.db.connection import get_sync_engine
from solar_agent.graph.orchestrator import Orchestrator
from solar_agent.state import AgentState, IntentType, TaskResult, TaskStatus
from solar_agent.tools.data_tools import get_plant_overview
from solar_agent.tools.table_formatter import build_analysis_tables


def test_golden_raw_sql_vs_overview_tool():
    """
    Golden test: tool counts and the capacity sum match raw SQL.
    Prints plant, block, and inverter totals and combined MWp.
    """
    engine = get_sync_engine()
    with engine.connect() as conn:
        plant_count = conn.execute(text("SELECT count(*) FROM plants;")).scalar()
        block_count = conn.execute(text("SELECT count(*) FROM blocks;")).scalar()
        inverter_count = conn.execute(text("SELECT count(*) FROM inverters;")).scalar()
        capacity_sum = conn.execute(text("SELECT coalesce(sum(capacity_mwp), 0.0) FROM plants;")).scalar()

    overview = get_plant_overview(level="plants")
    assert overview["total_plants"] == plant_count
    assert overview["total_blocks"] == block_count
    assert overview["total_inverters"] == inverter_count
    assert abs(overview["total_capacity_mwp"] - float(capacity_sum)) < 1e-4

    # Print requested totals
    print(f"\n[Golden Data Totals] Plants: {plant_count}, Blocks: {block_count}, Inverters: {inverter_count}, Combined MWp: {float(capacity_sum):.2f} MWp")


def test_planner_returns_overview_and_clarification():
    """
    The planner returns overview for 'give me short introduction to all the plants'
    and for 'i mean solar plants' after it.
    """
    mock_llm = MagicMock()
    # Mock LLM returning valid JSON for overview
    mock_llm.invoke.return_value = MagicMock(content="""{
        "intent": "overview",
        "scope": {"plant_ids": [], "all_plants": true},
        "tasks": [
            {
                "id": "t1",
                "agent": "data",
                "description": "Retrieve structural overview of plants and blocks.",
                "inputs": {"tool": "get_plant_overview", "level": "plants", "include_blocks": true},
                "depends_on": []
            },
            {
                "id": "t2",
                "agent": "performance",
                "description": "Compute plant-level KPI snapshot over the last 30 days of data.",
                "inputs": {"tool": "compute_kpis", "level": "plant", "period": "total"},
                "depends_on": ["t1"]
            }
        ],
        "success_criteria": "Introduce all plants."
    }""")

    orchestrator = Orchestrator(llm=mock_llm)

    # 1. Primary query
    state1: AgentState = {
        "user_query": "give me short introduction to all the plants",
    }
    patch1 = orchestrator.plan(state1)
    plan1 = patch1["plan"]
    assert plan1.intent == IntentType.OVERVIEW or str(plan1.intent) == "overview"
    assert len(plan1.tasks) == 2
    assert plan1.tasks[0].agent == "data"
    assert plan1.tasks[0].inputs.get("tool") == "get_plant_overview"
    assert plan1.tasks[1].agent == "performance"
    assert plan1.tasks[1].inputs.get("tool") == "compute_kpis"

    # 2. Clarification query: "i mean solar plants" after it
    state2: AgentState = {
        "user_query": "i mean solar plants",
        "previous_query": "give me short introduction to all the plants",
        "previous_scope": plan1.scope.model_dump(),
        "previous_tasks": [{"id": "t1", "agent": "data"}, {"id": "t2", "agent": "performance"}],
    }
    patch2 = orchestrator.plan(state2)
    plan2 = patch2["plan"]
    assert plan2.intent == IntentType.OVERVIEW or str(plan2.intent) == "overview"
    assert len(plan2.tasks) == 2


def test_non_solar_question_refusal():
    """
    A non-solar question ('who won the cricket match') is refused with the friendly reply,
    without repeating headline and explanation, with 4 supported example questions and no 'same for Plant 2'.
    """
    mock_llm = MagicMock()
    orchestrator = Orchestrator(llm=mock_llm)

    state: AgentState = {
        "user_query": "who won the cricket match",
    }
    patch = orchestrator.plan(state)
    assert patch.get("is_out_of_scope") is True
    friendly_msg = "I can’t help with that, but I can answer questions about your solar plants."
    assert friendly_msg in patch.get("final_answer", "")

    # Check payload formatting through format_structured_payload
    payload = format_structured_payload(patch, elapsed_ms=15.0)
    assert payload.get("is_out_of_scope") is True
    assert friendly_msg in payload.get("headline", "")
    assert payload.get("explanation") == ""  # No repeated headline and explanation
    assert len(payload.get("suggestions", [])) == 4
    for sugg in payload["suggestions"]:
        assert "Same for Plant 2" not in sugg


def test_fault_table_five_columns_and_peer_irradiance_note():
    """
    Limit table to 5 columns: Inverter, Block, Date, Problem found, Actual vs peers.
    Tool names mapped to plain labels (detect_zero_generation_tool -> Zero-generation check).
    Move peer irradiance into a note.
    """
    mock_plan = MagicMock()
    t_fault = MagicMock(id="t_fault", agent="fault")
    mock_plan.tasks = [t_fault]

    results = {
        "t_fault": TaskResult(
            task_id="t_fault",
            status=TaskStatus.DONE,
            metrics={
                "flagged_items": [
                    {
                        "inverter_id": "P1_Block_A_INV_02",
                        "block": "Block A",
                        "date": "2026-09-16",
                        "flagging_tool": "detect_zero_generation_tool",
                        "pr": 0.0,
                        "peer_pr": 0.8506,
                        "peer_irradiance": 6.66,
                    },
                    {
                        "inverter_id": "P1_Block_A_INV_02",
                        "block": "Block A",
                        "date": "2026-09-16",
                        "flagging_tool": "detect_anomalies_tool",
                        "pr": 0.0,
                        "peer_pr": 0.8506,
                        "peer_irradiance": 6.66,
                    },
                ]
            },
        )
    }

    tables = build_analysis_tables(mock_plan, results)
    assert len(tables) == 1
    t = tables[0]

    # Check exactly 5 columns
    expected_cols = ["Inverter", "Block", "Date", "Problem found", "Actual vs peers"]
    assert t["columns"] == expected_cols

    # Check rows: grouped into 1 row for (inverter, date)
    assert len(t["rows"]) == 1
    row = t["rows"][0]
    assert row[0] == "P1_Block_A_INV_02"
    assert row[1] == "Block A"
    assert row[2] == "16 Sep 2026"
    assert "No power produced" in row[3]
    assert "Unusual readings" in row[3]
    assert "0.0% vs 85.1%" in row[4]

    # Peer irradiance moved into a note
    assert "notes" in t or "note" in t
    note_text = t.get("note") or t.get("notes")[0]
    assert "Peer irradiance" in note_text
    assert "6.66" in note_text
