"""
test_dag_validation.py — Unit tests for Plan / DAG validation logic in state.py

Tests:
  - Valid plans pass validation
  - Cyclic plans are rejected with a clear error
  - Missing dependency references are rejected
  - Duplicate task IDs are rejected
  - topological_layers() returns correct parallel layers
  - Scope date validation (end < start rejected)
  - Task ID format validation

Run:  pytest tests/test_dag_validation.py -v
"""

from __future__ import annotations

from datetime import date

import pytest
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from pydantic import ValidationError

from solar_agent.state import Plan, Scope, Task, IntentType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _scope(
    start: str = "2025-07-01",
    end: str = "2025-07-31",
    plant_ids: list[int] = None,
) -> Scope:
    return Scope(
        start_date=date.fromisoformat(start),
        end_date=date.fromisoformat(end),
        plant_ids=plant_ids or [],
    )


def _task(id: str, agent: str = "data", depends_on: list[str] = None) -> dict:
    return dict(
        id=id,
        agent=agent,
        description="Fetch daily inverter data for the specified scope.",
        inputs={},
        depends_on=depends_on or [],
    )


def _plan(tasks: list[dict], **kwargs) -> Plan:
    """Helper to build a Plan from task dicts."""
    return Plan(
        intent=kwargs.get("intent", IntentType.PERFORMANCE_RANKING),
        scope=kwargs.get("scope", _scope()),
        tasks=[Task(**t) for t in tasks],
        success_criteria=kwargs.get(
            "success_criteria",
            "Identify the block with the lowest PR and explain the result."
        ),
    )


# ---------------------------------------------------------------------------
# Valid Plans
# ---------------------------------------------------------------------------

class TestValidPlans:

    def test_single_task_plan(self):
        """A plan with a single task and no dependencies is always valid."""
        plan = _plan([_task("t1", "data")])
        assert len(plan.tasks) == 1

    def test_linear_chain(self):
        """t1 → t2 → t3 — a linear dependency chain with no cycles."""
        plan = _plan([
            _task("t1", "data"),
            _task("t2", "performance", depends_on=["t1"]),
            _task("t3", "fault", depends_on=["t2"]),
        ])
        assert len(plan.tasks) == 3

    def test_diamond_dag(self):
        """
        t1 → t2
        t1 → t3
        t2, t3 → t4
        A diamond shape — valid DAG, no cycles.
        """
        plan = _plan([
            _task("t1", "data"),
            _task("t2", "performance", depends_on=["t1"]),
            _task("t3", "fault", depends_on=["t1"]),
            _task("t4", "environment", depends_on=["t2", "t3"]),
        ])
        assert len(plan.tasks) == 4

    def test_parallel_independent_tasks(self):
        """Two tasks with no dependencies on each other are valid."""
        plan = _plan([
            _task("t1", "performance"),
            _task("t2", "fault"),
        ])
        assert len(plan.tasks) == 2


# ---------------------------------------------------------------------------
# Cyclic Plans — must raise ValidationError
# ---------------------------------------------------------------------------

class TestCyclicPlans:

    def test_self_loop(self):
        """A task that depends on itself."""
        with pytest.raises(ValidationError, match="[Cc]ycle"):
            _plan([_task("t1", "data", depends_on=["t1"])])

    def test_two_node_cycle(self):
        """t1 → t2, t2 → t1."""
        with pytest.raises(ValidationError, match="[Cc]ycle"):
            _plan([
                _task("t1", "data", depends_on=["t2"]),
                _task("t2", "performance", depends_on=["t1"]),
            ])

    def test_three_node_cycle(self):
        """t1 → t2 → t3 → t1."""
        with pytest.raises(ValidationError, match="[Cc]ycle"):
            _plan([
                _task("t1", "data", depends_on=["t3"]),
                _task("t2", "performance", depends_on=["t1"]),
                _task("t3", "fault", depends_on=["t2"]),
            ])


# ---------------------------------------------------------------------------
# Missing Dependencies — must raise ValidationError
# ---------------------------------------------------------------------------

class TestMissingDependencies:

    def test_depends_on_nonexistent_task(self):
        """t2 depends on 't99' which doesn't exist."""
        with pytest.raises(ValidationError, match="does not exist"):
            _plan([
                _task("t1", "data"),
                _task("t2", "performance", depends_on=["t99"]),
            ])

    def test_depends_on_empty_string(self):
        """depends_on with an empty string ID is not a valid task ID."""
        with pytest.raises(ValidationError):
            _plan([
                _task("t1", "data"),
                _task("t2", "performance", depends_on=[""]),
            ])


# ---------------------------------------------------------------------------
# Duplicate Task IDs — must raise ValidationError
# ---------------------------------------------------------------------------

class TestDuplicateTaskIds:

    def test_duplicate_ids(self):
        """Two tasks with the same ID is invalid."""
        with pytest.raises(ValidationError, match="[Dd]uplicate"):
            _plan([
                _task("t1", "data"),
                _task("t1", "performance"),  # duplicate!
            ])


# ---------------------------------------------------------------------------
# Task ID Format — must match pattern "t\d+"
# ---------------------------------------------------------------------------

class TestTaskIdFormat:

    @pytest.mark.parametrize("bad_id", [
        "task1", "T1", "1", "task-1", "t", "t1a",
    ])
    def test_invalid_task_id_rejected(self, bad_id: str):
        with pytest.raises(ValidationError):
            Task(
                id=bad_id,
                agent="data",
                description="Some description that is long enough.",
                inputs={},
                depends_on=[],
            )

    @pytest.mark.parametrize("good_id", ["t1", "t2", "t10", "t99"])
    def test_valid_task_id_accepted(self, good_id: str):
        task = Task(
            id=good_id,
            agent="data",
            description="Some description that is long enough.",
            inputs={},
            depends_on=[],
        )
        assert task.id == good_id


# ---------------------------------------------------------------------------
# Scope Validation
# ---------------------------------------------------------------------------

class TestScopeValidation:

    def test_end_before_start_rejected(self):
        with pytest.raises(ValidationError):
            Scope(
                start_date=date(2025, 7, 31),
                end_date=date(2025, 7, 1),   # end < start
            )

    def test_same_start_and_end_valid(self):
        scope = Scope(start_date=date(2025, 7, 15), end_date=date(2025, 7, 15))
        assert scope.start_date == scope.end_date


# ---------------------------------------------------------------------------
# topological_layers() — parallel execution scheduling
# ---------------------------------------------------------------------------

class TestTopologicalLayers:

    def test_single_task_one_layer(self):
        plan = _plan([_task("t1")])
        layers = plan.topological_layers()
        assert layers == [["t1"]]

    def test_linear_chain_layers(self):
        plan = _plan([
            _task("t1"),
            _task("t2", depends_on=["t1"]),
            _task("t3", depends_on=["t2"]),
        ])
        layers = plan.topological_layers()
        assert layers == [["t1"], ["t2"], ["t3"]]

    def test_parallel_tasks_same_layer(self):
        """t1 and t2 are independent → both in layer 0."""
        plan = _plan([
            _task("t1"),
            _task("t2"),
        ])
        layers = plan.topological_layers()
        assert len(layers) == 1
        assert sorted(layers[0]) == ["t1", "t2"]

    def test_diamond_layers(self):
        """
        t1 → t2
        t1 → t3
        t2, t3 → t4

        Layer 0: [t1]
        Layer 1: [t2, t3]
        Layer 2: [t4]
        """
        plan = _plan([
            _task("t1"),
            _task("t2", depends_on=["t1"]),
            _task("t3", depends_on=["t1"]),
            _task("t4", depends_on=["t2", "t3"]),
        ])
        layers = plan.topological_layers()
        assert layers[0] == ["t1"]
        assert sorted(layers[1]) == ["t2", "t3"]
        assert layers[2] == ["t4"]

    def test_example_q1_plan(self):
        """
        Example Q1 from section 8:
        t1 (data) → t2 (performance)
        Two layers, no parallelism.
        """
        plan = _plan([
            _task("t1", "data"),
            _task("t2", "performance", depends_on=["t1"]),
        ])
        layers = plan.topological_layers()
        assert layers == [["t1"], ["t2"]]

    def test_example_q2_plan(self):
        """
        Example Q2 from section 8:
        t1 (data) → t2, t3, t4
        t2, t3, t4 run in parallel.
        """
        plan = _plan([
            _task("t1", "data"),
            _task("t2", "fault", depends_on=["t1"]),
            _task("t3", "fault", depends_on=["t1"]),
            _task("t4", "environment", depends_on=["t1"]),
        ])
        layers = plan.topological_layers()
        assert layers[0] == ["t1"]
        assert sorted(layers[1]) == ["t2", "t3", "t4"]

    def test_all_layers_cover_all_tasks(self):
        """Every task ID must appear in exactly one layer."""
        plan = _plan([
            _task("t1"),
            _task("t2", depends_on=["t1"]),
            _task("t3", depends_on=["t1"]),
            _task("t4", depends_on=["t2"]),
            _task("t5", depends_on=["t3"]),
        ])
        layers = plan.topological_layers()
        all_in_layers = [tid for layer in layers for tid in layer]
        assert sorted(all_in_layers) == ["t1", "t2", "t3", "t4", "t5"]


# ---------------------------------------------------------------------------
# Plan — out_of_scope intent is valid
# ---------------------------------------------------------------------------

class TestOutOfScopeIntent:

    def test_out_of_scope_plan_valid(self):
        """The Orchestrator can produce an out_of_scope plan (single task)."""
        plan = _plan(
            [_task("t1", "data")],
            intent=IntentType.OUT_OF_SCOPE,
            success_criteria="Politely decline and explain what the system can answer.",
        )
        assert plan.intent == IntentType.OUT_OF_SCOPE
