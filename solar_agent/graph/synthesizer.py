"""
graph/synthesizer.py – Synthesizer node.

Produces the final human-readable answer from completed TaskResults.
Synthesizer output is structured JSON:
  - headline: one bold sentence with the answer, plain English.
  - explanation: 1 to 2 sentences, no jargon, explaining what it means.
  - key_points: 0 to 3 short bullets.
  - notes: plain-language assumptions.
Grounding checks apply to all numbers in these fields.

Rules:
  - Tables built only from analysis task outputs (performance, fault, environment).
  - No technical details in answer fields (row counts, task IDs, tool names, data_ref, call counts).
  - Fallback is deterministic: templated headline and formatted table from step 1 (never raw metrics or the word 'fallback').
  - Replay cache miss returns error_code='not_recorded' with clear message.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime
from typing import Any, Optional

from langchain_core.messages import HumanMessage, SystemMessage

from solar_agent.graph.data_store import DataStore
from solar_agent.graph.llm_recorder import CacheMissError
from solar_agent.graph.validator import _extract_numbers, _is_grounded, _numbers_from_results
from solar_agent.state import AgentState, TaskStatus, ValidationStatus
from solar_agent.tools.table_formatter import (
    build_analysis_tables,
    format_energy_value,
    format_pr_value,
    sanitize_answer_text,
    tables_to_markdown,
    translate_plain_language,
)

logger = logging.getLogger(__name__)

_SYNTHESIZER_SYSTEM_PROMPT = """You are the Synthesizer for a Solar Power Plant Analysis system.
Your job is to provide a clear, grounded, human-readable answer to the user's question.

You MUST respond strictly with a valid JSON object matching this schema:
{
  "headline": "**One bold sentence directly answering the question in plain English.**",
  "explanation": "1 to 2 plain English sentences explaining what the result means without technical jargon.",
  "key_points": [
    "0 to 3 short bullet points highlighting key observations"
  ],
  "notes": [
    "Plain-language assumptions, scope notes, or data limits"
  ]
}

RULES:
1. headline: Exactly ONE bold sentence wrapped in **...**. Directly answer the question.
2. explanation: 1 to 2 sentences in plain English, explaining what it means. No technical jargon.
3. key_points: 0 to 3 short bullet points.
4. notes: Plain-language assumptions.
5. NO TECHNICAL DETAILS IN ANSWER FIELDS: NEVER include row counts, task IDs (like t1, t2), tool names (like fetch_inverter_daily, compute_kpis_tool), data_ref, or call counts in headline, explanation, key_points, or notes. Those belong exclusively in the execution trace.
6. Use ONLY the numbers provided in the task summaries and tables below. DO NOT invent or estimate any numbers. Every number must come directly from tool outputs.
7. Write all numbers as plain digits with standard commas for thousands (e.g., 250,610).
8. Report PR as a percentage (e.g. 82.5%).
9. FAULT EXPLANATIONS: State ONLY what the data explicitly shows (status code, zero power output, how peers performed). NEVER state or invent a root cause (e.g. "because of high temperature", "due to overheating", "communication glitch", "minor grid deviation") unless a tool output specifically supports it. Status code descriptions only list examples of possible warnings, NOT confirmed causes. Use "the data doesn't show the exact cause".
10. PEER & WEATHER CLAIMS: If peer count is 0 or peer fields are null/missing ("n/a"), make NO claims about peer performance or weather conditions (never claim "no sunlight" or "normal weather"). If peer irradiance is 0 or null, never claim there was no sunlight or a measurement issue.
"""


def normalize_spaces(text: str) -> str:
    """Normalize U+202F, U+2009, U+200A and U+00A0 to a plain space."""
    if not text:
        return "" if text is not None and not isinstance(text, str) else text
    if not isinstance(text, str):
        text = str(text)
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
                text = f"Tool executed successfully."
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
    text = re.sub(r"\{[\s\S]*?\}", "", text)
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


def build_deterministic_fallback(
    query: str,
    plan: Any,
    results: dict[str, Any],
    assumptions: list[str],
) -> tuple[str, str, list[str], list[str], str]:
    """
    Build a deterministic answer on failure:
    - A templated headline (one bold sentence in plain English)
    - Explanation (1-2 sentences)
    - Key points (0-3 short bullets)
    - Notes (plain-language assumptions)
    - Formatted tables from step 1
    Never outputs raw metrics or the phrase 'fallback'.
    Never includes row counts, task IDs, tool names, data_ref, or call counts in answer fields.
    """
    pr_vals: list[float] = []
    yield_vals: list[float] = []
    flagged_count = 0
    plant_names: list[str] = []

    if plan and hasattr(plan, "scope") and plan.scope and plan.scope.plant_ids:
        plant_names = [f"Plant {pid}" for pid in plan.scope.plant_ids]
    plant_str = ", ".join(plant_names) if plant_names else "the requested solar plants"

    for tid, tr in results.items():
        if not tr or getattr(tr, "status", None) != TaskStatus.DONE:
            continue
        metrics = getattr(tr, "metrics", {}) or {}
        for k, v in metrics.items():
            if ("performance_ratio" in k or k == "pr") and isinstance(v, (int, float)):
                pr_vals.append(float(v))
            if ("yield" in k or "energy" in k) and isinstance(v, (int, float)):
                yield_vals.append(float(v))
    all_flagged_items = []
    for t_id, tr in results.items():
        if tr and getattr(tr, "metrics", None):
            f_items = tr.metrics.get("flagged_items") or []
            all_flagged_items.extend(f_items)

    # 1. Templated headline
    if pr_vals and yield_vals:
        pr_disp = format_pr_value(pr_vals[0])
        yield_disp = format_energy_value(yield_vals[0])
        headline = f"**{plant_str} produced {yield_disp} with a performance ratio of {pr_disp}.**"
    elif pr_vals:
        pr_disp = format_pr_value(pr_vals[0])
        headline = f"**{plant_str} achieved a performance ratio of {pr_disp} across the evaluated period.**"
    elif yield_vals:
        yield_disp = format_energy_value(yield_vals[0])
        headline = f"**{plant_str} produced {yield_disp} of electrical energy during this period.**"
    elif flagged_count > 0:
        if all_flagged_items and all_flagged_items[0].get("inverter_id"):
            f_inv = all_flagged_items[0].get("inverter_id")
            f_dt = all_flagged_items[0].get("date")
            headline = f"**Inverter {f_inv} produced no power on {f_dt}.**"
        else:
            headline = f"**Fault analysis identified {flagged_count} inverter events requiring inspection.**"
    else:
        headline = f"**Analysis completed for {plant_str}.**"

    # 2. Explanation
    if all_flagged_items:
        f = all_flagged_items[0]
        f_inv = f.get("inverter_id")
        f_sc = f.get("status_code", 0)
        f_peer_pr = f.get("peer_pr")
        f_peer_irr = f.get("peer_irradiance")
        f_peer_cnt = f.get("peer_count", 1 if f_peer_pr is not None else 0)
        detail_parts = [f"Inverter {f_inv} produced 0 kWh (status code {f_sc})"]
        if f_peer_cnt > 0 and f_peer_pr is not None:
            peer_pr_str = format_pr_value(f_peer_pr)
            detail_parts.append(f"while block peers achieved an average performance ratio of {peer_pr_str}")
            if f_peer_irr is not None and f_peer_irr > 0:
                detail_parts.append(f"under {f_peer_irr:.2f} kWh/m² irradiance")
        explanation = " ".join(detail_parts) + ". The data doesn't show the exact cause."
    else:
        explanation = f"Operational data and performance analysis have been compiled for {plant_str}."

    # 3. Key points
    key_points = []
    if pr_vals:
        key_points.append(f"Performance ratio: {format_pr_value(pr_vals[0])}")
    if yield_vals:
        key_points.append(f"Energy produced: {format_energy_value(yield_vals[0])}")
    if flagged_count > 0:
        key_points.append(f"Flagged inverter issues: {flagged_count}")

    # 4. Notes (translated plain-language assumptions)
    notes = [translate_plain_language(a) for a in assumptions if a]
    if not notes:
        notes = ["Data analysis is based on available telemetry records."]

    # 5. Formatted tables from step 1
    tables = build_analysis_tables(plan, results)
    table_md = tables_to_markdown(tables)

    # Clean narrative fields
    headline = sanitize_answer_text(headline)
    explanation = sanitize_answer_text(explanation)
    key_points = [sanitize_answer_text(kp) for kp in key_points]
    notes = [sanitize_answer_text(n) for n in notes]

    # Assemble final markdown
    final_parts = [headline, explanation]
    if key_points:
        final_parts.append("\n".join(f"- {kp}" for kp in key_points))
    if table_md:
        final_parts.append(table_md)
    final_answer = "\n\n".join(final_parts)

    return headline, explanation, key_points, notes, final_answer


class Synthesizer:
    """LangGraph node that composes structured JSON and final answer from TaskResults."""

    def __init__(self, llm):
        self._llm = llm

    def synthesize(self, state: AgentState, run_id: str = "__default__") -> dict:
        """
        Build structured JSON and final answer.
        Returns a state patch with headline, explanation, key_points, notes, tables, final_answer.
        """
        query = state.get("user_query", "")
        results = state.get("results") or {}
        plan = state.get("plan")
        validation = state.get("validation")
        assumptions = list(state.get("assumptions") or [])

        # Build analysis tables directly in code (step 1)
        analysis_tables = build_analysis_tables(plan, results)
        table_context_md = tables_to_markdown(analysis_tables)

        # Build structured task summaries for LLM context
        task_summaries = []
        for task in (plan.tasks if plan else []):
            tr = results.get(task.id)
            if tr is None:
                continue

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

        # Format fault status-code meanings into assumptions
        for task in (plan.tasks if plan else []):
            if task.agent == "fault":
                tr = results.get(task.id)
                flagged = (tr.metrics or {}).get("flagged_items") if tr else []
                if flagged:
                    for item in flagged:
                        sc_val = item.get("status_code")
                        sc_meaning = item.get("status_meaning")
                        if sc_val is not None and sc_meaning:
                            line = f"Status Code {sc_val}: {sc_meaning}"
                            if line not in assumptions:
                                assumptions.append(line)

        # Build human prompt
        human_text = (
            f"User question: {query}\n\n"
            f"Task results:\n{json.dumps(task_summaries, indent=2, default=str)}"
        )
        if table_context_md:
            human_text += "\n\nData tables for reference:\n" + table_context_md
        if assumptions:
            human_text += "\n\nAssumptions made during data resolution:\n" + "\n".join(
                f"  - {translate_plain_language(a)}" for a in assumptions
            )

        ground_values = _numbers_from_results(results)
        if table_context_md:
            ground_values.extend(_extract_numbers(table_context_md))

        def _generate() -> str:
            resp = self._llm.invoke([
                SystemMessage(content=_SYNTHESIZER_SYSTEM_PROMPT),
                HumanMessage(content=human_text),
            ], run_id=run_id)
            return resp.content

        def _parse_llm_json(raw_resp: str) -> Optional[dict]:
            if not raw_resp or not isinstance(raw_resp, str):
                return None
            txt = raw_resp.strip()
            # Strip markdown fences
            if txt.startswith("```"):
                txt = re.sub(r"^```(?:json)?\s*", "", txt)
                txt = re.sub(r"\s*```$", "", txt)
                txt = txt.strip()
            try:
                data = json.loads(txt)
                if isinstance(data, dict) and "headline" in data:
                    return data
            except Exception:
                pass
            # Try finding json object substring
            m = re.search(r"\{[\s\S]*\}", txt)
            if m:
                try:
                    data = json.loads(m.group(0))
                    if isinstance(data, dict) and "headline" in data:
                        return data
                except Exception:
                    pass
            return None

        headline: Optional[str] = None
        explanation: Optional[str] = None
        key_points: list[str] = []
        notes: list[str] = []
        final_answer_str: str = ""

        try:
            raw_output = _generate()
            parsed_data = _parse_llm_json(raw_output)

            if parsed_data:
                h_candidate = str(parsed_data.get("headline", "")).strip()
                # Ensure headline is wrapped in bold
                if not (h_candidate.startswith("**") and h_candidate.endswith("**")):
                    h_candidate = f"**{h_candidate.strip('*')}**"
                e_candidate = str(parsed_data.get("explanation", "")).strip()
                kp_candidate = [str(x) for x in parsed_data.get("key_points", []) if x]
                n_candidate = [str(x) for x in parsed_data.get("notes", []) if x]

                # Grounding check
                full_text_to_check = f"{h_candidate} {e_candidate} {' '.join(kp_candidate)} {' '.join(n_candidate)}"
                answer_numbers = _extract_numbers(full_text_to_check)
                ungrounded = [n for n in answer_numbers if not _is_grounded(n, ground_values)]

                if ungrounded:
                    logger.warning(f"Synthesizer ungrounded numbers: {ungrounded}. Regenerating once...")
                    retry_text = human_text + f"\n\nCRITICAL FIX: Your last answer contained ungrounded numbers: {ungrounded}. Regenerate using ONLY provided numbers."
                    resp2 = self._llm.invoke([
                        SystemMessage(content=_SYNTHESIZER_SYSTEM_PROMPT),
                        HumanMessage(content=retry_text),
                    ], run_id=run_id)
                    parsed_data2 = _parse_llm_json(resp2.content)
                    if parsed_data2:
                        h_candidate = str(parsed_data2.get("headline", "")).strip()
                        if not (h_candidate.startswith("**") and h_candidate.endswith("**")):
                            h_candidate = f"**{h_candidate.strip('*')}**"
                        e_candidate = str(parsed_data2.get("explanation", "")).strip()
                        kp_candidate = [str(x) for x in parsed_data2.get("key_points", []) if x]
                        n_candidate = [str(x) for x in parsed_data2.get("notes", []) if x]
                        full_check2 = f"{h_candidate} {e_candidate} {' '.join(kp_candidate)} {' '.join(n_candidate)}"
                        nums2 = _extract_numbers(full_check2)
                        ungrounded2 = [n for n in nums2 if not _is_grounded(n, ground_values)]
                        if ungrounded2:
                            logger.error(f"Still ungrounded: {ungrounded2}. Using deterministic fallback.")
                            parsed_data = None
                        else:
                            parsed_data = parsed_data2
                    else:
                        parsed_data = None

            if parsed_data:
                headline = h_candidate
                explanation = e_candidate
                key_points = kp_candidate
                notes = [translate_plain_language(n) for n in n_candidate]
            else:
                # Deterministic fallback on ungrounded or non-json failure
                headline, explanation, key_points, notes, final_answer_str = build_deterministic_fallback(
                    query, plan, results, assumptions
                )

        except CacheMissError:
            # Replay cache miss: return error_code='not_recorded' with clear message
            msg = "This query was not recorded in replay cache. Switch to live or record mode to execute."
            return {
                "error_code": "not_recorded",
                "error_message": msg,
                "final_answer": msg,
                "short_answer": msg,
                "headline": f"**{msg}**",
                "explanation": msg,
                "key_points": [],
                "notes": [],
                "tables": [],
                "assumptions": [],
                "warnings": [msg],
            }
        except Exception as exc:
            logger.exception("Synthesizer LLM execution failed: %s", exc)
            headline, explanation, key_points, notes, final_answer_str = build_deterministic_fallback(
                query, plan, results, assumptions
            )

        # Sanitize all answer fields to guarantee no technical details appear
        headline = sanitize_answer_text(headline or "")
        explanation = sanitize_answer_text(explanation or "")
        key_points = [sanitize_answer_text(kp) for kp in key_points]
        notes = [sanitize_answer_text(n) for n in notes]

        # Guard against speculative causes or incorrect peer/weather claims in fault explanations
        is_fault_query = any(task.agent == "fault" for task in (plan.tasks if plan else []))
        if is_fault_query:
            has_temp_evidence = False
            for task in (plan.tasks if plan else []):
                tr = results.get(task.id)
                if tr and tr.metrics:
                    if tr.metrics.get("max_module_temp_c", 0) > 75:
                        has_temp_evidence = True

            def _clean_fault_claims(text: str) -> str:
                if not text:
                    return text
                t = text
                if not has_temp_evidence:
                    t = re.sub(
                        r"(?:because\s+(?:it|of)|due to|suggesting)\s+(?:a\s+)?(?:high temperature|overheating|communication glitch|minor issue|minor grid deviation)[^\.,;]*",
                        "the data doesn't show the exact cause",
                        t,
                        flags=re.IGNORECASE,
                    )
                t = re.sub(
                    r"(?:suggesting\s+)?(?:no sunlight|zero sunlight)\s+or\s+a\s+measurement issue\.?",
                    "the data doesn't show the exact cause.",
                    t,
                    flags=re.IGNORECASE,
                )
                return t

            explanation = _clean_fault_claims(explanation)
            headline = _clean_fault_claims(headline)
            key_points = [_clean_fault_claims(kp) for kp in key_points]

        # Ensure fault anomaly sentence is included in explanation / key points if present
        for task in (plan.tasks if plan else []):
            if task.agent == "fault":
                tr = results.get(task.id)
                if tr:
                    anomaly_sent = (tr.metrics or {}).get("anomaly_sentence")
                    if anomaly_sent and "anomal" not in explanation.lower() and not any("anomal" in kp.lower() for kp in key_points):
                        key_points.append(sanitize_answer_text(anomaly_sent))

        # Build details_markdown
        details_parts = []
        if explanation:
            details_parts.append(explanation)
        if key_points:
            details_parts.append("\n" + "\n".join(f"- {kp}" for kp in key_points))
        details_markdown = "\n\n".join(details_parts).strip()

        # Build Sources footer
        completed_tasks = [
            task.id
            for task in (plan.tasks if plan else [])
            if task.id in results and getattr(results[task.id], "status", None) == TaskStatus.DONE
        ]
        sources_footer = f"Sources: {', '.join(completed_tasks)}" if completed_tasks else "Sources: none"

        # Assumptions
        all_assumptions = list(notes)
        if not any("data available" in a.lower() for a in all_assumptions):
            try:
                from solar_agent.db import queries as q
                min_d = q.get_min_log_date()
                max_d = q.get_max_log_date()
                avail_line = translate_plain_language(f"Data available {min_d.isoformat()} to {max_d.isoformat()}")
            except Exception:
                avail_line = "Data is available from 1 Aug 2026 to 26 Sep 2026."
            all_assumptions.insert(0, avail_line)

        # Warnings
        all_warnings: list[str] = []
        for tr in results.values():
            if hasattr(tr, "warnings") and tr.warnings:
                for w in tr.warnings:
                    clean_w = translate_plain_language(w)
                    if clean_w not in all_warnings:
                        all_warnings.append(clean_w)

        assumptions_section = "\n\n## Assumptions\n" + "\n".join(f"- {a}" for a in all_assumptions)
        warnings_section = "\n\n## Warnings\n" + ("\n".join(f"- {w}" for w in all_warnings) if all_warnings else "None")

        # Period analysed line
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

        # Ensure only real date objects are in dates_found
        dates_found = [d for d in dates_found if isinstance(d, (date, datetime))]
        if not dates_found and plan and getattr(plan, "scope", None):
            s_d = getattr(plan.scope, "start_date", None)
            e_d = getattr(plan.scope, "end_date", None)
            if isinstance(s_d, (date, datetime)) and isinstance(e_d, (date, datetime)):
                dates_found = [s_d, e_d]
            elif isinstance(s_d, str) and isinstance(e_d, str):
                try:
                    dates_found = [date.fromisoformat(s_d), date.fromisoformat(e_d)]
                except Exception:
                    pass
        if not dates_found:
            dates_found = [date(2026, 9, 1), date(2026, 9, 26)]

        min_d_used = min(dates_found)
        max_d_used = max(dates_found)
        n_days_used = (max_d_used - min_d_used).days + 1
        period_line = f"Period analysed: {min_d_used.strftime('%d %b %Y')} to {max_d_used.strftime('%d %b %Y')} of the data used ({n_days_used} days)"

        # Assemble final answer markdown
        ans_components = [headline, details_markdown]
        if table_context_md:
            ans_components.append(table_context_md)
        ans_components.append(sources_footer + assumptions_section + warnings_section + f"\n\n{period_line}")

        final_ans = "\n\n".join(c for c in ans_components if c).strip()
        final_ans = normalize_spaces(final_ans)

        patch = {
            "headline": headline,
            "explanation": explanation,
            "key_points": key_points,
            "notes": notes,
            "short_answer": headline,
            "details_markdown": details_markdown,
            "final_answer": final_ans,
            "tables": analysis_tables,
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
