"""
config/llm.py – LLM factory with role-based configuration.

Roles
─────
  planner      → Orchestrator. Highest-quality reasoning.
  agent        → Specialist agents (Fault, Performance, Environment).
  synthesizer  → Synthesizer. High-quality prose generation.

Provider selection
──────────────────
Each role reads its provider and model from settings:
  - planner_provider  / planner_model
  - agent_provider    / agent_model
  - synthesizer_provider / synthesizer_model

Supported providers (lazily imported so unused packages are never required):
  gemini    → langchain_google_genai.ChatGoogleGenerativeAI
  anthropic → langchain_anthropic.ChatAnthropic
  openai    → langchain_openai.ChatOpenAI
  ollama    → langchain_community.chat_models.ChatOllama

Budget + Cache
──────────────
get_llm(role) ALWAYS returns the raw provider LLM.
Wrap it yourself:
    from solar_agent.graph.budgeted_llm import BudgetedLLM
    from solar_agent.graph.llm_recorder import ReplayLLM
    budgeted = BudgetedLLM(ReplayLLM(get_llm(role), mode="replay"))

The graph module (`graph.py`) is the single place that applies both wrappers,
so changing provider never changes how budget counting or cache keying works.
Cache keys are based on the serialised message content — they are provider-
agnostic. Changing provider does NOT change cache keys; changing model DOES
(because the model name appears in the system prompt context).

Usage
─────
    from solar_agent.config.llm import get_llm
    llm = get_llm("planner")   # returns a ChatGoogleGenerativeAI (or other)
"""

from __future__ import annotations

import logging
from typing import Any

from solar_agent.config.settings import settings

import os

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Known roles
# ---------------------------------------------------------------------------

_ROLES = {"planner", "agent", "synthesizer"}


# ---------------------------------------------------------------------------
# Provider factory (lazy imports)
# ---------------------------------------------------------------------------

def _build_groq(model: str, **kwargs: Any):
    try:
        from langchain_groq import ChatGroq  # type: ignore[import]
    except ImportError as e:
        raise ImportError(
            "Provider 'groq' requires the langchain-groq package. "
            "Install it with: pip install langchain-groq"
        ) from e
    api_key = os.getenv("GROQ_API_KEY") or getattr(settings, "groq_api_key", "") or None
    call_kwargs = dict(kwargs)
    if api_key:
        call_kwargs["groq_api_key"] = api_key
    if model:
        call_kwargs["model"] = model
    return ChatGroq(**call_kwargs)


def _build_gemini(model: str, **kwargs: Any):
    try:
        from langchain_google_genai import ChatGoogleGenerativeAI  # type: ignore[import]
    except ImportError as e:
        raise ImportError(
            "Provider 'gemini' requires the langchain-google-genai package. "
            "Install it with: pip install langchain-google-genai"
        ) from e
    api_key = settings.google_api_key or None
    return ChatGoogleGenerativeAI(model=model, google_api_key=api_key, **kwargs)


def _build_anthropic(model: str, **kwargs: Any):
    try:
        from langchain_anthropic import ChatAnthropic  # type: ignore[import]
    except ImportError as e:
        raise ImportError(
            "Provider 'anthropic' requires the langchain-anthropic package. "
            "Install it with: pip install langchain-anthropic"
        ) from e
    return ChatAnthropic(model=model, **kwargs)


def _build_openai(model: str, **kwargs: Any):
    try:
        from langchain_openai import ChatOpenAI  # type: ignore[import]
    except ImportError as e:
        raise ImportError(
            "Provider 'openai' requires the langchain-openai package. "
            "Install it with: pip install langchain-openai"
        ) from e
    return ChatOpenAI(model=model, **kwargs)


def _build_ollama(model: str, **kwargs: Any):
    try:
        from langchain_community.chat_models import ChatOllama  # type: ignore[import]
    except ImportError as e:
        raise ImportError(
            "Provider 'ollama' requires the langchain-community package. "
            "Install it with: pip install langchain-community"
        ) from e
    return ChatOllama(model=model, **kwargs)


_PROVIDER_BUILDERS = {
    "groq": _build_groq,
    "gemini": _build_gemini,
    "anthropic": _build_anthropic,
    "openai": _build_openai,
    "ollama": _build_ollama,
}


# ---------------------------------------------------------------------------
# Role → (provider, model) resolution
# ---------------------------------------------------------------------------

def _role_config(role: str) -> tuple[str, str]:
    """Return (provider, model) for the given role, reading from env and settings."""
    default_provider = getattr(settings, "llm_provider", "groq") or os.getenv("LLM_PROVIDER") or "groq"

    if role == "planner":
        provider = getattr(settings, "planner_provider", "") or os.getenv("PLANNER_PROVIDER") or default_provider
        model = (
            getattr(settings, "planner_model", "")
            or os.getenv("LLM_PLANNER_MODEL", "")
            or os.getenv("PLANNER_MODEL", "")
        )
    elif role == "agent":
        provider = getattr(settings, "agent_provider", "") or os.getenv("AGENT_PROVIDER") or default_provider
        model = (
            getattr(settings, "agent_model", "")
            or os.getenv("LLM_AGENT_MODEL", "")
            or getattr(settings, "fast_model", "")
            or os.getenv("AGENT_MODEL", "")
        )
    elif role == "synthesizer":
        provider = getattr(settings, "synthesizer_provider", "") or os.getenv("SYNTHESIZER_PROVIDER") or default_provider
        model = (
            getattr(settings, "synthesizer_model", "")
            or os.getenv("LLM_SYNTH_MODEL", "")
            or os.getenv("SYNTHESIZER_MODEL", "")
        )
    else:
        raise ValueError(
            f"Unknown LLM role '{role}'. Valid roles: {sorted(_ROLES)}"
        )
    return provider, model


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_llm(role: str, **kwargs: Any) -> Any:
    """
    Return a raw LangChain chat model for *role*.

    The caller is responsible for wrapping with BudgetedLLM / ReplayLLM.

    Parameters
    ----------
    role   : One of "planner", "agent", "synthesizer".
    **kwargs: Extra kwargs forwarded to the provider constructor
              (e.g. temperature=0).

    Raises
    ------
    ValueError  if role or provider is unknown.
    ImportError if the required provider package is not installed.
    """
    if role not in _ROLES:
        raise ValueError(
            f"Unknown LLM role '{role}'. Valid roles: {sorted(_ROLES)}"
        )

    provider, model = _role_config(role)
    builder = _PROVIDER_BUILDERS.get(provider)
    if builder is None:
        raise ValueError(
            f"Unknown LLM provider '{provider}' for role '{role}'. "
            f"Supported providers: {sorted(_PROVIDER_BUILDERS)}"
        )

    logger.info("Building LLM for role='%s': provider=%s model=%s", role, provider, model)
    return builder(model, **kwargs)
