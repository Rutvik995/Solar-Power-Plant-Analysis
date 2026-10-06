"""
graph/budgeted_llm.py – LLM wrapper that enforces a per-run call budget.

BudgetedLLM wraps any LangChain chat model (or ReplayLLM) and tracks how many
times invoke() is called per run_id. When the count exceeds the configured
budget it raises BudgetExceeded instead of making the API call.

Budget isolation
────────────────
Each call to `graph.invoke()` generates a unique run_id.  The call counter is
stored in a per-instance dict keyed by run_id so that two sequential (or even
concurrent) queries on the same BudgetedLLM instance each receive a full, fresh
budget.  A threading.Lock protects the counter dict.

Usage:
    from solar_agent.graph.budgeted_llm import BudgetedLLM
    llm = BudgetedLLM(inner_llm, budget=12)
    # pass run_id per-call:
    response = llm.invoke(messages, run_id="run-abc")
    print(llm.call_count("run-abc"))

Design:
  - Each BudgetedLLM instance owns a _counters dict and a Lock.
  - bind_tools() / bind() return a new BudgetedLLM sharing the SAME _counters
    dict + lock, so tool-bound copies (from BaseSpecialistAgent.__init__)
    stay budgeted under the same run_id.
  - Supports all methods required by LangChain chat model interface:
    invoke(), bind_tools(), bind()
"""

from __future__ import annotations

import logging
import threading
import uuid
from typing import Any

from solar_agent.config.settings import settings
from solar_agent.state import BudgetExceeded

logger = logging.getLogger(__name__)

_DEFAULT_RUN_ID = "__default__"


class BudgetedLLM:
    """
    Thin wrapper that counts LLM calls per run_id and raises BudgetExceeded at the limit.

    Parameters
    ----------
    inner_llm  : The underlying LangChain model (ChatGoogleGenerativeAI, ReplayLLM, mock …)
    budget     : Max calls allowed per run_id. Defaults to settings.max_llm_calls_per_query.
    _counters  : Internal dict shared across bind_tools copies. Do not pass externally.
    _lock      : Threading lock shared across bind_tools copies. Do not pass externally.
    """

    def __init__(
        self,
        inner_llm: Any,
        budget: int | None = None,
        _counters: dict[str, int] | None = None,
        _lock: threading.Lock | None = None,
    ) -> None:
        self._inner = inner_llm
        self._budget = budget if budget is not None else settings.max_llm_calls_per_query
        # Shared mutable dict: run_id -> call_count
        self._counters: dict[str, int] = _counters if _counters is not None else {}
        self._lock: threading.Lock = _lock if _lock is not None else threading.Lock()

    # ── properties ──────────────────────────────────────────────────────────

    def call_count(self, run_id: str = _DEFAULT_RUN_ID) -> int:
        """Return the call count for the given run_id."""
        with self._lock:
            return self._counters.get(run_id, 0)

    # ── LLM interface ───────────────────────────────────────────────────────

    def invoke(self, messages: Any, **kwargs: Any) -> Any:
        # Extract run_id from kwargs (passed by graph nodes) or use default
        run_id: str = kwargs.pop("run_id", _DEFAULT_RUN_ID)

        with self._lock:
            self._counters[run_id] = self._counters.get(run_id, 0) + 1
            count = self._counters[run_id]

        logger.debug(
            "[BudgetedLLM] run_id=%s call=%d/%d", run_id, count, self._budget
        )
        if count > self._budget:
            raise BudgetExceeded(
                f"LLM budget exceeded: {count} calls made, limit is {self._budget} "
                f"(run_id={run_id}). "
                "Returning partial answer from completed tasks."
            )
        return self._inner.invoke(messages, **kwargs)

    def bind_tools(self, tools: list, **kwargs: Any) -> "BudgetedLLM":
        """Return a new BudgetedLLM sharing the SAME counters + lock, with tools bound."""
        bound_inner = self._inner.bind_tools(tools, **kwargs)
        return BudgetedLLM(
            inner_llm=bound_inner,
            budget=self._budget,
            _counters=self._counters,  # share counter dict
            _lock=self._lock,          # share lock
        )

    def bind(self, **kwargs: Any) -> "BudgetedLLM":
        bound_inner = self._inner.bind(**kwargs)
        return BudgetedLLM(
            inner_llm=bound_inner,
            budget=self._budget,
            _counters=self._counters,
            _lock=self._lock,
        )

    # Forward any other attribute lookups to the inner LLM
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
