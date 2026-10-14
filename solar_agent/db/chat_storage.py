"""
solar_agent/db/chat_storage.py — Database storage for chat sessions and messages.

Features:
  - Uses a separate write-enabled engine (chat_database_url or database_url).
  - Initializes chat schema from db/chat_schema.sql (CREATE TABLE IF NOT EXISTS).
  - At startup, marks any running messages as stopped.
  - CRUD operations for sessions and messages with cascade delete.
  - Question & answer pair deletion.
  - Follow-up context extraction from the last completed assistant payload.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import create_engine, event, text
from sqlalchemy.engine import Engine

from solar_agent.config.settings import settings

logger = logging.getLogger(__name__)

_CHAT_ENGINE: Optional[Engine] = None


def get_chat_engine(url: Optional[str] = None) -> Engine:
    """Return a write-enabled SQLAlchemy engine for chat persistence."""
    global _CHAT_ENGINE
    if url is not None:
        target_url = url
    else:
        if _CHAT_ENGINE is not None:
            return _CHAT_ENGINE
        target_url = settings.chat_database_url or settings.database_url

    # Normalize PostgreSQL driver if needed
    if target_url.startswith("postgresql+asyncpg://"):
        target_url = target_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    elif target_url.startswith("postgresql://"):
        target_url = target_url.replace("postgresql://", "postgresql+psycopg://")

    # Important: Write-enabled engine, do NOT attach read-only guard
    engine = create_engine(
        target_url,
        pool_pre_ping=True,
        echo=False,
    )

    if "sqlite" in target_url:
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_connection, connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    if url is None:
        _CHAT_ENGINE = engine
    return engine


def _load_schema_sql() -> str:
    """Load DDL from db/chat_schema.sql."""
    candidates = [
        Path(__file__).resolve().parent / "chat_schema.sql",
        Path(__file__).resolve().parent.parent.parent / "db" / "chat_schema.sql",
        Path("db/chat_schema.sql").resolve(),
    ]
    for path in candidates:
        if path.exists():
            return path.read_text(encoding="utf-8")

    # Fallback embedded DDL
    return """
    CREATE TABLE IF NOT EXISTS chat_sessions (
        id UUID PRIMARY KEY,
        title TEXT NOT NULL,
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
        updated_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );

    CREATE TABLE IF NOT EXISTS chat_messages (
        id UUID PRIMARY KEY,
        session_id UUID NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
        role VARCHAR(20) NOT NULL,
        content TEXT,
        payload JSONB,
        status VARCHAR(20) NOT NULL CHECK (status IN ('complete', 'running', 'stopped', 'error')),
        created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
    );
    """


def init_chat_db(engine: Optional[Engine] = None) -> None:
    """
    Initialize chat tables using CREATE TABLE IF NOT EXISTS and
    mark any stale running messages as 'stopped' on startup.
    """
    eng = engine or get_chat_engine()
    schema_sql = _load_schema_sql()

    with eng.begin() as conn:
        for stmt in schema_sql.split(";"):
            stmt_clean = stmt.strip()
            if stmt_clean:
                conn.execute(text(stmt_clean))

        # At startup, mark any running message as stopped
        conn.execute(
            text("UPDATE chat_messages SET status = 'stopped' WHERE status = 'running'")
        )

    logger.info("Chat database initialized; stale running messages marked as stopped.")


# ---------------------------------------------------------------------------
# Session Operations
# ---------------------------------------------------------------------------

def list_sessions(engine: Optional[Engine] = None) -> list[dict[str, Any]]:
    eng = engine or get_chat_engine()
    with eng.connect() as conn:
        result = conn.execute(
            text("SELECT id, title, created_at, updated_at FROM chat_sessions ORDER BY updated_at DESC")
        )
        sessions = []
        for row in result.mappings():
            sessions.append({
                "id": str(row["id"]),
                "title": row["title"],
                "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
                "updated_at": row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
            })
        return sessions


def create_session(
    session_id: Optional[uuid.UUID | str] = None,
    title: str = "New Chat",
    engine: Optional[Engine] = None,
) -> dict[str, Any]:
    eng = engine or get_chat_engine()
    s_id = uuid.UUID(str(session_id)) if session_id else uuid.uuid4()
    now = datetime.now(timezone.utc)
    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_sessions (id, title, created_at, updated_at) "
                "VALUES (:id, :title, :created_at, :updated_at)"
            ),
            {"id": str(s_id), "title": title, "created_at": now, "updated_at": now},
        )
    return {
        "id": str(s_id),
        "title": title,
        "created_at": now.isoformat(),
        "updated_at": now.isoformat(),
    }


def get_session(session_id: uuid.UUID | str, engine: Optional[Engine] = None) -> Optional[dict[str, Any]]:
    eng = engine or get_chat_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT id, title, created_at, updated_at FROM chat_sessions WHERE id = :id"),
            {"id": str(session_id)},
        ).mappings().first()
        if not row:
            return None
        return {
            "id": str(row["id"]),
            "title": row["title"],
            "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
            "updated_at": row["updated_at"].isoformat() if hasattr(row["updated_at"], "isoformat") else str(row["updated_at"]),
        }


def update_session(
    session_id: uuid.UUID | str,
    title: str,
    engine: Optional[Engine] = None,
) -> Optional[dict[str, Any]]:
    eng = engine or get_chat_engine()
    now = datetime.now(timezone.utc)
    with eng.begin() as conn:
        res = conn.execute(
            text(
                "UPDATE chat_sessions SET title = :title, updated_at = :updated_at WHERE id = :id"
            ),
            {"id": str(session_id), "title": title, "updated_at": now},
        )
        if res.rowcount == 0:
            return None
    return get_session(session_id, engine=eng)


def delete_session(session_id: uuid.UUID | str, engine: Optional[Engine] = None) -> bool:
    eng = engine or get_chat_engine()
    with eng.begin() as conn:
        res = conn.execute(
            text("DELETE FROM chat_sessions WHERE id = :id"),
            {"id": str(session_id)},
        )
        return res.rowcount > 0


# ---------------------------------------------------------------------------
# Message Operations
# ---------------------------------------------------------------------------

def _clean_payload(payload_raw: Any) -> Any:
    if payload_raw is None:
        return None
    if isinstance(payload_raw, dict):
        return payload_raw
    if isinstance(payload_raw, str):
        try:
            return json.loads(payload_raw)
        except Exception:
            return payload_raw
    return payload_raw


def list_messages(session_id: uuid.UUID | str, engine: Optional[Engine] = None) -> list[dict[str, Any]]:
    eng = engine or get_chat_engine()
    with eng.connect() as conn:
        result = conn.execute(
            text(
                "SELECT id, session_id, role, content, payload, status, created_at "
                "FROM chat_messages WHERE session_id = :session_id ORDER BY created_at ASC"
            ),
            {"session_id": str(session_id)},
        )
        messages = []
        for row in result.mappings():
            messages.append({
                "id": str(row["id"]),
                "session_id": str(row["session_id"]),
                "role": row["role"],
                "content": row["content"] or "",
                "payload": _clean_payload(row["payload"]),
                "status": row["status"],
                "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
            })
        return messages


def create_message(
    message_id: Optional[uuid.UUID | str] = None,
    session_id: uuid.UUID | str = "",
    role: str = "user",
    content: str = "",
    payload: Optional[dict[str, Any]] = None,
    status: str = "complete",
    engine: Optional[Engine] = None,
) -> dict[str, Any]:
    eng = engine or get_chat_engine()
    m_id = uuid.UUID(str(message_id)) if message_id else uuid.uuid4()
    now = datetime.now(timezone.utc)
    payload_to_store = json.dumps(payload) if payload is not None else None

    with eng.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO chat_messages (id, session_id, role, content, payload, status, created_at) "
                "VALUES (:id, :session_id, :role, :content, :payload, :status, :created_at)"
            ),
            {
                "id": str(m_id),
                "session_id": str(session_id),
                "role": role,
                "content": content,
                "payload": payload_to_store,
                "status": status,
                "created_at": now,
            },
        )
        # Touch session updated_at
        conn.execute(
            text("UPDATE chat_sessions SET updated_at = :now WHERE id = :session_id"),
            {"now": now, "session_id": str(session_id)},
        )

    return {
        "id": str(m_id),
        "session_id": str(session_id),
        "role": role,
        "content": content,
        "payload": payload,
        "status": status,
        "created_at": now.isoformat(),
    }


def get_message(message_id: uuid.UUID | str, engine: Optional[Engine] = None) -> Optional[dict[str, Any]]:
    eng = engine or get_chat_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text(
                "SELECT id, session_id, role, content, payload, status, created_at "
                "FROM chat_messages WHERE id = :id"
            ),
            {"id": str(message_id)},
        ).mappings().first()
        if not row:
            return None
        return {
            "id": str(row["id"]),
            "session_id": str(row["session_id"]),
            "role": row["role"],
            "content": row["content"] or "",
            "payload": _clean_payload(row["payload"]),
            "status": row["status"],
            "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
        }


def update_message(
    message_id: uuid.UUID | str,
    content: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
    status: Optional[str] = None,
    engine: Optional[Engine] = None,
) -> Optional[dict[str, Any]]:
    eng = engine or get_chat_engine()
    now = datetime.now(timezone.utc)
    set_clauses = []
    params: dict[str, Any] = {"id": str(message_id)}

    if content is not None:
        set_clauses.append("content = :content")
        params["content"] = content
    if payload is not None:
        set_clauses.append("payload = :payload")
        params["payload"] = json.dumps(payload)
    if status is not None:
        set_clauses.append("status = :status")
        params["status"] = status

    if not set_clauses:
        return get_message(message_id, engine=eng)

    with eng.begin() as conn:
        conn.execute(
            text(f"UPDATE chat_messages SET {', '.join(set_clauses)} WHERE id = :id"),
            params,
        )
        # Touch session updated_at
        row = conn.execute(
            text("SELECT session_id FROM chat_messages WHERE id = :id"),
            {"id": str(message_id)},
        ).first()
        if row:
            conn.execute(
                text("UPDATE chat_sessions SET updated_at = :now WHERE id = :session_id"),
                {"now": now, "session_id": str(row[0])},
            )

    return get_message(message_id, engine=eng)


def delete_message_pair(message_id: uuid.UUID | str, engine: Optional[Engine] = None) -> bool:
    """
    Deletes that question and its answer pair.
    - If user message: deletes it and the corresponding assistant message.
    - If assistant message: deletes it and the preceding user message.
    """
    eng = engine or get_chat_engine()
    msg = get_message(message_id, engine=eng)
    if not msg:
        return False

    session_id = msg["session_id"]
    all_msgs = list_messages(session_id, engine=eng)

    target_idx = -1
    for idx, m in enumerate(all_msgs):
        if m["id"] == str(message_id):
            target_idx = idx
            break

    if target_idx == -1:
        return False

    ids_to_delete = [str(message_id)]
    if msg["role"] == "user":
        # Delete next assistant message if present
        if target_idx + 1 < len(all_msgs) and all_msgs[target_idx + 1]["role"] == "assistant":
            ids_to_delete.append(all_msgs[target_idx + 1]["id"])
    elif msg["role"] == "assistant":
        # Delete preceding user message if present
        if target_idx - 1 >= 0 and all_msgs[target_idx - 1]["role"] == "user":
            ids_to_delete.append(all_msgs[target_idx - 1]["id"])

    with eng.begin() as conn:
        for mid in ids_to_delete:
            conn.execute(text("DELETE FROM chat_messages WHERE id = :id"), {"id": mid})

    return True


def delete_messages_after(
    session_id: uuid.UUID | str,
    target_message_id: uuid.UUID | str,
    engine: Optional[Engine] = None,
) -> list[str]:
    """
    Deletes all messages in the session that occur after target_message_id.
    Used by /api/messages/{id}/edit.
    """
    eng = engine or get_chat_engine()
    all_msgs = list_messages(session_id, engine=eng)

    target_idx = -1
    for idx, m in enumerate(all_msgs):
        if m["id"] == str(target_message_id):
            target_idx = idx
            break

    if target_idx == -1 or target_idx >= len(all_msgs) - 1:
        return []

    later_ids = [m["id"] for m in all_msgs[target_idx + 1:]]
    with eng.begin() as conn:
        for mid in later_ids:
            conn.execute(text("DELETE FROM chat_messages WHERE id = :id"), {"id": mid})

    return later_ids


def get_last_completed_assistant_context(
    session_id: uuid.UUID | str,
    exclude_message_id: Optional[uuid.UUID | str] = None,
    engine: Optional[Engine] = None,
) -> tuple[Optional[str], Optional[list[dict]], Optional[dict]]:
    """
    Extract follow-up context (previous query, previous task types, previous scope)
    from the last completed assistant message in the database.
    """
    all_msgs = list_messages(session_id, engine=engine)
    if not all_msgs:
        return None, None, None

    # Filter out current run/message if specified
    relevant_msgs = all_msgs
    if exclude_message_id:
        ex_str = str(exclude_message_id)
        cut_idx = len(all_msgs)
        for idx, m in enumerate(all_msgs):
            if m["id"] == ex_str:
                cut_idx = idx
                break
        relevant_msgs = all_msgs[:cut_idx]

    # Find the last completed assistant message
    asst_idx = -1
    for idx in range(len(relevant_msgs) - 1, -1, -1):
        m = relevant_msgs[idx]
        if m["role"] == "assistant" and m["status"] == "complete" and m.get("payload"):
            asst_idx = idx
            break

    if asst_idx == -1:
        return None, None, None

    asst_msg = relevant_msgs[asst_idx]
    payload = asst_msg.get("payload") or {}

    # Preceding user question
    prev_query: Optional[str] = None
    if asst_idx - 1 >= 0 and relevant_msgs[asst_idx - 1]["role"] == "user":
        prev_query = relevant_msgs[asst_idx - 1]["content"]

    # Previous tasks
    prev_tasks: Optional[list[dict]] = None
    plan_raw = payload.get("plan")
    if plan_raw and isinstance(plan_raw, list):
        prev_tasks = [
            {"agent": t.get("agent"), "tool": t.get("tool")}
            for t in plan_raw
            if isinstance(t, dict) and t.get("agent")
        ]

    # Previous scope
    prev_scope: Optional[dict] = payload.get("period")

    return prev_query, prev_tasks, prev_scope


def get_message_by_run_id(run_id: str, engine: Optional[Engine] = None) -> Optional[dict[str, Any]]:
    """Lookup an assistant message by run_id (either directly by id or within JSON payload)."""
    eng = engine or get_chat_engine()
    with eng.connect() as conn:
        row = conn.execute(
            text("SELECT id, session_id, role, content, payload, status, created_at FROM chat_messages WHERE id = :id"),
            {"id": str(run_id)},
        ).mappings().first()
        if row:
            return {
                "id": str(row["id"]),
                "session_id": str(row["session_id"]),
                "role": row["role"],
                "content": row["content"] or "",
                "payload": _clean_payload(row["payload"]),
                "status": row["status"],
                "created_at": row["created_at"].isoformat() if hasattr(row["created_at"], "isoformat") else str(row["created_at"]),
            }

        rows = conn.execute(
            text("SELECT id, session_id, role, content, payload, status, created_at FROM chat_messages WHERE role = 'assistant' ORDER BY created_at DESC LIMIT 100")
        ).mappings().all()
        for r in rows:
            pl = _clean_payload(r["payload"])
            if pl and isinstance(pl, dict) and pl.get("run_id") == str(run_id):
                return {
                    "id": str(r["id"]),
                    "session_id": str(r["session_id"]),
                    "role": r["role"],
                    "content": r["content"] or "",
                    "payload": pl,
                    "status": r["status"],
                    "created_at": r["created_at"].isoformat() if hasattr(r["created_at"], "isoformat") else str(r["created_at"]),
                }
        return None

