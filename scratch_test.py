from solar_agent.db.connection import get_sync_engine
from solar_agent.tools.data_tools import fetch_inverter_daily_tool
from solar_agent.tools.performance_tools import compute_kpis
from solar_agent.tools.fault_tools import detect_peer_underperformance, detect_status_faults, detect_zero_generation, detect_anomalies
from solar_agent.tools.environment_tools import estimate_soiling_loss, analyze_weather_correlation
from solar_agent.graph.data_store import DataStore
import pprint
import pandas as pd

engine = get_sync_engine()

with engine.connect() as conn:
    plant_df = pd.read_sql("SELECT plant_id, plant_name FROM plants LIMIT 1", conn)

if plant_df.empty:
    print("No plants found in DB.")
else:
    plant_id = int(plant_df.iloc[0]['plant_id'])
    plant_name = plant_df.iloc[0]['plant_name']
    print(f"Testing against Plant: {plant_name} (ID: {plant_id}) for 2026-09-01 to 2026-09-30")
    
    # 1. Fetch data
    result = fetch_inverter_daily_tool.invoke({
        "plant_id": plant_id,
        "start_date": "2026-09-01",
        "end_date": "2026-09-30"
    })
    print("\n--- fetch_inverter_daily_tool ---")
    pprint.pprint(result)
    
    data_ref = result["data_ref"]
    df = DataStore.get(data_ref)
    
    print("\n--- Status Code Counts ---")
    if "status_code" in df.columns:
        print(df["status_code"].value_counts().to_dict())
        
    print("\n--- Soiling Ratio Range ---")
    if "soiling_ratio" in df.columns:
        print(f"Min: {df['soiling_ratio'].min():.4f}, Avg: {df['soiling_ratio'].mean():.4f}, Max: {df['soiling_ratio'].max():.4f}")
    
    # 2. Performance
    print("\n--- compute_kpis (plant, total) ---")
    pprint.pprint(compute_kpis(data_ref, level="plant", period="total"))
    
    print("\n--- compute_kpis (inverter, total) ---")
    inv_kpis = compute_kpis(data_ref, level="inverter", period="total")
    df_inv = DataStore.get(inv_kpis["data_ref"])
    if "performance_ratio" in df_inv.columns:
        pr_col = df_inv["performance_ratio"]
        print(f"Inverter PR -> Min: {pr_col.min():.4f}, Median: {pr_col.median():.4f}, Max: {pr_col.max():.4f}")
    
    # 3. Faults
    print("\n--- detect_zero_generation ---")
    pprint.pprint(detect_zero_generation(data_ref))
    
    print("\n--- detect_status_faults ---")
    pprint.pprint(detect_status_faults(data_ref))
    
    print("\n--- detect_peer_underperformance ---")
    pprint.pprint(detect_peer_underperformance(data_ref))
    
    print("\n--- detect_anomalies ---")
    pprint.pprint(detect_anomalies(data_ref))
    
    # 4. Environment
    print("\n--- estimate_soiling_loss ---")
    pprint.pprint(estimate_soiling_loss(data_ref))
    
    print("\n--- analyze_weather_correlation ---")
    pprint.pprint(analyze_weather_correlation(data_ref))
