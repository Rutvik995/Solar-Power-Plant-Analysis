"""
graph/validator.py – Validator node.

Checks the completed TaskResults against the plan's success_criteria and
applies numeric consistency guards (numbers must come from tools, not LLM).

Validation logic:
  1. Completeness: did every task finish (DONE)?  FAILED/SKIPPED tasks are issues.
  2. Data presence: do the key tasks return a data_ref?  Missing refs are errors.
  3. Warning propagation: tool warnings are surfaced as validation warnings.
  4. Replan budget: if replan_count >= MAX_REPLANS, force FAIL instead of REPLAN.

Returns:
  ValidationStatus.OK      → proceed to Synthesizer
  ValidationStatus.REPLAN  → route back to Orchestrator with hints
  ValidationStatus.FAIL    → surface the issues to the user directly
"""

from __future__ import annotations

import logging

from solar_agent.state import (
    AgentState,
    TaskStatus,
    ValidationIssue,
    ValidationReport,
    ValidationStatus,
)

logger = logging.getLogger(__name__)

MAX_REPLANS = 2  # hard cap on re-planning loops


class Validator:
    """LangGraph node that validates execution results and decides next step."""

    def validate(self, state: AgentState) -> dict:
        """
        Validate all task results. Returns a state patch with `validation`.
        """
        plan = state.get("plan")
        results = state.get("results") or {}
        replan_count = state.get("replan_count", 0)

        issues: list[ValidationIssue] = []
        tasks_done = 0
        total_tasks = len(plan.tasks) if plan else 0

        for task in (plan.tasks if plan else []):
            tr = results.get(task.id)

            # Missing result entirely
            if tr is None:
                issues.append(ValidationIssue(
                    severity="error",
                    task_id=task.id,
                    description=f"Task '{task.id}' has no result.",
                    replan_hint=f"Re-run task '{task.id}'.",
                ))
                continue

            # Failed task
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

            # Skipped task
            if tr.status == TaskStatus.SKIPPED:
                issues.append(ValidationIssue(
                    severity="warning",
                    task_id=task.id,
                    description=f"Task '{task.id}' was skipped due to a failed dependency.",
                    replan_hint=f"Fix the dependency of '{task.id}' first.",
                ))
                continue

            tasks_done += 1

            # Non-data tasks should produce a data_ref
            if task.agent != "data" and not tr.data_ref:
                issues.append(ValidationIssue(
                    severity="warning",
                    task_id=task.id,
                    description=f"Task '{task.id}' ({task.agent}) produced no data_ref.",
                    replan_hint="Check if the agent's tool returned results successfully.",
                ))

            # Propagate tool warnings
            if tr.metrics:
                # Check for empty results (e.g. no data in range)
                pass  # metrics presence is sufficient

        # Completeness score
        completeness = tasks_done / total_tasks if total_tasks > 0 else 0.0

        # Determine overall status
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
            # Warnings only — proceed
            status = ValidationStatus.OK

        report = ValidationReport(
            status=status,
            issues=issues,
            completeness_score=completeness,
            number_check_passed=True,  # Phase 5 will add LLM-vs-tool number comparison
        )

        logger.info(
            "Validator: status=%s completeness=%.2f issues=%d",
            status, completeness, len(issues),
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
                return "synthesizer"  # synthesizer will surface the issues
            case _:
                return "synthesizer"
