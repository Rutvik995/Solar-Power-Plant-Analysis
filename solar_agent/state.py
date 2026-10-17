"""
State models for the Solar Power Plant Analysis multi-agent system.

This module defines:
  - Pydantic models: Scope, Task, Plan, TaskResult, ValidationReport
  - LangGraph TypedDict: AgentState
  - Enums for valid agent and intent values

These are the shared data contracts between all agents and the executor.
"""

from __future__ import annotations

from datetime import date
from enum import Enum
from typing import Annotated, Any, Literal, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field, field_validator, model_validator
from typing_extensions import TypedDict


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class AgentName(str, Enum):
    DATA = "data"
    PERFORMANCE = "performance"
    FAULT = "fault"
    ENVIRONMENT = "environment"
    # Internal (not user-callable agents, but referenced in plan execution)
    ORCHESTRATOR = "orchestrator"
    VALIDATOR = "validator"
    SYNTHESIZER = "synthesizer"


class IntentType(str, Enum):
    OVERVIEW = "overview"
    PERFORMANCE_RANKING = "performance_ranking"
    FAULT_DIAGNOSIS = "fault_diagnosis"
    COMPARISON = "comparison"
    TREND = "trend"
    ENVIRONMENTAL_IMPACT = "environmental_impact"
    ROOT_CAUSE = "root_cause"
    SUMMARY = "summary"
    OUT_OF_SCOPE = "out_of_scope"


class TaskStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    SKIPPED = "skipped"   # set when a dependency failed


class ValidationStatus(str, Enum):
    OK = "ok"
    REPLAN = "replan"
    FAIL = "fail"


# ---------------------------------------------------------------------------
# Exceptions & Cancellation
# ---------------------------------------------------------------------------

import threading


class BudgetExceeded(RuntimeError):
    """Raised when the LLM call counter exceeds max_llm_calls_per_query."""


class Cancelled(RuntimeError):
    """Raised when an active run is cancelled via CancelToken."""

    def __init__(self, message: str = "Execution cancelled", partial_results: dict | None = None) -> None:
        super().__init__(message)
        self.partial_results = partial_results or {}


class CancelToken:
    """Thread-safe cancellation token wrapping a threading.Event."""

    def __init__(self, event: threading.Event | None = None) -> None:
        self._event = event or threading.Event()

    def cancel(self) -> None:
        self._event.set()

    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def check(self, partial_results: dict | None = None) -> None:
        if self._event.is_set():
            raise Cancelled("Run was cancelled by user.", partial_results=partial_results)


_RUN_CANCEL_TOKENS: dict[str, CancelToken] = {}
_RUN_CANCEL_LOCK = threading.Lock()


def register_cancel_token(run_id: str, token: CancelToken | None = None) -> CancelToken:
    tok = token or CancelToken()
    with _RUN_CANCEL_LOCK:
        _RUN_CANCEL_TOKENS[run_id] = tok
    return tok


def get_cancel_token(run_id: str) -> CancelToken | None:
    with _RUN_CANCEL_LOCK:
        return _RUN_CANCEL_TOKENS.get(run_id)


def unregister_cancel_token(run_id: str) -> None:
    with _RUN_CANCEL_LOCK:
        _RUN_CANCEL_TOKENS.pop(run_id, None)


# ---------------------------------------------------------------------------
# Plan components
# ---------------------------------------------------------------------------

class Scope(BaseModel):
    """Resolved scope for a user query."""
    plant_ids: list[int] = Field(default_factory=list)
    block_ids: list[int | str] = Field(default_factory=list)
    inverter_ids: list[int | str] = Field(default_factory=list)
    start_date: date
    end_date: date
    all_plants: bool = Field(default=False, description="Set to true when all plants are intentionally queried.")

    @field_validator("end_date")
    @classmethod
    def end_after_start(cls, v: date, info) -> date:
        if "start_date" in info.data and v < info.data["start_date"]:
            raise ValueError(
                f"end_date ({v}) must be >= start_date ({info.data['start_date']})"
            )
        return v

    @model_validator(mode="after")
    def scope_not_empty(self) -> "Scope":
        # At least a date range must be set; entity scope can be "all"
        if self.start_date > self.end_date:
            raise ValueError("Scope has invalid date range.")
        return self


class Task(BaseModel):
    """A single node in the execution DAG."""
    id: str = Field(
        ...,
        description="Unique task identifier within this plan (e.g. 't1', 't2').",
        pattern=r"^t\d+$",
    )
    agent: Literal["data", "performance", "fault", "environment", "chart"] = Field(
        ...,
        description="Which specialist agent executes this task.",
    )
    tool_hint: Optional[str] = Field(
        default=None,
        description="Suggested tool name for the agent (non-binding guidance).",
    )
    description: str = Field(
        ...,
        description="What this task must achieve. Precise enough for the agent to act.",
        min_length=10,
    )
    inputs: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "Input parameters for this task. "
            "Use '$t1.output' syntax to reference another task's output."
        ),
    )
    depends_on: list[str] = Field(
        default_factory=list,
        description="List of task IDs that must complete before this task runs.",
    )


class Plan(BaseModel):
    """The full execution plan emitted by the Orchestrator/Planner."""
    intent: IntentType = Field(
        ...,
        description="Classified user intent — drives Synthesizer framing.",
    )
    scope: Scope
    tasks: list[Task] = Field(
        ...,
        min_length=1,
        description="Ordered list of tasks forming the DAG.",
    )
    success_criteria: str = Field(
        ...,
        min_length=10,
        description=(
            "What constitutes a successful answer. "
            "The Validator checks the results against this string."
        ),
    )

    @field_validator("tasks")
    @classmethod
    def unique_task_ids(cls, tasks: list[Task]) -> list[Task]:
        ids = [t.id for t in tasks]
        if len(ids) != len(set(ids)):
            duplicates = [tid for tid in ids if ids.count(tid) > 1]
            raise ValueError(f"Duplicate task IDs in plan: {set(duplicates)}")
        return tasks

    @model_validator(mode="after")
    def validate_dag(self) -> "Plan":
        """
        Validates DAG integrity:
          1. All depends_on references exist as task IDs.
          2. No cycles (via topological sort).
        """
        id_set = {t.id for t in self.tasks}

        # Check all dependencies exist
        for task in self.tasks:
            for dep in task.depends_on:
                if dep not in id_set:
                    raise ValueError(
                        f"Task '{task.id}' depends_on '{dep}', which does not exist in this plan."
                    )

        # Cycle detection via Kahn's algorithm
        in_degree: dict[str, int] = {t.id: 0 for t in self.tasks}
        adj: dict[str, list[str]] = {t.id: [] for t in self.tasks}
        for task in self.tasks:
            for dep in task.depends_on:
                adj[dep].append(task.id)
                in_degree[task.id] += 1

        queue = [tid for tid, deg in in_degree.items() if deg == 0]
        visited = 0
        while queue:
            node = queue.pop(0)
            visited += 1
            for neighbor in adj[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if visited != len(self.tasks):
            raise ValueError(
                "Cycle detected in the DAG. All tasks must form a directed acyclic graph."
            )

        return self

    def topological_layers(self) -> list[list[str]]:
        """
        Returns tasks grouped into execution layers.
        Tasks in the same layer have no dependencies on each other and can run in parallel.

        Returns:
            List of layers, each layer is a list of task IDs.
        """
        in_degree: dict[str, int] = {t.id: 0 for t in self.tasks}
        adj: dict[str, list[str]] = {t.id: [] for t in self.tasks}
        for task in self.tasks:
            for dep in task.depends_on:
                adj[dep].append(task.id)
                in_degree[task.id] += 1

        layers: list[list[str]] = []
        current_layer = [tid for tid, deg in in_degree.items() if deg == 0]
        while current_layer:
            layers.append(sorted(current_layer))  # sorted for determinism
            next_layer = []
            for node in current_layer:
                for neighbor in adj[node]:
                    in_degree[neighbor] -= 1
                    if in_degree[neighbor] == 0:
                        next_layer.append(neighbor)
            current_layer = next_layer

        return layers


# ---------------------------------------------------------------------------
# Task Result
# ---------------------------------------------------------------------------

class TaskResult(BaseModel):
    """The output of a single executed task."""
    task_id: str
    status: TaskStatus = TaskStatus.PENDING
    summary: Optional[str] = Field(
        default=None,
        description="Short human-readable summary of what was computed (1–3 sentences).",
    )
    data_ref: Optional[str] = Field(
        default=None,
        description="Key into DataStore for the full DataFrame result.",
    )
    metrics: Optional[dict[str, Any]] = Field(
        default=None,
        description="Key scalar metrics (e.g. {'avg_pr': 0.78, 'worst_inverter': 42}).",
    )
    error: Optional[str] = Field(
        default=None,
        description="Error message if status is FAILED.",
    )
    tool_calls_made: list[str] = Field(
        default_factory=list,
        description="Names of tools called during this task (for observability).",
    )
    warnings: list[str] = Field(
        default_factory=list,
        description="Warnings generated during task execution.",
    )
    latency_ms: Optional[float] = None


# ---------------------------------------------------------------------------
# Validation Report
# ---------------------------------------------------------------------------

class ValidationIssue(BaseModel):
    severity: Literal["error", "warning", "info"]
    task_id: Optional[str] = None
    description: str
    replan_hint: Optional[str] = Field(
        default=None,
        description="Suggestion for the re-planner on how to fix this issue.",
    )


class ValidationReport(BaseModel):
    status: ValidationStatus
    issues: list[ValidationIssue] = Field(default_factory=list)
    completeness_score: float = Field(
        ge=0.0, le=1.0,
        description="Fraction of success_criteria that appear to be satisfied.",
    )
    number_check_passed: bool = Field(
        default=True,
        description="True if all numeric values in the draft answer match tool outputs.",
    )


# ---------------------------------------------------------------------------
# LangGraph Agent State
# ---------------------------------------------------------------------------

class AgentState(TypedDict):
    """Shared state object passed between all LangGraph nodes."""

    # The original user query (immutable after initial set)
    user_query: str

    # LangGraph message history (accumulates all agent messages)
    messages: Annotated[list, add_messages]

    # The current execution plan (None until Orchestrator emits one)
    plan: Optional[Plan]

    # Results from each task: task_id → TaskResult
    results: dict[str, TaskResult]

    # Most recent validation report (None until Validator runs)
    validation: Optional[ValidationReport]

    # How many times the Orchestrator has re-planned
    replan_count: int

    # The final answer string (None until Synthesizer completes)
    final_answer: Optional[str]

    # Assumptions made during entity/date resolution (appended by tools)
    assumptions: list[str]

    # Whether the query was flagged as out-of-scope by the Orchestrator
    is_out_of_scope: bool

    # Running count of LLM API calls made in this query
    llm_call_count: int

    # Warnings gathered from all tasks executed
    warnings: list[str]

    # Per-invocation unique identifier for budget isolation (set by start_node)
    run_id: Optional[str]

    # Preserved previous scope on this thread for follow-up query inheritance
    previous_scope: Optional[dict]

    # Structured answer breakdown fields
    short_answer: Optional[str]
    details_markdown: Optional[str]
    headline: Optional[str]
    explanation: Optional[str]
    key_points: Optional[list[str]]
    notes: Optional[list[str]]

    # Preserved previous user query on this thread for follow-up query inheritance
    previous_query: Optional[str]

    # Preserved previous task types and tools on this thread for follow-up query inheritance
    previous_tasks: Optional[list[dict]]

    # Preserved previous intent
    previous_intent: Optional[str]
