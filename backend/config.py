# ============================================================
# config.py — Centralised configuration & DB connection
# ============================================================

import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# ─── Database ────────────────────────────────────────────────
DB_HOST = os.getenv("DB_HOST", "localhost")
DB_NAME = os.getenv("DB_NAME", "postgres")
DB_USER = os.getenv("DB_USER", "postgres")
DB_PASS = os.getenv("DB_PASS", "123456789")
DB_PORT = os.getenv("DB_PORT", "5432")

DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

engine = create_engine(DATABASE_URL, connect_args={"options": "-c search_path=public"})
SessionLocal = sessionmaker(bind=engine)

def get_db_session():
    return SessionLocal()

def execute_query(sql: str, params: dict = None) -> list[dict]:
    """Execute a SQL query and return results as a list of dicts."""
    with engine.connect() as conn:
        result = conn.execute(text(sql), params or {})
        cols = list(result.keys())
        return [dict(zip(cols, row)) for row in result.fetchall()]

# ─── LLM ─────────────────────────────────────────────────────
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"

# Model aliases (swap anytime)
SUPERVISOR_MODEL   = "anthropic/claude-3-haiku"          # cheap fast router
ANALYST_MODEL      = "anthropic/claude-3.5-sonnet"       # smart analyst
SYNTHESIS_MODEL    = "anthropic/claude-3.5-sonnet"       # narrative writer
