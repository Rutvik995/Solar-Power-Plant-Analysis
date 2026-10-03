"""DataStore: in-memory store for DataFrames shared between tasks."""
from __future__ import annotations
import uuid
import pandas as pd
from typing import Optional

_store: dict[str, pd.DataFrame] = {}


class DataStore:
    """
    Thread-safe (within a single run) in-memory store for DataFrames.

    Agents store large DataFrames here and pass only the string key
    ('data_ref') through the LangGraph state, keeping LLM context lean.
    """

    @classmethod
    def store(cls, df: pd.DataFrame) -> str:
        """Store a DataFrame and return its key."""
        key = f"df_{uuid.uuid4().hex[:8]}"
        _store[key] = df
        return key

    @classmethod
    def get(cls, key: str) -> Optional[pd.DataFrame]:
        """Retrieve a DataFrame by key. Returns None if not found."""
        return _store.get(key)

    @classmethod
    def clear(cls) -> None:
        """Clear all stored DataFrames (call between runs)."""
        _store.clear()

    @classmethod
    def size(cls) -> int:
        return len(_store)
