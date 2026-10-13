"""
graph/llm_recorder.py – LLM record/replay wrapper.

Wraps any LangChain chat model to cache responses to a JSON file.

Cache key = MD5 of the full request:
  - model name (from the underlying LLM)
  - system prompt
  - all messages (role + content + tool_calls + tool results)
  - tool schemas (names + parameter descriptions, sorted)

Modes:
  record     – call live LLM, save response to cache file
  replay     – serve from cache; raise CacheMissError on miss (never calls live LLM)
  passthrough – ignore cache, always call live LLM

Usage:
    from solar_agent.graph.llm_recorder import ReplayLLM
    llm = ReplayLLM(live_llm, mode="record")   # populates cache
    llm = ReplayLLM(live_llm, mode="replay")   # reads from cache only
"""

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage, AIMessage, ToolMessage

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).parent.parent / "tests" / "llm_cache"

_SHARED_CACHES: dict[str, dict[str, dict]] = {}


class CacheMissError(RuntimeError):
    """Raised in replay mode when a message hash has no cached response."""


def _normalize_content(content: Any) -> str:
    if not isinstance(content, str):
        content = str(content)
    # Normalize ephemeral dataframe references like df_8e45daf4 or df_20260901 to df_ref
    return re.sub(r"\bdf_[0-9a-f]{8}\b", "df_ref", content)


def _serialize_tool_schema(tool) -> dict:
    """Extract name + parameter schema from a LangChain tool for hashing."""
    try:
        name = getattr(tool, "name", "") or str(tool)
        schema = {}
        if hasattr(tool, "args_schema") and tool.args_schema is not None:
            s = tool.args_schema
            if hasattr(s, "schema"):
                schema = s.schema()
            elif hasattr(s, "model_json_schema"):
                schema = s.model_json_schema()
        return {"name": name, "schema": schema}
    except Exception:
        return {"name": str(tool), "schema": {}}


def _serialize_messages(messages: list[BaseMessage], model_name: str = "", tool_schemas: list = None) -> dict:
    """Serialize all request components into a stable dict for hashing."""
    msgs = []
    for m in messages:
        if isinstance(m, SystemMessage):
            msgs.append({"type": "system", "content": _normalize_content(m.content)})
        elif isinstance(m, HumanMessage):
            msgs.append({"type": "human", "content": _normalize_content(m.content)})
        elif isinstance(m, AIMessage):
            msgs.append({
                "type": "ai",
                "content": _normalize_content(m.content),
                "tool_calls": getattr(m, "tool_calls", []),
            })
        elif isinstance(m, ToolMessage):
            msgs.append({
                "type": "tool",
                "content": _normalize_content(m.content),
                "tool_call_id": getattr(m, "tool_call_id", ""),
            })
        else:
            msgs.append({"type": "other", "content": _normalize_content(str(m.content))})

    schemas = sorted(tool_schemas or [], key=lambda s: s.get("name", ""))

    return {
        "model": model_name,
        "messages": msgs,
        "tool_schemas": schemas,
    }


def _hash_request(messages: list[BaseMessage], model_name: str = "", tool_schemas: list = None) -> str:
    payload = _serialize_messages(messages, model_name=model_name, tool_schemas=tool_schemas)
    return hashlib.md5(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


class ReplayLLM:
    """
    Wraps an LLM to record responses to a JSON cache or replay them without quota.

    Cache key = MD5(model_name + all messages incl. tool results + tool schemas).

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
        # Track bound tool schemas for this instance
        self._bound_tool_schemas: list[dict] = []

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

    def _model_name(self) -> str:
        """Extract the model name string from the underlying LLM."""
        llm = self.fallback_llm
        if llm is None:
            return ""
        # Try common attribute names
        for attr in ("model", "model_name", "model_id", "deployment_name"):
            v = getattr(llm, attr, None)
            if v and isinstance(v, str):
                return v
        return str(type(llm).__name__)

    # ---- public API -------------------------------------------------------

    def cache_size(self) -> int:
        """Return number of cached entries."""
        return len(self.cache)

    def bind_tools(self, tools: list, **kwargs) -> "ReplayLLM":
        bound_fallback = (
            self.fallback_llm.bind_tools(tools, **kwargs)
            if self.fallback_llm and hasattr(self.fallback_llm, "bind_tools")
            else self.fallback_llm
        )
        r = ReplayLLM(
            fallback_llm=bound_fallback,
            mode=self.mode,
            cache_name=self.cache_file.name,
        )
        r.cache = self.cache
        r._bound_tool_schemas = [_serialize_tool_schema(t) for t in tools]
        return r

    def bind(self, **kwargs) -> "ReplayLLM":
        bound_fallback = (
            self.fallback_llm.bind(**kwargs)
            if self.fallback_llm and hasattr(self.fallback_llm, "bind")
            else self.fallback_llm
        )
        r = ReplayLLM(
            fallback_llm=bound_fallback,
            mode=self.mode,
            cache_name=self.cache_file.name,
        )
        r.cache = self.cache
        r._bound_tool_schemas = list(self._bound_tool_schemas)
        return r

    def invoke(self, messages: list[BaseMessage], **kwargs) -> BaseMessage:
        if self.mode == "passthrough":
            return self.fallback_llm.invoke(messages, **kwargs)

        model_name = self._model_name()
        msg_hash = _hash_request(messages, model_name=model_name, tool_schemas=self._bound_tool_schemas)

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
                f"Run scripts/run_eval.py --record to populate the cache. "
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
