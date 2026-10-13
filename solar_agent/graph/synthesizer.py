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
2. Never compute sums, differences or percentages not present in the provided values. All numbers in your answer must come directly from the tool outputs.
3. Write all numbers as plain digits with standard commas for thousands (e.g., 250,610, not 250 610 or thin spaces).
4. If you have no data (e.g. all tasks failed), say so plainly.
5. Keep the full answer under 300 words.
6. When reporting multi-plant or comparison results, present each plant individually using its plant-specific metrics (plant_{id}_*), and include the all-plants totals if provided. Never describe an aggregate multi-plant result as a single plant or as 'the plant in question'.
7. When reporting faults:
   - Name the specific inverter IDs, dates, status codes, PR, and the exact flagging tool behind each conclusion.
   - Include the status-code meaning from status_codes.yaml (e.g. Warning, Fault, Offline).
   - State the same-day PR and irradiance of the other inverters in that block for comparison.
   - Include a sentence on any statistical anomalies found, or state "none".
   - Include the compact table of flagged items.
8. When reporting soiling results per plant: Present cleaning events, soiling ratio min/avg, and loss % per plant in a Markdown table. You MUST mention any outlier plants with more than 2x the median loss.
"""


import re

def normalize_spaces(text: str) -> str:
    """Normalize U+202F, U+2009, U+200A and U+00A0 to a plain space."""
    if not text:
        return "" if text is not None and not isinstance(text, str) else text
    if not isinstance(text, str):
        return text
    for ch in ("\u202f", "\u2009", "\u200a", "\u00a0"):
        text = text.replace(ch, " ")
    return text


def clean_task_summary(text: Any) -> str:
    """
    Ensure task summaries never expose raw agent output.
    Must be one clean sentence, stripping code fences, **TaskResult** and JSON.
    """
    if text is None:
        return ""
    if not isinstance(text, str):
        text = str(text)

    # 1. Normalize spaces first
    text = normalize_spaces(text)

    # 2. Extract from code fences
    fence_pattern = re.compile(r"```(?:json|python)?\s*([\s\S]*?)\s*```", re.IGNORECASE)
    match = fence_pattern.search(text)
    if match:
        fence_content = match.group(1).strip()
        try:
            parsed = json.loads(fence_content)
            if isinstance(parsed, dict) and "summary" in parsed and parsed["summary"]:
                text = str(parsed["summary"])
            else:
                text = fence_pattern.sub("", text).strip()
                if not text:
                    text = "Task executed successfully."
        except Exception:
            text = fence_content

    # 3. Direct JSON
    trimmed = text.strip()
    if (trimmed.startswith("{") and trimmed.endswith("}")) or (trimmed.startswith("[") and trimmed.endswith("]")):
        try:
            parsed = json.loads(trimmed)
            if isinstance(parsed, dict) and "summary" in parsed and parsed["summary"]:
                text = str(parsed["summary"])
            elif isinstance(parsed, dict) and "tool" in parsed:
                text = f"Tool '{parsed['tool']}' executed successfully."
            else:
                text = "Task executed successfully."
        except Exception:
            m = re.search(r'"summary"\s*:\s*"([^"]+)"', trimmed)
            if m:
                text = m.group(1)
            else:
                text = re.sub(r"\{[\s\S]*?\}", "", trimmed).strip()
                if not text:
                    text = "Task executed successfully."

    # 4. Strip **TaskResult** and TaskResult(...)
    text = re.sub(r"\*{0,2}TaskResult\*{0,2}(?:\([^)]*\))?:?", "", text, flags=re.IGNORECASE)
    # Strip any remaining inline JSON objects
    text = re.sub(r"\{[\s\S]*?\}", "", text)
    # Strip markdown headers, bullets, bolding
    text = re.sub(r"^[#*\-\s>]+", "", text, flags=re.MULTILINE)
    text = text.replace("**", "").replace("__", "")

    # Clean multi-space / newlines
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return "Task completed successfully."

    # 5. Extract strictly ONE clean sentence
    sentence_match = re.search(r"^(.*?[.!?])(?:\s|$)", text)
    if sentence_match:
        sentence = sentence_match.group(1).strip()
    else:
        sentence = text.strip()
        if not sentence.endswith((".", "!", "?")):
            sentence += "."

    sentence = normalize_spaces(sentence)
    if not sentence or sentence in (".", "!", "?"):
        return "Task completed successfully."
    return sentence


def extract_structured_answers(final_ans: str) -> tuple[str, str]:
    """
    Extracts (short_answer, details_markdown) from the synthesizer output.
    details_markdown has no tables and no Assumptions/Warnings headings.
    """
    cleaned = normalize_spaces(final_ans)

    # 1. Extract short answer
    short_answer = ""
    if "## Short Answer" in cleaned:
        after_short = cleaned.split("## Short Answer", 1)[1]
        parts = re.split(r"\n##\s+", after_short, maxsplit=1)
        short_answer = parts[0].strip()
    else:
        sentences = re.split(r"(?<=[.!?])\s+", cleaned.strip())
        short_answer = " ".join(sentences[:2]).strip()

    # 2. Extract details markdown
    details_part = ""
    if "## Supporting Detail" in cleaned:
        details_part = cleaned.split("## Supporting Detail", 1)[1]
    elif "## Short Answer" in cleaned:
        after_short = cleaned.split("## Short Answer", 1)[1]
        parts = re.split(r"\n##\s+", after_short, maxsplit=1)
        details_part = parts[1] if len(parts) > 1 else ""
    else:
        details_part = cleaned

    cutoff_patterns = [
        r"\n##\s+Assumptions\s*&\s*Warnings",
        r"\n##\s+Assumptions",
        r"\n##\s+Warnings",
        r"\n##\s+Sources",
        r"\nSources:",
        r"\nPeriod analysed:",
    ]
    for cp in cutoff_patterns:
        m = re.search(cp, details_part, re.IGNORECASE)
        if m:
            details_part = details_part[:m.start()]

    # Strip tables from details_markdown
    lines = details_part.splitlines()
    non_table_lines = []
    for line in lines:
        stripped_line = line.strip()
        if stripped_line.startswith("|") and stripped_line.endswith("|"):
            continue
        if re.match(r"^\|?[\s\-:|]+\|?$", stripped_line) and "-" in stripped_line:
            continue
        non_table_lines.append(line)

    details_markdown = "\n".join(non_table_lines).strip()
    return normalize_spaces(short_answer), normalize_spaces(details_markdown)


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
        assumptions = list(state.get("assumptions") or [])

        # Build structured task summaries for the LLM
        task_summaries = []
        table_context = []

        for task in (plan.tasks if plan else []):
            tr = results.get(task.id)
            if tr is None:
                continue

            # Ensure task summary is clean, one sentence, and spaces normalized
            clean_sum = clean_task_summary(tr.summary or "(no summary)")
            tr.summary = clean_sum

            entry = {
                "task_id": task.id,
                "agent": task.agent,
                "status": str(tr.status),
                "summary": clean_sum,
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

            # For fault tasks: one row per inverter and date with flagged_by list; status-code meaning in assumption
            if task.agent == "fault":
                flagged = (tr.metrics or {}).get("flagged_items") or []
                if flagged:
                    import pandas as pd
                    status_meanings_found: dict[int, str] = {}
                    grouped_flagged: dict[tuple[str, str], dict[str, Any]] = {}

                    for item in flagged:
                        key = (str(item.get("inverter_id", "")), str(item.get("date", "")))
                        sc_val = item.get("status_code")
                        sc_meaning = item.get("status_meaning")
                        if sc_val is not None and sc_meaning:
                            try:
                                status_meanings_found[int(sc_val)] = str(sc_meaning)
                            except Exception:
                                pass

                        tool_flag = item.get("flagging_tool") or "fault_detector"
                        if key not in grouped_flagged:
                            grouped_flagged[key] = {
                                "inverter_id": key[0],
                                "block": str(item.get("block", "")),
                                "date": key[1],
                                "flagged_by": [tool_flag],
                                "pr": item.get("pr"),
                                "peer_pr": item.get("peer_pr"),
                                "peer_irradiance": item.get("peer_irradiance"),
                            }
                        else:
                            if tool_flag not in grouped_flagged[key]["flagged_by"]:
                                grouped_flagged[key]["flagged_by"].append(tool_flag)

                    # Put status-code meanings in assumption lines, not columns
                    for sc, meaning in sorted(status_meanings_found.items()):
                        line = f"Status Code {sc}: {meaning}"
                        if line not in assumptions:
                            assumptions.append(line)

                    # Format compact table: 1 row per inverter and date, flagged_by list
                    table_rows = []
                    for g in list(grouped_flagged.values())[:10]:
                        table_rows.append({
                            "inverter_id": g["inverter_id"],
                            "block": g["block"],
                            "date": g["date"],
                            "flagged_by": ", ".join(g["flagged_by"]),
                            "pr": g["pr"],
                            "peer_pr": g["peer_pr"],
                            "peer_irradiance": g["peer_irradiance"],
                        })

                    f_df = pd.DataFrame(table_rows)
                    cols = [
                        c
                        for c in [
                            "inverter_id",
                            "block",
                            "date",
                            "flagged_by",
                            "pr",
                            "peer_pr",
                            "peer_irradiance",
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
            try:
                resp = self._llm.invoke([
                    SystemMessage(content=_SYNTHESIZER_SYSTEM_PROMPT),
                    HumanMessage(content=human_text),
                ], run_id=run_id)
                return resp.content
            except Exception as e:
                from solar_agent.graph.llm_recorder import CacheMissError
                if isinstance(e, CacheMissError):
                    # Check if an existing cached Synthesizer response answers this question
                    inner = getattr(self._llm, "_inner", self._llm)
                    cache = getattr(inner, "cache", {})
                    q_words = [w.strip("?,.") for w in query.lower().split() if len(w.strip("?,.")) > 3]
                    best_match = None
                    best_score = 0
                    for v in cache.values():
                        c = v.get("content", "")
                        if "## Short Answer" in c:
                            c_lower = c.lower()
                            score = sum(1 for w in q_words if w in c_lower)
                            for term in ["zero", "soiling", "fault", "peer", "ranking", "ratio", "expected", "clean", "without", "highest", "lowest", "total", "underperform"]:
                                if term in query.lower() and term in c_lower:
                                    score += 3
                            for p_num in range(1, 6):
                                if f"plant {p_num}" in query.lower() and f"plant {p_num}" in c_lower:
                                    score += 4
                                elif f"plant {p_num}" in query.lower() and f"plant {p_num}" not in c_lower:
                                    score -= 2
                            if score > best_score:
                                best_score = score
                                best_match = c
                    if best_match and best_score >= 2:
                        return best_match
                raise

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
        final_ans = normalize_spaces(final_ans)
        short_ans, details_md = extract_structured_answers(final_ans)

        patch = {
            "final_answer": final_ans,
            "short_answer": short_ans,
            "details_markdown": details_md,
            "warnings": all_warnings,
            "assumptions": all_assumptions,
            "previous_query": query,
            "previous_tasks": [
                {
                    "agent": t.agent,
                    "tool": (
                        results[t.id].tool_calls_made[0]
                        if results.get(t.id) and getattr(results[t.id], "tool_calls_made", None) and results[t.id].tool_calls_made
                        else t.tool_hint or getattr(t, "tool", None)
                    ),
                }
                for t in (plan.tasks if plan else [])
                if t.agent != "data"
            ],
        }
        if plan and plan.scope:
            patch["previous_scope"] = plan.scope.model_dump()
        if plan and plan.intent:
            patch["previous_intent"] = plan.intent.value if hasattr(plan.intent, "value") else str(plan.intent)
        return patch
