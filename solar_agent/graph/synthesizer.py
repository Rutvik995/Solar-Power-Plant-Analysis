"""
graph/synthesizer.py – Synthesizer node.

Produces the final human-readable answer from the completed TaskResults.

Design constraints:
  - The LLM writes the narrative. Numbers come ONLY from tool outputs (TaskResult.metrics).
  - The synthesizer receives a flat summary of all completed task results and
    constructs a grounded, cited answer.
  - Warnings from tools are included in the answer when present.
  - If validation failed (status=FAIL), the synthesizer surfaces a partial answer
    with clear caveats.
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from solar_agent.state import AgentState, TaskStatus, ValidationStatus

logger = logging.getLogger(__name__)

_SYNTHESIZER_SYSTEM_PROMPT = """You are the Synthesizer for a Solar Power Plant Analysis system.
Your job is to write a clear, grounded, human-readable answer to the user's question.

RULES:
1. Use ONLY the numbers provided to you in the task summaries below. DO NOT invent or estimate any numbers.
2. Every numeric claim must cite which task produced it (e.g. "Performance task: PR = 0.866").
3. Group findings logically: Performance → Faults → Environmental.
4. If any task produced warnings, mention them clearly.
5. If results are partial (some tasks failed), state clearly what could not be determined.
6. Keep the answer concise and actionable. Avoid technical jargon where possible.
7. If you have no data (e.g. all tasks failed), say so plainly.
"""


class Synthesizer:
    """LangGraph node that composes the final answer from TaskResults."""

    def __init__(self, llm):
        self._llm = llm

    def synthesize(self, state: AgentState) -> dict:
        """
        Build the final answer and return a state patch with `final_answer`.
        """
        query = state.get("user_query", "")
        results = state.get("results") or {}
        plan = state.get("plan")
        validation = state.get("validation")
        assumptions = state.get("assumptions") or []

        # Build a structured summary of all task outputs for the LLM
        task_summaries = []
        for task in (plan.tasks if plan else []):
            tr = results.get(task.id)
            if tr is None:
                continue
            entry = {
                "task_id": task.id,
                "agent": task.agent,
                "status": tr.status,
                "summary": tr.summary or "(no summary)",
                "metrics": tr.metrics or {},
            }
            task_summaries.append(entry)

        # Gather all tool warnings
        all_warnings = []
        for tr in results.values():
            if tr and tr.status == TaskStatus.DONE:
                pass  # warnings are in tool payloads, already summarised by agent

        # Partial results caveat
        caveat = ""
        if validation and validation.status == ValidationStatus.FAIL:
            failed = [
                i.description for i in validation.issues if i.severity == "error"
            ]
            caveat = (
                "\n\nNOTE: Some tasks failed and results are partial:\n"
                + "\n".join(f"  - {d}" for d in failed)
            )

        human_text = (
            f"User question: {query}\n\n"
            f"Task results (JSON):\n{json.dumps(task_summaries, indent=2, default=str)}"
        )
        if assumptions:
            human_text += "\n\nAssumptions made:\n" + "\n".join(f"  - {a}" for a in assumptions)
        if caveat:
            human_text += caveat

        try:
            response = self._llm.invoke([
                SystemMessage(content=_SYNTHESIZER_SYSTEM_PROMPT),
                HumanMessage(content=human_text),
            ])
            answer = response.content
        except Exception as exc:
            logger.exception("Synthesizer LLM call failed: %s", exc)
            # Fallback: dump raw metrics
            lines = [f"Answer generation failed ({exc}). Raw task outputs:"]
            for ts in task_summaries:
                lines.append(f"  [{ts['task_id']} / {ts['agent']}] {ts['summary']}")
                for k, v in ts["metrics"].items():
                    lines.append(f"    {k}: {v}")
            answer = "\n".join(lines)

        return {"final_answer": answer}
