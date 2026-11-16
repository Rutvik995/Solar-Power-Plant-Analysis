"""
ground_truth.py — Independent ground truth calculation for Solar Plant System Tests.

RULES:
- Compute expected values with raw SQL (SQLAlchemy text() or psycopg).
- Never import tools/metrics.py, the dashboard API, or agents.
- Provide a secondary SQL formulation for double-checking mismatches.
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any, Optional
import numpy as np
from sqlalchemy import create_engine, text

def get_engine():
    # Load database URL from environment or fallback to project default
    db_url = os.getenv(
        "DATABASE_URL", 
        "postgresql+psycopg://postgres:123456789@localhost:5432/solardata"
    )
    if db_url.startswith("postgresql+asyncpg://"):
        db_url = db_url.replace("postgresql+asyncpg://", "postgresql+psycopg://")
    elif db_url.startswith("postgresql://") and not db_url.startswith("postgresql+psycopg://"):
        db_url = db_url.replace("postgresql://", "postgresql+psycopg://")
    return create_engine(db_url, pool_pre_ping=True)


class GroundTruth:
    def __init__(self):
        self.engine = get_engine()

    def execute_raw(self, sql_query: str, params: dict | None = None) -> list[dict]:
        with self.engine.connect() as conn:
            res = conn.execute(text(sql_query), params or {})
            cols = list(res.keys())
            return [dict(zip(cols, row)) for row in res.fetchall()]

    def execute_scalar(self, sql_query: str, params: dict | None = None) -> Any:
        with self.engine.connect() as conn:
            return conn.execute(text(sql_query), params or {}).scalar()

    # -------------------------------------------------------------------------
    # Harness Self-Check (Section 1)
    # -------------------------------------------------------------------------
    def self_check(self) -> dict[str, Any]:
        """
        Verify reference values stated in section 1:
        - 5 plants, 21 blocks, 116 inverters; Plant 1 has 20 inverters;
          rows in daily_inverter_telemetry per plant = inverters × 57 days (data 2026-08-01 to 2026-09-26)
        - Plant 1, 2026-09-01 to 2026-09-26: PR = 0.8705, yield = 4,135,384 kWh
        - 2026-08-01 to 2026-09-26 energy = 58,565.7 MWh (all plants)
        - P1_Block_A_INV_02 has zero yield on 2026-09-16 (status code 2)
        - Plant 3 has highest soiling loss in September (~9.7% for 09-01 to 09-26)
        """
        checks = {}

        plants_count = self.execute_scalar("SELECT count(*) FROM plants")
        blocks_count = self.execute_scalar("SELECT count(*) FROM blocks")
        inverters_count = self.execute_scalar("SELECT count(*) FROM inverters")
        p1_inv_count = self.execute_scalar("SELECT count(*) FROM inverters WHERE plant_id = 1")
        
        checks["plant_counts"] = (plants_count == 5 and blocks_count == 21 and inverters_count == 116 and p1_inv_count == 20)
        
        # Inverter days for 2026-08-01 to 2026-09-26 (57 days)
        rows_57 = self.execute_scalar("""
            SELECT count(*) FROM daily_inverter_telemetry 
            WHERE log_date BETWEEN '2026-08-01' AND '2026-09-26'
        """)
        checks["inverter_days_57"] = (rows_57 == 116 * 57)

        # Plant 1 Sep 1-26 yield and PR
        p1_sep = self.execute_raw("""
            SELECT 
                SUM(dit.total_daily_yield_kwh) AS total_yield,
                SUM(dit.total_daily_yield_kwh) / SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2) AS pr
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE dit.plant_id = 1 AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-26'
        """)[0]
        p1_yield = float(p1_sep["total_yield"])
        p1_pr = float(p1_sep["pr"])
        checks["plant1_sep_yield"] = abs(p1_yield - 4135384.29) < 100.0
        checks["plant1_sep_pr"] = abs(p1_pr - 0.8705) < 0.0005

        # 58,565.7 MWh
        energy_57 = float(self.execute_scalar("""
            SELECT SUM(total_daily_yield_kwh) / 1000.0
            FROM daily_inverter_telemetry
            WHERE log_date BETWEEN '2026-08-01' AND '2026-09-26'
        """))
        checks["energy_57"] = abs(energy_57 - 58565.7) < 5.0

        # P1_Block_A_INV_02 zero yield on 2026-09-16
        inv02 = self.execute_raw("""
            SELECT total_daily_yield_kwh, inverter_status_code
            FROM daily_inverter_telemetry
            WHERE inverter_id = 'P1_Block_A_INV_02' AND log_date = '2026-09-16'
        """)[0]
        checks["inv02_zero_gen"] = (float(inv02["total_daily_yield_kwh"]) == 0.0 and inv02["inverter_status_code"] == 2)

        # Plant 3 soiling loss
        p3_soiling = self.execute_raw("""
            SELECT 
                SUM(dit.total_daily_yield_kwh * (1.0 / dwt.soiling_ratio - 1.0)) / SUM(dit.total_daily_yield_kwh) * 100.0 AS loss_pct
            FROM daily_inverter_telemetry dit
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE dit.plant_id = 3 AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-26'
        """)[0]
        checks["plant3_sep_soiling"] = abs(float(p3_soiling["loss_pct"]) - 9.7) < 0.2

        all_passed = all(checks.values())
        return {"passed": all_passed, "checks": checks}

    # -------------------------------------------------------------------------
    # Core Ground Truth Calculations
    # -------------------------------------------------------------------------
    def get_plant_facts(self) -> dict[str, Any]:
        plants = self.execute_raw("SELECT plant_id, plant_name, capacity_mwp FROM plants ORDER BY plant_id")
        blocks_count = self.execute_scalar("SELECT count(*) FROM blocks")
        inverters_count = self.execute_scalar("SELECT count(*) FROM inverters")
        
        largest_plant = max(plants, key=lambda p: float(p["capacity_mwp"]))
        total_capacity_mwp = sum(float(p["capacity_mwp"]) for p in plants)
        total_dc_kw = float(self.execute_scalar("SELECT SUM(rated_dc_kw) FROM inverters"))
        
        p1_block_a_invs = self.execute_raw("""
            SELECT inv.inverter_id, inv.rated_dc_kw
            FROM inverters inv
            JOIN blocks b ON inv.block_id = b.block_id
            WHERE inv.plant_id = 1 AND (b.block_name = 'Block_A' OR b.block_id = 1)
            ORDER BY inv.inverter_id
        """)
        
        date_range = self.execute_raw("SELECT min(log_date) as min_date, max(log_date) as max_date FROM daily_inverter_telemetry")[0]
        
        return {
            "num_plants": len(plants),
            "num_blocks": blocks_count,
            "num_inverters": inverters_count,
            "largest_plant": largest_plant,
            "total_capacity_mwp": total_capacity_mwp,
            "total_capacity_from_dc_mwp": total_dc_kw / 1000.0,
            "p1_block_a_inverters": p1_block_a_invs,
            "min_date": date_range["min_date"].isoformat(),
            "max_date": date_range["max_date"].isoformat(),
        }

    def compute_pr_and_yield(
        self,
        plant_id: Optional[int] = None,
        block_id: Optional[int] = None,
        inverter_id: Optional[str] = None,
        start_date: str = "2026-08-01",
        end_date: str = "2026-09-26",
    ) -> dict[str, Any]:
        """
        Formulation 1: Standard SUM(yield) / SUM(rated_dc_kw * irradiance)
        """
        where_clauses = ["dit.log_date BETWEEN :start_date AND :end_date"]
        params = {"start_date": start_date, "end_date": end_date}
        if plant_id is not None:
            where_clauses.append("dit.plant_id = :plant_id")
            params["plant_id"] = plant_id
        if block_id is not None:
            where_clauses.append("inv.block_id = :block_id")
            params["block_id"] = block_id
        if inverter_id is not None:
            where_clauses.append("dit.inverter_id = :inverter_id")
            params["inverter_id"] = inverter_id

        where_sql = " AND ".join(where_clauses)
        sql = f"""
            SELECT 
                SUM(dit.total_daily_yield_kwh) AS total_yield_kwh,
                SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2) AS expected_yield_kwh,
                SUM(dit.total_daily_yield_kwh) / NULLIF(SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2), 0) AS pr
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE {where_sql}
        """
        row = self.execute_raw(sql, params)[0]
        y_kwh = float(row["total_yield_kwh"] or 0.0)
        exp_kwh = float(row["expected_yield_kwh"] or 0.0)
        pr = float(row["pr"]) if row["pr"] is not None else None
        
        return {
            "yield_kwh": y_kwh,
            "yield_mwh": y_kwh / 1000.0,
            "expected_yield_kwh": exp_kwh,
            "pr": pr,
            "pr_pct": pr * 100.0 if pr is not None else None,
        }

    def compute_pr_and_yield_formulation_2(
        self,
        plant_id: Optional[int] = None,
        block_id: Optional[int] = None,
        inverter_id: Optional[str] = None,
        start_date: str = "2026-08-01",
        end_date: str = "2026-09-26",
    ) -> dict[str, Any]:
        """
        Formulation 2 (Double-check): Aggregate daily first, then sum totals.
        """
        where_clauses = ["dit.log_date BETWEEN :start_date AND :end_date"]
        params = {"start_date": start_date, "end_date": end_date}
        if plant_id is not None:
            where_clauses.append("dit.plant_id = :plant_id")
            params["plant_id"] = plant_id
        if block_id is not None:
            where_clauses.append("inv.block_id = :block_id")
            params["block_id"] = block_id
        if inverter_id is not None:
            where_clauses.append("dit.inverter_id = :inverter_id")
            params["inverter_id"] = inverter_id

        where_sql = " AND ".join(where_clauses)
        sql = f"""
            WITH daily_sums AS (
                SELECT 
                    dit.log_date,
                    SUM(dit.total_daily_yield_kwh) AS day_yield,
                    SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2) AS day_exp
                FROM daily_inverter_telemetry dit
                JOIN inverters inv ON dit.inverter_id = inv.inverter_id
                JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
                WHERE {where_sql}
                GROUP BY dit.log_date
            )
            SELECT 
                SUM(day_yield) AS total_yield_kwh,
                SUM(day_exp) AS expected_yield_kwh,
                SUM(day_yield) / NULLIF(SUM(day_exp), 0) AS pr
            FROM daily_sums
        """
        row = self.execute_raw(sql, params)[0]
        y_kwh = float(row["total_yield_kwh"] or 0.0)
        exp_kwh = float(row["expected_yield_kwh"] or 0.0)
        pr = float(row["pr"]) if row["pr"] is not None else None
        return {
            "yield_kwh": y_kwh,
            "yield_mwh": y_kwh / 1000.0,
            "expected_yield_kwh": exp_kwh,
            "pr": pr,
            "pr_pct": pr * 100.0 if pr is not None else None,
        }

    def rank_plants_by_pr(self, start_date: str, end_date: str, ascending: bool = False) -> list[dict]:
        order = "ASC" if ascending else "DESC"
        sql = f"""
            SELECT 
                p.plant_id,
                p.plant_name,
                SUM(dit.total_daily_yield_kwh) AS total_yield_kwh,
                SUM(dit.total_daily_yield_kwh) / NULLIF(SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2), 0) AS pr
            FROM daily_inverter_telemetry dit
            JOIN plants p ON dit.plant_id = p.plant_id
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE dit.log_date BETWEEN :start_date AND :end_date
            GROUP BY p.plant_id, p.plant_name
            ORDER BY pr {order}
        """
        rows = self.execute_raw(sql, {"start_date": start_date, "end_date": end_date})
        return [
            {
                "plant_id": r["plant_id"],
                "plant_name": r["plant_name"],
                "yield_mwh": float(r["total_yield_kwh"]) / 1000.0,
                "pr": float(r["pr"]),
                "pr_pct": float(r["pr"]) * 100.0,
            }
            for r in rows
        ]

    def rank_blocks_in_plant(self, plant_id: int, start_date: str, end_date: str, ascending: bool = True) -> list[dict]:
        order = "ASC" if ascending else "DESC"
        sql = f"""
            SELECT 
                b.block_id,
                b.block_name,
                SUM(dit.total_daily_yield_kwh) AS total_yield_kwh,
                SUM(dit.total_daily_yield_kwh) / NULLIF(SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2), 0) AS pr
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            JOIN blocks b ON inv.block_id = b.block_id
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE dit.plant_id = :plant_id AND dit.log_date BETWEEN :start_date AND :end_date
            GROUP BY b.block_id, b.block_name
            ORDER BY pr {order}
        """
        rows = self.execute_raw(sql, {"plant_id": plant_id, "start_date": start_date, "end_date": end_date})
        return [
            {
                "block_id": r["block_id"],
                "block_name": r["block_name"],
                "pr": float(r["pr"]) if r["pr"] is not None else None,
            }
            for r in rows
        ]

    def get_table_counts(self) -> dict[str, Any]:
        facts = self.get_plant_facts()
        return {
            "date_min": facts["min_date"],
            "date_max": facts["max_date"],
            "plants": facts["num_plants"],
            "blocks": facts["num_blocks"],
            "inverters": facts["num_inverters"],
        }

    def detect_cleaning_events(self, plant_id: Optional[int] = None, threshold: float = 0.02, start_date: str = "2026-08-01", end_date: str = "2026-09-26") -> list[dict]:
        return self.get_cleaning_events(plant_id=plant_id, start_date=start_date, end_date=end_date)

    def find_peer_underperformers(self, plant_id: Optional[int] = None, threshold_delta: float = 0.03, min_consecutive_days: int = 3, start_date: str = "2026-08-01", end_date: str = "2026-09-26") -> list[dict]:
        return self.get_peer_underperformers(plant_id=plant_id, start_date=start_date, end_date=end_date)

    def rank_inverters_in_plant(self, plant_id: int, start_date: str, end_date: str, ascending: bool = True) -> list[dict]:
        order = "ASC" if ascending else "DESC"
        sql = f"""
            SELECT 
                inv.inverter_id,
                b.block_name,
                SUM(dit.total_daily_yield_kwh) AS total_yield_kwh,
                SUM(dit.total_daily_yield_kwh) / NULLIF(SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2), 0) AS pr
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            JOIN blocks b ON inv.block_id = b.block_id
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE dit.plant_id = :plant_id AND dit.log_date BETWEEN :start_date AND :end_date
            GROUP BY inv.inverter_id, b.block_name
            ORDER BY pr {order}
        """
        rows = self.execute_raw(sql, {"plant_id": plant_id, "start_date": start_date, "end_date": end_date})
        return [
            {
                "inverter_id": r["inverter_id"],
                "block_name": r["block_name"],
                "yield_kwh": float(r["total_yield_kwh"]),
                "pr": float(r["pr"]) if r["pr"] is not None else None,
            }
            for r in rows
        ]

    def compute_soiling_loss(self, plant_id: Optional[int] = None, start_date: str = "2026-08-01", end_date: str = "2026-09-26") -> list[dict]:
        where = ["dit.log_date BETWEEN :start_date AND :end_date"]
        params = {"start_date": start_date, "end_date": end_date}
        if plant_id is not None:
            where.append("dit.plant_id = :plant_id")
            params["plant_id"] = plant_id

        where_sql = " AND ".join(where)
        sql = f"""
            SELECT 
                p.plant_id,
                p.plant_name,
                SUM(dit.total_daily_yield_kwh) AS total_yield,
                SUM(dit.total_daily_yield_kwh * (1.0 / NULLIF(dwt.soiling_ratio, 0) - 1.0)) AS loss_kwh,
                SUM(dit.total_daily_yield_kwh * (1.0 / NULLIF(dwt.soiling_ratio, 0) - 1.0)) / NULLIF(SUM(dit.total_daily_yield_kwh), 0) * 100.0 AS loss_pct
            FROM daily_inverter_telemetry dit
            JOIN plants p ON dit.plant_id = p.plant_id
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE {where_sql}
            GROUP BY p.plant_id, p.plant_name
            ORDER BY loss_pct DESC
        """
        rows = self.execute_raw(sql, params)
        return [
            {
                "plant_id": r["plant_id"],
                "plant_name": r["plant_name"],
                "total_yield_kwh": float(r["total_yield"]),
                "loss_kwh": float(r["loss_kwh"]),
                "loss_pct": float(r["loss_pct"]),
            }
            for r in rows
        ]

    def get_cleaning_events(self, plant_id: Optional[int] = None, start_date: str = "2026-08-01", end_date: str = "2026-09-26") -> list[dict]:
        where = ["log_date BETWEEN :start_date AND :end_date"]
        params = {"start_date": start_date, "end_date": end_date}
        if plant_id is not None:
            where.append("plant_id = :plant_id")
            params["plant_id"] = plant_id
        where_sql = " AND ".join(where)

        sql = f"""
            SELECT plant_id, log_date, soiling_ratio
            FROM daily_weather_telemetry
            WHERE {where_sql}
            ORDER BY plant_id, log_date
        """
        rows = self.execute_raw(sql, params)
        cleanings = []
        by_plant = {}
        for r in rows:
            by_plant.setdefault(r["plant_id"], []).append(r)

        for pid, p_rows in by_plant.items():
            for i in range(1, len(p_rows)):
                prev_sr = float(p_rows[i-1]["soiling_ratio"])
                curr_sr = float(p_rows[i]["soiling_ratio"])
                jump = curr_sr - prev_sr
                if jump > 0.02:
                    cleanings.append({
                        "plant_id": pid,
                        "date": p_rows[i]["log_date"].isoformat(),
                        "jump": jump,
                        "prev_ratio": prev_sr,
                        "ratio": curr_sr,
                    })
        return cleanings

    def get_flagged_inverter_days(self, plant_id: Optional[int] = None, start_date: str = "2026-08-01", end_date: str = "2026-09-26") -> list[dict]:
        """
        Flagged inverter-days: fault status codes (>= 3) OR zero generation (yield <= 1.0 while radiation > 0.5 and status != 1).
        """
        where = ["dit.log_date BETWEEN :start_date AND :end_date"]
        params = {"start_date": start_date, "end_date": end_date}
        if plant_id is not None:
            where.append("dit.plant_id = :plant_id")
            params["plant_id"] = plant_id
        where_sql = " AND ".join(where)

        sql = f"""
            SELECT 
                dit.log_date,
                dit.inverter_id,
                dit.plant_id,
                b.block_name,
                dit.total_daily_yield_kwh,
                dit.inverter_status_code,
                dwt.total_solar_radiation_kwh_m2
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            JOIN blocks b ON inv.block_id = b.block_id
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE {where_sql}
              AND (
                  dit.inverter_status_code IN (3, 4)
                  OR (
                      dwt.total_solar_radiation_kwh_m2 > 0.5
                      AND dit.inverter_status_code != 1
                      AND dit.total_daily_yield_kwh <= 1.0
                  )
              )
            ORDER BY dit.log_date, dit.inverter_id
        """
        rows = self.execute_raw(sql, params)
        return [
            {
                "date": r["log_date"].isoformat(),
                "inverter_id": r["inverter_id"],
                "plant_id": r["plant_id"],
                "block_name": r["block_name"],
                "yield": float(r["total_daily_yield_kwh"]),
                "status_code": r["inverter_status_code"],
                "radiation": float(r["total_solar_radiation_kwh_m2"]),
            }
            for r in rows
        ]

    def get_warning_status_inverters(self, plant_id: Optional[int] = None, start_date: str = "2026-08-01", end_date: str = "2026-08-31") -> list[dict]:
        where = ["dit.log_date BETWEEN :start_date AND :end_date", "dit.inverter_status_code = 2"]
        params = {"start_date": start_date, "end_date": end_date}
        if plant_id is not None:
            where.append("dit.plant_id = :plant_id")
            params["plant_id"] = plant_id
        where_sql = " AND ".join(where)

        sql = f"""
            SELECT dit.log_date, dit.inverter_id, dit.plant_id, dit.total_daily_yield_kwh, dit.inverter_status_code
            FROM daily_inverter_telemetry dit
            WHERE {where_sql}
            ORDER BY dit.log_date, dit.inverter_id
        """
        rows = self.execute_raw(sql, params)
        return [
            {
                "date": r["log_date"].isoformat(),
                "inverter_id": r["inverter_id"],
                "plant_id": r["plant_id"],
                "status_code": r["inverter_status_code"],
            }
            for r in rows
        ]

    def get_peer_underperformers(self, plant_id: Optional[int] = None, start_date: str = "2026-08-01", end_date: str = "2026-09-26") -> list[dict]:
        """
        Inverter PR below block median PR (same day) by > 0.03 for 3 or more consecutive days.
        """
        where = ["dit.log_date BETWEEN :start_date AND :end_date"]
        params = {"start_date": start_date, "end_date": end_date}
        if plant_id is not None:
            where.append("dit.plant_id = :plant_id")
            params["plant_id"] = plant_id
        where_sql = " AND ".join(where)

        sql = f"""
            SELECT 
                dit.log_date,
                dit.inverter_id,
                inv.block_id,
                dit.total_daily_yield_kwh / NULLIF(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2, 0) AS pr
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE {where_sql}
            ORDER BY inv.block_id, dit.log_date, dit.inverter_id
        """
        rows = self.execute_raw(sql, params)
        by_block_date = {}
        for r in rows:
            if r["pr"] is not None:
                by_block_date.setdefault((r["block_id"], r["log_date"]), []).append((r["inverter_id"], float(r["pr"])))

        deviations = []
        for (bid, dt), inv_prs in by_block_date.items():
            prs = [p for _, p in inv_prs]
            median_pr = float(np.median(prs))
            for inv_id, pr in inv_prs:
                if (median_pr - pr) > 0.03:
                    deviations.append({"inverter_id": inv_id, "block_id": bid, "date": dt, "diff": median_pr - pr})

        # Check consecutive streaks >= 3
        deviations.sort(key=lambda x: (x["inverter_id"], x["date"]))
        underperformers = []
        streak = []
        for d in deviations:
            if not streak:
                streak.append(d)
            else:
                last_d = streak[-1]
                if d["inverter_id"] == last_d["inverter_id"] and (d["date"] - last_d["date"]).days == 1:
                    streak.append(d)
                else:
                    if len(streak) >= 3:
                        underperformers.extend(streak)
                    streak = [d]
        if len(streak) >= 3:
            underperformers.extend(streak)

        return underperformers

    def compute_temperature_effect(self, plant_id: int = 1, start_date: str = "2026-08-01", end_date: str = "2026-09-26") -> dict:
        """
        Slope of daily plant-level PR against max_module_temp_c.
        """
        sql = """
            SELECT 
                dwt.max_module_temp_c,
                SUM(dit.total_daily_yield_kwh) / NULLIF(SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2), 0) AS pr
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE dit.plant_id = :plant_id AND dit.log_date BETWEEN :start_date AND :end_date
            GROUP BY dwt.log_date, dwt.max_module_temp_c
            ORDER BY dwt.log_date
        """
        rows = self.execute_raw(sql, {"plant_id": plant_id, "start_date": start_date, "end_date": end_date})
        temps = [float(r["max_module_temp_c"]) for r in rows if r["max_module_temp_c"] is not None and r["pr"] is not None]
        prs = [float(r["pr"]) for r in rows if r["max_module_temp_c"] is not None and r["pr"] is not None]
        if len(temps) < 3:
            return {"slope": 0.0, "r2": 0.0}
        slope, intercept = np.polyfit(temps, prs, 1)
        r = np.corrcoef(temps, prs)[0, 1]
        return {"slope": float(slope), "r2": float(r**2)}

    def compute_sunlight_effect(self, plant_id: int = 2, start_date: str = "2026-08-01", end_date: str = "2026-09-26") -> dict:
        """
        R^2 of specific yield (yield/rated_dc_kw) against total_solar_radiation_kwh_m2.
        """
        sql = """
            SELECT 
                dit.total_daily_yield_kwh / NULLIF(inv.rated_dc_kw, 0) AS spec_yield,
                dwt.total_solar_radiation_kwh_m2 AS radiation
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE dit.plant_id = :plant_id AND dit.log_date BETWEEN :start_date AND :end_date
        """
        rows = self.execute_raw(sql, {"plant_id": plant_id, "start_date": start_date, "end_date": end_date})
        sy = [float(r["spec_yield"]) for r in rows if r["spec_yield"] is not None and r["radiation"] is not None]
        rad = [float(r["radiation"]) for r in rows if r["spec_yield"] is not None and r["radiation"] is not None]
        if len(sy) < 3:
            return {"r2": 0.0}
        r = np.corrcoef(rad, sy)[0, 1]
        return {"r2": float(r**2)}

    def get_inverter_day_details(self, inverter_id: str, log_date: str) -> dict[str, Any]:
        """
        Detailed metrics for a single inverter day:
        inverter PR, block median PR, plant irradiance, status code.
        """
        sql = """
            SELECT 
                dit.inverter_id,
                dit.plant_id,
                inv.block_id,
                inv.rated_dc_kw,
                inv.rated_ac_kw,
                b.block_name,
                dit.total_daily_yield_kwh,
                dit.inverter_status_code,
                dwt.total_solar_radiation_kwh_m2,
                dit.total_daily_yield_kwh / NULLIF(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2, 0) AS pr
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            JOIN blocks b ON inv.block_id = b.block_id
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE dit.inverter_id = :inverter_id AND dit.log_date = :log_date
        """
        rows = self.execute_raw(sql, {"inverter_id": inverter_id, "log_date": log_date})
        if not rows:
            return {}
        row = rows[0]
        block_id = row["block_id"]
        plant_id = row["plant_id"]

        # Peer block median PR on same day
        peer_sql = """
            SELECT 
                dit.inverter_id,
                dit.total_daily_yield_kwh / NULLIF(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2, 0) AS pr
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            JOIN daily_weather_telemetry dwt ON dit.plant_id = dwt.plant_id AND dit.log_date = dwt.log_date
            WHERE inv.block_id = :block_id AND dit.log_date = :log_date
        """
        peers = self.execute_raw(peer_sql, {"block_id": block_id, "log_date": log_date})
        peer_prs = [float(p["pr"]) for p in peers if p["pr"] is not None]
        median_peer_pr = float(np.median(peer_prs)) if peer_prs else None

        return {
            "inverter_id": row["inverter_id"],
            "rated_dc_kw": float(row["rated_dc_kw"]),
            "rated_ac_kw": float(row["rated_ac_kw"]),
            "block_id": row["block_id"],
            "block_name": row["block_name"],
            "yield_kwh": float(row["total_daily_yield_kwh"]),
            "status_code": row["inverter_status_code"],
            "irradiance": float(row["total_solar_radiation_kwh_m2"]),
            "pr": float(row["pr"]) if row["pr"] is not None else None,
            "block_median_pr": median_peer_pr,
        }

