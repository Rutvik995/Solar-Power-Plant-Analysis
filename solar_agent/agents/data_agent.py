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
    FetchInverterDailySchema,
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
            if inputs.get("tool") == "get_plant_overview" or "level" in inputs:
                from solar_agent.tools.data_tools import get_plant_overview
                plant_ids = inputs.get("plant_ids")
                level = inputs.get("level", "plants")
                include_blocks = inputs.get("include_blocks", False)
                overview_res = get_plant_overview(plant_ids=plant_ids, level=level)
                tool_calls_made.append("get_plant_overview")
                metrics = dict(overview_res.get("metrics") or {})
                if "plants" in overview_res:
                    metrics["plants"] = overview_res["plants"]
                if "blocks" in overview_res:
                    metrics["blocks"] = overview_res["blocks"]
                if "inverters" in overview_res:
                    metrics["inverters"] = overview_res["inverters"]
                if "data_coverage" in overview_res:
                    metrics["data_coverage"] = overview_res["data_coverage"]

                if include_blocks and level == "plants":
                    blocks_res = get_plant_overview(plant_ids=plant_ids, level="blocks")
                    metrics["blocks"] = blocks_res["blocks"]

                return TaskResult(
                    task_id=task_id,
                    status=TaskStatus.DONE,
                    summary=overview_res.get("summary") or "Retrieved structural overview of solar plants.",
                    data_ref=overview_res.get("data_ref"),
                    metrics=metrics,
                    tool_calls_made=tool_calls_made,
                )

            plant_ids   = list(inputs.get("plant_ids") or [])
            block_ids   = list(inputs.get("block_ids") or [])
            inverter_ids = list(inputs.get("inverter_ids") or [])
            named_inverter_ids = list(inputs.get("named_inverter_ids") or [])
            start_date  = inputs.get("start_date")
            end_date    = inputs.get("end_date")

            # When inverter(s) are named, fetch the whole block (all inverters in that block)
            all_named = list(dict.fromkeys(inverter_ids + named_inverter_ids))
            if all_named:
                try:
                    from solar_agent.db import queries as q
                    all_inv = q.get_all_inverters()
                    matched = all_inv[all_inv["inverter_id"].str.upper().isin([i.upper() for i in all_named])]
                    if not matched.empty:
                        for p in matched["plant_id"].unique():
                            if int(p) not in plant_ids:
                                plant_ids.append(int(p))
                        for b in matched["block_id"].unique():
                            if int(b) not in block_ids:
                                block_ids.append(int(b))
                except Exception as e:
                    logger.warning("Could not resolve block for inverters %s: %s", all_named, e)
                # DO NOT filter inverter_ids in fetch_kwargs — fetch the whole block
                inverter_ids = []

            if not start_date or not end_date:
                raise ValueError(
                    "DataAgent requires 'start_date' and 'end_date' in inputs. "
                    f"Received: {list(inputs.keys())}"
                )

            fetch_kwargs: dict[str, Any] = {
                "start_date": str(start_date),
                "end_date": str(end_date),
                "all_plants": bool(inputs.get("all_plants", False)),
            }
            if plant_ids:
                fetch_kwargs["plant_ids"] = list(plant_ids)
            if block_ids:
                fetch_kwargs["block_ids"] = [str(b) for b in block_ids]
            if inverter_ids:
                fetch_kwargs["inverter_ids"] = list(inverter_ids)

            # Validate args through strict schema
            schema_model = FetchInverterDailySchema.model_validate(fetch_kwargs)
            validated_kwargs = schema_model.model_dump(exclude_none=True)

            # --- Fetch inverter telemetry (required) ---
            logger.info(
                "DataAgent[%s]: fetching inverter data scope=%s %s→%s",
                task_id, {k: v for k, v in validated_kwargs.items() if k != "start_date" and k != "end_date"},
                start_date, end_date,
            )
            inv_result = fetch_inverter_daily_tool.invoke(validated_kwargs)
            tool_calls_made.append("fetch_inverter_daily_tool")

            warnings = list(inv_result.get("warnings", []))

            if "error" in inv_result:
                return TaskResult(
                    task_id=task_id,
                    status=TaskStatus.FAILED,
                    error=inv_result["error"],
                    warnings=warnings,
                    tool_calls_made=tool_calls_made,
                )

            data_ref = inv_result.get("data_ref")
            row_count = inv_result.get("row_count")
            if row_count is None and "shape" in inv_result:
                row_count = inv_result["shape"][0]
            row_count = row_count or 0

            # Empty data must stop the chain: mark t1 FAILED with warning
            if row_count == 0:
                empty_warn = (
                    f"fetch_inverter_daily returned 0 rows for filters "
                    f"(plant_ids={plant_ids}, dates={start_date} to {end_date})."
                )
                if empty_warn not in warnings:
                    warnings.append(empty_warn)
                return TaskResult(
                    task_id=task_id,
                    status=TaskStatus.FAILED,
                    error="No telemetry data found for the specified scope and date range (0 rows).",
                    warnings=warnings,
                    tool_calls_made=tool_calls_made,
                    latency_ms=(time.monotonic() - t_start) * 1000,
                )

            summary = (
                f"Fetched {row_count} inverter-days "
                f"({start_date} → {end_date}). data_ref={data_ref}"
            )

            from solar_agent.graph.synthesizer import clean_task_summary
            return TaskResult(
                task_id=task_id,
                status=TaskStatus.DONE,
                summary=clean_task_summary(f"Fetched {row_count} inverter-days from {start_date} to {end_date}."),
                data_ref=data_ref,
                metrics={"row_count": row_count, "named_inverter_ids": all_named} if all_named else {"row_count": row_count},
                warnings=warnings,
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
