"""
Central configuration for the Solar Power Plant Analysis system.
All tunable thresholds, DB/LLM settings, and environment-variable bindings live here.
"""

from __future__ import annotations

import os
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
ROOT_DIR = Path(__file__).resolve().parent.parent  # solar_agent/
CONFIG_DIR = ROOT_DIR / "config"
PROMPTS_DIR = ROOT_DIR / "prompts"

STATUS_CODES_PATH = CONFIG_DIR / "status_codes.yaml"
SEMANTIC_LAYER_PATH = CONFIG_DIR / "semantic_layer.yaml"


# ---------------------------------------------------------------------------
# Settings (loaded from environment / .env file)
# ---------------------------------------------------------------------------

class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=ROOT_DIR.parent / ".env",  # project root .env
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Database ---
    database_url: str = Field(
        default="postgresql+psycopg://solar_ro:password@localhost:5432/solar_db",
        description="Read-only PostgreSQL connection string (psycopg v3 driver).",
    )

    # --- LLM Models (per role) ---
    # Provider choices: "gemini", "anthropic", "openai", "ollama"
    planner_provider: str = Field(
        default="gemini",
        description="LLM provider for the Orchestrator (planner) role.",
    )
    planner_model: str = Field(
        default="gemini-3.8-flash",
        description="LLM model for Orchestrator and Synthesizer.",
    )
    agent_provider: str = Field(
        default="gemini",
        description="LLM provider for specialist agents (fault, performance, environment).",
    )
    agent_model: str = Field(
        default="gemini-3.8-flash",
        description="LLM model for specialist agents.",
    )
    synthesizer_provider: str = Field(
        default="gemini",
        description="LLM provider for the Synthesizer role.",
    )
    synthesizer_model: str = Field(
        default="gemini-3.8-flash",
        description="LLM model for the Synthesizer.",
    )
    # Data Agent / Validator → cost-efficient, fast (legacy alias)
    fast_model: str = Field(
        default="gemini-3.8-flash",
        description="LLM model for Data Agent and Validator (legacy alias for agent_model).",
    )
    google_api_key: str = Field(
        default="",
        description="Google AI API key (for Gemini models).",
    )

    # --- SQL Guardrails ---
    sql_row_limit: int = Field(
        default=50_000,
        description="Maximum rows any SQL query may return.",
    )
    sql_timeout_seconds: int = Field(
        default=15,
        description="Statement-level query timeout in seconds.",
    )
    sql_allowed_tables: list[str] = Field(
        default=[
            "plants",
            "blocks",
            "inverters",
            "daily_weather_telemetry",
            "daily_inverter_telemetry",
        ],
        description="Allowlisted table names for the SQL guard.",
    )

    # --- Physics / Metric Thresholds ---
    min_irradiance_kwh_m2: float = Field(
        default=0.5,
        description=(
            "Minimum total_solar_radiation_kwh_m2 for a day to be included in PR "
            "and yield calculations. Days below this are 'night/low-light' and excluded."
        ),
    )
    min_dc_power_kw_for_efficiency: float = Field(
        default=1.0,
        description="Minimum peak_dc_power_kw before inverter efficiency is computed.",
    )
    clipping_threshold_fraction: float = Field(
        default=0.98,
        description=(
            "Fraction of rated_ac_kw at which peak_ac_power_kw is considered clipping "
            "(i.e. peak_ac ≥ threshold × rated_ac_kw)."
        ),
    )
    zero_generation_max_yield_kwh: float = Field(
        default=1.0,
        description=(
            "total_daily_yield_kwh below this threshold (on a valid irradiance day) "
            "is treated as zero generation."
        ),
    )
    peer_underperformance_z_threshold: float = Field(
        default=3.0,
        description="Robust z-score (MAD-based) threshold to flag peer underperformance.",
    )
    peer_underperformance_min_days: int = Field(
        default=3,
        description="Minimum consecutive days of underperformance to flag as a fault.",
    )
    soiling_cleaning_jump_threshold: float = Field(
        default=0.02,
        description="Threshold for a day-over-day jump in soiling_ratio to be considered a cleaning event.",
    )

    # --- Re-planning ---
    max_replan_loops: int = Field(
        default=2,
        description="Maximum number of Validator → Orchestrator re-plan cycles.",
    )

    # --- Observability ---
    langsmith_tracing: bool = Field(
        default=False,
        description="Enable LangSmith tracing.",
    )
    langsmith_api_key: str = Field(
        default="",
        description="LangSmith API key.",
    )
    langsmith_project: str = Field(
        default="solar-power-analysis",
        description="LangSmith project name.",
    )

    # --- Executor ---
    task_timeout_seconds: int = Field(
        default=120,
        description="Per-task timeout in the DAG executor.",
    )
    task_max_retries: int = Field(
        default=1,
        description="Number of times the executor retries a failed task before marking it FAILED.",
    )
    max_llm_calls_per_query: int = Field(
        default=12,
        description=(
            "Maximum number of LLM API calls allowed per query/thread. "
            "When exceeded, BudgetExceeded is raised and the graph returns a partial answer."
        ),
    )

    # --- 'Today' definition ---
    # We derive 'today' from MAX(log_date) in the DB at query-time.
    # This constant is kept here for documentation; actual resolution is in
    # tools/data_tools.py::resolve_date_range().
    today_source: str = Field(
        default="max_log_date",
        description=(
            "How 'today' is defined. Only 'max_log_date' is supported; "
            "kept here for future extensibility."
        ),
    )


# Singleton instance — import this everywhere
settings = Settings()

# ---------------------------------------------------------------------------
# LangSmith activation (side-effect on import if enabled)
# ---------------------------------------------------------------------------
if settings.langsmith_tracing:
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    os.environ.setdefault("LANGSMITH_API_KEY", settings.langsmith_api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", settings.langsmith_project)
