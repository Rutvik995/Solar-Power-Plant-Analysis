"""
Derive all key_numbers for eval_set.json by running SQL against the real DB.
Run this once to generate the expected values; output is printed as JSON-ready dicts.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from solar_agent.db.connection import get_sync_engine
import pandas as pd
import yaml
import json

engine = get_sync_engine()

def q(sql):
    with engine.connect() as conn:
        return pd.read_sql(sql, conn)

with open("solar_agent/config/status_codes.yaml") as f:
    sc = yaml.safe_load(f)["status_codes"]
fault_codes_sql = ",".join(str(k) for k, v in sc.items() if v.get("is_fault"))
warning_codes_sql = ",".join(str(k) for k, v in sc.items()
                             if not v.get("is_fault") and k not in (0, 1))

results = {}

# Q1: highest total yield in Sep 2026
r = q("""SELECT plant_id, SUM(total_daily_yield_kwh) as y 
         FROM daily_inverter_telemetry 
         WHERE log_date BETWEEN '2026-09-01' AND '2026-09-30' 
         GROUP BY plant_id ORDER BY y DESC LIMIT 1""")
results["q1_top_yield"] = round(float(r["y"].iloc[0]), 2)
results["q1_top_plant_id"] = int(r["plant_id"].iloc[0])

# Q2: PR of plant 1 Sep 2026
r = q("""SELECT SUM(dit.total_daily_yield_kwh) / SUM(inv.rated_dc_kw * dwt.total_solar_radiation_kwh_m2) as pr,
         SUM(dit.total_daily_yield_kwh) as y
         FROM daily_inverter_telemetry dit
         JOIN inverters inv ON dit.inverter_id=inv.inverter_id
         LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
         WHERE dit.plant_id=1 AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-30'
         AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
results["q2_plant1_pr"] = round(float(r["pr"].iloc[0]), 4)
results["q2_plant1_yield"] = round(float(r["y"].iloc[0]), 2)

# Q3: zero-gen inverter days at plant 1 Sep 2026
r = q("""SELECT COUNT(*) as n FROM daily_inverter_telemetry dit
         LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
         WHERE dit.plant_id=1 AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-30'
         AND dit.total_daily_yield_kwh = 0 AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
results["q3_zero_gen_days_plant1"] = int(r["n"].iloc[0])

# Q5: soiling plant 1 Sep 2026
r = q("""SELECT SUM(dit.total_daily_yield_kwh*(1.0/dwt.soiling_ratio-1)) as sl
         FROM daily_inverter_telemetry dit
         LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
         WHERE dit.plant_id=1 AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-30'
         AND dwt.soiling_ratio > 0 AND dwt.soiling_ratio <= 1 AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
results["q5_soiling_plant1"] = round(float(r["sl"].iloc[0]), 2)

# Q6: faults plant 3 Sep 2026
r = q(f"""SELECT COUNT(*) as n FROM daily_inverter_telemetry 
          WHERE plant_id=3 AND log_date BETWEEN '2026-09-01' AND '2026-09-30'
          AND inverter_status_code IN ({fault_codes_sql})""")
results["q6_faults_plant3"] = int(r["n"].iloc[0])

# Q7: PR for all plants
r = q("""SELECT dit.plant_id, ROUND((SUM(dit.total_daily_yield_kwh) / SUM(inv.rated_dc_kw*dwt.total_solar_radiation_kwh_m2))::numeric,4) as pr
         FROM daily_inverter_telemetry dit
         JOIN inverters inv ON dit.inverter_id=inv.inverter_id
         LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
         WHERE dit.log_date BETWEEN '2026-09-01' AND '2026-09-30' AND dwt.total_solar_radiation_kwh_m2 > 0.5
         GROUP BY dit.plant_id ORDER BY dit.plant_id""")
results["q7_all_prs"] = {int(row["plant_id"]): round(float(row["pr"]), 4) for _, row in r.iterrows()}

# Q8: soiling plant 2
r = q("""SELECT SUM(dit.total_daily_yield_kwh*(1.0/dwt.soiling_ratio-1)) as sl
         FROM daily_inverter_telemetry dit
         LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
         WHERE dit.plant_id=2 AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-30'
         AND dwt.soiling_ratio > 0 AND dwt.soiling_ratio <= 1 AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
results["q8_soiling_plant2"] = round(float(r["sl"].iloc[0]), 2)

# Q9: top block PR plant 2
r = q("""SELECT inv.block_id, SUM(dit.total_daily_yield_kwh)/SUM(inv.rated_dc_kw*dwt.total_solar_radiation_kwh_m2) as pr
         FROM daily_inverter_telemetry dit
         JOIN inverters inv ON dit.inverter_id=inv.inverter_id
         LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
         WHERE dit.plant_id=2 AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-30' AND dwt.total_solar_radiation_kwh_m2 > 0.5
         GROUP BY inv.block_id ORDER BY pr DESC LIMIT 1""")
results["q9_top_block_plant2_pr"] = round(float(r["pr"].iloc[0]), 4)

# Q11: soiling plant 3
r = q("""SELECT SUM(dit.total_daily_yield_kwh*(1.0/dwt.soiling_ratio-1)) as sl
         FROM daily_inverter_telemetry dit
         LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
         WHERE dit.plant_id=3 AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-30'
         AND dwt.soiling_ratio > 0 AND dwt.soiling_ratio <= 1 AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
results["q11_soiling_plant3"] = round(float(r["sl"].iloc[0]), 2)

# Q13: fault count plant 2
r = q(f"""SELECT COUNT(*) as n FROM daily_inverter_telemetry
          WHERE plant_id=2 AND log_date BETWEEN '2026-09-01' AND '2026-09-30'
          AND inverter_status_code IN ({fault_codes_sql})""")
results["q13_faults_plant2"] = int(r["n"].iloc[0])

# Q14: plant 2 block PR worst
r = q("""SELECT inv.block_id, SUM(dit.total_daily_yield_kwh)/SUM(inv.rated_dc_kw*dwt.total_solar_radiation_kwh_m2) as pr
         FROM daily_inverter_telemetry dit
         JOIN inverters inv ON dit.inverter_id=inv.inverter_id
         LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
         WHERE dit.plant_id=2 AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-30' AND dwt.total_solar_radiation_kwh_m2 > 0.5
         GROUP BY inv.block_id ORDER BY pr ASC LIMIT 1""")
results["q14_worst_block_plant2_pr"] = round(float(r["pr"].iloc[0]), 4)

# Q15: soiling loss plant 1 as % of yield
r = q("""SELECT 
         SUM(dit.total_daily_yield_kwh*(1.0/dwt.soiling_ratio-1)) / SUM(dit.total_daily_yield_kwh) * 100 as sl_pct
         FROM daily_inverter_telemetry dit
         LEFT JOIN daily_weather_telemetry dwt ON dit.plant_id=dwt.plant_id AND dit.log_date=dwt.log_date
         WHERE dit.plant_id=1 AND dit.log_date BETWEEN '2026-09-01' AND '2026-09-30'
         AND dwt.soiling_ratio > 0 AND dwt.soiling_ratio <= 1 AND dwt.total_solar_radiation_kwh_m2 > 0.5""")
results["q15_soiling_pct_plant1"] = round(float(r["sl_pct"].iloc[0]), 2)

print(json.dumps(results, indent=2))
