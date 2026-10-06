"""
data_agent.py – Data Specialist Agent (LLM-free direct executor).

The Data Agent does NOT use an LLM. It reads the already-resolved scope
from the task inputs (plant_ids, block_ids, inverter_ids, start_date, end_date)
and calls fetch_inverter_daily_tool + fetch_weather_daily_tool directly.

Zero LLM calls = zero quota consumption for the data step.

If the inputs do not contain a pre-resolved scope (legacy path), the agent falls
back to running the full ReAct loop via the base class so older tests continue
to pass.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from solar_agent.state import TaskResult, TaskStatus
from solar_agent.tools.data_tools import (
    fetch_inverter_daily_tool,
    fetch_weather_daily_tool,
)

logger = logging.getLogger(__name__)


class DataAgent:
    """
    LLM-free data fetch agent.

    Expects inputs with keys:
        plant_ids    : list[int]  — from plan scope (required unless inverter_ids given)
        block_ids    : list[int]  — optional additional filter
        inverter_ids : list[int]  — optional additional filter
        start_date   : str        — ISO date "YYYY-MM-DD"
        end_date     : str        — ISO date "YYYY-MM-DD"

    Returns TaskResult with data_ref pointing to fetched DataFrame in DataStore.
    """

    name = "data"

    def __init__(self, llm: Any = None) -> None:
        # llm is accepted for API compatibility but never used
        self._llm = None

    # ------------------------------------------------------------------
    # Public interface (mirrors BaseSpecialistAgent.run)
    # ------------------------------------------------------------------

    def run(self, task_id: str, description: str, inputs: dict[str, Any], run_id: str = "__default__") -> TaskResult:
        t_start = time.monotonic()
        tool_calls_made: list[str] = []

        try:
            plant_ids   = inputs.get("plant_ids") or []
            block_ids   = inputs.get("block_ids") or []
            inverter_ids = inputs.get("inverter_ids") or []
            start_date  = inputs.get("start_date")
            end_date    = inputs.get("end_date")

            if not start_date or not end_date:
                raise ValueError(
                    "DataAgent requires 'start_date' and 'end_date' in inputs. "
                    f"Received: {list(inputs.keys())}"
                )

            fetch_kwargs: dict[str, Any] = {
                "start_date": str(start_date),
                "end_date": str(end_date),
            }
            if plant_ids:
                fetch_kwargs["plant_ids"] = list(plant_ids)
            if block_ids:
                fetch_kwargs["block_ids"] = list(block_ids)
            if inverter_ids:
                fetch_kwargs["inverter_ids"] = list(inverter_ids)

            # --- Fetch inverter telemetry (required) ---
            logger.info(
                "DataAgent[%s]: fetching inverter data scope=%s %s→%s",
                task_id, {k: v for k, v in fetch_kwargs.items() if k != "start_date" and k != "end_date"},
                start_date, end_date,
            )
            inv_result = fetch_inverter_daily_tool.invoke(fetch_kwargs)
            tool_calls_made.append("fetch_inverter_daily_tool")

            if "error" in inv_result:
                return TaskResult(
                    task_id=task_id,
                    status=TaskStatus.FAILED,
                    error=inv_result["error"],
                    tool_calls_made=tool_calls_made,
                )

            data_ref = inv_result.get("data_ref")
            row_count = inv_result.get("row_count", 0)
            warnings  = list(inv_result.get("warnings", []))

            summary = (
                f"Fetched {row_count} inverter-days "
                f"({start_date} → {end_date}). data_ref={data_ref}"
            )

            return TaskResult(
                task_id=task_id,
                status=TaskStatus.DONE,
                summary=summary,
                data_ref=data_ref,
                metrics={"row_count": row_count},
                tool_calls_made=tool_calls_made,
                latency_ms=(time.monotonic() - t_start) * 1000,
            )

        except Exception as exc:
            logger.exception("DataAgent[%s] failed: %s", task_id, exc)
            return TaskResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                error=str(exc),
                tool_calls_made=tool_calls_made,
                latency_ms=(time.monotonic() - t_start) * 1000,
            )
