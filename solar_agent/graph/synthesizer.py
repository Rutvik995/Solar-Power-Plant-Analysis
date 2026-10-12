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
- Use a Markdown table when the answer involves ranking, comparison, faults, or soiling.
- Do NOT attach per-number source citations like (t1) or (t2) inline. Source tasks will be presented in a single Sources footer.
- Do NOT write prose paragraphs; use bullet points or a table.

## Assumptions & Warnings
- List any date clamping, missing data, or tool warnings.
- State data availability, e.g. "Data available 2026-08-01 to 2026-09-26".

RULES:
1. Use ONLY the numbers provided in the task summaries and tables below. DO NOT invent or estimate any numbers.
2. If you have no data (e.g. all tasks failed), say so plainly.
3. Keep the full answer under 300 words.
4. When reporting multi-plant or comparison results, present each plant individually using its plant-specific metrics (plant_{id}_*). Never describe an aggregate multi-plant result as a single plant or as 'the plant in question'.
5. When reporting faults:
   - Name the specific inverter IDs, dates, status codes, PR, and the exact flagging tool behind each conclusion.
   - Include the status-code meaning from status_codes.yaml (e.g. Warning, Fault, Offline).
   - State the same-day PR and irradiance of the other inverters in that block for comparison.
   - Include a sentence on any statistical anomalies found, or state "none".
   - Include the compact table of flagged items.
6. When reporting soiling results per plant: Present cleaning events, soiling ratio min/avg, and loss % per plant in a Markdown table. You MUST mention any outlier plants with more than 2x the median loss.
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

            # For fault tasks, build compact table of flagged items (max 10 rows)
            if task.agent == "fault":
                flagged = (tr.metrics or {}).get("flagged_items") or []
                if flagged:
                    import pandas as pd
                    f_df = pd.DataFrame(flagged[:10])
                    cols = [
                        c
                        for c in [
                            "inverter_id",
                            "block",
                            "date",
                            "status_code",
                            "status_meaning",
                            "pr",
                            "peer_pr",
                            "peer_irradiance",
                            "flagging_tool",
                        ]
                        if c in f_df.columns
                    ]
                    table_md = f_df[cols].to_markdown(index=False)
                    table_context.append(
                        f"### Flagged fault items from task {task.id} (fault agent):\n{table_md}"
                    )
                anomaly_sent = (tr.metrics or {}).get("anomaly_sentence")
                if anomaly_sent:
                    table_context.append(
                        f"### Anomaly findings from task {task.id} (fault agent):\n{anomaly_sent}"
                    )

            # For environment soiling tasks, build soiling table per plant
            if task.agent == "environment":
                metrics = tr.metrics or {}
                import pandas as pd
                pids = sorted(list({int(k.split("_")[1]) for k in metrics if k.startswith("plant_") and k.split("_")[1].isdigit()}))
                if pids:
                    soiling_rows = []
                    for pid in pids:
                        soiling_rows.append({
                            "Plant": f"Plant {pid}",
                            "Cleaning events": metrics.get(f"plant_{pid}_cleaning_events", 0),
                            "Soiling ratio min": metrics.get(f"plant_{pid}_soiling_ratio_min", "-"),
                            "Soiling ratio avg": metrics.get(f"plant_{pid}_soiling_ratio_avg", "-"),
                            "Loss %": f"{metrics.get(f'plant_{pid}_soiling_loss_pct', 0)}%",
                            "Loss (kWh)": f"{metrics.get(f'plant_{pid}_soiling_loss_kwh', 0):,}",
                        })
                    s_df = pd.DataFrame(soiling_rows)
                    table_context.append(
                        f"### Soiling analysis per plant from task {task.id} (environment agent):\n{s_df.to_markdown(index=False)}"
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

        # Clean inline (t1), (t2) tags from narrative
        import re
        answer = re.sub(
            r"\s*\((?:t\d+(?:,\s*t\d+)*|task\s*t\d+|performance task|fault task|environment task|data task)\)",
            "",
            answer,
        )

        # Ensure fault anomaly sentence is included if present
        for task in (plan.tasks if plan else []):
            if task.agent == "fault":
                tr = results.get(task.id)
                if tr:
                    anomaly_sent = (tr.metrics or {}).get("anomaly_sentence")
                    if anomaly_sent and "anomal" not in answer.lower():
                        answer += f"\n\n{anomaly_sent}"

        # Build deterministic Sources footer
        completed_tasks = [
            task.id
            for task in (plan.tasks if plan else [])
            if task.id in results and getattr(results[task.id], "status", None) == TaskStatus.DONE
        ]
        if not completed_tasks:
            completed_tasks = [
                k for k, v in results.items() if getattr(v, "status", None) == TaskStatus.DONE
            ]
        if not completed_tasks:
            completed_tasks = [task.id for task in (plan.tasks if plan else [])]
        sources_footer = f"Sources: {', '.join(completed_tasks)}" if completed_tasks else "Sources: none"

        # Collect assumptions deterministically
        all_assumptions: list[str] = list(state.get("assumptions") or [])
        try:
            from solar_agent.db import queries as q
            min_d = q.get_min_log_date()
            max_d = q.get_max_log_date()
            avail_line = f"Data available {min_d.isoformat()} to {max_d.isoformat()}"
        except Exception:
            avail_line = "Data available 2026-08-01 to 2026-09-26"

        if not any("Data available" in a for a in all_assumptions):
            all_assumptions.insert(0, avail_line)

        # Collect all warnings deterministically
        all_warnings: list[str] = list(state.get("warnings") or [])
        for tr in results.values():
            if hasattr(tr, "warnings") and tr.warnings:
                for w in tr.warnings:
                    if w not in all_warnings:
                        all_warnings.append(w)

        # Strip any LLM-written Assumptions, Warnings, or Sources headers
        if "## Assumptions & Warnings" in answer:
            answer = answer.split("## Assumptions & Warnings")[0].rstrip()
        elif "## Assumptions" in answer:
            answer = answer.split("## Assumptions")[0].rstrip()
        if "## Warnings" in answer:
            answer = answer.split("## Warnings")[0].rstrip()
        if "\nSources:" in answer:
            answer = answer.split("\nSources:")[0].rstrip()
        elif "\n## Sources" in answer:
            answer = answer.split("\n## Sources")[0].rstrip()

        assumptions_section = "\n\n## Assumptions\n" + "\n".join(f"- {a}" for a in all_assumptions)
        if all_warnings:
            warnings_section = "\n\n## Warnings\n" + "\n".join(f"- {w}" for w in all_warnings)
        else:
            warnings_section = "\n\n## Warnings\nNone"

        # Deterministic Period analysed line computed from data
        import pandas as pd
        from datetime import date

        dates_found: list[date] = []
        for task in (plan.tasks if plan else []):
            tr = results.get(task.id)
            if tr and tr.data_ref:
                try:
                    df = DataStore.get_or_load(tr.data_ref)
                    if df is not None and not df.empty:
                        for col in ["log_date", "date"]:
                            if col in df.columns:
                                d_series = pd.to_datetime(df[col]).dropna()
                                if not d_series.empty:
                                    dates_found.extend(d_series.dt.date.tolist())
                except Exception:
                    pass

        if not dates_found and plan and plan.scope and plan.scope.start_date and plan.scope.end_date:
            dates_found = [plan.scope.start_date, plan.scope.end_date]

        if not dates_found:
            try:
                from solar_agent.db import queries as q
                dates_found = [q.get_min_log_date(), q.get_max_log_date()]
            except Exception:
                dates_found = [date(2026, 9, 1), date(2026, 9, 26)]

        min_d_used = min(dates_found)
        max_d_used = max(dates_found)
        n_days_used = (max_d_used - min_d_used).days + 1
        period_line = f"Period analysed: {min_d_used.isoformat()} to {max_d_used.isoformat()} of the data used ({n_days_used} days)"

        final_ans = (
            answer.strip()
            + f"\n\n{sources_footer}"
            + assumptions_section
            + warnings_section
            + f"\n\n{period_line}"
        )
        return {"final_answer": final_ans, "warnings": all_warnings}
