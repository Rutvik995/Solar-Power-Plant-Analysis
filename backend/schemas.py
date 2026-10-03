# ============================================================
# schemas.py — Pydantic v2 models for strict LLM output validation
# ============================================================

from __future__ import annotations
from typing import List, Literal, Optional, Any, Dict
from pydantic import BaseModel, Field


# ─── Supervisor ──────────────────────────────────────────────
class SupervisorRoute(BaseModel):
    """Strict routing decision produced by the Supervisor LLM."""
    next_node: Literal["sql_orchestrator", "diagnostic_agent", "predictive_agent"] = Field(
        description=(
            "Which specialist node to invoke next. "
            "sql_orchestrator: data lookup / aggregation questions. "
            "diagnostic_agent: fault / anomaly / health-check questions. "
            "predictive_agent: forecast / prediction / future yield questions."
        )
    )
    reasoning: str = Field(
        description="One-sentence explanation of why this node was chosen."
    )


# ─── Diagnostic ──────────────────────────────────────────────
class FaultDetail(BaseModel):
    """Individual inverter fault detected during analysis."""
    inverter_id: str
    fault_category: Literal[
        "Trip/Offline",
        "Localized Shading",
        "Thermal Loss",
        "Dust Accumulation",
        "Normal",
    ]
    severity: str = Field(
        description="One of: Critical, High, Medium, Low, None"
    )
    description: Optional[str] = Field(default=None)


class DiagnosticReport(BaseModel):
    """Full diagnostic scan result for one plant on one date (or range)."""
    plant_id: int
    log_date: str = Field(description="ISO date string, e.g. '2026-09-16'")
    faults_detected: List[FaultDetail]
    summary: str


# ─── Forecast ────────────────────────────────────────────────
class PredictionReport(BaseModel):
    """XGBoost-based daily yield forecast for a plant."""
    plant_id: int
    target_date: str = Field(description="ISO date of the predicted day")
    total_predicted_yield_kwh: float
    confidence_note: str = Field(
        default="Estimate based on 15-day historical window.",
        description="Short note about model reliability."
    )


# ─── Graph State ─────────────────────────────────────────────
from typing import TypedDict

class AgentState(TypedDict, total=False):
    user_query:        str
    next_node:         str
    sql_query:         Optional[str]
    query_results:     Optional[List[Dict[str, Any]]]
    diagnostic_report: Optional[DiagnosticReport]
    forecast_report:   Optional[PredictionReport]
    final_response:    str
