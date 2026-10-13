"""
solar_agent/tests/test_api.py — Unit & integration tests for FastAPI service.

6 required tests:
  1. GET /api/meta: verify plant/block metadata, dates ISO, provider/mode
  2. GET /api/dashboard/kpis: verify yield, PR, soiling loss %, fault days, sparkline
  3. GET /api/dashboard/*: verify /daily, /blocks, /heatmap, /soiling, /flagged formatting
  4. POST /api/chat (mocked LLM): verify answer_markdown, tables, plan, llm_calls, elapsed_ms
  5. POST /api/chat BudgetExceeded: mapped to clear error object, status 429, not 500
  6. POST /api/chat HTTP 429 rate limit: mapped to clear error object, status 429, not 500
"""

from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.main import app
from solar_agent.state import BudgetExceeded, IntentType, Plan, Scope, Task, TaskResult, TaskStatus


@pytest.fixture
def client():
    return TestClient(app)


# ---------------------------------------------------------------------------
# Test 1: GET /api/meta
# ---------------------------------------------------------------------------
def test_get_meta(client):
    res = client.get("/api/meta")
    assert res.status_code == 200
    data = res.json()

    assert "plants" in data
    assert len(data["plants"]) > 0
    first_plant = data["plants"][0]
    assert "id" in first_plant
    assert "name" in first_plant
    assert "capacity_mwp" in first_plant

    assert "blocks" in data
    assert len(data["blocks"]) > 0

    assert "min_log_date" in data
    assert "max_log_date" in data
    assert data["min_log_date"] <= data["max_log_date"]

    assert "llm_provider" in data
    assert "mode" in data


# ---------------------------------------------------------------------------
# Test 2: GET /api/dashboard/kpis
# ---------------------------------------------------------------------------
def test_dashboard_kpis(client):
    res = client.get("/api/dashboard/kpis?plant_id=1&start=2026-09-01&end=2026-09-10")
    assert res.status_code == 200
    data = res.json()

    assert "yield" in data
    assert "pr" in data
    assert "soiling_loss_pct" in data
    assert "fault_days" in data
    assert "previous_period" in data
    assert "sparkline" in data

    assert isinstance(data["yield"], (int, float))
    assert data["yield"] > 0
    if data["pr"] is not None:
        assert 0.0 <= data["pr"] <= 1.0

    assert isinstance(data["sparkline"], list)
    if data["sparkline"]:
        sp = data["sparkline"][0]
        assert "date" in sp
        assert "yield" in sp
        assert "pr" in sp


# ---------------------------------------------------------------------------
# Test 3: Dashboard sub-endpoints (/daily, /blocks, /heatmap, /soiling, /flagged)
# ---------------------------------------------------------------------------
def test_dashboard_breakdowns(client):
    # /daily
    res_daily = client.get("/api/dashboard/daily?plant_id=1&start=2026-09-01&end=2026-09-05")
    assert res_daily.status_code == 200
    daily_data = res_daily.json()
    assert isinstance(daily_data, list)
    if daily_data:
        assert "date" in daily_data[0]
        assert "yield" in daily_data[0]
        assert "irradiance" in daily_data[0]

    # /blocks
    res_blocks = client.get("/api/dashboard/blocks?plant_id=1&start=2026-09-01&end=2026-09-05")
    assert res_blocks.status_code == 200
    blocks_data = res_blocks.json()
    assert isinstance(blocks_data, list)
    if blocks_data:
        assert "block_id" in blocks_data[0]
        assert "performance_ratio" in blocks_data[0]

    # /heatmap
    res_heat = client.get("/api/dashboard/heatmap?plant_id=1&start=2026-09-01&end=2026-09-02")
    assert res_heat.status_code == 200
    heat_data = res_heat.json()
    assert isinstance(heat_data, list)
    if heat_data:
        assert "inverter_id" in heat_data[0]
        assert "date" in heat_data[0]
        assert "pr" in heat_data[0]

    # /soiling
    res_soil = client.get("/api/dashboard/soiling?plant_id=1&start=2026-09-01&end=2026-09-05")
    assert res_soil.status_code == 200
    soil_data = res_soil.json()
    assert isinstance(soil_data, list)
    if soil_data:
        assert "plant" in soil_data[0]
        assert "date" in soil_data[0]
        assert "ratio" in soil_data[0]

    # /flagged
    res_flag = client.get("/api/dashboard/flagged?start=2026-08-01&end=2026-09-26")
    assert res_flag.status_code == 200
    flag_data = res_flag.json()
    assert isinstance(flag_data, list)
    if flag_data:
        f = flag_data[0]
        assert "inverter" in f
        assert "block" in f
        assert "date" in f
        assert "type" in f
        assert "status_code" in f
        assert "pr" in f


# ---------------------------------------------------------------------------
# Test 4: POST /api/chat (mocked LLM)
# ---------------------------------------------------------------------------
def test_chat_endpoint_mocked_llm(client):
    mock_graph = MagicMock()
    mock_graph.invoke.return_value = {
        "user_query": "What was the PR of Plant 1 in September 2026?",
        "final_answer": "The PR of Plant 1 in September 2026 was 87.6%.",
        "plan": Plan(
            intent=IntentType.SUMMARY,
            scope=Scope(start_date=date(2026, 9, 1), end_date=date(2026, 9, 5), plant_ids=[1]),
            tasks=[
                Task(id="t1", agent="data", description="Fetch inverter telemetry"),
                Task(id="t2", agent="performance", description="Compute PR metrics", depends_on=["t1"]),
            ],
            success_criteria="Provide plant 1 PR",
        ),
        "results": {
            "t1": TaskResult(task_id="t1", status=TaskStatus.DONE, summary="Telemetry fetched"),
            "t2": TaskResult(
                task_id="t2",
                status=TaskStatus.DONE,
                summary="PR computed",
                metrics={"performance_ratio": 0.876},
            ),
        },
        "llm_call_count": 2,
        "warnings": ["Sensor calibration notice"],
    }

    with patch("api.main.build_graph", return_value=mock_graph):
        res = client.post(
            "/api/chat",
            json={"query": "What was the PR of Plant 1 in September 2026?", "thread_id": "test-1"},
        )
        assert res.status_code == 200
        data = res.json()

        assert "answer_markdown" in data
        assert "87.6%" in data["answer_markdown"]
        assert "plan" in data
        assert len(data["plan"]) == 2
        assert data["plan"][0]["id"] == "t1"
        assert data["plan"][0]["status"] == "done"

        assert "llm_calls" in data
        assert data["llm_calls"]["total"] == 2
        assert "budget" in data["llm_calls"]

        assert "warnings" in data
        assert len(data["warnings"]) == 1

        assert "period" in data
        assert data["period"] == {"start": "2026-09-01", "end": "2026-09-05"}

        assert "elapsed_ms" in data
        assert data["elapsed_ms"] >= 0
        assert data["error"] is None


# ---------------------------------------------------------------------------
# Test 5: POST /api/chat BudgetExceeded mapped to clear error object (not 500)
# ---------------------------------------------------------------------------
def test_chat_endpoint_budget_exceeded(client):
    mock_graph = MagicMock()
    mock_graph.invoke.side_effect = BudgetExceeded("Max LLM calls exceeded limit 12")

    with patch("api.main.build_graph", return_value=mock_graph):
        res = client.post(
            "/api/chat",
            json={"query": "Compute complex cross-plant analysis", "thread_id": "test-budget"},
        )
        # Must map to clear error object, NOT 500
        assert res.status_code == 429
        data = res.json()
        assert data["error"] is not None
        assert data["error"]["code"] == 429
        assert data["error"]["type"] == "BudgetExceeded"
        assert "Max LLM calls exceeded" in data["error"]["message"]
        assert "llm_calls" in data


# ---------------------------------------------------------------------------
# Test 6: POST /api/chat HTTP 429 RateLimit mapped to clear error object (not 500)
# ---------------------------------------------------------------------------
def test_chat_endpoint_rate_limit_429(client):
    mock_graph = MagicMock()
    mock_graph.invoke.side_effect = RuntimeError("HTTP 429: Rate limit exceeded for model")

    with patch("api.main.build_graph", return_value=mock_graph):
        res = client.post(
            "/api/chat",
            json={"query": "What is the status of inverter 1?", "thread_id": "test-429"},
        )
        # Must map to clear error object, NOT 500
        assert res.status_code == 429
        data = res.json()
        assert data["error"] is not None
        assert data["error"]["code"] == 429
        assert data["error"]["type"] == "RateLimitError"
        assert "Rate limit exceeded" in data["error"]["message"]
        assert "elapsed_ms" in data
