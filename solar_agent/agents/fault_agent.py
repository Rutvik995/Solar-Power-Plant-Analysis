"""
fault_agent.py – Fault & Diagnostic Specialist Agent.

Responsible for:
  - Detecting unexpected zero generation (irradiance present, yield = 0)
  - Detecting explicit status-code faults and computing streaks
  - Detecting peer underperformance via MAD z-score
  - Detecting statistical anomalies (rolling z-score on PR/voltage/current)

The agent MUST cross-reference all four tools and state which evidence supports
each conclusion. A single tool is NOT sufficient to confirm a fault.

Allowed tools: detect_zero_generation_tool, detect_status_faults_tool,
               detect_peer_underperformance_tool, detect_anomalies_tool
"""

from __future__ import annotations

from langchain_core.tools import tool

from solar_agent.agents.base_agent import BaseSpecialistAgent
from solar_agent.prompts.agent_prompts import FAULT_AGENT_PROMPT
from solar_agent.tools.fault_tools import (
    detect_anomalies,
    detect_peer_underperformance,
    detect_status_faults,
    detect_zero_generation,
)


@tool
def detect_zero_generation_tool(data_ref: str, irradiance_threshold_kwh_m2: float = 0.5) -> dict:
    """
    Identify inverter-days where yield is 0 kWh despite sufficient irradiance.
    Inverters in standby (exclude_from_yield_check) are skipped.

    Args:
        data_ref: DataStore key to the fetched telemetry DataFrame.
        irradiance_threshold_kwh_m2: Minimum irradiance to flag a zero-yield day (default 0.5).
    """
    return detect_zero_generation(data_ref, irradiance_threshold_kwh_m2=irradiance_threshold_kwh_m2)


@tool
def detect_status_faults_tool(data_ref: str) -> dict:
    """
    Identify inverter-days with fault/warning/offline status codes (>= 2).
    Computes per-inverter consecutive streaks.

    Args:
        data_ref: DataStore key to the fetched telemetry DataFrame.
    """
    return detect_status_faults(data_ref)


@tool
def detect_peer_underperformance_tool(data_ref: str) -> dict:
    """
    Flag inverters whose PR z-score vs block-median falls below the configured
    threshold for the configured minimum number of days.

    Args:
        data_ref: DataStore key to the fetched telemetry DataFrame.
    """
    return detect_peer_underperformance(data_ref)


@tool
def detect_anomalies_tool(data_ref: str, z_threshold: float = 3.0, window: int = 7) -> dict:
    """
    Statistical anomaly detection using rolling z-score on PR, DC voltage, and DC current.

    Args:
        data_ref:    DataStore key to the fetched telemetry DataFrame.
        z_threshold: Rolling z-score threshold for flagging (default 3.0).
        window:      Rolling window size in days (default 7).
    """
    return detect_anomalies(data_ref, z_threshold=z_threshold, window=window)


class FaultAgent(BaseSpecialistAgent):
    name = "fault"
    system_prompt = FAULT_AGENT_PROMPT
    tools = [
        detect_zero_generation_tool,
        detect_status_faults_tool,
        detect_peer_underperformance_tool,
        detect_anomalies_tool,
    ]
