"""
base_agent.py – Shared base classes for all specialist LangGraph tool-calling agents.

Two agent modes:
  ┌────────────────────────────────────────────────────────────────────────┐
  │  BaseSpecialistAgent (ReAct loop, max 5 iterations)                   │
  │    • Used by FaultAgent (must cross-reference multiple tools)          │
  │                                                                        │
  │  SingleCallAgent (1 LLM call → tool selection → direct execution)     │
  │    • Used by PerformanceAgent and EnvironmentAgent                     │
  │    • Pattern: LLM picks the tool + args → we run it → done            │
  │    • Never more than 1 LLM call per task                              │
  └────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from solar_agent.state import TaskResult, TaskStatus, BudgetExceeded

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5  # hard cap on ReAct rounds for FaultAgent


# ---------------------------------------------------------------------------
# 1. ReAct loop base (FaultAgent)
# ---------------------------------------------------------------------------

class BaseSpecialistAgent:
    """
    ReAct loop agent. Each run() may use up to MAX_ITERATIONS LLM calls.
    Subclasses define: name, system_prompt, tools.
    """

    name: str = "base"
    system_prompt: str = ""
    tools: list[BaseTool] = []

    def __init__(self, llm):
        if not self.tools:
            raise ValueError(f"Agent '{self.name}' must define at least one tool.")
        self._llm = llm.bind_tools(self.tools)
        self._tool_map: dict[str, BaseTool] = {t.name: t for t in self.tools}

    def run(self, task_id: str, description: str, inputs: dict[str, Any], run_id: str = "__default__") -> TaskResult:
        t_start = time.monotonic()
        tool_calls_made: list[str] = []

        human_text = self._format_human_message(description, inputs)
        messages: list = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=human_text),
        ]

        try:
            for _iteration in range(MAX_ITERATIONS):
                response: AIMessage = self._llm.invoke(messages, run_id=run_id)
                messages.append(response)

                if not response.tool_calls:
                    break

                for tc in response.tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["args"]
                    tool_calls_made.append(tool_name)
                    logger.debug(
                        "Agent '%s' calling tool '%s' with %s", self.name, tool_name, tool_args
                    )
                    tool_output = self._call_tool(tool_name, tool_args)
                    messages.append(
                        ToolMessage(
                            content=json.dumps(tool_output, default=str),
                            tool_call_id=tc["id"],
                        )
                    )

            result = self._parse_result(task_id, messages, tool_calls_made)

        except BudgetExceeded:
            raise
        except Exception as exc:
            logger.exception("Agent '%s' failed on task '%s': %s", self.name, task_id, exc)
            result = TaskResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                error=str(exc),
                tool_calls_made=tool_calls_made,
            )

        result.latency_ms = (time.monotonic() - t_start) * 1000
        return result

    def _format_human_message(self, description: str, inputs: dict[str, Any]) -> str:
        parts = [f"Task: {description}"]
        if inputs:
            parts.append("\nInputs:")
            for k, v in inputs.items():
                parts.append(f"  {k}: {v}")
        parts.append(
            "\nComplete this task using only your tools. "
            "Return a summary of findings and include any data_ref values from tool outputs."
        )
        return "\n".join(parts)

    def _call_tool(self, name: str, args: dict) -> Any:
        tool = self._tool_map.get(name)
        if tool is None:
            raise ValueError(f"Tool '{name}' is not available to agent '{self.name}'.")
        return tool.invoke(args)

    def _parse_result(
        self,
        task_id: str,
        messages: list,
        tool_calls_made: list[str],
    ) -> TaskResult:
        final_ai_msg = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)), None
        )
        summary = (
            final_ai_msg.content
            if final_ai_msg and isinstance(final_ai_msg.content, str)
            else ""
        )

        metrics: dict[str, Any] = {}
        data_ref: str | None = None
        warnings: list[str] = []

        for msg in messages:
            if isinstance(msg, ToolMessage):
                try:
                    payload = json.loads(msg.content)
                    if isinstance(payload, dict):
                        if "data_ref" in payload and payload["data_ref"]:
                            data_ref = payload["data_ref"]
                        if "metrics" in payload and isinstance(payload["metrics"], dict):
                            metrics.update(payload["metrics"])
                        if "warnings" in payload and isinstance(payload["warnings"], list):
                            warnings.extend(payload["warnings"])
                except (json.JSONDecodeError, TypeError):
                    pass

        return TaskResult(
            task_id=task_id,
            status=TaskStatus.DONE,
            summary=summary,
            data_ref=data_ref,
            metrics=metrics if metrics else None,
            tool_calls_made=tool_calls_made,
        )


# ---------------------------------------------------------------------------
# 2. Single-call structured agent (PerformanceAgent, EnvironmentAgent)
# ---------------------------------------------------------------------------

_TOOL_SELECT_SYSTEM = """\
You are a specialist tool selector. Given the task description and inputs,
respond with a JSON object that selects EXACTLY ONE tool to call and its arguments.

Response format (JSON only, no markdown fences):
{{
  "tool": "<tool_name>",
  "args": {{<argument key-value pairs>}}
}}

Available tools:
{tool_descriptions}

Rules:
1. Pick exactly one tool — the most appropriate one for the task.
2. Fill args from the task inputs. Use the data_ref from inputs if available.
3. Never fabricate data or compute answers yourself.
4. Respond with valid JSON only.
"""


class SingleCallAgent:
    """
    One-LLM-call agent for deterministic, single-tool tasks.

    Flow per run():
      1. One LLM call → structured JSON {tool, args}
      2. Parse the JSON, call the tool directly (no further LLM calls)
      3. Build TaskResult from the tool output deterministically

    Maximum LLM calls per task: 1
    """

    name: str = "single_call"
    tools: list[BaseTool] = []

    def __init__(self, llm):
        if not self.tools:
            raise ValueError(f"Agent '{self.name}' must define at least one tool.")
        self._tool_map: dict[str, BaseTool] = {t.name: t for t in self.tools}
        # Build tool descriptions for the system prompt
        tool_descs = "\n".join(
            f"  - {t.name}: {(t.description or '').split(chr(10))[0]}"
            for t in self.tools
        )
        system_content = _TOOL_SELECT_SYSTEM.format(tool_descriptions=tool_descs)
        self._system_msg = SystemMessage(content=system_content)
        # Bind WITHOUT tools so the LLM returns plain JSON text (not tool_calls)
        self._llm = llm

    def run(self, task_id: str, description: str, inputs: dict[str, Any], run_id: str = "__default__") -> TaskResult:
        t_start = time.monotonic()
        tool_calls_made: list[str] = []

        # Build human message
        human_parts = [f"Task: {description}", "\nInputs:"]
        for k, v in inputs.items():
            human_parts.append(f"  {k}: {v}")
        human_text = "\n".join(human_parts)

        try:
            # --- Single LLM call: ask it to pick a tool ---
            response: AIMessage = self._llm.invoke(
                [self._system_msg, HumanMessage(content=human_text)],
                run_id=run_id,
            )
            raw = response.content.strip()
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()

            # ── malformed JSON → return FAILED immediately (item 5) ──
            try:
                selection = json.loads(raw)
            except (json.JSONDecodeError, ValueError) as parse_err:
                logger.error(
                    "SingleCallAgent[%s] malformed JSON from LLM: %s | raw=%r",
                    task_id, parse_err, raw[:120],
                )
                return TaskResult(
                    task_id=task_id,
                    status=TaskStatus.FAILED,
                    error=f"Malformed JSON from LLM: {parse_err}",
                    tool_calls_made=tool_calls_made,
                    latency_ms=(time.monotonic() - t_start) * 1000,
                )

            tool_name = selection.get("tool", "")
            tool_args = selection.get("args", {})

            # ── unknown tool → return FAILED immediately (item 5) ──
            tool = self._tool_map.get(tool_name)
            if tool is None:
                logger.error(
                    "SingleCallAgent[%s] unknown tool '%s'. Available: %s",
                    task_id, tool_name, list(self._tool_map.keys()),
                )
                return TaskResult(
                    task_id=task_id,
                    status=TaskStatus.FAILED,
                    error=(
                        f"LLM selected unknown tool '{tool_name}'. "
                        f"Available: {list(self._tool_map.keys())}"
                    ),
                    tool_calls_made=tool_calls_made,
                    latency_ms=(time.monotonic() - t_start) * 1000,
                )

            logger.info(
                "SingleCallAgent[%s]: executing tool '%s' with args=%s",
                task_id, tool_name, tool_args,
            )
            tool_calls_made.append(tool_name)
            tool_output = tool.invoke(tool_args)

            # --- Deterministic result summary ---
            data_ref = tool_output.get("data_ref")
            metrics = tool_output.get("metrics") or {}
            warnings = tool_output.get("warnings") or []
            summary_text = tool_output.get("summary", f"Tool '{tool_name}' completed.")

            return TaskResult(
                task_id=task_id,
                status=TaskStatus.DONE,
                summary=summary_text,
                data_ref=data_ref,
                metrics=metrics if metrics else None,
                tool_calls_made=tool_calls_made,
                latency_ms=(time.monotonic() - t_start) * 1000,
            )

        except BudgetExceeded:
            raise
        except Exception as exc:
            logger.exception(
                "SingleCallAgent[%s] failed: %s", task_id, exc
            )
            return TaskResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                error=str(exc),
                tool_calls_made=tool_calls_made,
                latency_ms=(time.monotonic() - t_start) * 1000,
            )
