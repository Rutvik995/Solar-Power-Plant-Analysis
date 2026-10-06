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
