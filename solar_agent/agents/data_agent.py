"""
data_agent.py – Data Specialist Agent.

Responsible for:
  - Entity resolution (plant/block/inverter names → IDs)
  - Date range resolution (natural language → concrete dates)
  - Fetching inverter and weather telemetry from the DB

Allowed tools: resolve_entities, resolve_date_range, fetch_inverter_daily_tool,
               fetch_weather_daily_tool, get_schema_info
"""

from __future__ import annotations

from solar_agent.agents.base_agent import BaseSpecialistAgent
from solar_agent.prompts.agent_prompts import DATA_AGENT_PROMPT
from solar_agent.tools.data_tools import (
    fetch_inverter_daily_tool,
    fetch_weather_daily_tool,
    get_schema_info,
    resolve_date_range,
    resolve_entities,
)


class DataAgent(BaseSpecialistAgent):
    name = "data"
    system_prompt = DATA_AGENT_PROMPT
    tools = [
        get_schema_info,
        resolve_entities,
        resolve_date_range,
        fetch_inverter_daily_tool,
        fetch_weather_daily_tool,
    ]
