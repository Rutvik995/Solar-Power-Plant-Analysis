"""
graph/llm_recorder.py – LLM record/replay wrapper.

Wraps any LangChain chat model to cache responses to a JSON file.

Modes:
  record     – call live LLM, save response to cache file
  replay     – serve from cache; raise CacheMissError on miss (never calls live LLM)
  passthrough – ignore cache, always call live LLM

Usage:
    from solar_agent.graph.llm_recorder import ReplayLLM
    llm = ReplayLLM(live_llm, mode="record")   # populates cache
    llm = ReplayLLM(live_llm, mode="replay")   # reads from cache only
"""

import json
import logging
import os
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent / "tests" / "llm_cache"


class CacheMissError(RuntimeError):
    """Raised in replay mode when a message hash has no cached response."""


def _serialize_messages(messages: list[BaseMessage]) -> list[dict]:
    res = []
    for m in messages:
        if isinstance(m, SystemMessage):
            res.append({"type": "system", "content": m.content})
        elif isinstance(m, HumanMessage):
            res.append({"type": "human", "content": m.content})
        elif isinstance(m, AIMessage):
            res.append({"type": "ai", "content": m.content, "tool_calls": getattr(m, "tool_calls", [])})
        else:
            res.append({"type": "other", "content": str(m.content)})
    return res


def _hash_messages(messages: list[BaseMessage]) -> str:
    import hashlib
    serialized = _serialize_messages(messages)
    return hashlib.md5(json.dumps(serialized, sort_keys=True, default=str).encode()).hexdigest()


class ReplayLLM:
    """
    Wraps an LLM to record responses to a JSON cache or replay them without quota.

    In 'replay' mode a cache miss raises CacheMissError — it never silently falls
    through to the live LLM. This makes eval runs deterministic and quota-safe.
    """

    def __init__(
        self,
        fallback_llm: Any = None,
        mode: str = "replay",
        cache_name: str = "llm_cache.json",
    ):
        self.fallback_llm = fallback_llm
        self.mode = mode
        self.cache_file = CACHE_DIR / cache_name
        self.cache: dict[str, dict] = {}

        if self.mode in ("replay", "record") and self.cache_file.exists():
            try:
                with open(self.cache_file, "r") as f:
                    self.cache = json.load(f)
                logger.info(
                    "Loaded %d cached LLM responses from %s", len(self.cache), self.cache_file
                )
            except Exception as e:
                logger.error("Failed to load LLM cache: %s", e)

    # ---- public API -------------------------------------------------------

    def cache_size(self) -> int:
        """Return number of cached entries."""
        return len(self.cache)

    def bind_tools(self, tools: list, **kwargs) -> "ReplayLLM":
        # In replay mode, we just return self because the cached response already has the tool calls.
        # But we do need to save this signature so fallback works if we switch to record mode.
        return self

    def bind(self, **kwargs) -> "ReplayLLM":
        return self

    def invoke(self, messages: list[BaseMessage], **kwargs) -> BaseMessage:
        if self.mode == "passthrough":
            return self.fallback_llm.invoke(messages, **kwargs)

        msg_hash = _hash_messages(messages)

        if msg_hash in self.cache:
            logger.info("LLM cache HIT (%s)", msg_hash[:8])
            cached = self.cache[msg_hash]
            return AIMessage(
                content=cached.get("content", ""),
                tool_calls=cached.get("tool_calls", []),
            )

        if self.mode == "replay":
            raise CacheMissError(
                f"Cache miss in replay mode (hash={msg_hash[:8]}). "
                f"Run scripts/rerecord.py to populate the cache. "
                f"Cache currently holds {len(self.cache)} entries."
            )

        # mode == "record" → call live LLM and save
        if not self.fallback_llm:
            raise RuntimeError("No fallback LLM provided for recording mode.")

        logger.info("Calling live LLM (record mode, hash=%s)...", msg_hash[:8])
        resp = self.fallback_llm.invoke(messages, **kwargs)

        self.cache[msg_hash] = {
            "content": resp.content,
            "tool_calls": getattr(resp, "tool_calls", []),
        }
        self._save()
        logger.info(
            "Saved response to cache (hash=%s, total=%d entries)", msg_hash[:8], len(self.cache)
        )
        return resp

    # ---- private ----------------------------------------------------------

    def _save(self) -> None:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        with open(self.cache_file, "w") as f:
            json.dump(self.cache, f, indent=2, default=str)
