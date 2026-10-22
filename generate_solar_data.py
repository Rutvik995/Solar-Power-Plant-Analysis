import os
import random
import argparse
from datetime import datetime, timedelta
from urllib.parse import urlparse

try:
    import psycopg
    USE_PSYCOPG3 = True
except ImportError:
    import psycopg2 as psycopg
    from psycopg2.extras import execute_values
    USE_PSYCOPG3 = False

# --------------------------------------------------------------------------------
# DATABASE CREDENTIALS (LOAD FROM ENV OR FALLBACK)
# --------------------------------------------------------------------------------
DB_HOST = "localhost"
DB_NAME = "solardata"
DB_USER = "postgres"
DB_PASS = "123456789"
DB_PORT = "5432"

db_url = os.environ.get("DATABASE_URL")
if db_url:
    clean_url = db_url.replace("postgresql+psycopg://", "postgresql://").replace("postgresql+asyncpg://", "postgresql://")
    parsed = urlparse(clean_url)
    if parsed.hostname:
        DB_HOST = parsed.hostname
    if parsed.port:
        DB_PORT = str(parsed.port)
    if parsed.username:
        DB_USER = parsed.username
    if parsed.password:
        DB_PASS = parsed.password
    if parsed.path:
        DB_NAME = parsed.path.lstrip("/")

# Connect to database
def get_connection():
    try:
        return psycopg.connect(
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
                total_invs = p['blocks'] * p['inv_per_block']
                rated_dc = (p['mwp'] * 1000) / total_invs
                rated_ac = rated_dc * 0.95 # Typical AC to DC ratio
                inverter_records.append((inv_id, block_id_counter, plant_id, rated_dc, rated_ac))
                
            block_id_counter += 1
            
    print(f"Seeding 5 Plants, {block_id_counter-1} Blocks, {len(inverter_records)} Inverters...")
    if USE_PSYCOPG3:
        cur.executemany("INSERT INTO plants (plant_id, plant_name, area_acres, capacity_mwp) VALUES (%s, %s, %s, %s)", plant_records)
        cur.executemany("INSERT INTO blocks (block_id, plant_id, block_name) VALUES (%s, %s, %s)", block_records)
        cur.executemany("INSERT INTO inverters (inverter_id, block_id, plant_id, rated_dc_kw, rated_ac_kw) VALUES (%s, %s, %s, %s, %s)", inverter_records)
    else:
        execute_values(cur, "INSERT INTO plants (plant_id, plant_name, area_acres, capacity_mwp) VALUES %s", plant_records)
        execute_values(cur, "INSERT INTO blocks (block_id, plant_id, block_name) VALUES %s", block_records)
        execute_values(cur, "INSERT INTO inverters (inverter_id, block_id, plant_id, rated_dc_kw, rated_ac_kw) VALUES %s", inverter_records)
    
    conn.commit()
    cur.close()
    conn.close()
    
    return inverter_records

def get_or_seed_inverters():
    """
    Fetch existing inverters from the database.
    If none exist, seeds the hierarchy and returns them.
    """
    try:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute("SELECT inverter_id, block_id, plant_id, rated_dc_kw, rated_ac_kw FROM inverters ORDER BY plant_id, inverter_id")
        rows = cur.fetchall()
        cur.close()
        conn.close()
        if rows and len(rows) > 0:
            return rows
    except Exception as e:
        print(f"Notice: Table check failed ({e}). Seeding schema and hierarchy...")
        create_schema()
    return seed_hierarchy()

def ingest_single_day(log_date, inverter_data_list, weather_data_list, overwrite=False):
    """
    4. DAILY INGESTION FUNCTION
    Executes INSERT / UPSERT on inverter and weather telemetry.
    If overwrite is False, uses ON CONFLICT DO NOTHING so existing data is preserved.
    """
    conn = get_connection()
    cur = conn.cursor()
    
    if overwrite:
        weather_conflict = """
            ON CONFLICT (plant_id, log_date) DO UPDATE SET
                peak_poa_irradiance_w_m2 = EXCLUDED.peak_poa_irradiance_w_m2,
                total_solar_radiation_kwh_m2 = EXCLUDED.total_solar_radiation_kwh_m2,
                avg_ambient_temp_c = EXCLUDED.avg_ambient_temp_c,
                max_module_temp_c = EXCLUDED.max_module_temp_c,
                soiling_ratio = EXCLUDED.soiling_ratio;
        """
        inverter_conflict = """
            ON CONFLICT (inverter_id, log_date) DO UPDATE SET
                peak_dc_power_kw = EXCLUDED.peak_dc_power_kw,
                peak_ac_power_kw = EXCLUDED.peak_ac_power_kw,
                total_daily_yield_kwh = EXCLUDED.total_daily_yield_kwh,
                avg_dc_voltage_v = EXCLUDED.avg_dc_voltage_v,
                avg_dc_current_a = EXCLUDED.avg_dc_current_a,
                inverter_status_code = EXCLUDED.inverter_status_code;
        """
    else:
        weather_conflict = "ON CONFLICT (plant_id, log_date) DO NOTHING;"
        inverter_conflict = "ON CONFLICT (inverter_id, log_date) DO NOTHING;"

    weather_query = f"""
        INSERT INTO daily_weather_telemetry 
        (log_date, plant_id, peak_poa_irradiance_w_m2, total_solar_radiation_kwh_m2, 
        avg_ambient_temp_c, max_module_temp_c, soiling_ratio)
        VALUES (%s, %s, %s, %s, %s, %s, %s)
        {weather_conflict}
    """

    inverter_query = f"""
        INSERT INTO daily_inverter_telemetry 
        (log_date, inverter_id, plant_id, peak_dc_power_kw, peak_ac_power_kw, 
        total_daily_yield_kwh, avg_dc_voltage_v, avg_dc_current_a, inverter_status_code)
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        {inverter_conflict}
    """

    if weather_data_list:
        if USE_PSYCOPG3:
            cur.executemany(weather_query, weather_data_list)
        else:
            execute_values(cur, weather_query.replace("(%s, %s, %s, %s, %s, %s, %s)", "%s"), weather_data_list)
        
    if inverter_data_list:
        if USE_PSYCOPG3:
            cur.executemany(inverter_query, inverter_data_list)
        else:
            execute_values(cur, inverter_query.replace("(%s, %s, %s, %s, %s, %s, %s, %s, %s)", "%s"), inverter_data_list)
        
    conn.commit()
    cur.close()
    conn.close()

def generate_data(inverter_records, start_date=None, end_date=None, overwrite=False):
    # --------------------------------------------------------------------------------
    # 3. DAILY DATA GENERATION LOGIC WITH INJECTED ANOMALIES
    # Supports July to October 2026 (or custom date range)
    # --------------------------------------------------------------------------------
    if start_date is None:
        start_date = datetime(2026, 7, 1).date()
    if end_date is None:
        end_date = datetime(2026, 10, 21).date()
        
    days = (end_date - start_date).days + 1
    
    # Pre-organize inverters by plant
    inverters_by_plant = {i: [] for i in range(1, 6)}
    for inv in inverter_records:
        inverters_by_plant[inv[2]].append(inv)
        
    for day_offset in range(days):
        current_date = start_date + timedelta(days=day_offset)
        
        daily_weather = []
        daily_inverters = []
        
        for plant_id in range(1, 6):
            # Normal randomized baseline weather for the day
            # Seasonal variation: July slightly higher humidity/clouds, October cooler
            if current_date.month == 7:
                # July monsoon/summer: occasional overcast day
                if current_date.day in (11, 12):
                    peak_irrad = random.uniform(480, 680)
                    total_rad = peak_irrad * random.uniform(3.5, 4.5) / 1000.0
                    avg_amb_temp = random.uniform(26, 30)
                    max_mod_temp = random.uniform(34, 42)
                    soiling = random.uniform(0.98, 0.99)
                else:
                    peak_irrad = random.uniform(820, 1020)
                    total_rad = peak_irrad * random.uniform(5.4, 7.2) / 1000.0
                    avg_amb_temp = random.uniform(28, 37)
                    max_mod_temp = random.uniform(42, 60)
                    soiling = random.uniform(0.96, 0.99)
            elif current_date.month == 10:
                # October autumn: crisp sunny days, cooler ambient
                peak_irrad = random.uniform(840, 1010)
                total_rad = peak_irrad * random.uniform(5.2, 6.8) / 1000.0
                avg_amb_temp = random.uniform(24, 32)
                max_mod_temp = random.uniform(38, 52)
                soiling = random.uniform(0.94, 0.97)
            else:
                peak_irrad = random.uniform(850, 1050)
                total_rad = peak_irrad * random.uniform(5.5, 7.5) / 1000.0
                avg_amb_temp = random.uniform(28, 38)
                max_mod_temp = random.uniform(42, 62)
                soiling = random.uniform(0.95, 0.98)
            
            # --- WEATHER ANOMALIES (preserve benchmark anomalies) ---
            # 3. Thermal Efficiency Loss (2026-09-23, Plant 5)
            if current_date == datetime(2026, 9, 23).date() and plant_id == 5:
                max_mod_temp = 64.0
            
            # 4. Dust Accumulation Loss (2026-09-19 to 2026-09-26, Plant 3)
            if current_date >= datetime(2026, 9, 19).date() and current_date <= datetime(2026, 9, 26).date() and plant_id == 3:
                soiling = 0.78
                
            # 5. August Dust Accumulation & Cleaning Event (Plant 4)
            if current_date.month == 8 and plant_id == 4:
                if 5 <= current_date.day <= 14:
                    soiling = 0.95 - (current_date.day - 5) * 0.01
                elif current_date.day == 15:
                    soiling = 0.98
                
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
                rated_dc = float(rated_dc)
                rated_ac = float(rated_ac)
                
                status_code = 0
                efficiency = 0.98
                
                # Base yield calculation: Yield (kWh) = rated_ac (kW) * total_rad (kWh/m2) / 1000W/m2 (STC) * PR
                # PR (Performance Ratio) = efficiency * soiling * temperature_derating
                temp_derating = 1 - (avg_amb_temp - 25) * 0.004
                pr = efficiency * soiling * temp_derating
                
                # Apply Anomaly 3 Impact: 15% drop due to heat degradation
                if current_date == datetime(2026, 9, 23).date() and plant_id == 5:
                    pr *= 0.85
                
                yield_kwh = rated_ac * total_rad * pr
                
                peak_dc = rated_dc * (peak_irrad / 1000.0) * soiling
                peak_ac = peak_dc * efficiency
                
                avg_voltage = random.uniform(700, 750)
                avg_current = (peak_dc * 1000) / avg_voltage if avg_voltage > 0 else 0
                
                # --- SPECIFIC INVERTER ANOMALIES ---
                # 1. Complete Inverter Trip (2026-09-16, Plant 1, P1_Block_A_INV_02)
                if current_date == datetime(2026, 9, 16).date() and plant_id == 1 and inv_id == "P1_Block_A_INV_02":
                    peak_dc = 0.0
                    peak_ac = 0.0
                    yield_kwh = 0.0
                    avg_current = 0.0
                    status_code = 2
                    
                # 2. Localized Shading / String Loss (2026-09-21, Plant 2, P2_Block_B_INV_04)
                if current_date == datetime(2026, 9, 21).date() and plant_id == 2 and inv_id == "P2_Block_B_INV_04":
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
        ingest_single_day(current_date, daily_inverters, daily_weather, overwrite=overwrite)
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
        
    cur.execute("SELECT MIN(log_date), MAX(log_date) FROM daily_weather_telemetry")
    row = cur.fetchone()
    print(f"Weather date range: {row[0]} to {row[1]}")

    cur.execute("SELECT MIN(log_date), MAX(log_date) FROM daily_inverter_telemetry")
    row = cur.fetchone()
    print(f"Inverter date range: {row[0]} to {row[1]}")

    print("\n---------------------------------------------------------")
    print("VERIFYING INJECTED ANOMALIES")
    print("---------------------------------------------------------")
    
    # 1. Complete Inverter Trip (Day 5 = 2026-09-16)
    cur.execute("""
        SELECT log_date, inverter_id, peak_dc_power_kw, total_daily_yield_kwh, inverter_status_code 
        FROM daily_inverter_telemetry 
        WHERE inverter_id = 'P1_Block_A_INV_02' AND log_date = '2026-09-16'
    """)
    print("1. Complete Inverter Trip (Plant 1, P1_Block_A_INV_02, 2026-09-16):")
    print("   ->", cur.fetchone())
    
    # 2. Localized Shading (2026-09-21)
    cur.execute("""
        SELECT log_date, inverter_id, total_daily_yield_kwh, inverter_status_code 
        FROM daily_inverter_telemetry 
        WHERE inverter_id = 'P2_Block_B_INV_04' AND log_date = '2026-09-21'
    """)
    print("\n2. Localized Shading / String Loss (Plant 2, P2_Block_B_INV_04, 2026-09-21):")
    print("   ->", cur.fetchone())
    
    cur.close()
    conn.close()

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Generate and insert solar power plant telemetry data")
    parser.add_argument("--start-date", default="2026-07-01", help="Start date (YYYY-MM-DD), default: 2026-07-01")
    parser.add_argument("--end-date", default="2026-10-21", help="End date (YYYY-MM-DD), default: 2026-10-21")
    parser.add_argument("--reset-schema", action="store_true", help="Drop and recreate database schema before generating")
    parser.add_argument("--overwrite", action="store_true", help="Overwrite existing records on conflict")
    args = parser.parse_args()

    s_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    e_date = datetime.strptime(args.end_date, "%Y-%m-%d").date()

    print(f"Starting solar telemetry generation from {s_date} to {e_date}...")
    try:
        if args.reset_schema:
            create_schema()
            inverters = seed_hierarchy()
        else:
            inverters = get_or_seed_inverters()

        print(f"\nGenerating Data from {s_date} to {e_date} ({(e_date - s_date).days + 1} Days)...")
        generate_data(inverters, start_date=s_date, end_date=e_date, overwrite=args.overwrite)
        run_tests()
        print("\nData generation complete!")
    except Exception as e:
        print(f"\nAn error occurred: {e}")
        raise
