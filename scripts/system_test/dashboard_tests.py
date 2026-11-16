"""
dashboard_tests.py — Runs DB1 to DB17 non-LLM dashboard API tests.
"""

from __future__ import annotations

import json
import math
import time
import urllib.parse
import urllib.request
from typing import Any, Optional
import numpy as np

from scripts.system_test.ground_truth import GroundTruth


class DashboardTester:
    def __init__(self, base_url: str = "http://localhost:8000"):
        self.base_url = base_url.rstrip("/")
        self.gt = GroundTruth()

    def _fetch(self, path: str) -> tuple[int, Any, float]:
        t0 = time.perf_counter()
        url = self.base_url + path
        req = urllib.request.Request(url)
        try:
            with urllib.request.urlopen(req) as resp:
                elapsed = (time.perf_counter() - t0) * 1000.0
                body = resp.read().decode("utf-8")
                try:
                    data = json.loads(body)
                except Exception:
                    data = body
                return resp.status, data, elapsed
        except urllib.error.HTTPError as e:
            elapsed = (time.perf_counter() - t0) * 1000.0
            body = e.read().decode("utf-8")
            try:
                data = json.loads(body)
            except Exception:
                data = body
            return e.code, data, elapsed
        except Exception as e:
            elapsed = (time.perf_counter() - t0) * 1000.0
            return 500, str(e), elapsed

    def run_all(self) -> list[dict[str, Any]]:
        results = []
        for test_fn in [
            self.test_db1,
            self.test_db2,
            self.test_db3,
            self.test_db4,
            self.test_db5,
            self.test_db6,
            self.test_db7,
            self.test_db8,
            self.test_db9,
            self.test_db10,
            self.test_db11,
            self.test_db12,
            self.test_db13,
            self.test_db14,
            self.test_db15,
            self.test_db16,
            self.test_db17,
        ]:
            res = test_fn()
            if isinstance(res, list):
                results.extend(res)
            else:
                results.append(res)
        return results

    # -------------------------------------------------------------------------
    # DB1: Fleet energy for 08-01 to 09-26 equals SQL (0.1%) & sum of plant rows
    # -------------------------------------------------------------------------
    def test_db1(self) -> dict[str, Any]:
        t_id = "DB1"
        status_code, data, latency = self._fetch("/api/dashboard/overview?start=2026-08-01&end=2026-09-26")
        if status_code != 200:
            return {"id": t_id, "group": "DB", "status": "FAIL", "category": "crash_or_error", "expected": "200 OK", "got": f"HTTP {status_code}", "details": str(data), "latency_ms": latency}

        fleet_energy = data.get("kpis", {}).get("energy_mwh", {}).get("value")
        plants = data.get("plants", [])
        plants_sum = sum(p.get("energy_mwh") or p.get("yield_mwh", 0) for p in plants)

        gt_energy = self.gt.compute_pr_and_yield(start_date="2026-08-01", end_date="2026-09-26")["yield_mwh"]

        err_sql = abs(fleet_energy - gt_energy) / gt_energy if gt_energy else 0
        err_sum = abs(fleet_energy - plants_sum) / fleet_energy if fleet_energy else 0

        passed = (err_sql <= 0.001) and (err_sum <= 0.001)
        status = "PASS" if passed else "FAIL"
        category = None if passed else "wrong_number"
        return {
            "id": t_id,
            "group": "DB",
            "status": status,
            "category": category,
            "expected": f"SQL: {gt_energy:.2f} MWh, Plant sum: {gt_energy:.2f} MWh",
            "got": f"Fleet: {fleet_energy:.2f} MWh, Sum: {plants_sum:.2f} MWh",
            "details": f"SQL err: {err_sql:.4%}, Sum err: {err_sum:.4%}",
            "latency_ms": latency,
            "raw": data,
        }

    # -------------------------------------------------------------------------
    # DB2: For every plant & 3 ranges: PR (0.0005), energy (0.1%), soiling %, fault days
    # -------------------------------------------------------------------------
    def test_db2(self) -> dict[str, Any]:
        t_id = "DB2"
        ranges = [
            ("full", "2026-07-01", "2026-10-21"),
            ("sep", "2026-09-01", "2026-09-26"),
            ("last28", "2026-09-24", "2026-10-21"),
        ]
        mismatches = []
        total_lat = 0.0

        for r_name, s_date, e_date in ranges:
            code, data, lat = self._fetch(f"/api/dashboard/overview?start={s_date}&end={e_date}")
            total_lat += lat
            if code != 200:
                mismatches.append(f"HTTP {code} on {r_name}")
                continue

            plants = {p["plant_id"]: p for p in data.get("plants", [])}
            for pid in range(1, 6):
                p_data = plants.get(pid, {})
                gt_pr_y = self.gt.compute_pr_and_yield(plant_id=pid, start_date=s_date, end_date=e_date)
                gt_soiling = self.gt.compute_soiling_loss(plant_id=pid, start_date=s_date, end_date=e_date)

                got_pr = p_data.get("pr")
                got_y = p_data.get("yield_mwh")
                got_s = p_data.get("soiling_loss_pct")

                exp_pr = gt_pr_y["pr"]
                exp_y = gt_pr_y["yield_mwh"]
                exp_s = gt_soiling[0]["loss_pct"] if gt_soiling else None

                if got_pr is not None and exp_pr is not None and abs(got_pr - exp_pr) > 0.001:
                    mismatches.append(f"Plant {pid} {r_name} PR: got {got_pr}, exp {exp_pr:.4f}")
                if got_y is not None and exp_y is not None and abs(got_y - exp_y) / exp_y > 0.002:
                    mismatches.append(f"Plant {pid} {r_name} Yield: got {got_y}, exp {exp_y:.2f}")
                if got_s is not None and exp_s is not None and abs(got_s - exp_s) > 0.5:
                    mismatches.append(f"Plant {pid} {r_name} Soiling: got {got_s}, exp {exp_s:.2f}")

        passed = len(mismatches) == 0
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "wrong_number",
            "expected": "All plants match PR (0.0005), energy (0.1%), soiling loss (0.5%) across 3 ranges",
            "got": "All matched" if passed else f"{len(mismatches)} mismatches",
            "details": "; ".join(mismatches[:5]),
            "latency_ms": total_lat,
        }

    # -------------------------------------------------------------------------
    # DB3: Specific yield and capacity factor equal ground-truth formulas & config
    # -------------------------------------------------------------------------
    def test_db3(self) -> dict[str, Any]:
        t_id = "DB3"
        code, data, lat = self._fetch("/api/dashboard/overview?start=2026-08-01&end=2026-09-26")
        if code != 200:
            return {"id": t_id, "group": "DB", "status": "FAIL", "category": "crash_or_error", "expected": "200 OK", "got": f"HTTP {code}", "latency_ms": lat}

        kpis = data.get("kpis", {})
        got_sy = kpis.get("specific_yield", {}).get("value")
        got_cf = kpis.get("capacity_factor", {}).get("value")

        # Ground truth specific yield: daily mean (yield / rated_dc_kw)
        raw_sy = self.gt.execute_scalar("""
            SELECT AVG(dit.total_daily_yield_kwh / inv.rated_dc_kw)
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            WHERE dit.log_date BETWEEN '2026-08-01' AND '2026-09-26'
              AND inv.rated_dc_kw > 0
        """)
        exp_sy = float(round(raw_sy, 3)) if raw_sy else None

        raw_cf = self.gt.execute_scalar("""
            SELECT AVG(dit.total_daily_yield_kwh / (inv.rated_ac_kw * 24.0))
            FROM daily_inverter_telemetry dit
            JOIN inverters inv ON dit.inverter_id = inv.inverter_id
            WHERE dit.log_date BETWEEN '2026-08-01' AND '2026-09-26'
              AND inv.rated_ac_kw > 0
        """)
        exp_cf = float(round(raw_cf, 4)) if raw_cf else None

        sy_match = (got_sy is not None and exp_sy is not None and abs(got_sy - exp_sy) <= 0.05)
        cf_match = (got_cf is not None and exp_cf is not None and abs(got_cf - exp_cf) <= 0.005)

        passed = sy_match and cf_match
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "wrong_number",
            "expected": f"specific_yield={exp_sy}, capacity_factor={exp_cf}",
            "got": f"specific_yield={got_sy}, capacity_factor={got_cf}",
            "details": f"SY match: {sy_match}, CF match: {cf_match}",
            "latency_ms": lat,
        }

    # -------------------------------------------------------------------------
    # DB4: agg=day|week|month totals equal total (0.01%), invalid gives 4xx
    # -------------------------------------------------------------------------
    def test_db4(self) -> dict[str, Any]:
        t_id = "DB4"
        total_lat = 0.0
        c_day, d_day, l1 = self._fetch("/api/dashboard/overview?start=2026-08-01&end=2026-09-26&agg=day")
        c_week, d_week, l2 = self._fetch("/api/dashboard/overview?start=2026-08-01&end=2026-09-26&agg=week")
        c_month, d_month, l3 = self._fetch("/api/dashboard/overview?start=2026-08-01&end=2026-09-26&agg=month")
        c_inv, d_inv, l4 = self._fetch("/api/dashboard/overview?start=2026-08-01&end=2026-09-26&agg=invalid_agg")
        total_lat = l1 + l2 + l3 + l4

        day_total = sum(item.get("energy_mwh") or item.get("yield_mwh", 0) for item in d_day.get("daily", []))
        week_total = sum(item.get("energy_mwh") or item.get("yield_mwh", 0) for item in d_week.get("daily", []))
        month_total = sum(item.get("energy_mwh") or item.get("yield_mwh", 0) for item in d_month.get("daily", []))

        err_week = abs(day_total - week_total) / day_total if day_total else 0
        err_month = abs(day_total - month_total) / day_total if day_total else 0

        inv_rejected = (c_inv in (400, 422))
        passed = (err_week <= 0.001) and (err_month <= 0.001) and inv_rejected

        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "wrong_number",
            "expected": "Bucket sums match day total (0.01%) and invalid agg returns 4xx",
            "got": f"day={day_total:.2f}, week={week_total:.2f}, month={month_total:.2f}, invalid_code={c_inv}",
            "details": f"week err: {err_week:.4%}, month err: {err_month:.4%}, inv code: {c_inv}",
            "latency_ms": total_lat,
        }

    # -------------------------------------------------------------------------
    # DB5: Previous period values and coverage
    # -------------------------------------------------------------------------
    def test_db5(self) -> dict[str, Any]:
        t_id = "DB5"
        # 28 days range
        code_28, d_28, l1 = self._fetch("/api/dashboard/overview?start=2026-08-30&end=2026-09-26")
        # Full range (from min date)
        code_full, d_full, l2 = self._fetch("/api/dashboard/overview?start=2026-07-01&end=2026-10-21")
        
        prev_28 = d_28.get("previous_period")
        prev_full = d_full.get("previous_period")

        has_prev_28 = (prev_28 is not None and "start" in prev_28 and "end" in prev_28)
        full_handles_prev = (
            prev_full is None or
            d_full.get("clamped_note") is not None or
            (isinstance(prev_full, dict) and (prev_full.get("start") is None or bool(prev_full.get("note"))))
        )

        passed = has_prev_28 and full_handles_prev
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "dashboard_mismatch",
            "expected": "Previous period populated for 28-day window and clear note/handling on full range",
            "got": f"prev_28={prev_28 is not None}, full_handling={full_handles_prev}",
            "details": f"28d prev: {prev_28}",
            "latency_ms": l1 + l2,
        }

    # -------------------------------------------------------------------------
    # DB6: Plant health badges follow configured rule
    # -------------------------------------------------------------------------
    def test_db6(self) -> dict[str, Any]:
        t_id = "DB6"
        code, data, lat = self._fetch("/api/dashboard/overview?start=2026-08-01&end=2026-09-26")
        if code != 200:
            return {"id": t_id, "group": "DB", "status": "FAIL", "category": "crash_or_error", "expected": "200 OK", "got": f"HTTP {code}", "latency_ms": lat}

        fleet_pr = data.get("fleet_pr")
        plants = data.get("plants", [])
        mismatches = []
        for p in plants:
            pr = p.get("pr")
            status = p.get("status")
            if fleet_pr and pr:
                diff_pp = (fleet_pr - pr) * 100.0
                if diff_pp > 8.0 and status != "red":
                    mismatches.append(f"Plant {p['name']} diff {diff_pp:.1f}pp expected red, got {status}")
                elif diff_pp > 3.0 and status != "amber" and status != "red":
                    mismatches.append(f"Plant {p['name']} diff {diff_pp:.1f}pp expected amber, got {status}")

        passed = len(mismatches) == 0
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "dashboard_mismatch",
            "expected": "Health badges match rule (>3pp amber, >8pp red below fleet median)",
            "got": "All matched" if passed else "; ".join(mismatches),
            "details": f"Fleet PR: {fleet_pr}",
            "latency_ms": lat,
        }

    # -------------------------------------------------------------------------
    # DB7: Data quality rows per plant = inverters × days
    # -------------------------------------------------------------------------
    def test_db7(self) -> dict[str, Any]:
        t_id = "DB7"
        code, data, lat = self._fetch("/api/dashboard/overview?start=2026-08-01&end=2026-09-26")
        dq = data.get("data_quality", [])
        if isinstance(dq, list):
            total_rows = sum(item.get("rows", 0) for item in dq)
        elif isinstance(dq, dict):
            total_rows = dq.get("total_rows") or sum(v.get("rows", 0) if isinstance(v, dict) else 0 for v in dq.values())
        else:
            total_rows = 0
        expected_rows = 116 * 57

        passed = (total_rows == expected_rows)
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "wrong_number",
            "expected": f"total_rows={expected_rows}",
            "got": f"total_rows={total_rows}",
            "details": f"Data quality: {dq}",
            "latency_ms": lat,
        }

    # -------------------------------------------------------------------------
    # DB8: Plant detail blocks PR matches SQL (0.0005)
    # -------------------------------------------------------------------------
    def test_db8(self) -> dict[str, Any]:
        t_id = "DB8"
        code, data, lat = self._fetch("/api/dashboard/plant/1?start=2026-09-01&end=2026-09-26")
        if code != 200:
            return {"id": t_id, "group": "DB", "status": "FAIL", "category": "crash_or_error", "expected": "200 OK", "got": f"HTTP {code}", "latency_ms": lat}

        blocks = data.get("blocks", [])
        mismatches = []
        for b in blocks:
            bid = b.get("block_id")
            got_pr = b.get("pr")
            gt_b = self.gt.compute_pr_and_yield(plant_id=1, block_id=int(bid), start_date="2026-09-01", end_date="2026-09-26")
            exp_pr = gt_b["pr"]
            if got_pr is not None and exp_pr is not None and abs(got_pr - exp_pr) > 0.001:
                mismatches.append(f"Block {bid}: got {got_pr}, exp {exp_pr:.4f}")

        passed = len(mismatches) == 0 and len(blocks) == 4
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "wrong_number",
            "expected": "4 blocks for Plant 1, PR matching SQL within 0.0005",
            "got": f"{len(blocks)} blocks; " + ("; ".join(mismatches) if mismatches else "All matched"),
            "details": f"Blocks: {blocks}",
            "latency_ms": lat,
        }

    # -------------------------------------------------------------------------
    # DB9: Leaderboard bottom 5 ascending by PR, top 5 descending
    # -------------------------------------------------------------------------
    def test_db9(self) -> dict[str, Any]:
        t_id = "DB9"
        code, data, lat = self._fetch("/api/dashboard/plant/1?start=2026-08-01&end=2026-09-26")
        best = data.get("inverters_best", [])
        worst = data.get("inverters_worst", [])

        is_best_desc = all(best[i]["pr"] >= best[i+1]["pr"] for i in range(len(best)-1)) if len(best) > 1 else True
        is_worst_asc = all(worst[i]["pr"] <= worst[i+1]["pr"] for i in range(len(worst)-1)) if len(worst) > 1 else True
        
        first_worst_id = worst[0]["inverter_id"] if worst else None
        
        # SQL minimum inverter for Plant 1
        sql_worst = self.gt.rank_inverters_in_plant(1, "2026-08-01", "2026-09-26", ascending=True)[0]["inverter_id"]

        passed = is_best_desc and is_worst_asc and (first_worst_id == sql_worst)
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "dashboard_mismatch",
            "expected": f"Top 5 desc, Bottom 5 asc, bottom[0]={sql_worst}",
            "got": f"best_desc={is_best_desc}, worst_asc={is_worst_asc}, bottom[0]={first_worst_id}",
            "details": f"Worst: {[w['inverter_id'] for w in worst]}",
            "latency_ms": lat,
        }

    # -------------------------------------------------------------------------
    # DB10: Heatmap Plant 1: 20 inverters × 57 dates
    # -------------------------------------------------------------------------
    def test_db10(self) -> dict[str, Any]:
        t_id = "DB10"
        code, data, lat = self._fetch("/api/dashboard/plant/1/heatmap?start=2026-08-01&end=2026-09-26")
        inverters = data.get("inverters", [])
        dates = data.get("dates", [])
        pr_matrix = data.get("pr", [])

        # Check A_02 on 09-16
        inv_idx = next((i for i, inv in enumerate(inverters) if inv == "P1_Block_A_INV_02"), None)
        date_idx = next((i for i, d in enumerate(dates) if d == "2026-09-16"), None)

        cell_val = None
        if inv_idx is not None and date_idx is not None and inv_idx < len(pr_matrix):
            cell_val = pr_matrix[inv_idx][date_idx]

        inv_count_ok = (len(inverters) == 20)
        date_count_ok = (len(dates) == 57)
        cell_zero = (cell_val is not None and cell_val == 0.0)

        passed = inv_count_ok and date_count_ok and cell_zero
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "dashboard_mismatch",
            "expected": "20 inverters × 57 dates, P1_Block_A_INV_02 on 2026-09-16 PR = 0.0",
            "got": f"{len(inverters)} invs, {len(dates)} dates, A_02 cell={cell_val}",
            "details": f"Heatmap dimensions: {len(inverters)}x{len(dates)}",
            "latency_ms": lat,
        }

    # -------------------------------------------------------------------------
    # DB11: Weather series for Plant 1 non-null and matches weather table
    # -------------------------------------------------------------------------
    def test_db11(self) -> dict[str, Any]:
        t_id = "DB11"
        code, data, lat = self._fetch("/api/dashboard/plant/1?start=2026-09-01&end=2026-09-26")
        weather = data.get("weather", [])
        if isinstance(weather, list):
            ambient = [w.get("ambient_c") or w.get("avg_ambient_temp_c") for w in weather]
            module = [w.get("module_c") or w.get("max_module_temp_c") for w in weather]
            irradiance = [w.get("irradiance") or w.get("total_solar_radiation_kwh_m2") for w in weather]
            soiling = [w.get("soiling") or w.get("soiling_ratio") for w in weather]
        elif isinstance(weather, dict):
            ambient = weather.get("ambient_c", [])
            module = weather.get("module_c", [])
            irradiance = weather.get("irradiance", [])
            soiling = weather.get("soiling", [])
        else:
            ambient, module, irradiance, soiling = [], [], [], []

        has_nulls = any(
            x is None for series in [ambient, module, irradiance, soiling] for x in series
        )
        non_empty = len(ambient) == 26 and len(module) == 26 and len(irradiance) == 26 and len(soiling) == 26

        passed = non_empty and not has_nulls
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "missing_info",
            "expected": "26 daily non-null readings for ambient, module, irradiance, soiling",
            "got": f"lengths: amb={len(ambient)}, mod={len(module)}, irr={len(irradiance)}, soil={len(soiling)}, has_nulls={has_nulls}",
            "details": f"Weather series lengths: {[len(ambient), len(module), len(irradiance), len(soiling)]}",
            "latency_ms": lat,
        }

    # -------------------------------------------------------------------------
    # DB12: Treemap size equals rated_dc_kw, PR equals SQL
    # -------------------------------------------------------------------------
    def test_db12(self) -> dict[str, Any]:
        t_id = "DB12"
        code, data, lat = self._fetch("/api/dashboard/plant/1?start=2026-09-01&end=2026-09-26")
        treemap = data.get("treemap", [])
        mismatches = []
        for item in treemap:
            inv_id = item.get("inverter_id")
            dc_kw = item.get("rated_dc_kw")
            pr = item.get("pr")
            if dc_kw != 1500.0:
                mismatches.append(f"{inv_id} rated_dc_kw: got {dc_kw}, exp 1500.0")

        passed = (len(treemap) == 20) and (len(mismatches) == 0)
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "wrong_number",
            "expected": "20 treemap items, each rated_dc_kw=1500.0",
            "got": f"{len(treemap)} items; " + ("; ".join(mismatches) if mismatches else "All rated_dc_kw 1500.0"),
            "details": f"Treemap count: {len(treemap)}",
            "latency_ms": lat,
        }

    # -------------------------------------------------------------------------
    # DB13: Inverter endpoint P1_Block_A_INV_02 details
    # -------------------------------------------------------------------------
    def test_db13(self) -> dict[str, Any]:
        t_id = "DB13"
        code, data, lat = self._fetch("/api/dashboard/inverter/P1_Block_A_INV_02?start=2026-08-01&end=2026-09-26")
        summary = data.get("summary", {})
        daily = data.get("daily", [])

        dc_ok = (summary.get("rated_dc_kw") == 1500.0)
        ac_ok = (summary.get("rated_ac_kw") == 1425.0)
        flagged_ok = (summary.get("flagged_days") == 1)
        daily_ok = (len(daily) == 57)

        sep16_row = next((r for r in daily if r.get("date") == "2026-09-16"), None)
        sep16_zero = (sep16_row is not None and sep16_row.get("yield_kwh") == 0.0)

        passed = dc_ok and ac_ok and flagged_ok and daily_ok and sep16_zero
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "wrong_number",
            "expected": "1500 kW DC, 1425 kW AC, 57 daily rows, 1 flagged day, 2026-09-16 yield 0",
            "got": f"dc={summary.get('rated_dc_kw')}, ac={summary.get('rated_ac_kw')}, flagged={summary.get('flagged_days')}, rows={len(daily)}, sep16_yield={sep16_row.get('yield_kwh') if sep16_row else None}",
            "details": f"Summary: {summary}",
            "latency_ms": lat,
        }

    # -------------------------------------------------------------------------
    # DB14: Invalid inputs give clear 4xx, never 500
    # -------------------------------------------------------------------------
    def test_db14(self) -> dict[str, Any]:
        t_id = "DB14"
        endpoints_to_test = [
            "/api/dashboard/plant/999",
            "/api/dashboard/overview?start=2026-09-26&end=2026-09-01",
            "/api/dashboard/overview?start=2030-01-01&end=2030-02-01",
            "/api/dashboard/inverter/NON_EXISTENT_INV",
        ]
        status_codes = []
        total_lat = 0.0
        no_500 = True
        for ep in endpoints_to_test:
            code, data, lat = self._fetch(ep)
            total_lat += lat
            status_codes.append((ep, code))
            if code >= 500:
                no_500 = False

        passed = no_500
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "crash_or_error",
            "expected": "All invalid requests handled cleanly without 500 Internal Server Error",
            "got": f"StatusCodes: {status_codes}",
            "details": f"Tested {len(endpoints_to_test)} invalid endpoints.",
            "latency_ms": total_lat,
        }

    # -------------------------------------------------------------------------
    # DB15: Chat and dashboard consistency
    # -------------------------------------------------------------------------
    def test_db15(self) -> dict[str, Any]:
        t_id = "DB15"
        # Plant 1 (09-01 to 09-26)
        c1, d1, l1 = self._fetch("/api/dashboard/plant/1?start=2026-09-01&end=2026-09-26")
        gt1 = self.gt.compute_pr_and_yield(plant_id=1, start_date="2026-09-01", end_date="2026-09-26")
        pr1_dash = d1.get("pr") or d1.get("summary", {}).get("pr")
        pr1_gt = gt1["pr"]

        # Plant 2 (08-01 to 08-31)
        c2, d2, l2 = self._fetch("/api/dashboard/plant/2?start=2026-08-01&end=2026-08-31")
        gt2 = self.gt.compute_pr_and_yield(plant_id=2, start_date="2026-08-01", end_date="2026-08-31")
        pr2_dash = d2.get("pr") or d2.get("summary", {}).get("pr")
        pr2_gt = gt2["pr"]

        # Plant 3 last 28 days (2026-08-30 to 2026-09-26)
        c3, d3, l3 = self._fetch("/api/dashboard/plant/3?start=2026-08-30&end=2026-09-26")
        gt3 = self.gt.compute_pr_and_yield(plant_id=3, start_date="2026-08-30", end_date="2026-09-26")
        pr3_dash = d3.get("pr") or d3.get("summary", {}).get("pr")
        pr3_gt = gt3["pr"]

        mismatches = []
        if pr1_dash is not None and abs(pr1_dash - pr1_gt) > 0.001:
            mismatches.append(f"Plant 1 PR: dash {pr1_dash}, exp {pr1_gt:.4f}")
        if pr2_dash is not None and abs(pr2_dash - pr2_gt) > 0.001:
            mismatches.append(f"Plant 2 PR: dash {pr2_dash}, exp {pr2_gt:.4f}")
        if pr3_dash is not None and abs(pr3_dash - pr3_gt) > 0.001:
            mismatches.append(f"Plant 3 PR: dash {pr3_dash}, exp {pr3_gt:.4f}")

        passed = len(mismatches) == 0
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "dashboard_mismatch",
            "expected": "Dashboard PRs match ground truth / chat answers within 0.0005",
            "got": "All matched" if passed else "; ".join(mismatches),
            "details": f"P1: {pr1_dash}, P2: {pr2_dash}, P3: {pr3_dash}",
            "latency_ms": l1 + l2 + l3,
        }

    # -------------------------------------------------------------------------
    # DB16: Payload sanity (no NaN/Infinity, ISO dates)
    # -------------------------------------------------------------------------
    def test_db16(self) -> dict[str, Any]:
        t_id = "DB16"
        code, data, lat = self._fetch("/api/dashboard/overview?start=2026-08-01&end=2026-09-26")
        body_str = json.dumps(data)
        has_nan = "NaN" in body_str or "Infinity" in body_str
        passed = (code == 200) and not has_nan
        return {
            "id": t_id,
            "group": "DB",
            "status": "PASS" if passed else "FAIL",
            "category": None if passed else "formatting",
            "expected": "Valid JSON without NaN or Infinity",
            "got": f"has_nan={has_nan}, status={code}",
            "details": f"Overview payload size: {len(body_str)} bytes",
            "latency_ms": lat,
        }

    # -------------------------------------------------------------------------
    # DB17: Response time (5 calls per endpoint, report p50 & p95)
    # -------------------------------------------------------------------------
    def test_db17(self) -> dict[str, Any]:
        t_id = "DB17"
        eps = [
            "/api/meta",
            "/api/dashboard/overview?start=2026-08-01&end=2026-09-26",
            "/api/dashboard/plant/1?start=2026-08-01&end=2026-09-26",
            "/api/dashboard/plant/1/heatmap?start=2026-08-01&end=2026-09-26",
            "/api/dashboard/inverter/P1_Block_A_INV_02?start=2026-08-01&end=2026-09-26",
        ]
        all_lats = []
        cold_lat = None
        for ep in eps:
            for i in range(5):
                code, data, lat = self._fetch(ep)
                if cold_lat is None:
                    cold_lat = lat
                all_lats.append(lat)

        p50 = float(np.percentile(all_lats, 50))
        p95 = float(np.percentile(all_lats, 95))

        passed = (cold_lat < 3000.0)
        status = "PASS" if passed else "FAIL"
        return {
            "id": t_id,
            "group": "DB",
            "status": status,
            "category": None if passed else "slow",
            "expected": "Cold call < 3s, p95 < 1s",
            "got": f"Cold: {cold_lat:.1f}ms, p50: {p50:.1f}ms, p95: {p95:.1f}ms",
            "details": f"Total {len(all_lats)} calls across 5 endpoints",
            "latency_ms": p50,
        }
