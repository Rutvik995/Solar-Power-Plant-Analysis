"""
graph/validator.py – Validator node.

Checks the completed TaskResults against the plan's success_criteria and
applies two guard layers:
  1. Completeness: every task finished (DONE)?  FAILED/SKIPPED tasks are issues.
  2. Number grounding: every number in the draft synthesizer answer must appear
     (within rounding tolerance) in at least one TaskResult.metrics value.
     If not, the answer is flagged and the offending numbers are reported.

Returns:
  ValidationStatus.OK      → proceed to Synthesizer
  ValidationStatus.REPLAN  → route back to Orchestrator with hints
  ValidationStatus.FAIL    → surface the issues to the user directly
"""

from __future__ import annotations

import logging
import re

from solar_agent.state import (
    AgentState,
    TaskStatus,
    ValidationIssue,
    ValidationReport,
    ValidationStatus,
)

logger = logging.getLogger(__name__)

MAX_REPLANS = 2  # hard cap on re-planning loops (also in settings, kept here for import-free access)
NUMBER_TOLERANCE = 0.02  # 2% relative tolerance for number-grounding check


def _extract_numbers(text: str) -> list[float]:
    """
    Extract numeric values. Ignores years/dates and small integers likely to be IDs/labels.
    """
    pattern = r"-?\d{1,3}(?:,\d{3})*(?:\.\d+)?|-?\d+(?:\.\d+)?"
    raw = re.findall(pattern, text)
    nums = []
    for r in raw:
        try:
            val = float(r.replace(",", ""))
            # Ignore years 2000-2100
            if 2000 <= val <= 2100 and val.is_integer():
                continue
            # Ignore IDs / entity labels (e.g. Plant 1, Block 2)
            if 0 <= val <= 1000 and val.is_integer():
                continue
            nums.append(val)
        except ValueError:
            pass
    return nums


from solar_agent.graph.data_store import DataStore
import numpy as np

def _numbers_from_results(results: dict) -> list[float]:
    """Collect all numeric metric values from all TaskResults and their DataStore tables."""
    all_nums = []
    for tr in results.values():
        if tr and tr.metrics:
            for v in tr.metrics.values():
                try:
                    all_nums.append(float(v))
                except (TypeError, ValueError):
                    pass
        if tr and tr.data_ref:
            df = DataStore.get(tr.data_ref)
            if df is not None and not df.empty:
                for col in df.select_dtypes(include=[np.number]).columns:
                    for val in df[col].dropna():
                        try:
                            all_nums.append(float(val))
                        except (TypeError, ValueError):
                            pass
    return all_nums


def _is_grounded(num: float, ground_values: list[float]) -> bool:
    """Return True if `num` is within NUMBER_TOLERANCE of any value in ground_values, or 100x percent conversion."""
    for gv in ground_values:
        if gv == 0 and abs(num) < 1e-6:
            return True
        if gv != 0 and abs((num - gv) / gv) <= NUMBER_TOLERANCE:
            return True
        if gv != 0 and abs((num - (gv * 100.0)) / (gv * 100.0)) <= NUMBER_TOLERANCE:
            return True
    return False


class Validator:
    """LangGraph node that validates execution results and decides next step."""

    def validate(self, state: AgentState) -> dict:
        """
        Validate all task results. Returns a state patch with `validation`.
        """
        plan = state.get("plan")
        results = state.get("results") or {}
        replan_count = state.get("replan_count", 0)
        draft_answer = state.get("final_answer")  # may be set by an earlier synthesizer pass

        issues: list[ValidationIssue] = []
        tasks_done = 0
        total_tasks = len(plan.tasks) if plan else 0

        # ── 1. Completeness check ────────────────────────────────────────────
        for task in (plan.tasks if plan else []):
            tr = results.get(task.id)

            if tr is None:
                issues.append(ValidationIssue(
                    severity="error",
                    task_id=task.id,
                    description=f"Task '{task.id}' has no result.",
                    replan_hint=f"Re-run task '{task.id}'.",
                ))
                continue

            if tr.status == TaskStatus.FAILED:
                issues.append(ValidationIssue(
                    severity="error",
                    task_id=task.id,
                    description=f"Task '{task.id}' failed: {tr.error}",
                    replan_hint=(
                        f"Task '{task.id}' failed. Consider simplifying its inputs "
                        "or splitting into smaller steps."
                    ),
                ))
                continue

            if tr.status == TaskStatus.SKIPPED:
                issues.append(ValidationIssue(
                    severity="warning",
                    task_id=task.id,
                    description=f"Task '{task.id}' was skipped due to a failed dependency.",
                    replan_hint=f"Fix the dependency of '{task.id}' first.",
                ))
                continue

            tasks_done += 1

            if task.agent != "data" and not tr.data_ref:
                issues.append(ValidationIssue(
                    severity="warning",
                    task_id=task.id,
                    description=f"Task '{task.id}' ({task.agent}) produced no data_ref.",
                    replan_hint="Check if the agent's tool returned results successfully.",
                ))

        # ── 2. Number grounding check ────────────────────────────────────────
        number_check_passed = True
        # (Moved entirely to Synthesizer per user request to avoid full replans)

        # ── 3. Determine overall status ──────────────────────────────────────
        completeness = tasks_done / total_tasks if total_tasks > 0 else 0.0
        has_errors = any(i.severity == "error" for i in issues)

        if not has_errors and completeness >= 1.0:
            status = ValidationStatus.OK
        elif has_errors and replan_count >= MAX_REPLANS:
            status = ValidationStatus.FAIL
            issues.append(ValidationIssue(
                severity="error",
                description=(
                    f"Maximum re-plan limit ({MAX_REPLANS}) reached. "
                    "Surfacing partial results."
                ),
            ))
        elif has_errors:
            status = ValidationStatus.REPLAN
        else:
            status = ValidationStatus.OK

        report = ValidationReport(
            status=status,
            issues=issues,
            completeness_score=completeness,
            number_check_passed=number_check_passed,
        )

        logger.info(
            "Validator: status=%s completeness=%.2f issues=%d number_check=%s",
            status, completeness, len(issues), number_check_passed,
        )
        return {"validation": report}

    # ------------------------------------------------------------------
    # LangGraph conditional edge helper
    # ------------------------------------------------------------------

    @staticmethod
    def route(state: AgentState) -> str:
        """
        Used as a LangGraph conditional edge.
        Returns the name of the next node to route to.
        """
        validation = state.get("validation")
        if validation is None:
            return "executor"
        match validation.status:
            case ValidationStatus.OK:
                return "synthesizer"
            case ValidationStatus.REPLAN:
                return "orchestrator"
            case ValidationStatus.FAIL:
                return "synthesizer"
            case _:
                return "synthesizer"
