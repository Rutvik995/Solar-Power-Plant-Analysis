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
  - The executor runs ONE layer per graph node call; the LangGraph edge
    re-routes back to the executor until all layers are done.
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from solar_agent.agents.data_agent import DataAgent
from solar_agent.agents.environment_agent import EnvironmentAgent
from solar_agent.agents.fault_agent import FaultAgent
from solar_agent.agents.performance_agent import PerformanceAgent
from solar_agent.state import AgentState, Plan, Task, TaskResult, TaskStatus

logger = logging.getLogger(__name__)

# Maximum parallel workers per layer
MAX_WORKERS = 4


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
            # Resolve any refs inside lists
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
        }

    # ------------------------------------------------------------------
    # LangGraph node entry point
    # ------------------------------------------------------------------

    def run_next_layer(self, state: AgentState) -> dict:
        """
        Execute the next pending layer of the plan.
        Returns a state patch with updated `results`.
        """
        plan: Plan | None = state.get("plan")
        if plan is None:
            logger.error("Executor called without a plan in state.")
            return {}

        results: dict[str, TaskResult] = dict(state.get("results") or {})

        # Determine which tasks are still pending
        layers = plan.topological_layers()
        next_layer_ids = self._find_next_layer(layers, results)

        if not next_layer_ids:
            logger.info("Executor: no more pending tasks.")
            return {"results": results}

        # Fetch the full Task objects for this layer
        task_map = {t.id: t for t in plan.tasks}
        layer_tasks = [task_map[tid] for tid in next_layer_ids]

        logger.info("Executor: running layer %s", next_layer_ids)
        new_results = self._run_layer(layer_tasks, results)
        results.update(new_results)

        return {"results": results}

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
        self, tasks: list[Task], results: dict[str, TaskResult]
    ) -> dict[str, TaskResult]:
        """Run all tasks in this layer concurrently. Return new TaskResult dict."""
        new_results: dict[str, TaskResult] = {}

        with ThreadPoolExecutor(max_workers=min(MAX_WORKERS, len(tasks))) as pool:
            futures = {}
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

                # Resolve input references
                inputs = _resolve_refs(task.inputs, results)
                agent = self._agents.get(task.agent)
                if agent is None:
                    new_results[task.id] = TaskResult(
                        task_id=task.id,
                        status=TaskStatus.FAILED,
                        error=f"Unknown agent '{task.agent}'.",
                    )
                    continue

                fut = pool.submit(agent.run, task.id, task.description, inputs)
                futures[fut] = task.id

            for fut in as_completed(futures):
                tid = futures[fut]
                try:
                    result = fut.result()
                    new_results[tid] = result
                    logger.info(
                        "Task '%s' completed with status=%s.", tid, result.status
                    )
                except Exception as exc:
                    logger.exception("Task '%s' raised unexpectedly: %s", tid, exc)
                    new_results[tid] = TaskResult(
                        task_id=tid,
                        status=TaskStatus.FAILED,
                        error=str(exc),
                    )

        return new_results

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
