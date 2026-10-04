"""
tests/test_orchestrator_executor.py – Unit tests for Phase 4.

Tests cover:
  - Orchestrator: valid plan parsing, out-of-scope detection, replan with hints,
    LLM JSON parse failures, markdown-fence stripping.
  - Executor: layer scheduling, $ref resolution, parallel dispatch,
    dependency-failed → SKIPPED propagation, unknown agent → FAILED.
  - Validator: OK / REPLAN / FAIL routing, replan cap.
  - Graph routing: conditional edge helpers.
"""

from __future__ import annotations

import json
from datetime import date
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage

from solar_agent.graph.executor import Executor, _resolve_refs
from solar_agent.graph.orchestrator import Orchestrator
from solar_agent.graph.validator import MAX_REPLANS, Validator
from solar_agent.state import (
    AgentState,
    IntentType,
    Plan,
    Scope,
    Task,
    TaskResult,
    TaskStatus,
    ValidationIssue,
    ValidationReport,
    ValidationStatus,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_llm(response_json: dict | str) -> MagicMock:
    """Return a mock LLM whose invoke() returns the given JSON as AIMessage content."""
    content = response_json if isinstance(response_json, str) else json.dumps(response_json)
    mock = MagicMock()
    mock.invoke.return_value = AIMessage(content=content)
    return mock


def _base_state(**overrides) -> AgentState:
    s = {
        "user_query": "How did Plant Alpha perform last month?",
        "messages": [],
        "plan": None,
        "results": {},
        "validation": None,
        "replan_count": 0,
        "final_answer": None,
        "assumptions": [],
        "is_out_of_scope": False,
    }
    s.update(overrides)
    return AgentState(**s)


def _minimal_plan() -> Plan:
    return Plan(
        intent=IntentType.PERFORMANCE_RANKING,
        scope=Scope(plant_ids=[1], start_date=date(2026, 9, 1), end_date=date(2026, 9, 30)),
        tasks=[
            Task(id="t1", agent="data", description="Fetch data for Plant 1.", depends_on=[]),
            Task(id="t2", agent="performance", description="Compute KPIs.", inputs={"data_ref": "$t1.data_ref"}, depends_on=["t1"]),
        ],
        success_criteria="Return PR and yield for Plant 1.",
    )


def _done_result(task_id: str, data_ref: str = "df_abc", metrics: dict | None = None) -> TaskResult:
    return TaskResult(
        task_id=task_id,
        status=TaskStatus.DONE,
        summary="Done.",
        data_ref=data_ref,
        metrics=metrics or {},
    )


def _failed_result(task_id: str, error: str = "Something went wrong.") -> TaskResult:
    return TaskResult(task_id=task_id, status=TaskStatus.FAILED, error=error)


# ===========================================================================
# Orchestrator tests
# ===========================================================================

class TestOrchestrator:

    def test_valid_plan_parsed(self):
        """Orchestrator should parse a valid LLM JSON plan into a Plan model."""
        plan_json = {
            "intent": "performance_ranking",
            "scope": {
                "plant_ids": [1], "block_ids": [], "inverter_ids": [],
                "start_date": "2026-09-01", "end_date": "2026-09-30"
            },
            "tasks": [
                {"id": "t1", "agent": "data", "description": "Fetch Plant 1 data.", "inputs": {}, "depends_on": []},
                {"id": "t2", "agent": "performance", "description": "Compute KPIs.", "inputs": {"data_ref": "$t1.data_ref"}, "depends_on": ["t1"]},
            ],
            "success_criteria": "Return KPIs for Plant 1."
        }
        llm = _make_llm(plan_json)
        orc = Orchestrator(llm=llm)
        patch = orc.plan(_base_state())

        assert "plan" in patch
        plan = patch["plan"]
        assert isinstance(plan, Plan)
        assert len(plan.tasks) == 2
        assert plan.intent == IntentType.PERFORMANCE_RANKING
        assert patch.get("is_out_of_scope") is False

    def test_out_of_scope_heuristic(self):
        """Queries mentioning 'stock' should be flagged without calling LLM."""
        orc = Orchestrator(llm=MagicMock())
        state = _base_state(user_query="What is the stock price of SolarCorp today?")
        patch = orc.plan(state)

        assert patch.get("is_out_of_scope") is True
        assert "final_answer" in patch

    def test_out_of_scope_from_llm(self):
        """Orchestrator should handle explicit {'out_of_scope': true} from LLM."""
        llm = _make_llm({"out_of_scope": True, "reason": "Weather forecasting is not supported."})
        orc = Orchestrator(llm=llm)
        patch = orc.plan(_base_state(user_query="Predict tomorrow's irradiance for Plant 1."))
        assert patch.get("is_out_of_scope") is True

    def test_markdown_fence_stripped(self):
        """Orchestrator should strip ```json ... ``` wrappers from LLM output."""
        plan_json = {
            "intent": "summary",
            "scope": {"plant_ids": [1], "block_ids": [], "inverter_ids": [],
                      "start_date": "2026-09-01", "end_date": "2026-09-30"},
            "tasks": [
                {"id": "t1", "agent": "data", "description": "Fetch data.", "inputs": {}, "depends_on": []}
            ],
            "success_criteria": "Summarise performance."
        }
        fenced = f"```json\n{json.dumps(plan_json)}\n```"
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content=fenced)
        orc = Orchestrator(llm=mock_llm)
        patch = orc.plan(_base_state())
        assert "plan" in patch

    def test_invalid_json_returns_out_of_scope(self):
        """If the LLM returns garbage, orchestrator should return out_of_scope."""
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content="Sorry, I cannot help with that.")
        orc = Orchestrator(llm=mock_llm)
        patch = orc.plan(_base_state())
        assert patch.get("is_out_of_scope") is True

    def test_dag_cycle_detected(self):
        """A plan with a cycle should fail validation and return out_of_scope."""
        plan_json = {
            "intent": "fault_diagnosis",
            "scope": {"plant_ids": [1], "block_ids": [], "inverter_ids": [],
                      "start_date": "2026-09-01", "end_date": "2026-09-30"},
            "tasks": [
                # t1 depends on t2, t2 depends on t1 → cycle
                {"id": "t1", "agent": "data", "description": "A.", "inputs": {}, "depends_on": ["t2"]},
                {"id": "t2", "agent": "fault", "description": "B.", "inputs": {}, "depends_on": ["t1"]},
            ],
            "success_criteria": "Detect faults."
        }
        orc = Orchestrator(llm=_make_llm(plan_json))
        patch = orc.plan(_base_state())
        assert patch.get("is_out_of_scope") is True

    def test_replan_context_included_in_prompt(self):
        """On re-plan, the human message should mention the validation issues."""
        from solar_agent.state import ValidationIssue, ValidationReport, ValidationStatus
        prev_report = ValidationReport(
            status=ValidationStatus.REPLAN,
            issues=[ValidationIssue(severity="error", task_id="t2",
                                     description="Task t2 failed.", replan_hint="Retry t2.")],
            completeness_score=0.5,
        )
        state = _base_state(
            plan=_minimal_plan(),
            validation=prev_report,
            replan_count=1,
        )
        mock_llm = MagicMock()
        mock_llm.invoke.return_value = AIMessage(content=json.dumps({
            "intent": "performance_ranking",
            "scope": {"plant_ids": [1], "block_ids": [], "inverter_ids": [],
                      "start_date": "2026-09-01", "end_date": "2026-09-30"},
            "tasks": [{"id": "t1", "agent": "data", "description": "Retry fetch.", "inputs": {}, "depends_on": []}],
            "success_criteria": "Return PR."
        }))
        orc = Orchestrator(llm=mock_llm)
        orc.plan(state)

        call_args = mock_llm.invoke.call_args[0][0]
        human_content = call_args[1].content  # second message is HumanMessage
        assert "re-plan" in human_content.lower() or "replan" in human_content.lower()


# ===========================================================================
# Executor tests
# ===========================================================================

class TestRefResolution:

    def test_simple_data_ref(self):
        """$t1.data_ref should resolve to the data_ref of task t1's result."""
        results = {"t1": _done_result("t1", data_ref="df_xyz")}
        resolved = _resolve_refs({"data_ref": "$t1.data_ref"}, results)
        assert resolved["data_ref"] == "df_xyz"

    def test_nested_metric_ref(self):
        """$t2.metrics.pr should resolve to metrics['pr']."""
        tr = TaskResult(task_id="t2", status=TaskStatus.DONE, metrics={"pr": 0.87})
        resolved = _resolve_refs({"val": "$t2.metrics.pr"}, {"t2": tr})
        # metrics is a dict attribute on TaskResult
        assert resolved["val"] == 0.87

    def test_missing_task_resolves_to_none(self):
        """If the referenced task doesn't exist yet, value should be None."""
        resolved = _resolve_refs({"data_ref": "$t99.data_ref"}, {})
        assert resolved["data_ref"] is None

    def test_literal_values_pass_through(self):
        """Non-ref values should be returned unchanged."""
        resolved = _resolve_refs({"plant_id": 1, "level": "plant"}, {})
        assert resolved == {"plant_id": 1, "level": "plant"}


class TestExecutorLayerScheduling:

    def test_first_layer_scheduled(self):
        """The executor should pick the first layer (tasks with no dependencies)."""
        plan = _minimal_plan()  # t1 has no deps, t2 depends on t1
        state = _base_state(plan=plan, results={})

        mock_agent = MagicMock()
        mock_agent.run.return_value = _done_result("t1")
        ex = Executor.__new__(Executor)
        ex._agents = {"data": mock_agent, "performance": mock_agent}

        layers = plan.topological_layers()
        first = ex._find_next_layer(layers, {})
        assert first == ["t1"]

    def test_second_layer_after_first_done(self):
        """After t1 is done, t2 should be scheduled."""
        plan = _minimal_plan()
        results = {"t1": _done_result("t1")}
        ex = Executor.__new__(Executor)
        layers = plan.topological_layers()
        next_layer = ex._find_next_layer(layers, results)
        assert next_layer == ["t2"]

    def test_all_done_no_more_layers(self):
        """When all tasks are done, _find_next_layer returns empty list."""
        plan = _minimal_plan()
        results = {
            "t1": _done_result("t1"),
            "t2": _done_result("t2"),
        }
        ex = Executor.__new__(Executor)
        layers = plan.topological_layers()
        assert ex._find_next_layer(layers, results) == []

    def test_failed_dependency_causes_skip(self):
        """If t1 failed, t2 (which depends on t1) should be SKIPPED."""
        plan = _minimal_plan()
        results = {"t1": _failed_result("t1")}

        ex = Executor.__new__(Executor)
        ex._agents = {
            "data": MagicMock(),
            "performance": MagicMock(),
        }

        t2 = plan.tasks[1]  # the performance task
        new_results = ex._run_layer([t2], results)
        assert new_results["t2"].status == TaskStatus.SKIPPED

    def test_unknown_agent_fails_task(self):
        """A task that references a non-existent agent should FAIL."""
        bad_task = Task(id="t1", agent="data", description="Fetch something.", depends_on=[])
        ex = Executor.__new__(Executor)
        ex._agents = {}  # empty
        new_results = ex._run_layer([bad_task], {})
        assert new_results["t1"].status == TaskStatus.FAILED

    def test_all_done_helper_true_when_done(self):
        plan = _minimal_plan()
        state = _base_state(
            plan=plan,
            results={"t1": _done_result("t1"), "t2": _done_result("t2")},
        )
        assert Executor.all_done(state) is True

    def test_all_done_helper_false_when_pending(self):
        plan = _minimal_plan()
        state = _base_state(plan=plan, results={"t1": _done_result("t1")})
        assert Executor.all_done(state) is False

    def test_all_done_no_plan(self):
        """No plan → all_done should return True (nothing to run)."""
        state = _base_state(plan=None)
        assert Executor.all_done(state) is True


# ===========================================================================
# Validator tests
# ===========================================================================

class TestValidator:

    def _run(self, plan, results, replan_count=0) -> ValidationReport:
        v = Validator()
        state = _base_state(plan=plan, results=results, replan_count=replan_count)
        patch = v.validate(state)
        return patch["validation"]

    def test_all_done_is_ok(self):
        plan = _minimal_plan()
        results = {"t1": _done_result("t1"), "t2": _done_result("t2")}
        report = self._run(plan, results)
        assert report.status == ValidationStatus.OK
        assert report.completeness_score == 1.0

    def test_one_failed_triggers_replan(self):
        plan = _minimal_plan()
        results = {"t1": _done_result("t1"), "t2": _failed_result("t2")}
        report = self._run(plan, results, replan_count=0)
        assert report.status == ValidationStatus.REPLAN
        assert any(i.severity == "error" for i in report.issues)

    def test_replan_cap_forces_fail(self):
        """When replan_count >= MAX_REPLANS, REPLAN becomes FAIL."""
        plan = _minimal_plan()
        results = {"t1": _done_result("t1"), "t2": _failed_result("t2")}
        report = self._run(plan, results, replan_count=MAX_REPLANS)
        assert report.status == ValidationStatus.FAIL

    def test_skipped_task_is_warning(self):
        plan = _minimal_plan()
        results = {
            "t1": _done_result("t1"),
            "t2": TaskResult(task_id="t2", status=TaskStatus.SKIPPED, error="dep failed"),
        }
        report = self._run(plan, results)
        assert any(i.severity == "warning" for i in report.issues)

    def test_missing_result_is_error(self):
        """If a task has no result at all, it should be an error."""
        plan = _minimal_plan()
        results = {"t1": _done_result("t1")}  # t2 missing entirely
        report = self._run(plan, results)
        assert any(i.task_id == "t2" for i in report.issues)

    def test_route_ok_to_synthesizer(self):
        report = ValidationReport(status=ValidationStatus.OK, completeness_score=1.0)
        state = _base_state(validation=report)
        assert Validator.route(state) == "synthesizer"

    def test_route_replan_to_orchestrator(self):
        report = ValidationReport(status=ValidationStatus.REPLAN, completeness_score=0.5)
        state = _base_state(validation=report)
        assert Validator.route(state) == "orchestrator"

    def test_route_fail_to_synthesizer(self):
        report = ValidationReport(status=ValidationStatus.FAIL, completeness_score=0.0)
        state = _base_state(validation=report)
        assert Validator.route(state) == "synthesizer"

    def test_no_validation_routes_to_executor(self):
        state = _base_state(validation=None)
        assert Validator.route(state) == "executor"


# ===========================================================================
# Graph routing helpers
# ===========================================================================

class TestGraphRouting:

    def test_route_after_orchestrator_with_plan(self):
        from solar_agent.graph.graph import _route_after_orchestrator
        state = _base_state(plan=_minimal_plan(), is_out_of_scope=False)
        assert _route_after_orchestrator(state) == "executor"

    def test_route_after_orchestrator_out_of_scope(self):
        from langgraph.graph import END
        from solar_agent.graph.graph import _route_after_orchestrator
        state = _base_state(plan=None, is_out_of_scope=True)
        assert _route_after_orchestrator(state) == END

    def test_route_after_executor_still_pending(self):
        from solar_agent.graph.graph import _route_after_executor
        plan = _minimal_plan()
        state = _base_state(plan=plan, results={"t1": _done_result("t1")})
        assert _route_after_executor(state) == "executor"

    def test_route_after_executor_all_done(self):
        from solar_agent.graph.graph import _route_after_executor
        plan = _minimal_plan()
        state = _base_state(
            plan=plan,
            results={"t1": _done_result("t1"), "t2": _done_result("t2")},
        )
        assert _route_after_executor(state) == "validator"

    def test_graph_compiles(self):
        """build_graph should compile without errors with a mock LLM."""
        from solar_agent.graph.graph import build_graph
        mock_llm = MagicMock()
        mock_llm.bind_tools.return_value = mock_llm
        graph = build_graph(llm=mock_llm)
        assert graph is not None


# ===========================================================================
# Integration test (skipped unless -m integration)
# ===========================================================================

@pytest.mark.integration
def test_full_pipeline_live():
    """
    End-to-end smoke test: query → plan → fetch → KPIs → answer.
    Requires DB and LLM environment variables.
    """
    from langchain_google_genai import ChatGoogleGenerativeAI

    from solar_agent.graph.graph import build_graph, initial_state

    llm = ChatGoogleGenerativeAI(model="gemini-2.0-flash")
    graph = build_graph(llm=llm)

    result = graph.invoke(initial_state(
        "What was the performance ratio of Solar Alpha in September 2026?"
    ))
    assert result.get("final_answer")
    assert result.get("is_out_of_scope") is False
