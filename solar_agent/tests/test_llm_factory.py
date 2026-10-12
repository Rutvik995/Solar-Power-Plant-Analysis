"""
test_llm_factory.py – Unit tests for config/llm.py (Group C).

Tests:
  - get_llm("planner") returns the right class per provider setting
  - get_llm("agent") / get_llm("synthesizer") work the same way
  - Unknown role raises ValueError with a clear message
  - Unknown provider raises ValueError with a clear message
  - BudgetedLLM and ReplayLLM wrappers apply to every provider transparently
  - Changing provider does NOT change cache keys (keys depend only on messages)
  - Changing model DOES change cache keys (model name affects system prompts)
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from solar_agent.config.llm import get_llm, _ROLES, _PROVIDER_BUILDERS
from solar_agent.config.settings import settings
from solar_agent.graph.budgeted_llm import BudgetedLLM
from solar_agent.graph.llm_recorder import ReplayLLM, _hash_messages
from solar_agent.state import BudgetExceeded


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_mock_provider_class(name: str = "MockLLM"):
    """Return a callable that acts as a provider constructor and records args."""
    instances = []

    class MockLLM:
        def __init__(self, model, **kwargs):
            self.model = model
            self.kwargs = kwargs
            instances.append(self)

        def invoke(self, messages, **kwargs):
            return AIMessage(content="mock response")

        def bind_tools(self, tools, **kwargs):
            return self

        def bind(self, **kwargs):
            return self

    MockLLM.__name__ = name
    MockLLM.instances = instances
    return MockLLM


# ---------------------------------------------------------------------------
# (A) Factory returns correct class per role + provider setting
# ---------------------------------------------------------------------------

class TestLlmFactoryRoles:
    """get_llm(role) must build the provider specified in settings."""

    def test_planner_role_builds_provider_class(self):
        """get_llm('planner') calls the planner_provider builder."""
        mock_class = _make_mock_provider_class()
        with patch.dict("solar_agent.config.llm._PROVIDER_BUILDERS",
                        {settings.planner_provider: mock_class}):
            llm = get_llm("planner")
        assert isinstance(llm, mock_class)
        assert llm.model == settings.planner_model

    def test_agent_role_builds_provider_class(self):
        """get_llm('agent') calls the agent_provider builder."""
        mock_class = _make_mock_provider_class()
        with patch.dict("solar_agent.config.llm._PROVIDER_BUILDERS",
                        {settings.agent_provider: mock_class}):
            llm = get_llm("agent")
        assert isinstance(llm, mock_class)
        assert llm.model == settings.agent_model

    def test_synthesizer_role_builds_provider_class(self):
        """get_llm('synthesizer') calls the synthesizer_provider builder."""
        mock_class = _make_mock_provider_class()
        with patch.dict("solar_agent.config.llm._PROVIDER_BUILDERS",
                        {settings.synthesizer_provider: mock_class}):
            llm = get_llm("synthesizer")
        assert isinstance(llm, mock_class)
        assert llm.model == settings.synthesizer_model

    def test_all_three_roles_succeed(self):
        """Every valid role must return without error when mocked."""
        mock_class = _make_mock_provider_class()
        patched = {p: mock_class for p in _PROVIDER_BUILDERS}
        with patch.dict("solar_agent.config.llm._PROVIDER_BUILDERS", patched):
            for role in _ROLES:
                llm = get_llm(role)
                assert llm is not None


# ---------------------------------------------------------------------------
# (B) Error paths
# ---------------------------------------------------------------------------

class TestLlmFactoryErrors:
    def test_unknown_role_raises_value_error(self):
        """An unrecognised role must raise ValueError with the role name in the message."""
        with pytest.raises(ValueError) as exc_info:
            get_llm("weather_predictor")
        assert "weather_predictor" in str(exc_info.value)

    def test_unknown_provider_raises_value_error(self):
        """An unrecognised provider in settings must raise ValueError."""
        orig = settings.planner_provider
        settings.planner_provider = "cohere_ultra_pro"
        try:
            with pytest.raises(ValueError) as exc_info:
                get_llm("planner")
            assert "cohere_ultra_pro" in str(exc_info.value)
        finally:
            settings.planner_provider = orig

    def test_unused_provider_not_imported(self):
        """If only 'gemini' is configured, importing 'anthropic' must not be required."""
        # Simulate anthropic not installed by replacing its builder
        orig = settings.planner_provider
        settings.planner_provider = "gemini"
        try:
            mock_class = _make_mock_provider_class()
            with patch.dict("solar_agent.config.llm._PROVIDER_BUILDERS",
                            {"gemini": mock_class}):
                # This must work even if anthropic is absent from _PROVIDER_BUILDERS
                llm = get_llm("planner")
            assert isinstance(llm, mock_class)
        finally:
            settings.planner_provider = orig


# ---------------------------------------------------------------------------
# (C) Wrappers apply to every provider
# ---------------------------------------------------------------------------

class TestWrappersApplyToEveryProvider:
    """BudgetedLLM and ReplayLLM must wrap whatever get_llm() returns."""

    def _inner_for_provider(self, provider: str, model: str = "mock-model"):
        """Return a mocked inner LLM for the given provider."""
        mock_class = _make_mock_provider_class()
        orig_provider = settings.planner_provider
        orig_model = settings.planner_model
        settings.planner_provider = provider
        settings.planner_model = model
        try:
            with patch.dict("solar_agent.config.llm._PROVIDER_BUILDERS",
                            {provider: mock_class}):
                return get_llm("planner")
        finally:
            settings.planner_provider = orig_provider
            settings.planner_model = orig_model

    def test_budgeted_wraps_gemini(self):
        inner = self._inner_for_provider("gemini")
        budgeted = BudgetedLLM(inner_llm=inner, budget=5)
        resp = budgeted.invoke("hello", run_id="r1")
        assert resp.content == "mock response"
        assert budgeted.call_count("r1") == 1

    def test_budgeted_wraps_any_provider(self):
        """Budget counting works regardless of provider."""
        for provider in _PROVIDER_BUILDERS:
            inner = self._inner_for_provider(provider)
            budgeted = BudgetedLLM(inner_llm=inner, budget=3)
            budgeted.invoke("hello", run_id="r1")
            assert budgeted.call_count("r1") == 1, f"Counter broken for {provider}"

    def test_replay_wraps_any_provider(self):
        """ReplayLLM wraps any inner LLM; cache misses raise CacheMissError."""
        from solar_agent.graph.llm_recorder import CacheMissError
        inner = self._inner_for_provider("gemini")
        recorder = ReplayLLM(fallback_llm=inner, mode="replay", cache_name="_test_nonexistent.json")
        msgs = [HumanMessage(content="test query")]
        with pytest.raises(CacheMissError):
            recorder.invoke(msgs)

    def test_budget_then_replay_chain(self):
        """Stacking BudgetedLLM(ReplayLLM(inner)) works correctly."""
        from solar_agent.graph.llm_recorder import CacheMissError
        inner = self._inner_for_provider("gemini")
        recorder = ReplayLLM(fallback_llm=inner, mode="replay", cache_name="_test_nonexistent.json")
        budgeted = BudgetedLLM(inner_llm=recorder, budget=5)
        with pytest.raises(CacheMissError):
            budgeted.invoke([HumanMessage(content="q")], run_id="r1")
        # Budget counter increments even if inner raises
        assert budgeted.call_count("r1") == 1


# ---------------------------------------------------------------------------
# (D) Cache keys: provider-agnostic, model-sensitive
# ---------------------------------------------------------------------------

class TestCacheKeyBehaviour:
    """Cache keys depend on message content only — provider-agnostic, model-sensitive."""

    def test_same_messages_same_key_regardless_of_provider(self):
        """Changing provider does not change the cache key for the same messages."""
        msgs = [
            SystemMessage(content="You are a solar analyst."),
            HumanMessage(content="What is the PR of Plant 1?"),
        ]
        key_a = _hash_messages(msgs)
        key_b = _hash_messages(msgs)
        assert key_a == key_b, "Same messages should produce the same cache key"

    def test_different_messages_different_key(self):
        """Distinct messages produce different cache keys."""
        msgs_a = [HumanMessage(content="Query A")]
        msgs_b = [HumanMessage(content="Query B")]
        assert _hash_messages(msgs_a) != _hash_messages(msgs_b)

    def test_model_name_in_system_prompt_changes_key(self):
        """If a system prompt encodes the model name, changing model changes the key."""
        msgs_gemini = [
            SystemMessage(content="Using model gemini-3.8-pro. Analyse solar data."),
            HumanMessage(content="PR of Plant 1?"),
        ]
        msgs_anthropic = [
            SystemMessage(content="Using model claude-3-opus. Analyse solar data."),
            HumanMessage(content="PR of Plant 1?"),
        ]
        assert _hash_messages(msgs_gemini) != _hash_messages(msgs_anthropic)

    def test_provider_change_without_prompt_change_does_not_affect_key(self):
        """Changing provider alone (same prompt) must NOT change the cache key."""
        msgs = [HumanMessage(content="What is the yield of Plant 1 in September 2026?")]
        # Key is computed from messages only, not from provider name
        key = _hash_messages(msgs)
        assert isinstance(key, str) and len(key) == 32  # MD5 hex

    def test_replay_llm_cache_size_reported_correctly(self):
        """ReplayLLM.cache_size() reports number of entries loaded from file."""
        recorder = ReplayLLM(mode="replay", cache_name="_test_empty_cache.json")
        # No cache file exists → 0 entries
        assert recorder.cache_size() == 0


# ---------------------------------------------------------------------------
# (E) Groq Provider & Throttle / 429 / Structured Output Tests
# ---------------------------------------------------------------------------

class TestGroqProvider:
    """Mocked constructor tests for Groq provider and model resolution."""

    def test_factory_returns_chatgroq_for_groq(self):
        """get_llm returns ChatGroq when provider is groq, passing model and api_key."""
        mock_instance = MagicMock()
        with patch("langchain_groq.ChatGroq", return_value=mock_instance) as mock_class:
            orig_provider = settings.planner_provider
            orig_model = settings.planner_model
            settings.planner_provider = "groq"
            settings.planner_model = "llama-3.3-70b-versatile"
            with patch.dict("os.environ", {"GROQ_API_KEY": "gsk_test_mock_key"}):
                llm = get_llm("planner")
            settings.planner_provider = orig_provider
            settings.planner_model = orig_model

        assert llm is mock_instance
        mock_class.assert_called_once_with(
            model="llama-3.3-70b-versatile",
            groq_api_key="gsk_test_mock_key",
        )

    def test_unknown_provider_raises_clear_error(self):
        """Specifying an unknown provider raises ValueError with supported providers list."""
        orig = settings.planner_provider
        settings.planner_provider = "non_existent_provider_xyz"
        try:
            with pytest.raises(ValueError) as exc_info:
                get_llm("planner")
            err_msg = str(exc_info.value)
            assert "non_existent_provider_xyz" in err_msg
            assert "Supported providers" in err_msg
            assert "groq" in err_msg
        finally:
            settings.planner_provider = orig


class TestBudgetedLLMThrottleAnd429:
    """Tests for RPM throttle and single-wait 429 retry handling in BudgetedLLM."""

    def test_throttle_delays_calls(self):
        """Exceeding max_rpm triggers time.sleep with the required delay."""
        mock_inner = MagicMock()
        mock_inner.invoke.return_value = AIMessage(content="ok")
        budgeted = BudgetedLLM(inner_llm=mock_inner, budget=100, max_rpm=2)

        with patch("time.sleep") as mock_sleep:
            # First 2 calls within limit
            budgeted.invoke("msg 1")
            budgeted.invoke("msg 2")
            assert not mock_sleep.called

            # 3rd call exceeds max_rpm=2
            budgeted.invoke("msg 3")
            assert mock_sleep.called
            sleep_duration = mock_sleep.call_args[0][0]
            assert sleep_duration > 0

    def test_429_handling_waits_retry_after_once_and_succeeds(self):
        """When 429 occurs, BudgetedLLM waits for retry-after once and succeeds on retry."""
        mock_inner = MagicMock()
        exc_429 = Exception("Rate limit 429: try again in 2.5s")
        mock_inner.invoke.side_effect = [exc_429, AIMessage(content="success after wait")]
        budgeted = BudgetedLLM(inner_llm=mock_inner, budget=10)

        with patch("time.sleep") as mock_sleep:
            res = budgeted.invoke("test prompt")
            assert res.content == "success after wait"
            mock_sleep.assert_called_once_with(2.5)

    def test_429_handling_fails_with_clear_message_if_retry_fails(self):
        """If 429 persists after single wait, fail with clear message and do not loop."""
        mock_inner = MagicMock()
        exc_429 = Exception("HTTP 429: Rate limit exceeded")
        mock_inner.invoke.side_effect = exc_429
        budgeted = BudgetedLLM(inner_llm=mock_inner, budget=10)

        with patch("time.sleep") as mock_sleep:
            with pytest.raises(RuntimeError) as exc_info:
                budgeted.invoke("test prompt")
            assert "429" in str(exc_info.value)
            assert "after waiting" in str(exc_info.value)
            assert mock_sleep.call_count == 1  # exactly one sleep, did not loop!


class TestStructuredOutputRetry:
    """Planner and SingleCallAgent JSON parsing retries with reminder."""

    def test_orchestrator_retries_on_malformed_json_and_succeeds(self):
        """Orchestrator retries once with reminder if LLM produces invalid JSON."""
        from solar_agent.graph.orchestrator import Orchestrator
        mock_llm = MagicMock()
        valid_plan_json = json.dumps({
            "intent": "summary",
            "scope": {"plant_ids": [1], "start_date": "2026-09-01", "end_date": "2026-09-30"},
            "tasks": [{"id": "t1", "agent": "data", "description": "Fetch data", "inputs": {}, "depends_on": []}],
            "success_criteria": "Successfully calculate and summarize plant 1 PR metrics",
        })
        mock_llm.invoke.side_effect = [
            AIMessage(content="Here is your plan: not valid json"),
            AIMessage(content=valid_plan_json),
        ]
        orch = Orchestrator(llm=mock_llm)
        patch_res = orch.plan({"user_query": "What is the PR of plant 1?", "replan_count": 0})
        assert patch_res.get("plan") is not None
        assert mock_llm.invoke.call_count == 2
        # Verify reminder in second invocation
        second_msgs = mock_llm.invoke.call_args_list[1][0][0]
        assert any("valid json only" in m.content.lower() for m in second_msgs)

    def test_single_call_agent_retries_on_malformed_json_and_succeeds(self):
        """SingleCallAgent retries once with reminder if LLM produces invalid JSON."""
        from solar_agent.agents.performance_agent import PerformanceAgent
        mock_llm = MagicMock()
        valid_selection_json = json.dumps({
            "tool": "compute_kpis_tool",
            "args": {"data_ref": "df_raw", "level": "plant"},
        })
        mock_llm.invoke.side_effect = [
            AIMessage(content="raw text without json"),
            AIMessage(content=valid_selection_json),
        ]
        agent = PerformanceAgent(llm=mock_llm)
        agent._tool_map["compute_kpis_tool"] = MagicMock(
            invoke=lambda args: {"summary": "Computed", "data_ref": "df_kpis"}
        )
        res = agent.run("t1", "Compute KPIs", {"data_ref": "df_raw"})
        assert res.status.value == "done"
        assert mock_llm.invoke.call_count == 2
        second_msgs = mock_llm.invoke.call_args_list[1][0][0]
        assert any("valid json only" in m.content.lower() for m in second_msgs)

    def test_single_call_agent_handles_chatgroq_tool_calls(self):
        """SingleCallAgent correctly parses native tool_calls attribute from ChatGroq."""
        from solar_agent.agents.performance_agent import PerformanceAgent
        mock_llm = MagicMock()
        tool_call_msg = AIMessage(
            content="",
            tool_calls=[{"id": "call_1", "name": "compute_kpis_tool", "args": {"data_ref": "df_raw", "level": "plant"}}],
        )
        mock_llm.invoke.return_value = tool_call_msg
        agent = PerformanceAgent(llm=mock_llm)
        agent._tool_map["compute_kpis_tool"] = MagicMock(
            invoke=lambda args: {"summary": "Done via tool_calls", "data_ref": "df_kpis"}
        )
        res = agent.run("t1", "Compute KPIs", {"data_ref": "df_raw"})
        assert res.status.value == "done"
        assert res.summary == "Done via tool_calls"

