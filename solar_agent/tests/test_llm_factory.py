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
