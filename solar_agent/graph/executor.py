"""
graph/executor.py – DAG Executor node.

Reads the current Plan from AgentState, resolves the next ready layer of tasks,
dispatches each task to its specialist agent (in parallel using a ThreadPoolExecutor),
and writes the TaskResults back to state.

Design:
  - Tasks are grouped into topological layers by Plan.topological_layers().
  - All tasks in a layer have no inter-dependencies and run concurrently.
  - Input references like "$t1.data_ref" are resolved from completed TaskResults.
  - A task whose dependency failed is marked SKIPPED (not executed).
  - Per-task timeout (settings.task_timeout_seconds) and one retry
    (settings.task_max_retries) are applied before marking a task FAILED.
  - The executor runs ONE layer per graph node call; the LangGraph edge
    re-routes back to the executor until all layers are done.
"""

from __future__ import annotations

import logging
import time
from concurrent.futures import Future, ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from typing import Any

from solar_agent.agents.data_agent import DataAgent
from solar_agent.agents.environment_agent import EnvironmentAgent
from solar_agent.agents.fault_agent import FaultAgent
from solar_agent.agents.performance_agent import PerformanceAgent
from solar_agent.config.settings import settings
from solar_agent.graph.data_store import DataStore
from solar_agent.state import AgentState, Plan, Task, TaskResult, TaskStatus, BudgetExceeded

logger = logging.getLogger(__name__)

# Maximum parallel workers per layer
MAX_WORKERS = 4

# ---------------------------------------------------------------------------
# Retry classification
# ---------------------------------------------------------------------------

_NON_RETRYABLE_FRAGMENTS = frozenset([
    "429",               # rate-limit – back off at a higher level, not here
    "401", "403",        # auth errors
    "auth",              # generic auth failure
    "validation",        # schema / request validation
    "resourceexhausted", # Google quota
    "invalid_argument",  # bad request
])


def _is_non_retryable(err_str: str) -> bool:
    """Return True if the error should NOT be retried (fail fast)."""
    s = err_str.lower()
    return any(fragment in s for fragment in _NON_RETRYABLE_FRAGMENTS)


# ---------------------------------------------------------------------------
# Input reference resolution
# ---------------------------------------------------------------------------

def _resolve_refs(inputs: dict[str, Any], results: dict[str, TaskResult]) -> dict[str, Any]:
    """
    Replace "$t1.data_ref" / "$t1.metrics.pr" style tokens with real values.
    Unresolved references (dependency failed / missing) are replaced with None.
    """
    resolved: dict[str, Any] = {}
    for key, val in inputs.items():
        if isinstance(val, str) and val.startswith("$"):
            parts = val[1:].split(".")   # e.g. ["t1", "data_ref"]
            task_id = parts[0]
            attr_chain = parts[1:]
            tr = results.get(task_id)
            if tr is None:
                resolved[key] = None
                continue
            obj: Any = tr
            for attr in attr_chain:
                if isinstance(obj, dict):
                    obj = obj.get(attr)
                elif hasattr(obj, attr):
                    obj = getattr(obj, attr)
                else:
                    obj = None
                    break
            resolved[key] = obj
        elif isinstance(val, list):
            resolved[key] = [
                _resolve_refs({"v": item}, results).get("v", item)
                if isinstance(item, str) and item.startswith("$")
                else item
                for item in val
            ]
        else:
            resolved[key] = val
    return resolved


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

class ChartAgent:
    """Deterministic agent that creates Plotly charts without LLM calls."""
    name = "chart"

    def run(self, task_id: str, description: str, inputs: dict[str, Any], run_id: str = "__default__") -> TaskResult:
        from solar_agent.tools.charts import make_chart
        data_ref = inputs.get("data_ref")
        kind = inputs.get("kind")
        if not kind:
            kind = "line" if "trend" in description.lower() else "bar"
        chart_json = make_chart(data_ref=data_ref, kind=kind)
        return TaskResult(
            task_id=task_id,
            status=TaskStatus.DONE,
            summary=f"Generated {kind} chart for {data_ref}.",
            data_ref=data_ref,
            metrics={"chart_json": chart_json, "chart_kind": kind},
            tool_calls_made=["make_chart"],
        )


class Executor:
    """
    LangGraph node that dispatches one topological layer of tasks at a time.

    The LLM is injected so the same LLM can be shared across all specialist agents.
    """

    def __init__(self, llm):
        self._agents = {
            "data": DataAgent(llm=llm),
            "performance": PerformanceAgent(llm=llm),
            "fault": FaultAgent(llm=llm),
            "environment": EnvironmentAgent(llm=llm),
            "chart": ChartAgent(),
        }

    # ------------------------------------------------------------------
    # LangGraph node entry point
    # ------------------------------------------------------------------

    def run_next_layer(self, state: AgentState, run_id: str = "__default__") -> dict:
        """
        Execute the next pending layer of the plan.
        Returns a state patch with updated `results`.
        """
        plan: Plan | None = state.get("plan")
        if plan is None:
            logger.error("Executor called without a plan in state.")
            return {}

        results: dict[str, TaskResult] = dict(state.get("results") or {})

        layers = plan.topological_layers()
        next_layer_ids = self._find_next_layer(layers, results)

        if not next_layer_ids:
            logger.info("Executor: no more pending tasks.")
            return {"results": results}

        task_map = {t.id: t for t in plan.tasks}
        layer_tasks = [task_map[tid] for tid in next_layer_ids]

        logger.info("Executor: running layer %s", next_layer_ids)
        try:
            new_results = self._run_layer(layer_tasks, results, run_id=run_id)
            results.update(new_results)
            warnings_list = list(state.get("warnings") or [])
            for tr in new_results.values():
                if hasattr(tr, "warnings") and tr.warnings:
                    for w in tr.warnings:
                        if w not in warnings_list:
                            warnings_list.append(w)
            return {"results": results, "warnings": warnings_list}
        except BudgetExceeded as exc:
            # The exception might have partial results attached from _run_layer
            if hasattr(exc, "partial_results"):
                results.update(exc.partial_results)
            exc.partial_results = results
            raise exc

    # ------------------------------------------------------------------
    # Layer execution
    # ------------------------------------------------------------------

    def _find_next_layer(
        self, layers: list[list[str]], results: dict[str, TaskResult]
    ) -> list[str]:
        """Return the first layer that has unexecuted tasks."""
        for layer in layers:
            pending = [
                tid for tid in layer
                if tid not in results
                or results[tid].status == TaskStatus.PENDING
            ]
            if pending:
                return pending
        return []

    def _run_layer(
        self, tasks: list[Task], results: dict[str, TaskResult], run_id: str = "__default__"
    ) -> dict[str, TaskResult]:
        """Run all tasks in this layer concurrently with timeout + retry."""
        new_results: dict[str, TaskResult] = {}

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(tasks))) as pool:
            futures: dict[Future, Task] = {}

            for task in tasks:
                # Skip if any dependency failed
                failed_deps = [
                    dep for dep in task.depends_on
                    if results.get(dep) and results[dep].status == TaskStatus.FAILED
                ]
                if failed_deps:
                    logger.warning(
                        "Task '%s' skipped: dependency %s failed.", task.id, failed_deps
                    )
                    new_results[task.id] = TaskResult(
                        task_id=task.id,
                        status=TaskStatus.SKIPPED,
                        error=f"Skipped because dependencies {failed_deps} failed.",
                    )
                    continue

                inputs = _resolve_refs(task.inputs, results)

                # Validate data_ref for analysis tasks
                if task.agent in ("performance", "fault", "environment"):
                    task_data_ref = inputs.get("data_ref")
                    if task_data_ref:
                        valid_dep_refs = {
                            results[dep].data_ref
                            for dep in task.depends_on
                            if results.get(dep) and results[dep].data_ref
                        }
                        if task_data_ref not in valid_dep_refs:
                            new_results[task.id] = TaskResult(
                                task_id=task.id,
                                status=TaskStatus.FAILED,
                                error=(
                                    f"Task '{task.id}' attempted to use data_ref '{task_data_ref}' "
                                    f"not produced by any of its declared dependencies {task.depends_on}."
                                ),
                            )
                            continue

                        df = DataStore.get_or_load(task_data_ref)
                        if df is None or df.empty:
                            new_results[task.id] = TaskResult(
                                task_id=task.id,
                                status=TaskStatus.FAILED,
                                error=f"Analysis task '{task.id}' cannot run on an empty DataFrame (data_ref='{task_data_ref}').",
                                warnings=[f"Analysis task '{task.id}' skipped execution on empty frame."],
                            )
                            continue

                agent = self._agents.get(task.agent)
                if agent is None:
                    new_results[task.id] = TaskResult(
                        task_id=task.id,
                        status=TaskStatus.FAILED,
                        error=f"Unknown agent '{task.agent}'.",
                    )
                    continue

                fut = pool.submit(self._run_with_retry, task, inputs, agent, run_id)
                futures[fut] = task

            timeout = settings.task_timeout_seconds
            for fut, task in futures.items():
                try:
                    result = fut.result(timeout=timeout)
                    if result and getattr(result, "summary", None):
                        from solar_agent.graph.synthesizer import clean_task_summary
                        result.summary = clean_task_summary(result.summary)
                    new_results[task.id] = result
                    logger.info(
                        "Task '%s' completed with status=%s.", task.id, result.status
                    )
                except FutureTimeoutError:
                    logger.error("Task '%s' timed out after %ds.", task.id, timeout)
                    new_results[task.id] = TaskResult(
                        task_id=task.id,
                        status=TaskStatus.FAILED,
                        error=f"Task timed out after {timeout} seconds.",
                    )
                except BudgetExceeded as exc:
                    exc.partial_results = new_results
                    raise
                except Exception as exc:
                    logger.exception("Task '%s' raised unexpectedly: %s", task.id, exc)
                    new_results[task.id] = TaskResult(
                        task_id=task.id,
                        status=TaskStatus.FAILED,
                        error=str(exc),
                    )

        return new_results

    def _run_with_retry(self, task: Task, inputs: dict, agent, run_id: str = "__default__") -> TaskResult:
        """
        Run a task with up to settings.task_max_retries retries on failure.
        - Retries only transient errors (timeout, 5xx, connection).
        - Fails fast on 429, 401, 403, auth errors, and validation errors.
        Each retry waits 2 seconds before re-attempting.
        """
        max_attempts = 1 + settings.task_max_retries
        last_result: TaskResult | None = None

        for attempt in range(max_attempts):
            try:
                result = agent.run(task.id, task.description, inputs, run_id=run_id)
                if result.status == TaskStatus.DONE:
                    return result

                err_str = str(result.error).lower()
                if _is_non_retryable(err_str):
                    logger.error("Fail-fast error detected in task %s: %s", task.id, result.error)
                    return result

                last_result = result
                if attempt < max_attempts - 1:
                    logger.warning(
                        "Task '%s' failed on attempt %d/%d (%s). Retrying...",
                        task.id, attempt + 1, max_attempts, result.error,
                    )
                    time.sleep(2)
            except BudgetExceeded:
                raise
            except Exception as exc:
                err_str = str(exc).lower()
                last_result = TaskResult(
                    task_id=task.id,
                    status=TaskStatus.FAILED,
                    error=str(exc),
                )
                if _is_non_retryable(err_str):
                    logger.error("Fail-fast error raised in task %s: %s", task.id, exc)
                    return last_result

                if attempt < max_attempts - 1:
                    logger.warning(
                        "Task '%s' raised on attempt %d/%d: %s. Retrying...",
                        task.id, attempt + 1, max_attempts, exc,
                    )
                    time.sleep(2)

        return last_result or TaskResult(
            task_id=task.id, status=TaskStatus.FAILED, error="All attempts failed."
        )

    # ------------------------------------------------------------------
    # Utility: check if all tasks are done
    # ------------------------------------------------------------------

    @staticmethod
    def all_done(state: AgentState) -> bool:
        """
        Returns True when every task in the plan has a terminal status
        (DONE, FAILED, or SKIPPED).
        """
        plan: Plan | None = state.get("plan")
        if plan is None:
            return True
        results: dict = state.get("results") or {}
        terminal = {TaskStatus.DONE, TaskStatus.FAILED, TaskStatus.SKIPPED}
        return all(
            results.get(t.id) is not None
            and results[t.id].status in terminal
            for t in plan.tasks
        )
