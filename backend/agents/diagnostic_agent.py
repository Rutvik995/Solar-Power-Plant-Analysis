# ============================================================
# agents/diagnostic_agent.py — Deterministic Pandas fault detection
# ============================================================

from __future__ import annotations
import pandas as pd
from backend.config import execute_query
from backend.schemas import AgentState, DiagnosticReport, FaultDetail

# ─── Thresholds ───────────────────────────────────────────────
TRIP_STATUS_CODE         = 2
SHADING_STATUS_CODE      = 3
THERMAL_MODULE_TEMP_C    = 63.0       # flag above this
DUST_SOILING_RATIO       = 0.85       # flag below this
SHADING_YIELD_THRESHOLD  = 0.50       # yield < 50% of plant avg → suspect shading


def _load_inverter_df() -> pd.DataFrame:
    rows = execute_query("""
        SELECT dit.log_date, dit.inverter_id, dit.plant_id,
               dit.peak_dc_power_kw, dit.total_daily_yield_kwh,
               dit.inverter_status_code,
               dwt.max_module_temp_c, dwt.soiling_ratio,
               p.plant_name
        FROM daily_inverter_telemetry dit
        JOIN daily_weather_telemetry dwt
             ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
        JOIN plants p ON dit.plant_id = p.plant_id
        ORDER BY dit.log_date, dit.plant_id, dit.inverter_id
    """)
    return pd.DataFrame(rows)


def _detect_faults(df: pd.DataFrame) -> list[FaultDetail]:
    """Run rule-based checks on the dataframe and return FaultDetail list."""
    faults: list[FaultDetail] = []

    # Per-plant-date average yield for relative shading check
    avg_yield = (
        df.groupby(["plant_id", "log_date"])["total_daily_yield_kwh"]
        .transform("mean")
    )
    df = df.copy()
    df["plant_avg_yield"] = avg_yield
    df["yield_ratio"]     = df["total_daily_yield_kwh"] / (df["plant_avg_yield"] + 1e-9)

    for _, row in df.iterrows():
        inv_id = row["inverter_id"]
        status = int(row["inverter_status_code"])
        mod_t  = float(row["max_module_temp_c"])
        soil   = float(row["soiling_ratio"])
        yr     = float(row["yield_ratio"])

        # Priority 1 — Trip / Offline
        if status == TRIP_STATUS_CODE or float(row["total_daily_yield_kwh"]) == 0.0:
            faults.append(FaultDetail(
                inverter_id=inv_id,
                fault_category="Trip/Offline",
                severity="Critical",
                description=f"Inverter offline. Yield=0, status_code={status}",
            ))
        # Priority 2 — Thermal Loss
        elif mod_t >= THERMAL_MODULE_TEMP_C:
            faults.append(FaultDetail(
                inverter_id=inv_id,
                fault_category="Thermal Loss",
                severity="High",
                description=f"Module temp {mod_t:.1f}°C exceeds {THERMAL_MODULE_TEMP_C}°C threshold.",
            ))
        # Priority 3 — Dust Accumulation
        elif soil < DUST_SOILING_RATIO:
            faults.append(FaultDetail(
                inverter_id=inv_id,
                fault_category="Dust Accumulation",
                severity="Medium",
                description=f"Soiling ratio {soil:.2f} below {DUST_SOILING_RATIO} — cleaning required.",
            ))
        # Priority 4 — Localized Shading
        elif status == SHADING_STATUS_CODE or yr < SHADING_YIELD_THRESHOLD:
            faults.append(FaultDetail(
                inverter_id=inv_id,
                fault_category="Localized Shading",
                severity="High",
                description=f"Yield only {yr*100:.0f}% of plant average — possible shading or string loss.",
            ))

    return faults


def diagnostic_agent_node(state: AgentState) -> AgentState:
    """Load all telemetry, run deterministic fault detection, build report."""
    df = _load_inverter_df()

    if df.empty:
        report = DiagnosticReport(
            plant_id=0,
            log_date="N/A",
            faults_detected=[],
            summary="No telemetry data found in the database.",
        )
        return {**state, "diagnostic_report": report}

    faults = _detect_faults(df)

    # Summarise for report header
    n_critical = sum(1 for f in faults if f.severity == "Critical")
    n_high     = sum(1 for f in faults if f.severity == "High")
    n_medium   = sum(1 for f in faults if f.severity == "Medium")

    date_range = f"{df['log_date'].min()} to {df['log_date'].max()}"
    plants_str = ", ".join(df["plant_name"].unique())

    summary = (
        f"Scanned {len(df)} inverter-day records across [{plants_str}] "
        f"({date_range}). "
        f"Detected {len(faults)} fault events: "
        f"{n_critical} Critical, {n_high} High, {n_medium} Medium."
    )

    # Use plant_id=0 to signal "all plants"
    report = DiagnosticReport(
        plant_id=0,
        log_date=date_range,
        faults_detected=faults,
        summary=summary,
    )

    return {**state, "diagnostic_report": report}
