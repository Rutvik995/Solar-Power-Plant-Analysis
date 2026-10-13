"""
environment_agent.py – Environmental Specialist Agent.

Responsible for:
  - Estimating soiling losses and detecting cleaning events
  - Analyzing weather correlations (temperature vs PR slope, irradiance vs yield)

Allowed tools: estimate_soiling_loss_tool, analyze_weather_correlation_tool
"""

from __future__ import annotations

from typing import Any

from langchain_core.tools import tool
from pydantic import BaseModel, ConfigDict

from solar_agent.agents.base_agent import SingleCallAgent
from solar_agent.prompts.agent_prompts import ENVIRONMENT_AGENT_PROMPT
from solar_agent.tools.environment_tools import (
    analyze_weather_correlation,
    estimate_soiling_loss,
)


class EstimateSoilingLossSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_ref: str

@tool(args_schema=EstimateSoilingLossSchema)
def estimate_soiling_loss_tool(data_ref: str) -> dict:
    """
    Estimate energy lost to soiling and detect cleaning events.

    Cleaning detection is done at plant-day level (one row per plant per date).
    Warns if loss exceeds 15% of total yield.

    Args:
        data_ref: DataStore key to the fetched telemetry DataFrame.
    """
    return estimate_soiling_loss(data_ref)


class AnalyzeWeatherCorrelationSchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    data_ref: str = ""
    performance_ref: str | None = None
    weather_ref: str | None = None
    pr_ref: str | None = None

    def model_post_init(self, __context: Any) -> None:
        if not self.data_ref:
            self.data_ref = self.performance_ref or self.weather_ref or self.pr_ref or ""

@tool(args_schema=AnalyzeWeatherCorrelationSchema)
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


class EnvironmentAgent(SingleCallAgent):
    name = "environment"
    # system_prompt is unused in SingleCallAgent
    tools = [
        estimate_soiling_loss_tool,
        analyze_weather_correlation_tool,
    ]
