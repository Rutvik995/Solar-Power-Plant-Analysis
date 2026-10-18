import pytest
from datetime import date
from unittest.mock import MagicMock

from solar_agent.db import queries as q
from solar_agent.graph.data_store import DataStore
from solar_agent.graph.orchestrator import _normalize_scope_and_tasks
from solar_agent.agents.data_agent import DataAgent
from solar_agent.agents.base_agent import _collect_flagged_items_for_tool
from solar_agent.tools.fault_tools import detect_zero_generation, detect_anomalies
from solar_agent.tools.table_formatter import build_analysis_tables, format_table_cell
from solar_agent.graph.synthesizer import build_deterministic_fallback, Synthesizer
from solar_agent.state import TaskResult, TaskStatus


def test_named_inverter_fetches_whole_block_and_peer_metrics():
    """
    For 'Explain why inverter P1_Block_A_INV_02 in Block_A failed on 16 Sept 2026':
    - The data task fetches the whole block (all 5 inverters in Block_A with weather).
    - Fault tools compute peer baselines first across the block, filtering to named inverter after.
    - Peers > 0, peer PR about 0.85 (0.8506), and peer irradiance about 6.66 on 2026-09-16.
    - 'Problem found' column maps to problem descriptions ('No power produced', 'Unusual readings').
    """
    query = "Explain why inverter P1_Block_A_INV_02 in Block_A failed on 16 Sept 2026"
    initial_plan = {
        "intent": "fault_diagnosis",
        "scope": {"plant_ids": [], "block_ids": [], "inverter_ids": ["P1_Block_A_INV_02"]},
        "tasks": [
            {
                "id": "t1",
                "agent": "data",
                "description": "Fetch daily telemetry for inverter P1_Block_A_INV_02",
                "inputs": {"inverter_ids": ["P1_Block_A_INV_02"]},
                "depends_on": [],
            },
            {
                "id": "t2",
                "agent": "fault",
                "description": "Diagnose fault for inverter P1_Block_A_INV_02",
                "inputs": {"data_ref": "$t1.data_ref"},
                "depends_on": ["t1"],
            },
        ],
    }

    # 1. Orchestrator plan normalization
    normalized = _normalize_scope_and_tasks(initial_plan, query)
    t1_inputs = normalized["tasks"][0]["inputs"]
    assert t1_inputs["plant_ids"] == [1]
    assert 1 in t1_inputs["block_ids"] or "Block A" in t1_inputs["block_ids"]
    assert "P1_Block_A_INV_02" in t1_inputs.get("named_inverter_ids", [])
    assert t1_inputs.get("inverter_ids") is None  # Whole block fetched!

    # 2. DataAgent execution
    data_agent = DataAgent()
    t1_res = data_agent.run("t1", "Fetch block telemetry", t1_inputs)
    assert t1_res.status == TaskStatus.DONE
    base_df = DataStore.get(t1_res.data_ref)
    assert base_df is not None
    # Block A has 5 inverters on 2026-09-16
    assert len(base_df) == 5
    assert "P1_Block_A_INV_01" in base_df["inverter_id"].values
    assert "P1_Block_A_INV_02" in base_df["inverter_id"].values

    # 3. Fault tool computation with peer baselines
    zero_res = detect_zero_generation(t1_res.data_ref, inverter_ids=["P1_Block_A_INV_02"])
    anom_res = detect_anomalies(t1_res.data_ref, inverter_ids=["P1_Block_A_INV_02"])

    flagged_items = []
    extra_warnings = []
    _collect_flagged_items_for_tool(
        tool_name="detect_zero_generation_tool",
        tool_output=zero_res,
        base_df=base_df,
        flagged_items=flagged_items,
        named_inverter_ids=["P1_Block_A_INV_02"],
        warnings=extra_warnings,
    )
    _collect_flagged_items_for_tool(
        tool_name="detect_anomalies_tool",
        tool_output=anom_res,
        base_df=base_df,
        flagged_items=flagged_items,
        named_inverter_ids=["P1_Block_A_INV_02"],
        warnings=extra_warnings,
    )

    assert len(flagged_items) >= 1
    item = flagged_items[0]
    assert item["inverter_id"] == "P1_Block_A_INV_02"
    assert item["date"] == "2026-09-16"
    assert item["peer_count"] > 0
    assert abs(item["peer_pr"] - 0.8506) < 0.01
    assert abs(item["peer_irradiance"] - 6.66) < 0.05

    # 4. Formatted Table checks
    mock_plan = MagicMock()
    t_fault = MagicMock(id="t2", agent="fault")
    mock_plan.tasks = [t_fault]
    results = {
        "t2": TaskResult(
            task_id="t2",
            status=TaskStatus.DONE,
            metrics={"flagged_items": flagged_items},
        )
    }
    tables = build_analysis_tables(mock_plan, results)
    assert len(tables) == 1
    tbl = tables[0]
    row = tbl["rows"][0]
    # Check problem found mappings
    assert "No power produced" in row[3]
    # Check actual vs peers
    assert "0.0% vs 85.1%" in row[4]


def test_zero_peer_case_gives_na_and_no_claims():
    """
    If the peer count is 0:
    - Return peer fields as null (None).
    - Table column 'Actual vs peers' shows 'n/a'.
    - Synthesizer makes no peer or weather claims.
    """
    flagged_items = [
        {
            "inverter_id": "Solo_INV_01",
            "block": "Block X",
            "date": "2026-09-16",
            "status_code": 2,
            "status_meaning": "Warning",
            "pr": 0.0,
            "peer_pr": None,
            "peer_irradiance": None,
            "peer_count": 0,
            "flagging_tool": "detect_zero_generation_tool",
        }
    ]

    mock_plan = MagicMock()
    t_fault = MagicMock(id="t_fault", agent="fault")
    mock_plan.tasks = [t_fault]
    results = {
        "t_fault": TaskResult(
            task_id="t_fault",
            status=TaskStatus.DONE,
            metrics={"flagged_items": flagged_items},
        )
    }

    tables = build_analysis_tables(mock_plan, results)
    assert len(tables) == 1
    t = tables[0]
    row = t["rows"][0]
    # Table shows n/a
    assert row[4] == "n/a"
    # No peer irradiance note created
    assert "notes" not in t or not t.get("notes")

    # Synthesizer fallback explanation makes no peer or weather claims
    headline, explanation, key_points, notes, final_answer = build_deterministic_fallback(
        "Explain why inverter Solo_INV_01 failed", mock_plan, results, []
    )
    assert "peers" not in explanation.lower()
    assert "irradiance" not in explanation.lower()
    assert "sunlight" not in explanation.lower()
    assert "the data doesn't show the exact cause" in explanation.lower()


def test_consistency_check_zero_peer_irr_with_positive_plant_irr():
    """
    Consistency check: peer irradiance 0 while plant's irradiance that day is above 0
    produces a warning and no claim (peer_irr set to None).
    """
    import pandas as pd
    base_df = pd.DataFrame([
        {
            "inverter_id": "INV_01",
            "block_id": 1,
            "block_name": "Block A",
            "log_date": date(2026, 9, 16),
            "performance_ratio": 0.0,
            "total_solar_radiation_kwh_m2": 5.5,
            "status_code": 2,
        },
        {
            "inverter_id": "INV_02",
            "block_id": 1,
            "block_name": "Block A",
            "log_date": date(2026, 9, 16),
            "performance_ratio": 0.85,
            "total_solar_radiation_kwh_m2": 0.0,  # Corrupted 0.0 peer reading
            "status_code": 0,
        },
    ])
    zero_tool_output = {
        "data_ref": DataStore.store(base_df[base_df["inverter_id"] == "INV_01"])
    }

    flagged_items = []
    warnings = []
    _collect_flagged_items_for_tool(
        tool_name="detect_zero_generation_tool",
        tool_output=zero_tool_output,
        base_df=base_df,
        flagged_items=flagged_items,
        named_inverter_ids=["INV_01"],
        warnings=warnings,
    )

    assert len(flagged_items) == 1
    item = flagged_items[0]
    # Consistency check triggered: peer_irradiance set to None
    assert item["peer_irradiance"] is None
    assert any("Inconsistency detected" in w for w in warnings)


def test_synthesizer_fault_explanation_no_unsupported_cause():
    """
    Never state a cause ('because of high temperature') unless supported.
    Uses 'the data doesn't show the exact cause'.
    """
    llm = MagicMock()
    # Mock LLM trying to invent high temperature cause or zero sunlight claim
    llm.invoke.return_value = MagicMock(
        content="""{
            "headline": "**Inverter P1_Block_A_INV_02 failed on 16 Sept 2026 because of high temperature.**",
            "explanation": "The inverter experienced high temperature and shut down, suggesting no sunlight or a measurement issue.",
            "key_points": ["Inverter failed due to high temperature."],
            "notes": ["Status code 2 warning."]
        }"""
    )
    synth = Synthesizer(llm)

    mock_plan = MagicMock()
    t_fault = MagicMock(id="t1", agent="fault")
    mock_plan.tasks = [t_fault]
    state = {
        "user_query": "Explain why inverter P1_Block_A_INV_02 in Block_A failed on 16 Sept 2026",
        "plan": mock_plan,
        "results": {
            "t1": TaskResult(
                task_id="t1",
                status=TaskStatus.DONE,
                metrics={
                    "flagged_items": [
                        {
                            "inverter_id": "P1_Block_A_INV_02",
                            "block": "Block A",
                            "date": "2026-09-16",
                            "status_code": 2,
                            "pr": 0.0,
                            "peer_pr": 0.8506,
                            "peer_irradiance": 6.66,
                        }
                    ]
                },
            )
        },
        "assumptions": [],
    }

    patch = synth.synthesize(state)
    assert "because of high temperature" not in patch["headline"].lower()
    assert "no sunlight or a measurement issue" not in patch["explanation"].lower()
    assert "the data doesn't show the exact cause" in patch["explanation"].lower()
