"""
graph/synthesizer.py – Synthesizer node.

Produces the final human-readable answer from the completed TaskResults.

Answer format (enforced via system prompt):
  1. SHORT ANSWER  – 1-3 sentence direct answer to the user's question.
  2. SUPPORTING TABLE (if applicable) – compact Markdown table of top entities.
  3. ASSUMPTIONS & WARNINGS – any caveats, data-range limits, tool warnings.

Design constraints:
  - The LLM writes the narrative. Numbers come ONLY from tool outputs (TaskResult.metrics).
  - Ranking tables are constructed from compact_table_summary fields, not raw DataFrames.
  - If validation failed (status=FAIL), a partial answer is surfaced with clear caveats.
"""

from __future__ import annotations

import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage

from solar_agent.graph.data_store import DataStore
from solar_agent.state import AgentState, TaskStatus, ValidationStatus

logger = logging.getLogger(__name__)

_SYNTHESIZER_SYSTEM_PROMPT = """You are the Synthesizer for a Solar Power Plant Analysis system.
Your job is to write a clear, grounded, human-readable answer to the user's question.

ANSWER FORMAT (always follow this structure):
## Short Answer
1-3 sentences directly answering the question. State the key finding.

## Supporting Detail
- Use a Markdown table when the answer involves ranking or comparing multiple entities.
- For each numeric value, cite the task that produced it in parentheses, e.g. (performance task).
- Do NOT write prose paragraphs; use bullet points or a table.

## Assumptions & Warnings
- List any date clamping, missing data, or tool warnings.
- If results are partial (some tasks failed), state clearly what could not be determined.

RULES:
1. Use ONLY the numbers provided in the task summaries below. DO NOT invent or estimate any numbers.
2. Every numeric claim must cite which task produced it.
3. If you have no data (e.g. all tasks failed), say so plainly.
4. Keep the full answer under 300 words.
"""


def _build_compact_table(data_ref: str, key_cols: list[str], value_col: str, top_n: int = 10) -> str:
    """
    Build a compact Markdown table from a DataStore DataFrame for LLM context.
    Returns at most top_n rows. Returns empty string if data unavailable.
    """
    try:
        df = DataStore.get_or_load(data_ref)
        if df is None or df.empty:
            return ""
        cols = [c for c in key_cols + [value_col] if c in df.columns]
        if not cols:
            return ""
        top = df.nsmallest(top_n, value_col) if value_col in df.columns else df.head(top_n)
        return top[cols].to_markdown(index=False)
    except Exception as exc:
        logger.debug("Could not build compact table: %s", exc)
        return ""


class Synthesizer:
    """LangGraph node that composes the final answer from TaskResults."""

    def __init__(self, llm):
        self._llm = llm

    def synthesize(self, state: AgentState, run_id: str = "__default__") -> dict:
        """
        Build the final answer and return a state patch with `final_answer`.
        """
        query = state.get("user_query", "")
        results = state.get("results") or {}
        plan = state.get("plan")
        validation = state.get("validation")
        assumptions = state.get("assumptions") or []

        # Build structured task summaries for the LLM
        task_summaries = []
        table_context = []

        for task in (plan.tasks if plan else []):
            tr = results.get(task.id)
            if tr is None:
                continue

            entry = {
                "task_id": task.id,
                "agent": task.agent,
                "status": str(tr.status),
                "summary": tr.summary or "(no summary)",
                "metrics": tr.metrics or {},
            }
            task_summaries.append(entry)

            # For ranking/performance tasks, build a compact table from the data_ref
            if tr.status == TaskStatus.DONE and tr.data_ref and task.agent == "performance":
                table_md = _build_compact_table(
                    tr.data_ref,
                    key_cols=["inverter_id", "block_id", "plant_id"],
                    value_col="performance_ratio",
                    top_n=10,
                )
                if table_md:
                    table_context.append(
                        f"### Ranking table from task {task.id} (performance agent):\n{table_md}"
                    )

        # Partial results caveat
        caveat = ""
        if validation and validation.status == ValidationStatus.FAIL:
            failed_desc = [i.description for i in validation.issues if i.severity == "error"]
            caveat = (
                "\n\nNOTE: Some tasks failed — results are partial:\n"
                + "\n".join(f"  - {d}" for d in failed_desc)
            )

        human_text = (
            f"User question: {query}\n\n"
            f"Task results:\n{json.dumps(task_summaries, indent=2, default=str)}"
        )
        if table_context:
            human_text += "\n\nData tables for reference:\n" + "\n\n".join(table_context)
        if assumptions:
            human_text += "\n\nAssumptions made during data resolution:\n" + "\n".join(f"  - {a}" for a in assumptions)
        if caveat:
            human_text += caveat

        from solar_agent.graph.validator import _extract_numbers, _numbers_from_results, _is_grounded

        def _generate():
            resp = self._llm.invoke([
                SystemMessage(content=_SYNTHESIZER_SYSTEM_PROMPT),
                HumanMessage(content=human_text),
            ], run_id=run_id)
            return resp.content

        def _fallback(task_summaries):
            lines = ["Here are the raw metrics (fallback due to generation failure or ungrounded numbers):"]
            for ts in task_summaries:
                lines.append(f"\n**[{ts['task_id']} / {ts['agent']}]** {ts['summary']}")
                for k, v in ts["metrics"].items():
                    lines.append(f"  - {k}: {v}")
            return "\n".join(lines)

        try:
            answer = _generate()
            # Validator Grounding
            ground_values = _numbers_from_results(results)
            for table_md in table_context:
                ground_values.extend(_extract_numbers(table_md))

            answer_numbers = _extract_numbers(answer)
            ungrounded = [n for n in answer_numbers if not _is_grounded(n, ground_values)]
            
            if ungrounded:
                logger.warning(f"Synthesizer answer ungrounded numbers: {ungrounded}. Regenerating once...")
                human_text += f"\n\nCRITICAL FIX: Your last answer hallucinates these numbers: {ungrounded}. Regenerate using ONLY the provided metrics."
                answer = _generate()
                # Check again
                answer_numbers2 = _extract_numbers(answer)
                ungrounded2 = [n for n in answer_numbers2 if not _is_grounded(n, ground_values)]
                if ungrounded2:
                    logger.error(f"Synthesizer answer STILL ungrounded: {ungrounded2}. Using templated fallback.")
                    answer = _fallback(task_summaries)
        except Exception as exc:
            logger.exception("Synthesizer LLM call failed: %s", exc)
            answer = _fallback(task_summaries)

        return {"final_answer": answer}
