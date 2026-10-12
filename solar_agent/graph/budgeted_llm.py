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
import re
import threading
import time
from typing import Any

from solar_agent.config.settings import settings
from solar_agent.state import BudgetExceeded

logger = logging.getLogger(__name__)

_DEFAULT_RUN_ID = "__default__"


def _is_429(exc: Exception) -> bool:
    """Check if an exception is an HTTP 429 / Rate Limit error."""
    if getattr(exc, "status_code", None) == 429:
        return True
    response = getattr(exc, "response", None)
    if response and getattr(response, "status_code", None) == 429:
        return True
    msg = str(exc).lower()
    return "429" in msg or "rate limit" in msg or "rate_limit" in msg


def _extract_retry_after(exc: Exception) -> float:
    """Extract retry-after time in seconds from headers or message, or default to 1.0s."""
    response = getattr(exc, "response", None)
    if response and hasattr(response, "headers") and response.headers:
        h = response.headers.get("retry-after") or response.headers.get("Retry-After")
        if h:
            try:
                return max(0.01, float(h))
            except (ValueError, TypeError):
                pass
    msg = str(exc)
    m = re.search(r"try again in ([\d\.]+)s", msg, re.IGNORECASE)
    if m:
        return max(0.01, float(m.group(1)))
    m = re.search(r"retry[- ]after[: ]+([\d\.]+)", msg, re.IGNORECASE)
    if m:
        return max(0.01, float(m.group(1)))
    return 1.0


class BudgetedLLM:
    """
    Thin wrapper that counts LLM calls per run_id, enforces RPM limits,
    and handles HTTP 429 with a single retry-after wait.

    Parameters
    ----------
    inner_llm    : The underlying LangChain model (ChatGroq, ReplayLLM, mock …)
    budget       : Max calls allowed per run_id. Defaults to settings.max_llm_calls_per_query.
    max_rpm      : Max calls per minute across calls. Defaults to settings.llm_max_rpm (20).
    _counters    : Internal dict shared across bind_tools copies. Do not pass externally.
    _timestamps  : Internal list shared across bind_tools copies. Do not pass externally.
    _lock        : Threading lock shared across bind_tools copies. Do not pass externally.
    """

    def __init__(
        self,
        inner_llm: Any,
        budget: int | None = None,
        max_rpm: int | None = None,
        _counters: dict[str, int] | None = None,
        _timestamps: list[float] | None = None,
        _lock: threading.Lock | None = None,
    ) -> None:
        self._inner = inner_llm
        self._budget = budget if budget is not None else settings.max_llm_calls_per_query
        self._max_rpm = max_rpm if max_rpm is not None else getattr(settings, "llm_max_rpm", 20)
        # Shared mutable dict: run_id -> call_count
        self._counters: dict[str, int] = _counters if _counters is not None else {}
        # Shared sliding-window call timestamps for RPM throttling
        self._timestamps: list[float] = _timestamps if _timestamps is not None else []
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

        # 1. Budget check (blocked before making call)
        with self._lock:
            current = self._counters.get(run_id, 0)
            if current >= self._budget:
                raise BudgetExceeded(
                    f"LLM budget exceeded: {current} calls made, limit is {self._budget} "
                    f"(run_id={run_id}). "
                    "Returning partial answer from completed tasks."
                )
            self._counters[run_id] = current + 1
            count = self._counters[run_id]

        logger.debug(
            "[BudgetedLLM] run_id=%s call=%d/%d", run_id, count, self._budget
        )

        # 2. RPM throttle
        if self._max_rpm and self._max_rpm > 0:
            with self._lock:
                now = time.monotonic()
                # Evict timestamps older than 60 seconds
                self._timestamps[:] = [t for t in self._timestamps if now - t < 60.0]
                if len(self._timestamps) >= self._max_rpm:
                    oldest = self._timestamps[0]
                    sleep_needed = 60.0 - (now - oldest)
                else:
                    sleep_needed = 0.0

            if sleep_needed > 0:
                logger.warning(
                    "[BudgetedLLM] RPM limit of %d reached; sleeping %.2fs",
                    self._max_rpm, sleep_needed,
                )
                time.sleep(sleep_needed)

            with self._lock:
                self._timestamps.append(time.monotonic())

        # 3. Call inner LLM with single 429 retry
        try:
            return self._inner.invoke(messages, **kwargs)
        except Exception as exc:
            if _is_429(exc):
                retry_after = _extract_retry_after(exc)
                logger.warning(
                    "[BudgetedLLM] HTTP 429 received. Waiting %.2fs for retry-after before single retry.",
                    retry_after,
                )
                time.sleep(retry_after)
                try:
                    return self._inner.invoke(messages, **kwargs)
                except Exception as retry_exc:
                    raise RuntimeError(
                        f"LLM request failed with HTTP 429 rate limit after waiting {retry_after}s: {retry_exc}"
                    ) from retry_exc
            raise

    def bind_tools(self, tools: list, **kwargs: Any) -> "BudgetedLLM":
        """Return a new BudgetedLLM sharing the SAME counters + lock + timestamps, with tools bound."""
        bound_inner = self._inner.bind_tools(tools, **kwargs)
        return BudgetedLLM(
            inner_llm=bound_inner,
            budget=self._budget,
            max_rpm=self._max_rpm,
            _counters=self._counters,    # share counter dict
            _timestamps=self._timestamps,# share timestamp window
            _lock=self._lock,            # share lock
        )

    def bind(self, **kwargs: Any) -> "BudgetedLLM":
        bound_inner = self._inner.bind(**kwargs)
        return BudgetedLLM(
            inner_llm=bound_inner,
            budget=self._budget,
            max_rpm=self._max_rpm,
            _counters=self._counters,
            _timestamps=self._timestamps,
            _lock=self._lock,
        )

    # Forward any other attribute lookups to the inner LLM
    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)
