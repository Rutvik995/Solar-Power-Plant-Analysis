"""
graph/executor.py — DataStore: thread-safe in-memory DataFrame store
with optional parquet spill for large datasets.

The DAG executor (Phase 4) will use this to:
  - Store full DataFrames from tool calls
  - Pass only the string key (data_ref) through the LangGraph state
  - Spill large frames to disk when memory is a concern

Keys are either:
  - Auto-generated:  f"df_{uuid4().hex[:8]}"   (used by data tools)
  - Task-keyed:      task_id string             (used by the executor)
"""

from __future__ import annotations

import threading
import uuid
from pathlib import Path
from typing import Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Module-level backing store + lock
# ---------------------------------------------------------------------------
_store: dict[str, pd.DataFrame] = {}
_lock = threading.RLock()  # re-entrant so nested calls in the same thread are safe

# Spill directory (relative to this file: solar_agent/graph/spill/)
_SPILL_DIR = Path(__file__).parent / "spill"


class DataStore:
    """
    Thread-safe in-memory store for DataFrames shared across agent tasks.

    Usage pattern:
        # Tool stores result and returns the key
        ref = DataStore.store(df)
        return {"data_ref": ref, ...}

        # Downstream tool retrieves it
        df = DataStore.get(ref)

    Parquet spill:
        For very large DataFrames, call spill_to_parquet(key) to write to disk
        and free memory. Use get_or_load(key) to restore transparently.
    """

    # ---------------------------------------------------------------------------
    # Core read/write
    # ---------------------------------------------------------------------------

    @classmethod
    def store(cls, df: pd.DataFrame, key: Optional[str] = None) -> str:
        """
        Store a DataFrame and return its key.
        Auto-generates a short UUID key if none is provided.
        """
        if key is None:
            key = f"df_{uuid.uuid4().hex[:8]}"
        with _lock:
            _store[key] = df
        return key

    @classmethod
    def store_with_key(cls, key: str, df: pd.DataFrame) -> str:
        """
        Store with an explicit key (e.g. task_id from the DAG executor).
        Overwrites silently if the key already exists.
        """
        with _lock:
            _store[key] = df
        return key

    @classmethod
    def get(cls, key: str) -> Optional[pd.DataFrame]:
        """
        Retrieve a DataFrame by key.
        Returns None (never raises) if the key is not found.
        """
        with _lock:
            return _store.get(key)

    @classmethod
    def get_or_load(cls, key: str) -> Optional[pd.DataFrame]:
        """
        Retrieve from memory; transparently loads from parquet spill if not found.
        Returns None if neither in memory nor on disk.
        """
        df = cls.get(key)
        if df is not None:
            return df
        # Check spill directory
        path = _SPILL_DIR / f"{key}.parquet"
        if path.exists():
            return cls._load_parquet(key, path)
        return None

    @classmethod
    def delete(cls, key: str) -> None:
        """Remove a single entry from the in-memory store (does not delete spill file)."""
        with _lock:
            _store.pop(key, None)

    @classmethod
    def clear(cls) -> None:
        """
        Remove ALL in-memory entries.
        Call this between independent query runs to avoid memory accumulation.
        Does NOT delete spill files.
        """
        with _lock:
            _store.clear()

    # ---------------------------------------------------------------------------
    # Introspection
    # ---------------------------------------------------------------------------

    @classmethod
    def size(cls) -> int:
        """Number of DataFrames currently in memory."""
        with _lock:
            return len(_store)

    @classmethod
    def keys(cls) -> list[str]:
        """All keys currently in memory."""
        with _lock:
            return list(_store.keys())

    @classmethod
    def memory_usage_mb(cls) -> float:
        """Approximate total memory used by all stored DataFrames (MB)."""
        with _lock:
            total_bytes = sum(
                df.memory_usage(deep=True).sum() for df in _store.values()
            )
        return total_bytes / (1024 * 1024)

    # ---------------------------------------------------------------------------
    # Parquet spill / restore
    # ---------------------------------------------------------------------------

    @classmethod
    def spill_to_parquet(cls, key: str) -> Path:
        """
        Write DataFrame to <spill_dir>/<key>.parquet and remove from memory.
        Useful when a DataFrame is very large and won't be needed immediately.

        Returns the Path of the written file.
        Raises KeyError if the key is not found in memory.
        """
        _SPILL_DIR.mkdir(parents=True, exist_ok=True)
        path = _SPILL_DIR / f"{key}.parquet"

        with _lock:
            df = _store.pop(key, None)

        if df is None:
            raise KeyError(f"DataStore: key '{key}' not found for spill.")

        df.to_parquet(path, index=False, engine="pyarrow")
        return path

    @classmethod
    def _load_parquet(cls, key: str, path: Path) -> pd.DataFrame:
        """Internal: load parquet file back into memory and return the DataFrame."""
        df = pd.read_parquet(path, engine="pyarrow")
        cls.store_with_key(key, df)
        return df

    @classmethod
    def load_from_parquet(cls, key: str) -> pd.DataFrame:
        """
        Explicitly load a spilled DataFrame back into memory.
        Raises FileNotFoundError if the spill file does not exist.
        """
        path = _SPILL_DIR / f"{key}.parquet"
        if not path.exists():
            raise FileNotFoundError(f"DataStore: spill file not found: {path}")
        return cls._load_parquet(key, path)

    @classmethod
    def clear_spill(cls) -> None:
        """Delete all parquet spill files (call at the end of a session)."""
        if _SPILL_DIR.exists():
            for f in _SPILL_DIR.glob("*.parquet"):
                f.unlink(missing_ok=True)
