"""
test_executor.py – Unit tests for executor retry logic.

Tests use MagicMock agents injected directly into _run_with_retry,
bypassing the Executor.__init__ which requires a real LLM.
"""
from unittest.mock import MagicMock, patch
from solar_agent.state import Task, TaskStatus, TaskResult
from solar_agent.config.settings import settings


def _make_task(tid: str = "t1") -> Task:
    return Task(id=tid, agent="data", description="Fetch inverter data for testing.", dependencies=[])


def _make_executor():
    """Build an Executor with a mock LLM so __init__ doesn't fail."""
    from unittest.mock import MagicMock
    mock_llm = MagicMock()
    mock_llm.bind_tools.return_value = mock_llm
    with patch("solar_agent.graph.executor.DataAgent"), \
         patch("solar_agent.graph.executor.PerformanceAgent"), \
         patch("solar_agent.graph.executor.FaultAgent"), \
         patch("solar_agent.graph.executor.EnvironmentAgent"):
        from solar_agent.graph.executor import Executor
        return Executor(llm=mock_llm)


def test_executor_retry_transient():
    """Transient errors should be retried up to max_retries times."""
    executor = _make_executor()

    agent = MagicMock()
    agent.run.side_effect = [
        TaskResult(task_id="t1", status=TaskStatus.FAILED, error="Connection timeout"),
        TaskResult(task_id="t1", status=TaskStatus.FAILED, error="Connection reset"),
        TaskResult(task_id="t1", status=TaskStatus.DONE, metrics={"a": 1}),
    ]

    settings.task_max_retries = 2
    res = executor._run_with_retry(_make_task(), {}, agent)

    assert res.status == TaskStatus.DONE
    assert res.metrics == {"a": 1}
    assert agent.run.call_count == 3


def test_executor_retry_fail_fast_429():
    """429 / ResourceExhausted errors must NOT be retried."""
    executor = _make_executor()

    agent = MagicMock()
    agent.run.side_effect = [
        TaskResult(task_id="t1", status=TaskStatus.FAILED,
                   error="ResourceExhausted: 429 Too Many Requests"),
        TaskResult(task_id="t1", status=TaskStatus.DONE, metrics={"a": 1}),  # never reached
    ]

    settings.task_max_retries = 2
    res = executor._run_with_retry(_make_task(), {}, agent)

    assert res.status == TaskStatus.FAILED
    assert "ResourceExhausted" in (res.error or "")
    assert agent.run.call_count == 1  # no retry


def test_executor_retry_fail_fast_auth():
    """401 / auth errors must also fail fast."""
    executor = _make_executor()

    agent = MagicMock()
    agent.run.side_effect = [
        TaskResult(task_id="t1", status=TaskStatus.FAILED, error="401 auth failed"),
        TaskResult(task_id="t1", status=TaskStatus.DONE, metrics={}),
    ]

    settings.task_max_retries = 2
    res = executor._run_with_retry(_make_task(), {}, agent)

    assert res.status == TaskStatus.FAILED
    assert agent.run.call_count == 1


def test_executor_retry_exception_is_retried():
    """General exceptions (e.g. timeouts) should be retried."""
    executor = _make_executor()

    call_count = 0

    def side_effect(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise ConnectionError("Connection refused")
        return TaskResult(task_id="t1", status=TaskStatus.DONE, metrics={"x": 42})

    agent = MagicMock()
    agent.run.side_effect = side_effect

    settings.task_max_retries = 2
    res = executor._run_with_retry(_make_task(), {}, agent)

    assert res.status == TaskStatus.DONE
    assert call_count == 3


def test_executor_retry_exception_fail_fast_429():
    """429 in exception message should also fail fast."""
    executor = _make_executor()

    agent = MagicMock()
    agent.run.side_effect = Exception("429 quota exceeded")

    settings.task_max_retries = 2
    res = executor._run_with_retry(_make_task(), {}, agent)

    assert res.status == TaskStatus.FAILED
    assert agent.run.call_count == 1


def test_empty_data_stops_chain_and_skips_dependants():
    """Empty data stops the chain: if t1 fails, dependants are SKIPPED."""
    executor = _make_executor()
    task_t1 = Task(id="t1", agent="data", description="Fetch data", depends_on=[])
    task_t2 = Task(id="t2", agent="performance", description="Compute KPIs", depends_on=["t1"])

    results = {
        "t1": TaskResult(
            task_id="t1",
            status=TaskStatus.FAILED,
            error="No data found for scope.",
            warnings=["No data returned (0 rows)."],
        )
    }

    # Running layer containing t2
    new_results = executor._run_layer([task_t2], results)
    assert new_results["t2"].status == TaskStatus.SKIPPED
    assert "failed" in (new_results["t2"].error or "")


def test_analysis_task_fails_on_empty_frame():
    """Analysis task may never run on an empty frame."""
    import pandas as pd
    from solar_agent.graph.data_store import DataStore
    executor = _make_executor()

    empty_ref = DataStore.store(pd.DataFrame())
    task_t2 = Task(
        id="t2",
        agent="performance",
        description="Compute KPIs",
        inputs={"data_ref": empty_ref},
        depends_on=["t1"],
    )
    results = {
        "t1": TaskResult(task_id="t1", status=TaskStatus.DONE, data_ref=empty_ref)
    }

    new_results = executor._run_layer([task_t2], results)
    assert new_results["t2"].status == TaskStatus.FAILED
    assert "empty DataFrame" in (new_results["t2"].error or "")


def test_analysis_task_only_uses_declared_dependency_data_ref():
    """Analysis task may only use a data_ref produced by one of its declared dependencies."""
    import pandas as pd
    from solar_agent.graph.data_store import DataStore
    executor = _make_executor()

    valid_ref = DataStore.store(pd.DataFrame({"a": [1, 2]}))
    # t2 does NOT declare t1 in depends_on
    task_t2 = Task(
        id="t2",
        agent="performance",
        description="Compute KPIs",
        inputs={"data_ref": valid_ref},
        depends_on=[],
    )
    results = {
        "t1": TaskResult(task_id="t1", status=TaskStatus.DONE, data_ref=valid_ref)
    }

    new_results = executor._run_layer([task_t2], results)
    assert new_results["t2"].status == TaskStatus.FAILED
    assert "not produced by any of its declared dependencies" in (new_results["t2"].error or "")


def test_warnings_collected_into_state_and_synthesizer_appends_deterministic_section():
    """Collect every TaskResult.warnings into state and Synthesizer appends deterministic Warnings section."""
    from solar_agent.state import AgentState, Plan, Scope, IntentType
    from datetime import date
    from solar_agent.graph.synthesizer import Synthesizer
    from unittest.mock import MagicMock
    from langchain_core.messages import AIMessage

    executor = _make_executor()
    plan = Plan(
        intent=IntentType.PERFORMANCE_RANKING,
        scope=Scope(plant_ids=[1], start_date=date(2026, 9, 1), end_date=date(2026, 9, 26)),
        tasks=[
            Task(id="t1", agent="data", description="Fetch daily telemetry data for Plant 1.", depends_on=[]),
        ],
        success_criteria="Criteria is long enough to pass validation.",
    )

    state = AgentState(
        user_query="What was PR?",
        messages=[],
        plan=plan,
        results={},
        warnings=[],
        run_id="test_run",
    )

    mock_agent = MagicMock()
    mock_agent.run.return_value = TaskResult(
        task_id="t1",
        status=TaskStatus.DONE,
        warnings=["Sensor calibration warning", "Irradiance high warning"],
    )
    executor._agents["data"] = mock_agent

    update = executor.run_next_layer(state)
    assert "Sensor calibration warning" in update["warnings"]
    assert "Irradiance high warning" in update["warnings"]

    # Now verify Synthesizer deterministic section
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = AIMessage(content="Performance Ratio was 0.8656.")
    synthesizer = Synthesizer(llm=mock_llm)

    state["results"] = update["results"]
    state["warnings"] = update["warnings"]

    synth_res = synthesizer.synthesize(state)
    final_text = synth_res["final_answer"]
    assert "## Warnings" in final_text
    assert "- Sensor calibration warning" in final_text
    assert "- Irradiance high warning" in final_text

