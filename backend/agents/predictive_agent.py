# ============================================================
# agents/predictive_agent.py — Gradient Boosting daily yield forecasting
# Uses sklearn GradientBoostingRegressor (same algorithm as XGBoost, no C build needed)
# ============================================================

from __future__ import annotations
import re
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

from sklearn.ensemble import GradientBoostingRegressor

from backend.config import execute_query
from backend.schemas import AgentState, PredictionReport


def _load_training_data() -> pd.DataFrame:
    rows = execute_query("""
        SELECT
            dit.log_date,
            dit.plant_id,
            SUM(dit.total_daily_yield_kwh)   AS total_yield_kwh,
            AVG(dwt.total_solar_radiation_kwh_m2) AS avg_radiation,
            AVG(dwt.avg_ambient_temp_c)       AS avg_temp,
            AVG(dwt.max_module_temp_c)        AS max_mod_temp,
            AVG(dwt.soiling_ratio)            AS soiling_ratio,
            AVG(dwt.peak_poa_irradiance_w_m2) AS peak_irrad
        FROM daily_inverter_telemetry dit
        JOIN daily_weather_telemetry dwt
             ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
        GROUP BY dit.log_date, dit.plant_id
        ORDER BY dit.log_date, dit.plant_id
    """)
    df = pd.DataFrame(rows)
    df["log_date"] = pd.to_datetime(df["log_date"])
    df["day_of_year"] = df["log_date"].dt.dayofyear
    df["day_num"]     = (df["log_date"] - df["log_date"].min()).dt.days
    return df


def _extract_plant_and_date(query: str) -> tuple[int | None, datetime | None]:
    """Attempt to extract plant_id and target date from a free-text query."""
    # Plant id
    plant_id = None
    m = re.search(r"plant[_\s]?(\d)", query, re.IGNORECASE)
    if m:
        plant_id = int(m.group(1))
    else:
        names = {
            "alpha": 1, "beta": 2, "gamma": 3, "delta": 4, "epsilon": 5
        }
        for name, pid in names.items():
            if name in query.lower():
                plant_id = pid
                break

    # Target date — look for ISO, "tomorrow", "next N days", etc.
    target_date = None
    iso_m = re.search(r"(\d{4}-\d{2}-\d{2})", query)
    if iso_m:
        target_date = datetime.strptime(iso_m.group(1), "%Y-%m-%d")
    elif "tomorrow" in query.lower():
        target_date = datetime.now() + timedelta(days=1)
    else:
        n_m = re.search(r"next\s+(\d+)\s+day", query, re.IGNORECASE)
        if n_m:
            target_date = datetime.now() + timedelta(days=int(n_m.group(1)))

    # Fall back to "next day after last record"
    if target_date is None:
        target_date = datetime(2026, 9, 27)   # day after our 15-day window

    return plant_id, target_date


def _train_and_predict(df: pd.DataFrame, plant_id: int, target_date: datetime) -> float:
    FEATURES = ["day_num", "day_of_year", "avg_radiation", "avg_temp",
                "max_mod_temp", "soiling_ratio", "peak_irrad", "plant_id"]

    train = df.copy()
    X_train = train[FEATURES].astype(float)
    y_train = train["total_yield_kwh"].astype(float)

    model = GradientBoostingRegressor(
        n_estimators=200,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        random_state=42,
    )
    model.fit(X_train, y_train)

    # Build a synthetic feature row for the target
    last_row = df[df["plant_id"] == plant_id].iloc[-1]
    day_num     = (target_date - df["log_date"].min()).days
    day_of_year = target_date.timetuple().tm_yday

    X_pred = pd.DataFrame([{
        "day_num":       day_num,
        "day_of_year":   day_of_year,
        "avg_radiation": float(last_row["avg_radiation"]),
        "avg_temp":      float(last_row["avg_temp"]),
        "max_mod_temp":  float(last_row["max_mod_temp"]),
        "soiling_ratio": float(last_row["soiling_ratio"]),
        "peak_irrad":    float(last_row["peak_irrad"]),
        "plant_id":      plant_id,
    }])

    pred = float(model.predict(X_pred)[0])
    return max(pred, 0.0)   # yield can't be negative


def predictive_agent_node(state: AgentState) -> AgentState:
    """Train an XGBoost model on historical data and predict future yield."""
    df = _load_training_data()

    if df.empty:
        report = PredictionReport(
            plant_id=0,
            target_date="N/A",
            total_predicted_yield_kwh=0.0,
            confidence_note="No historical data available for training.",
        )
        return {**state, "forecast_report": report}

    plant_id, target_date = _extract_plant_and_date(state["user_query"])

    # Default to plant 1 if not found
    if plant_id is None or plant_id not in df["plant_id"].unique():
        plant_id = int(df["plant_id"].iloc[0])

    predicted_kwh = _train_and_predict(df, plant_id, target_date)

    # Get plant name for display
    plant_names = {1: "Solar Alpha", 2: "Solar Beta", 3: "Solar Gamma",
                   4: "Solar Delta", 5: "Solar Epsilon"}
    plant_name = plant_names.get(plant_id, f"Plant {plant_id}")

    report = PredictionReport(
        plant_id=plant_id,
        target_date=target_date.strftime("%Y-%m-%d"),
        total_predicted_yield_kwh=round(predicted_kwh, 2),
        confidence_note=(
            f"GradientBoosting model (sklearn) trained on 15-day history for {plant_name}. "
            "Prediction reflects learned weather-yield correlations."
        ),
    )

    return {**state, "forecast_report": report}
