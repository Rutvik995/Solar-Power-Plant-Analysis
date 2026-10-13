"""
solar_agent/tests/test_chat_fixes.py

Unit and integration tests for:
1. Normalization of U+202F, U+2009, U+200A and U+00A0 to plain space in final answer and task summaries.
2. Clean task summaries: stripping code fences, **TaskResult**, and JSON into one clean sentence.
3. Structured /api/chat fields (short_answer, details_markdown without tables/assumptions/warnings, tables, assumptions, warnings, period, sources).
4. Flagged-inverter table: one row per inverter and date with flagged_by list; status code meaning in assumptions.
5. Follow-ups: passing previous question and task types so "same for Plant 2" repeats environment task for Plant 2.
"""

from datetime import date
from unittest.mock import MagicMock, patch
import pytest
from fastapi.testclient import TestClient

from api.main import app
from solar_agent.graph.synthesizer import (
    clean_task_summary,
    extract_structured_answers,
    normalize_spaces,
)
from solar_agent.graph.orchestrator import Orchestrator
from solar_agent.graph.llm_recorder import CacheMissError
from solar_agent.state import IntentType, Plan, Scope, Task, TaskResult, TaskStatus


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Test 1: Space normalization (U+202F, U+2009, U+200A, U+00A0 -> plain space)
# ---------------------------------------------------------------------------
def test_normalize_spaces():
    raw_text = "Plant\u202f1 generated 250\u00a0kWh with 0.85\u2009PR and 100\u200aW/m2 irradiance."
    normalized = normalize_spaces(raw_text)
    assert "\u202f" not in normalized
    assert "\u00a0" not in normalized
    assert "\u2009" not in normalized
    assert "\u200a" not in normalized
    assert normalized == "Plant 1 generated 250 kWh with 0.85 PR and 100 W/m2 irradiance."


# ---------------------------------------------------------------------------
# Test 2: Clean task summaries (strips code fences, **TaskResult**, JSON -> 1 sentence)
# ---------------------------------------------------------------------------
def test_clean_task_summary_stripping():
    # Case A: Code fence with JSON
    raw_fence_json = """```json
{
  "status": "done",
  "summary": "During the requested period, one inverter experienced zero generation.\nSecond sentence here.",
  "metrics": {"count": 1}
}
```"""
    cleaned_a = clean_task_summary(raw_fence_json)
    assert "```" not in cleaned_a
    assert "{" not in cleaned_a
    assert "}" not in cleaned_a
    assert cleaned_a == "During the requested period, one inverter experienced zero generation."

    # Case B: **TaskResult** prefix and multiple sentences
    raw_taskresult = "**TaskResult** Inverter PR for Plant 1 was computed at 0.865. Peer PR average was 0.850."
    cleaned_b = clean_task_summary(raw_taskresult)
    assert "**TaskResult**" not in cleaned_b
    assert cleaned_b == "Inverter PR for Plant 1 was computed at 0.865."

    # Case C: Raw JSON string without code fence
    raw_json = '{"summary": "Soiling loss estimated at 3.4% for the entire period.", "status": "DONE"}'
    cleaned_c = clean_task_summary(raw_json)
    assert "{" not in cleaned_c
    assert cleaned_c == "Soiling loss estimated at 3.4% for the entire period."

    # Case D: Space normalization inside task summary
    raw_spaces = "Plant\u202f2 recorded 12\u00a0fault days under 4.5\u2009kWh/m2."
    cleaned_d = clean_task_summary(raw_spaces)
    assert "\u202f" not in cleaned_d
    assert "\u00a0" not in cleaned_d
    assert "\u2009" not in cleaned_d
    assert cleaned_d == "Plant 2 recorded 12 fault days under 4.5 kWh/m2."


# ---------------------------------------------------------------------------
# Test 3: Structured /api/chat fields & details_markdown cleanup
# ---------------------------------------------------------------------------
def test_api_chat_structured_fields(client):
    final_ans = (
        "## Short Answer\n"
        "Plant\u202f1 generated 2,351,535\u00a0kWh with a PR of 0.85.\n\n"
        "## Supporting Detail\n"
        "Key performance highlights:\n"
        "- Block A operated at 0.88 PR.\n"
        "- Block B operated at 0.82 PR.\n"
        "| Inverter | PR |\n"
        "|---|---|\n"
        "| INV_1 | 0.88 |\n"
        "| INV_2 | 0.82 |\n\n"
        "## Assumptions\n"
        "- Data available 2026-08-01 to 2026-09-26\n\n"
        "## Warnings\n"
        "- Inverter 2 sensor drift detected\n\n"
        "Sources: t1, t2\n\n"
        "Period analysed: 2026-09-01 to 2026-09-26 of the data used (26 days)"
    )

    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "user_query": "What was the PR of Plant 1?",
        "final_answer": final_ans,
        "plan": Plan(
            intent=IntentType.SUMMARY,
            scope=Scope(start_date=date(2026, 9, 1), end_date=date(2026, 9, 26), plant_ids=[1]),
            tasks=[
                Task(id="t1", agent="data", description="Fetch daily telemetry"),
                Task(id="t2", agent="performance", description="Compute PR metrics", depends_on=["t1"]),
            ],
            success_criteria="PR for Plant 1",
        ),
        "results": {
            "t1": TaskResult(task_id="t1", status=TaskStatus.DONE, summary="Fetched 100 rows.", tool_calls_made=["fetch_inverter_daily"]),
            "t2": TaskResult(task_id="t2", status=TaskStatus.DONE, summary="PR computed at 0.85.", tool_calls_made=["compute_kpis_tool"]),
        },
        "llm_calls": {
            "planner": 1,
            "agent": 1,
            "synthesizer": 1,
            "total": 3,
            "budget": 12,
        },
        "warnings": ["Inverter 2 sensor drift detected"],
    }

    with patch("api.main.build_graph", return_value=mock_graph):
        res = client.post("/api/chat", json={"query": "What was the PR of Plant 1?", "thread_id": "test-chat-struct"})
        assert res.status_code == 200
        data = res.json()

        # 1. Short answer exists and spaces normalized
        assert "short_answer" in data
        assert "\u202f" not in data["short_answer"]
        assert "Plant 1 generated 2,351,535 kWh" in data["short_answer"]

        # 2. details_markdown has NO tables and NO Assumptions/Warnings headings
        assert "details_markdown" in data
        dm = data["details_markdown"]
        assert "| Inverter | PR |" not in dm
        assert "|---|---|" not in dm
        assert "## Assumptions" not in dm
        assert "## Warnings" not in dm
        assert "Block A operated at 0.88 PR." in dm

        # 3. assumptions and warnings lists
        assert "assumptions" in data
        assert isinstance(data["assumptions"], list)
        assert "warnings" in data
        assert "Inverter 2 sensor drift detected" in data["warnings"]

        # 4. sources has real tool names
        assert "sources" in data
        assert "fetch_inverter_daily" in data["sources"]
        assert "compute_kpis_tool" in data["sources"]

        # 5. Plan steps carry real agent and real tool (no 'auto')
        assert "plan" in data
        assert len(data["plan"]) == 2
        for step in data["plan"]:
            assert step["tool"] != "auto"
            assert step["tool"] is not None
            assert step["status"] == "done"

        # 6. llm_calls & elapsed_ms present
        assert "llm_calls" in data
        assert data["llm_calls"]["total"] == 3
        assert "elapsed_ms" in data
        assert data["elapsed_ms"] >= 0


# ---------------------------------------------------------------------------
# Test 4: Flagged-inverter table (1 row per inverter/date, flagged_by list, status code in assumption)
# ---------------------------------------------------------------------------
def test_dashboard_flagged_inverter_table(client):
    res = client.get("/api/dashboard/flagged?plant_id=1")
    assert res.status_code == 200
    rows = res.json()

    # Verify that each row has inverter, date, and flagged_by is a list
    seen_keys = set()
    for r in rows:
        key = (r["inverter"], r["date"])
        assert key not in seen_keys, f"Duplicate row for {key}; expected 1 row per inverter and date"
        seen_keys.add(key)
        assert "flagged_by" in r
        assert isinstance(r["flagged_by"], list)
        assert len(r["flagged_by"]) >= 1


# ---------------------------------------------------------------------------
# Test 5: Follow-ups: soiling question then "same for Plant 2" produces environment task for Plant 2
# ---------------------------------------------------------------------------
def test_followup_planning_soiling_then_same_for_plant2():
    mock_llm = MagicMock()
    mock_llm.invoke.side_effect = CacheMissError("cache miss")

    orchestrator = Orchestrator(llm=mock_llm)

    # Turn 1: Soiling question for Plant 1
    state_turn1 = {
        "user_query": "What was the soiling loss for Plant 1 in September?",
        "replan_count": 0,
        "plan": None,
        "results": {},
        "previous_query": None,
        "previous_tasks": None,
        "previous_scope": None,
    }
    plan_patch1 = orchestrator.plan(state_turn1)
    plan1 = plan_patch1["plan"]
    assert plan1 is not None
    assert any(t.agent == "environment" for t in plan1.tasks)

    # Turn 2: Follow-up "same for Plant 2"
    state_turn2 = {
        "user_query": "same for Plant 2",
        "replan_count": 0,
        "plan": None,
        "results": {},
        "previous_query": "What was the soiling loss for Plant 1 in September?",
        "previous_tasks": [
            {"agent": "environment", "tool": "estimate_soiling_loss_tool"}
        ],
        "previous_scope": {
            "plant_ids": [1],
            "start_date": date(2026, 9, 1),
            "end_date": date(2026, 9, 26),
        },
    }
    plan_patch2 = orchestrator.plan(state_turn2)
    plan2 = plan_patch2["plan"]
    assert plan2 is not None

    # Must produce an environment task for Plant 2
    env_task = next((t for t in plan2.tasks if t.agent == "environment"), None)
    assert env_task is not None, "Expected an environment task for follow-up query 'same for Plant 2'"
    assert plan2.scope.plant_ids == [2], f"Expected plant_ids=[2], got {plan2.scope.plant_ids}"
