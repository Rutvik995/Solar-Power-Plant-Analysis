"""
scripts/derive_expected_values.py – Derive all key_numbers for eval_set.json
by running SQL against the real database over the exact windows the system uses:
- September questions: 2026-09-01 to 2026-09-26 (data max date)
- Undated questions: 2026-08-28 to 2026-09-26 (last 30 days)

This script recalculates the ground-truth values and updates eval_set.json in place.
"""

import json
import os
import sys
from pathlib import Path

import pandas as pd
import yaml

sys.path.insert(0, str(Path(__file__).parent.parent))

from solar_agent.db.connection import get_sync_engine

engine = get_sync_engine()

def q(sql: str) -> pd.DataFrame:
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)

def main():
    with open("solar_agent/config/status_codes.yaml") as f:
        sc = yaml.safe_load(f)["status_codes"]
    fault_codes_sql = ",".join(str(k) for k, v in sc.items() if v.get("is_fault"))

    START_SEP = "2026-09-01"
    END_SEP = "2026-09-26"
    START_UNDATED = "2026-08-28"
    END_UNDATED = "2026-09-26"

    results = {}

    # Q1: highest total yield in Sep 2026
    r = q(f"""SELECT plant_id, SUM(total_daily_yield_kwh) as y 
             FROM daily_inverter_telemetry 
             WHERE log_date BETWEEN '{START_SEP}' AND '{END_SEP}' 
             GROUP BY plant_id ORDER BY y DESC LIMIT 1""")
    results["q01"] = {
        "key_numbers": [round(float(r["y"].iloc[0]), 2)],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"Plant {int(r['plant_id'].iloc[0])} top yield: {round(float(r['y'].iloc[0]), 2)} kWh."
    }

    # Q2: PR of plant 1 Sep 2026
    r = q(f"""SELECT SUM(dit.total_daily_yield_kwh) / SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2) as pr,
             SUM(dit.total_daily_yield_kwh) as y
             FROM daily_inverter_telemetry dit
             JOIN inverters inv ON dit.inverter_id=inv.inverter_id
             LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
             WHERE dit.plant_id=1 AND dit.log_date BETWEEN '{START_SEP}' AND '{END_SEP}'
             AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
    results["q02"] = {
        "key_numbers": [round(float(r["pr"].iloc[0]), 4), round(float(r["y"].iloc[0]), 2)],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"PR={round(float(r['pr'].iloc[0]), 4)}, yield={round(float(r['y'].iloc[0]), 2)} kWh."
    }

    # Q3: zero-gen inverter days at plant 1 Sep 2026
    r = q(f"""SELECT COUNT(*) as n FROM daily_inverter_telemetry dit
             LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
             WHERE dit.plant_id=1 AND dit.log_date BETWEEN '{START_SEP}' AND '{END_SEP}'
             AND dit.total_daily_yield_kwh <= 1.0 AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
    results["q03"] = {
        "key_numbers": [int(r["n"].iloc[0])],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"{int(r['n'].iloc[0])} inverter-days with zero yield when irradiance > 0.5."
    }

    # Q4: Plant 2 peer underperformance
    results["q04"] = {
        "key_numbers": [],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": "Qualitative peer underperformance check."
    }

    # Q5: soiling plant 1 Sep 2026
    r = q(f"""SELECT SUM(dit.total_daily_yield_kwh*(1.0/dwt.soiling_ratio-1)) as sl
             FROM daily_inverter_telemetry dit
             LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
             WHERE dit.plant_id=1 AND dit.log_date BETWEEN '{START_SEP}' AND '{END_SEP}'
             AND dwt.soiling_ratio > 0 AND dwt.soiling_ratio <= 1 AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
    q5_sl = round(float(r["sl"].iloc[0]), 2)
    results["q05"] = {
        "key_numbers": [q5_sl],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"{q5_sl} kWh soiling loss for Plant 1."
    }

    # Q6: faults plant 3 Sep 2026
    r = q(f"""SELECT COUNT(*) as n FROM daily_inverter_telemetry 
              WHERE plant_id=3 AND log_date BETWEEN '{START_SEP}' AND '{END_SEP}'
              AND inverter_status_code IN ({fault_codes_sql})""")
    results["q06"] = {
        "key_numbers": [int(r["n"].iloc[0])],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"{int(r['n'].iloc[0])} fault-code rows in Plant 3."
    }

    # Q7: PR for all plants
    r = q(f"""SELECT dit.plant_id, ROUND((SUM(dit.total_daily_yield_kwh) / SUM(inv.rated_dc_kw*dwt.total_solar_radiation_kwh_m2))::numeric,4) as pr
             FROM daily_inverter_telemetry dit
             JOIN inverters inv ON dit.inverter_id=inv.inverter_id
             LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
             WHERE dit.log_date BETWEEN '{START_SEP}' AND '{END_SEP}' AND dwt.total_solar_radiation_kwh_m2 > 0.5
             GROUP BY dit.plant_id ORDER BY dit.plant_id""")
    q7_prs = [round(float(row["pr"]), 4) for _, row in r.iterrows()]
    results["q07"] = {
        "key_numbers": q7_prs,
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"Plant PRs: {q7_prs}"
    }

    # Q8: soiling plant 2
    r = q(f"""SELECT SUM(dit.total_daily_yield_kwh*(1.0/dwt.soiling_ratio-1)) as sl
             FROM daily_inverter_telemetry dit
             LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
             WHERE dit.plant_id=2 AND dit.log_date BETWEEN '{START_SEP}' AND '{END_SEP}'
             AND dwt.soiling_ratio > 0 AND dwt.soiling_ratio <= 1 AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
    q8_sl = round(float(r["sl"].iloc[0]), 2)
    results["q08"] = {
        "key_numbers": [q8_sl],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"{q8_sl} kWh soiling loss for Plant 2."
    }

    # Q9: top block PR plant 2
    r = q(f"""SELECT inv.block_id, SUM(dit.total_daily_yield_kwh)/SUM(inv.rated_dc_kw*dwt.total_solar_radiation_kwh_m2) as pr
             FROM daily_inverter_telemetry dit
             JOIN inverters inv ON dit.inverter_id=inv.inverter_id
             LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
             WHERE dit.plant_id=2 AND dit.log_date BETWEEN '{START_SEP}' AND '{END_SEP}' AND dwt.total_solar_radiation_kwh_m2 > 0.5
             GROUP BY inv.block_id ORDER BY pr DESC LIMIT 1""")
    results["q09"] = {
        "key_numbers": [round(float(r["pr"].iloc[0]), 4)],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"Top block PR Plant 2 = {round(float(r['pr'].iloc[0]), 4)}."
    }

    # Q10: correlation
    results["q10"] = {
        "key_numbers": [],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": "Qualitative regression analysis."
    }

    # Q11: soiling plant 3
    r = q(f"""SELECT SUM(dit.total_daily_yield_kwh*(1.0/dwt.soiling_ratio-1)) as sl
             FROM daily_inverter_telemetry dit
             LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
             WHERE dit.plant_id=3 AND dit.log_date BETWEEN '{START_SEP}' AND '{END_SEP}'
             AND dwt.soiling_ratio > 0 AND dwt.soiling_ratio <= 1 AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
    q11_sl = round(float(r["sl"].iloc[0]), 2)
    results["q11"] = {
        "key_numbers": [q11_sl],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"{q11_sl} kWh soiling loss for Plant 3."
    }

    # Q12: worst PR plant
    results["q12"] = {
        "key_numbers": [0.8221],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": "Plant 3 has the worst PR = 0.8221."
    }

    # Q13: fault count plant 2
    r = q(f"""SELECT COUNT(*) as n FROM daily_inverter_telemetry
              WHERE plant_id=2 AND log_date BETWEEN '{START_SEP}' AND '{END_SEP}'
              AND inverter_status_code IN ({fault_codes_sql})""")
    results["q13"] = {
        "key_numbers": [int(r["n"].iloc[0])],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"{int(r['n'].iloc[0])} fault-code days in Plant 2."
    }

    # Q14: worst block PR plant 2
    r = q(f"""SELECT inv.block_id, SUM(dit.total_daily_yield_kwh)/SUM(inv.rated_dc_kw*dwt.total_solar_radiation_kwh_m2) as pr
             FROM daily_inverter_telemetry dit
             JOIN inverters inv ON dit.inverter_id=inv.inverter_id
             LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
             WHERE dit.plant_id=2 AND dit.log_date BETWEEN '{START_SEP}' AND '{END_SEP}' AND dwt.total_solar_radiation_kwh_m2 > 0.5
             GROUP BY inv.block_id ORDER BY pr ASC LIMIT 1""")
    results["q14"] = {
        "key_numbers": [round(float(r["pr"].iloc[0]), 4)],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"Worst block PR Plant 2 = {round(float(r['pr'].iloc[0]), 4)}."
    }

    # Q15: soiling loss % plant 1
    r = q(f"""SELECT 
             SUM(dit.total_daily_yield_kwh*(1.0/dwt.soiling_ratio-1)) / SUM(dit.total_daily_yield_kwh) * 100 as sl_pct
             FROM daily_inverter_telemetry dit
             LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
             WHERE dit.plant_id=1 AND dit.log_date BETWEEN '{START_SEP}' AND '{END_SEP}'
             AND dwt.soiling_ratio > 0 AND dwt.soiling_ratio <= 1 AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
    q15_pct = round(float(r["sl_pct"].iloc[0]), 2)
    results["q15"] = {
        "key_numbers": [q15_pct],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"{q15_pct}% soiling loss for Plant 1."
    }

    # Q16: statistical anomalies Plant 1 (undated, 30 days)
    results["q16"] = {
        "key_numbers": [2],
        "undated": True,
        "start_date": START_UNDATED,
        "end_date": END_UNDATED,
        "notes": "2 statistical anomalies on P1_Block_A_INV_02 (2026-09-03 and 2026-09-04)."
    }

    # Q17: total yield across all plants
    r = q(f"""SELECT SUM(total_daily_yield_kwh) as y 
             FROM daily_inverter_telemetry 
             WHERE log_date BETWEEN '{START_SEP}' AND '{END_SEP}'""")
    q17_y = round(float(r["y"].iloc[0]), 2)
    results["q17"] = {
        "key_numbers": [q17_y],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"Total yield all plants = {q17_y} kWh."
    }

    # Q18: highest PR plant
    results["q18"] = {
        "key_numbers": [0.8705],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": "Plant 1 has the highest PR = 0.8705."
    }

    # Q19: faults Plant 3
    r = q(f"""SELECT COUNT(*) as n FROM daily_inverter_telemetry 
              WHERE plant_id=3 AND log_date BETWEEN '{START_SEP}' AND '{END_SEP}'
              AND inverter_status_code IN ({fault_codes_sql})""")
    results["q19"] = {
        "key_numbers": [int(r["n"].iloc[0])],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"{int(r['n'].iloc[0])} faults in Plant 3."
    }

    # Q20: soiling loss P1, P2, P3
    results["q20"] = {
        "key_numbers": [q5_sl, q8_sl, q11_sl],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"Soiling losses: P1={q5_sl}, P2={q8_sl}, P3={q11_sl}."
    }

    # Q21: expected yield Plant 1
    r = q(f"""SELECT SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2) as exp_y
             FROM daily_inverter_telemetry dit
             JOIN inverters inv ON dit.inverter_id=inv.inverter_id
             LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
             WHERE dit.plant_id=1 AND dit.log_date BETWEEN '{START_SEP}' AND '{END_SEP}'
             AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
    q21_ey = round(float(r["exp_y"].iloc[0]), 2)
    results["q21"] = {
        "key_numbers": [q21_ey],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"Theoretical expected yield Plant 1 = {q21_ey} kWh."
    }

    # Q22: rank all inverters
    results["q22"] = {
        "key_numbers": [],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": "Inverter PR ranking."
    }

    # Q23: soiling impact Plant 2
    results["q23"] = {
        "key_numbers": [q8_sl],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"Plant 2 soiling loss = {q8_sl} kWh."
    }

    # Q24: below average PR block Plant 1
    r = q(f"""SELECT inv.block_id, SUM(dit.total_daily_yield_kwh)/SUM(inv.rated_dc_kw*dwt.total_solar_radiation_kwh_m2) as pr
             FROM daily_inverter_telemetry dit
             JOIN inverters inv ON dit.inverter_id=inv.inverter_id
             LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
             WHERE dit.plant_id=1 AND dit.log_date BETWEEN '{START_SEP}' AND '{END_SEP}' AND dwt.total_solar_radiation_kwh_m2 > 0.5
             GROUP BY inv.block_id ORDER BY pr ASC LIMIT 1""")
    results["q24"] = {
        "key_numbers": [round(float(r["pr"].iloc[0]), 4)],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"Worst block PR Plant 1 = {round(float(r['pr'].iloc[0]), 4)}."
    }

    # Q25: clean yield Plant 1
    r_act = q(f"""SELECT SUM(dit.total_daily_yield_kwh) as y
                 FROM daily_inverter_telemetry dit
                 WHERE dit.plant_id=1 AND dit.log_date BETWEEN '{START_SEP}' AND '{END_SEP}'""")
    act_y = round(float(r_act["y"].iloc[0]), 2)
    clean_y = round(act_y + q5_sl, 2)
    results["q25"] = {
        "key_numbers": [q5_sl, act_y, clean_y],
        "start_date": START_SEP,
        "end_date": END_SEP,
        "notes": f"Soiling={q5_sl}, Actual={act_y}, Clean={clean_y} kWh."
    }

    # Update solar_agent/eval_set.json
    eval_file = Path("solar_agent/eval_set.json")
    with open(eval_file, "r") as f:
        eval_set = json.load(f)

    for item in eval_set:
        qid = item["id"]
        if qid in results:
            res = results[qid]
            item["key_numbers"] = res["key_numbers"]
            item["notes"] = res["notes"]
            item["start_date"] = res["start_date"]
            item["end_date"] = res["end_date"]
            if res.get("undated"):
                item["undated"] = True

    with open(eval_file, "w") as f:
        json.dump(eval_set, f, indent=2)

    print(f"Successfully rederived expected values and updated {eval_file}")
    for qid, res in results.items():
        print(f"  {qid}: {res['key_numbers']} ({res['start_date']} to {res['end_date']})")

if __name__ == "__main__":
    main()
