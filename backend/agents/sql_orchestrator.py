# ============================================================
# agents/sql_orchestrator.py — NL → SQL → results
# ============================================================

from langchain_openai import ChatOpenAI
from backend.config import (
    OPENROUTER_API_KEY, OPENROUTER_BASE_URL, ANALYST_MODEL, execute_query
)
from backend.schemas import AgentState

_llm = ChatOpenAI(
    api_key=OPENROUTER_API_KEY,
    base_url=OPENROUTER_BASE_URL,
    model=ANALYST_MODEL,
    temperature=0,
)

DB_SCHEMA_CONTEXT = """
PostgreSQL schema (schema: public):

TABLE plants       (plant_id SERIAL PK, plant_name VARCHAR, area_acres NUMERIC, capacity_mwp NUMERIC)
TABLE blocks       (block_id SERIAL PK, plant_id INT FK->plants, block_name VARCHAR)
TABLE inverters    (inverter_id VARCHAR PK, block_id INT FK->blocks, plant_id INT FK->plants,
                    rated_dc_kw NUMERIC, rated_ac_kw NUMERIC)
TABLE daily_inverter_telemetry (
    log_date DATE, inverter_id VARCHAR FK->inverters, plant_id INT FK->plants,
    peak_dc_power_kw NUMERIC, peak_ac_power_kw NUMERIC,
    total_daily_yield_kwh NUMERIC, avg_dc_voltage_v NUMERIC, avg_dc_current_a NUMERIC,
    inverter_status_code INT   -- 0=Normal, 1=Overheat, 2=Trip/Offline, 3=Shading/Fuse Fault
    PRIMARY KEY (inverter_id, log_date)
)
TABLE daily_weather_telemetry (
    log_date DATE, plant_id INT FK->plants,
    peak_poa_irradiance_w_m2 NUMERIC, total_solar_radiation_kwh_m2 NUMERIC,
    avg_ambient_temp_c NUMERIC, max_module_temp_c NUMERIC, soiling_ratio NUMERIC
    PRIMARY KEY (plant_id, log_date)
)

Date range in the database: 2026-09-12 to 2026-09-26 (15 days).
Plants: 1=Solar Alpha, 2=Solar Beta, 3=Solar Gamma, 4=Solar Delta, 5=Solar Epsilon
"""

SYSTEM_PROMPT = f"""\
You are a PostgreSQL expert for a solar power plant analytics platform.

{DB_SCHEMA_CONTEXT}

Rules:
- Generate a READ-ONLY SELECT query (no INSERT/UPDATE/DELETE/DROP).
- Use only tables and columns that exist in the schema above.
- Limit results to at most 200 rows unless the user asks for all.
- Return ONLY the raw SQL query — no markdown fences, no explanation, nothing else.
"""

def sql_orchestrator_node(state: AgentState) -> AgentState:
    """Translate the user query to SQL, execute it, return results."""
    response = _llm.invoke([
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": f"User question: {state['user_query']}"},
    ])
    sql = response.content.strip().strip(";").strip()

    try:
        rows = execute_query(sql)
    except Exception as e:
        rows = [{"error": str(e), "attempted_sql": sql}]

    return {
        **state,
        "sql_query": sql,
        "query_results": rows,
    }
