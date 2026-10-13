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


import re

_SHARED_CACHES: dict[str, dict[str, dict]] = {}


class CacheMissError(RuntimeError):
    """Raised in replay mode when a message hash has no cached response."""


def _normalize_content(content: Any) -> str:
    if not isinstance(content, str):
        content = str(content)
    # Normalize ephemeral dataframe references like df_8e45daf4 or df_20260901 to df_ref
    return re.sub(r"\bdf_[0-9a-f]{8}\b", "df_ref", content)


def _serialize_messages(messages: list[BaseMessage]) -> list[dict]:
    res = []
    for m in messages:
        if isinstance(m, SystemMessage):
            res.append({"type": "system", "content": _normalize_content(m.content)})
        elif isinstance(m, HumanMessage):
            res.append({"type": "human", "content": _normalize_content(m.content)})
        elif isinstance(m, AIMessage):
            res.append({"type": "ai", "content": _normalize_content(m.content), "tool_calls": getattr(m, "tool_calls", [])})
        else:
            res.append({"type": "other", "content": _normalize_content(str(m.content))})
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
        cache_key = str(self.cache_file.resolve())

        if cache_key not in _SHARED_CACHES:
            _SHARED_CACHES[cache_key] = {}
            if self.mode in ("replay", "record") and self.cache_file.exists():
                try:
                    with open(self.cache_file, "r", encoding="utf-8") as f:
                        _SHARED_CACHES[cache_key] = json.load(f)
                    logger.info(
                        "Loaded %d cached LLM responses from %s", len(_SHARED_CACHES[cache_key]), self.cache_file
                    )
                except Exception as e:
                    logger.error("Failed to load LLM cache: %s", e)
        elif self.mode in ("replay", "record") and not _SHARED_CACHES[cache_key] and self.cache_file.exists():
            try:
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    _SHARED_CACHES[cache_key] = json.load(f)
            except Exception:
                pass

        self.cache = _SHARED_CACHES[cache_key]

    # ---- public API -------------------------------------------------------

    def cache_size(self) -> int:
        """Return number of cached entries."""
        return len(self.cache)

    def bind_tools(self, tools: list, **kwargs) -> "ReplayLLM":
        bound_fallback = self.fallback_llm.bind_tools(tools, **kwargs) if self.fallback_llm and hasattr(self.fallback_llm, "bind_tools") else self.fallback_llm
        r = ReplayLLM(
            fallback_llm=bound_fallback,
            mode=self.mode,
            cache_name=self.cache_file.name,
        )
        r.cache = self.cache
        return r

    def bind(self, **kwargs) -> "ReplayLLM":
        bound_fallback = self.fallback_llm.bind(**kwargs) if self.fallback_llm and hasattr(self.fallback_llm, "bind") else self.fallback_llm
        r = ReplayLLM(
            fallback_llm=bound_fallback,
            mode=self.mode,
            cache_name=self.cache_file.name,
        )
        r.cache = self.cache
        return r


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
        try:
            if self.cache_file.exists():
                with open(self.cache_file, "r", encoding="utf-8") as f:
                    disk_cache = json.load(f)
                    self.cache.update(disk_cache)
        except Exception:
            pass
        with open(self.cache_file, "w", encoding="utf-8") as f:
            json.dump(self.cache, f, indent=2, default=str)
