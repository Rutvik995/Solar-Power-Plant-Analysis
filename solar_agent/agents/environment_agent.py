"""
environment_agent.py – Environmental Specialist Agent.

Responsible for:
  - Estimating soiling losses and detecting cleaning events
  - Analyzing weather correlations (temperature vs PR slope, irradiance vs yield)

Allowed tools: estimate_soiling_loss_tool, analyze_weather_correlation_tool
"""

from __future__ import annotations

from langchain_core.tools import tool

from solar_agent.agents.base_agent import BaseSpecialistAgent
from solar_agent.prompts.agent_prompts import ENVIRONMENT_AGENT_PROMPT
from solar_agent.tools.environment_tools import (
    analyze_weather_correlation,
    estimate_soiling_loss,
)


@tool
def estimate_soiling_loss_tool(data_ref: str) -> dict:
    """
    Estimate energy lost to soiling and detect cleaning events.

    Cleaning detection is done at plant-day level (one row per plant per date).
    Warns if loss exceeds 15% of total yield.

    Args:
        data_ref: DataStore key to the fetched telemetry DataFrame.
    """
    return estimate_soiling_loss(data_ref)


@tool
def analyze_weather_correlation_tool(data_ref: str) -> dict:
    """
    Compute statistical correlations:
      1. Module temperature vs PR (slope in %/°C — expected negative ~-0.3 to -0.4)
      2. Irradiance vs specific yield (R² — expected > 0.7 for a healthy site)

    Warns if sample size is below 14 data points.

    Args:
        data_ref: DataStore key to the fetched telemetry DataFrame.
    """
    return analyze_weather_correlation(data_ref)


class EnvironmentAgent(BaseSpecialistAgent):
    name = "environment"
    system_prompt = ENVIRONMENT_AGENT_PROMPT
    tools = [
        estimate_soiling_loss_tool,
        analyze_weather_correlation_tool,
    ]
