"""
base_agent.py – Shared base class for all specialist LangGraph tool-calling agents.

Each subclass binds its own tools and system prompt. The `run` method:
  1. Builds a ToolNode
  2. Invokes the LLM with bound tools in a ReAct loop (max `max_iterations`)
  3. Parses the final ToolMessage sequence into a TaskResult
  4. Returns a populated TaskResult without ever putting a DataFrame in LLM context
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from solar_agent.state import TaskResult, TaskStatus

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5  # hard cap on tool-call rounds per agent run


class BaseSpecialistAgent:
    """
    Base class for Data, Performance, Fault, and Environment agents.

    Subclasses MUST define:
        name: str           — unique agent identifier (matches AgentName enum)
        system_prompt: str  — the system message injected before each task
        tools: list[BaseTool]  — the tools this agent is allowed to call

    The LLM is injected at runtime so tests can mock it.
    """

    name: str = "base"
    system_prompt: str = ""
    tools: list[BaseTool] = []

    def __init__(self, llm):
        if not self.tools:
            raise ValueError(f"Agent '{self.name}' must define at least one tool.")
        self._llm = llm.bind_tools(self.tools)
        self._tool_map: dict[str, BaseTool] = {t.name: t for t in self.tools}

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    def run(self, task_id: str, description: str, inputs: dict[str, Any]) -> TaskResult:
        """
        Execute the agent on a single Task and return a TaskResult.

        Args:
            task_id:     The task's unique ID (for result attribution).
            description: Human-readable task description.
            inputs:      Dict of input parameters forwarded to the agent.
        """
        t_start = time.monotonic()
        tool_calls_made: list[str] = []

        # Build initial message list
        human_text = self._format_human_message(description, inputs)
        messages: list = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=human_text),
        ]

        try:
            for _iteration in range(MAX_ITERATIONS):
                response: AIMessage = self._llm.invoke(messages)
                messages.append(response)

                # If no tool calls, the LLM has finished
                if not response.tool_calls:
                    break

                # Execute every tool call in this round
                for tc in response.tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["args"]
                    tool_calls_made.append(tool_name)
                    logger.debug("Agent '%s' calling tool '%s' with %s", self.name, tool_name, tool_args)

                    tool_output = self._call_tool(tool_name, tool_args)
                    tool_msg = ToolMessage(
                        content=json.dumps(tool_output, default=str),
                        tool_call_id=tc["id"],
                    )
                    messages.append(tool_msg)

            # Parse final AIMessage into a TaskResult
            result = self._parse_result(task_id, messages, tool_calls_made)

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

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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
        """Invoke a tool by name. Raises if tool not found."""
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
        """
        Extract the final AI response and the most recent tool outputs to build a TaskResult.
        The LLM provides the summary; numbers come from tool ToolMessages.
        """
        # Gather the last AI message for the summary
        final_ai_msg = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)),
            None,
        )
        summary = final_ai_msg.content if final_ai_msg and isinstance(final_ai_msg.content, str) else ""

        # Collect the last ToolMessage from each unique tool call to grab metrics/data_refs
        metrics: dict[str, Any] = {}
        data_ref: str | None = None
        warnings: list[str] = []

        for msg in messages:
            if isinstance(msg, ToolMessage):
                try:
                    payload = json.loads(msg.content)
                    if isinstance(payload, dict):
                        # Grab data_ref from the most recent tool output that has one
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
