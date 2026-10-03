import psycopg2
from psycopg2.extras import execute_values
import random
from datetime import datetime, timedelta

# --------------------------------------------------------------------------------
# DATABASE CREDENTIALS (UPDATE THESE IF NEEDED)
# --------------------------------------------------------------------------------
DB_HOST = "localhost"
DB_NAME = "postgres"  # Change to your specific database if you have one created
DB_USER = "postgres"
DB_PASS = "123456789"  # Update with your local postgres password
DB_PORT = "5432"

# Connect to database
def get_connection():
    try:
        return psycopg2.connect(
            host=DB_HOST,
            dbname=DB_NAME,
            user=DB_USER,
            password=DB_PASS,
            port=DB_PORT,
            options="-c search_path=public"
        )
    except Exception as e:
        print(f"Error connecting to the database: {e}")
        print("Please verify that PostgreSQL is running and credentials are correct.")
        raise

def create_schema():
    conn = get_connection()
    cur = conn.cursor()
    
    # --------------------------------------------------------------------------------
    # 1. DATABASE SCHEMA REQUIREMENTS
    # --------------------------------------------------------------------------------
    print("Dropping existing tables if they exist...")
    cur.execute('''
        CREATE SCHEMA IF NOT EXISTS public;
        SET search_path TO public;
        
        DROP TABLE IF EXISTS daily_weather_telemetry CASCADE;
        DROP TABLE IF EXISTS daily_inverter_telemetry CASCADE;
        DROP TABLE IF EXISTS inverters CASCADE;
        DROP TABLE IF EXISTS blocks CASCADE;
        DROP TABLE IF EXISTS plants CASCADE;
    ''')

    print("Creating schema...")
    cur.execute('''
        CREATE TABLE plants (
            plant_id SERIAL PRIMARY KEY,
            plant_name VARCHAR(100),
            area_acres NUMERIC(8,2),
            capacity_mwp NUMERIC(8,2)
        );
    ''')

    cur.execute('''
        CREATE TABLE blocks (
            block_id SERIAL PRIMARY KEY,
            plant_id INT REFERENCES plants(plant_id),
            block_name VARCHAR(50)
        );
    ''')

    cur.execute('''
        CREATE TABLE inverters (
            inverter_id VARCHAR(50) PRIMARY KEY,
            block_id INT REFERENCES blocks(block_id),
            plant_id INT REFERENCES plants(plant_id),
            rated_dc_kw NUMERIC(8,2),
            rated_ac_kw NUMERIC(8,2)
        );
    ''')

    cur.execute('''
        CREATE TABLE daily_inverter_telemetry (
            log_date DATE NOT NULL,
            inverter_id VARCHAR(50) REFERENCES inverters(inverter_id),
            plant_id INT REFERENCES plants(plant_id),
            peak_dc_power_kw NUMERIC(10,2),
            peak_ac_power_kw NUMERIC(10,2),
            total_daily_yield_kwh NUMERIC(12,2),
            avg_dc_voltage_v NUMERIC(8,2),
            avg_dc_current_a NUMERIC(8,2),
            inverter_status_code INT DEFAULT 0,
            PRIMARY KEY (inverter_id, log_date)
        );
    ''')

    cur.execute('''
        CREATE TABLE daily_weather_telemetry (
            log_date DATE NOT NULL,
            plant_id INT REFERENCES plants(plant_id),
            peak_poa_irradiance_w_m2 NUMERIC(8,2),
            total_solar_radiation_kwh_m2 NUMERIC(8,2),
            avg_ambient_temp_c NUMERIC(5,2),
            max_module_temp_c NUMERIC(5,2),
            soiling_ratio NUMERIC(5,2),
            PRIMARY KEY (plant_id, log_date)
        );
    ''')

    conn.commit()
    cur.close()
    conn.close()
    print("Schema created successfully.")

def seed_hierarchy():
    # --------------------------------------------------------------------------------
    # 2. HIERARCHY & METADATA SEEDING
    # --------------------------------------------------------------------------------
    plants = [
        {"name": "Solar Alpha", "acres": 120, "mwp": 30, "blocks": 4, "inv_per_block": 5}, # P1
        {"name": "Solar Beta", "acres": 200, "mwp": 50, "blocks": 5, "inv_per_block": 6},  # P2
        {"name": "Solar Gamma", "acres": 80, "mwp": 20, "blocks": 2, "inv_per_block": 6},   # P3
        {"name": "Solar Delta", "acres": 150, "mwp": 35, "blocks": 4, "inv_per_block": 6},  # P4
        {"name": "Solar Epsilon", "acres": 250, "mwp": 60, "blocks": 6, "inv_per_block": 5} # P5
    ]
    
    conn = get_connection()
    cur = conn.cursor()
    
    plant_records = []
    block_records = []
    inverter_records = []
    
    block_id_counter = 1
    
    for i, p in enumerate(plants, 1):
        plant_id = i
        plant_records.append((plant_id, p['name'], p['acres'], p['mwp']))
        
        for b in range(1, p['blocks'] + 1):
            block_name = f"Block_{chr(64 + b)}" # Block_A, Block_B...
            block_records.append((block_id_counter, plant_id, block_name))
            
            for inv in range(1, p['inv_per_block'] + 1):
                inv_id = f"P{plant_id}_{block_name}_INV_{inv:02d}"
                # Rough approximation: divide total MWp equally among inverters
                total_invs = p['blocks'] * p['inv_per_block']
                rated_dc = (p['mwp'] * 1000) / total_invs
                rated_ac = rated_dc * 0.95 # Typical AC to DC ratio
                inverter_records.append((inv_id, block_id_counter, plant_id, rated_dc, rated_ac))
                
            block_id_counter += 1
            
    print(f"Seeding 5 Plants, {block_id_counter-1} Blocks, {len(inverter_records)} Inverters...")
    execute_values(cur, "INSERT INTO plants (plant_id, plant_name, area_acres, capacity_mwp) VALUES %s", plant_records)
    execute_values(cur, "INSERT INTO blocks (block_id, plant_id, block_name) VALUES %s", block_records)
    execute_values(cur, "INSERT INTO inverters (inverter_id, block_id, plant_id, rated_dc_kw, rated_ac_kw) VALUES %s", inverter_records)
    
    conn.commit()
    cur.close()
    conn.close()
    
    return inverter_records

def ingest_single_day(log_date_str, inverter_data_list, weather_data_list):
    """
    4. DAILY INGESTION FUNCTION FOR FUTURE USE
    Executes UPSERT on inverter and weather telemetry.
    """
    conn = get_connection()
    cur = conn.cursor()
    
    # Upsert Weather Telemetry
    weather_query = """
        INSERT INTO daily_weather_telemetry 
        (log_date, plant_id, peak_poa_irradiance_w_m2, total_solar_radiation_kwh_m2, 
        avg_ambient_temp_c, max_module_temp_c, soiling_ratio)
        VALUES %s
        ON CONFLICT (plant_id, log_date) DO UPDATE SET
            peak_poa_irradiance_w_m2 = EXCLUDED.peak_poa_irradiance_w_m2,
            total_solar_radiation_kwh_m2 = EXCLUDED.total_solar_radiation_kwh_m2,
            avg_ambient_temp_c = EXCLUDED.avg_ambient_temp_c,
            max_module_temp_c = EXCLUDED.max_module_temp_c,
            soiling_ratio = EXCLUDED.soiling_ratio;
    """
    if weather_data_list:
        execute_values(cur, weather_query, weather_data_list)
        
    # Upsert Inverter Telemetry
    inverter_query = """
        INSERT INTO daily_inverter_telemetry 
        (log_date, inverter_id, plant_id, peak_dc_power_kw, peak_ac_power_kw, 
        total_daily_yield_kwh, avg_dc_voltage_v, avg_dc_current_a, inverter_status_code)
        VALUES %s
        ON CONFLICT (inverter_id, log_date) DO UPDATE SET
            peak_dc_power_kw = EXCLUDED.peak_dc_power_kw,
            peak_ac_power_kw = EXCLUDED.peak_ac_power_kw,
            total_daily_yield_kwh = EXCLUDED.total_daily_yield_kwh,
            avg_dc_voltage_v = EXCLUDED.avg_dc_voltage_v,
            avg_dc_current_a = EXCLUDED.avg_dc_current_a,
            inverter_status_code = EXCLUDED.inverter_status_code;
    """
    if inverter_data_list:
        execute_values(cur, inverter_query, inverter_data_list)
        
    conn.commit()
    cur.close()
    conn.close()

def generate_data(inverter_records):
    # --------------------------------------------------------------------------------
    # 3. 15-DAY DAILY DATA GENERATION LOGIC WITH INJECTED ANOMALIES
    # --------------------------------------------------------------------------------
    start_date = datetime(2026, 9, 12).date()
    days = 15
    
    # Pre-organize inverters by plant
    inverters_by_plant = {i: [] for i in range(1, 6)}
    for inv in inverter_records:
        inverters_by_plant[inv[2]].append(inv)
        
    for day_offset in range(days):
        current_date = start_date + timedelta(days=day_offset)
        day_num = day_offset + 1
        
        daily_weather = []
        daily_inverters = []
        
        for plant_id in range(1, 6):
            # Normal randomized baseline weather for the day
            peak_irrad = random.uniform(850, 1050)
            total_rad = peak_irrad * random.uniform(5.5, 7.5) / 1000.0 # Standard rough conversion to kWh/m2
            avg_amb_temp = random.uniform(28, 38)
            max_mod_temp = random.uniform(42, 62)
            soiling = random.uniform(0.95, 0.98)
            
            # --- WEATHER ANOMALIES ---
            # 3. Thermal Efficiency Loss (Day 12, Plant 5)
            if day_num == 12 and plant_id == 5:
                max_mod_temp = 64.0
            
            # 4. Dust Accumulation Loss (Days 8-15, Plant 3)
            if day_num >= 8 and plant_id == 3:
                soiling = 0.78
                
            daily_weather.append((
                current_date,
                plant_id,
                round(peak_irrad, 2),
                round(total_rad, 2),
                round(avg_amb_temp, 2),
                round(max_mod_temp, 2),
                round(soiling, 2)
            ))
            
            # Generate inverter telemetry for this plant
            plant_invs = inverters_by_plant[plant_id]
            
            for inv in plant_invs:
                inv_id, block_id, p_id, rated_dc, rated_ac = inv
                
                status_code = 0
                efficiency = 0.98
                
                # Base yield calculation: Yield (kWh) = rated_ac (kW) * total_rad (kWh/m2) / 1000W/m2 (STC) * PR
                # PR (Performance Ratio) = efficiency * soiling * temperature_derating
                temp_derating = 1 - (avg_amb_temp - 25) * 0.004
                pr = efficiency * soiling * temp_derating
                
                # Apply Anomaly 3 Impact: 15% drop due to heat degradation
                if day_num == 12 and plant_id == 5:
                    pr *= 0.85
                
                # (Anomaly 4 Dust Accumulation is already handled by soiling=0.78 above)
                
                yield_kwh = rated_ac * total_rad * pr
                
                peak_dc = rated_dc * (peak_irrad / 1000.0) * soiling
                peak_ac = peak_dc * efficiency
                
                avg_voltage = random.uniform(700, 750)
                avg_current = (peak_dc * 1000) / avg_voltage if avg_voltage > 0 else 0
                
                # --- SPECIFIC INVERTER ANOMALIES ---
                # 1. Complete Inverter Trip (Day 5, Plant 1, P1_Block_A_INV_02)
                if day_num == 5 and plant_id == 1 and inv_id == "P1_Block_A_INV_02":
                    peak_dc = 0.0
                    peak_ac = 0.0
                    yield_kwh = 0.0
                    avg_current = 0.0
                    status_code = 2
                    
                # 2. Localized Shading / String Loss (Day 10, Plant 2, P2_Block_B_INV_04)
                if day_num == 10 and plant_id == 2 and inv_id == "P2_Block_B_INV_04":
                    yield_kwh *= 0.35
                    peak_dc *= 0.35
                    peak_ac *= 0.35
                    avg_current *= 0.35
                    status_code = 3
                    
                daily_inverters.append((
                    current_date,
                    inv_id,
                    plant_id,
                    round(peak_dc, 2),
                    round(peak_ac, 2),
                    round(yield_kwh, 2),
                    round(avg_voltage, 2),
                    round(avg_current, 2),
                    status_code
                ))
                
        # Ingest the day using the reusable function
        ingest_single_day(current_date, daily_inverters, daily_weather)
        print(f"Ingested telemetry for {current_date}")

def run_tests():
    conn = get_connection()
    cur = conn.cursor()
    
    print("\n---------------------------------------------------------")
    print("RUNNING ROW COUNT CHECKS")
    print("---------------------------------------------------------")
    tables = ['plants', 'blocks', 'inverters', 'daily_weather_telemetry', 'daily_inverter_telemetry']
    for t in tables:
        cur.execute(f"SELECT COUNT(*) FROM {t}")
        print(f"Total rows in {t.ljust(25)}: {cur.fetchone()[0]}")
        
    print("\n---------------------------------------------------------")
    print("VERIFYING INJECTED ANOMALIES")
    print("---------------------------------------------------------")
    
    # 1. Complete Inverter Trip (Day 5 = 2026-09-16)
    cur.execute("""
        SELECT log_date, inverter_id, peak_dc_power_kw, total_daily_yield_kwh, inverter_status_code 
        FROM daily_inverter_telemetry 
        WHERE inverter_id = 'P1_Block_A_INV_02' AND log_date = '2026-09-16'
    """)
    print("1. Complete Inverter Trip (Day 5, Plant 1):")
    print("   ->", cur.fetchone())
    
    # 2. Localized Shading (Day 10 = 2026-09-21)
    cur.execute("""
        SELECT log_date, inverter_id, total_daily_yield_kwh, inverter_status_code 
        FROM daily_inverter_telemetry 
        WHERE inverter_id = 'P2_Block_B_INV_04' AND log_date = '2026-09-21'
    """)
    print("\n2. Localized Shading / String Loss (Day 10, Plant 2):")
    print("   ->", cur.fetchone())
    
    # 3. Thermal Efficiency Loss (Day 12 = 2026-09-23)
    cur.execute("""
        SELECT w.log_date, w.plant_id, w.max_module_temp_c, ROUND(AVG(i.total_daily_yield_kwh), 2) as avg_yield_kwh
        FROM daily_weather_telemetry w
        JOIN daily_inverter_telemetry i ON w.plant_id = i.plant_id AND w.log_date = i.log_date
        WHERE w.plant_id = 5 AND w.log_date IN ('2026-09-22', '2026-09-23')
        GROUP BY w.log_date, w.plant_id, w.max_module_temp_c
        ORDER BY w.log_date
    """)
    print("\n3. Thermal Efficiency Loss (Plant 5, Normal Day 11 vs Anomaly Day 12):")
    for row in cur.fetchall():
        print("   ->", row)
        
    # 4. Dust Accumulation Loss (Day 8 = 2026-09-19)
    cur.execute("""
        SELECT w.log_date, w.plant_id, w.soiling_ratio, ROUND(AVG(i.total_daily_yield_kwh), 2) as avg_yield_kwh
        FROM daily_weather_telemetry w
        JOIN daily_inverter_telemetry i ON w.plant_id = i.plant_id AND w.log_date = i.log_date
        WHERE w.plant_id = 3 AND w.log_date IN ('2026-09-18', '2026-09-19')
        GROUP BY w.log_date, w.plant_id, w.soiling_ratio
        ORDER BY w.log_date
    """)
    print("\n4. Dust Accumulation Loss (Plant 3, Normal Day 7 vs Anomaly Day 8):")
    for row in cur.fetchall():
        print("   ->", row)

    cur.close()
    conn.close()

if __name__ == "__main__":
    print("Starting generation process...\n")
    try:
        create_schema()
        inverters = seed_hierarchy()
        print("\nGenerating 15 Days of Data...")
        generate_data(inverters)
        run_tests()
        print("\nData generation complete!")
    except psycopg2.Error as e:
        print(f"\nDatabase error occurred: {e}")
    except Exception as e:
        print(f"\nAn error occurred: {e}")
