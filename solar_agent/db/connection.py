"""
Database connection module for the Solar Power Plant Analysis system.

Provides:
- A READ-ONLY SQLAlchemy async engine (psycopg v3 driver)
- A synchronous engine for tools that use pandas.read_sql
- Connection health check
- Context managers for sessions

The DB user must only have SELECT privileges on the allowed tables.
Additional SQL-level guardrails are enforced in tools/data_tools.py::run_sql_readonly().

Note: Async SQLAlchemy imports are intentionally lazy (inside functions) to avoid
triggering the greenlet dependency at module import time. This allows test modules
that only use the sync engine or SQL validation to import without errors.
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager, contextmanager
from typing import TYPE_CHECKING, AsyncGenerator, Generator

from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session, sessionmaker

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker as AsyncSessionMaker

from solar_agent.config.settings import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine creation helpers
# ---------------------------------------------------------------------------

def _make_sync_engine():
    """
    Synchronous engine using psycopg (v3) driver.
    Used by pandas.read_sql and tools that are not async.
    """
    # Convert async URL to sync: replace 'postgresql+psycopg' → keep as is
    # psycopg v3 supports both sync and async; use same driver string for sync
    url = settings.database_url
    if url.startswith("postgresql+psycopg://"):
        sync_url = url  # already the right driver
    else:
        sync_url = url.replace("postgresql+asyncpg://", "postgresql+psycopg://")

    engine = create_engine(
        sync_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )
    _attach_readonly_guard(engine)
    return engine


def _make_async_engine():
    """
    Async engine for LangGraph agent nodes that run in async context.
    Uses psycopg (v3) async driver: 'postgresql+psycopg_async'.
    Imported lazily to avoid greenlet dependency at module load time.
    """
    from sqlalchemy.ext.asyncio import create_async_engine as _create_async_engine  # lazy

    # Build async URL
    url = settings.database_url
    if url.startswith("postgresql+psycopg://"):
        async_url = url.replace("postgresql+psycopg://", "postgresql+psycopg_async://")
    elif url.startswith("postgresql://"):
        async_url = url.replace("postgresql://", "postgresql+psycopg_async://")
    else:
        async_url = url  # assume already correct async URL

    engine = _create_async_engine(
        async_url,
        pool_pre_ping=True,
        pool_size=5,
        max_overflow=10,
        echo=False,
    )
    return engine


# ---------------------------------------------------------------------------
# Read-only guard: reject any non-SELECT statement at the connection level
# ---------------------------------------------------------------------------

def _attach_readonly_guard(sync_engine) -> None:
    """
    Fires before every statement on the sync engine.
    Raises RuntimeError if a non-SELECT statement somehow bypasses the tool-level guard.
    This is a defense-in-depth measure; the primary guard is in run_sql_readonly().
    """
    @event.listens_for(sync_engine, "before_cursor_execute")
    def before_cursor_execute(conn, cursor, statement, parameters, context, executemany):
        stripped = statement.strip().upper()
        if not stripped.startswith("SELECT") and not stripped.startswith("WITH"):
            raise RuntimeError(
                f"[DB GUARD] Rejected non-SELECT statement. First 80 chars: {statement[:80]!r}"
            )


# ---------------------------------------------------------------------------
# Module-level singletons (lazy initialisation)
# ---------------------------------------------------------------------------

_sync_engine = None
_async_engine = None
_SyncSession = None
_AsyncSession = None


def get_sync_engine():
    global _sync_engine
    if _sync_engine is None:
        _sync_engine = _make_sync_engine()
    return _sync_engine


def get_async_engine():
    global _async_engine
    if _async_engine is None:
        _async_engine = _make_async_engine()
    return _async_engine


def get_sync_sessionmaker() -> sessionmaker:
    global _SyncSession
    if _SyncSession is None:
        _SyncSession = sessionmaker(
            bind=get_sync_engine(),
            autocommit=False,
            autoflush=False,
        )
    return _SyncSession


def get_async_sessionmaker():
    """Returns an async_sessionmaker bound to the async engine. Lazily imported."""
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker  # lazy

    global _AsyncSession
    if _AsyncSession is None:
        _AsyncSession = async_sessionmaker(
            bind=get_async_engine(),
            class_=AsyncSession,
            expire_on_commit=False,
        )
    return _AsyncSession


# ---------------------------------------------------------------------------
# Context managers
# ---------------------------------------------------------------------------

@contextmanager
def sync_session() -> Generator[Session, None, None]:
    """Provide a transactional synchronous session scope."""
    Session_ = get_sync_sessionmaker()
    session = Session_()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


@asynccontextmanager
async def async_session():
    """Provide a transactional async session scope. Lazily imports async SQLAlchemy."""
    Session_ = get_async_sessionmaker()
    async with Session_() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

def check_connection() -> bool:
    """
    Quick connectivity test. Returns True if the DB is reachable, False otherwise.
    Call this at application startup to fail fast on misconfiguration.
    """
    try:
        engine = get_sync_engine()
        with engine.connect() as conn:
            result = conn.execute(text("SELECT 1")).scalar()
            ok = result == 1
            if ok:
                logger.info("DB connection health check: OK")
            else:
                logger.error("DB connection health check: unexpected result %r", result)
            return ok
    except Exception as exc:
        logger.error("DB connection health check FAILED: %s", exc)
        return False


async def async_check_connection() -> bool:
    """Async version of check_connection for use in async startup."""
    try:
        engine = get_async_engine()  # lazy async import happens inside _make_async_engine
        async with engine.connect() as conn:
            result = await conn.execute(text("SELECT 1"))
            ok = result.scalar() == 1
            if ok:
                logger.info("Async DB connection health check: OK")
            return ok
    except Exception as exc:
        logger.error("Async DB connection health check FAILED: %s", exc)
        return False
