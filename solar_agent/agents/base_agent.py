"""
base_agent.py – Shared base classes for all specialist LangGraph tool-calling agents.

Two agent modes:
  ┌────────────────────────────────────────────────────────────────────────┐
  │  BaseSpecialistAgent (ReAct loop, max 5 iterations)                   │
  │    • Used by FaultAgent (must cross-reference multiple tools)          │
  │                                                                        │
  │  SingleCallAgent (1 LLM call → tool selection → direct execution)     │
  │    • Used by PerformanceAgent and EnvironmentAgent                     │
  │    • Pattern: LLM picks the tool + args → we run it → done            │
  │    • Never more than 1 LLM call per task                              │
  └────────────────────────────────────────────────────────────────────────┘
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import BaseTool

from solar_agent.state import TaskResult, TaskStatus, BudgetExceeded, Cancelled
from solar_agent.graph.llm_recorder import CacheMissError

logger = logging.getLogger(__name__)

MAX_ITERATIONS = 5  # hard cap on ReAct rounds for FaultAgent


# ---------------------------------------------------------------------------
# 1. ReAct loop base (FaultAgent)
# ---------------------------------------------------------------------------

def _collect_flagged_items_for_tool(
    tool_name: str,
    tool_output: dict,
    base_df: Any,
    flagged_items: list[dict],
    named_inverter_ids: list[str] | None = None,
    warnings: list[str] | None = None,
) -> None:
    if not isinstance(tool_output, dict) or not tool_output.get("data_ref"):
        return
    try:
        from solar_agent.graph.data_store import DataStore
        import pandas as pd
        import yaml
        from solar_agent.config.settings import STATUS_CODES_PATH
        from solar_agent.tools import metrics as m

        sc_dict = {}
        try:
            with open(STATUS_CODES_PATH, "r", encoding="utf-8") as sc_f:
                sc_dict = yaml.safe_load(sc_f).get("status_codes", {})
        except Exception:
            pass

        if base_df is not None and not base_df.empty and "performance_ratio" not in base_df.columns:
            base_df = m.compute_performance_ratio(base_df)

        f_df = DataStore.get(tool_output["data_ref"])
        if f_df is None or f_df.empty:
            return

        if "anomalies" in tool_name:
            flag_cols = [c for c in f_df.columns if c.endswith("_is_anomaly")]
            if flag_cols:
                f_df = f_df[f_df[flag_cols].any(axis=1)]

        for _, row in f_df.head(20).iterrows():
            inv_id = str(row.get("inverter_id", ""))
            if named_inverter_ids and inv_id not in named_inverter_ids:
                continue

            blk = str(row.get("block_name", row.get("block_id", "")))
            dt_val = str(row.get("log_date", ""))
            sc_val = int(row.get("status_code", 0)) if pd.notna(row.get("status_code")) else 0
            pr_val = round(float(row.get("performance_ratio", 0.0)), 4) if "performance_ratio" in row and pd.notna(row.get("performance_ratio")) else 0.0

            sc_info = sc_dict.get(sc_val, {})
            sc_label = sc_info.get("label", str(sc_val))
            sc_desc = sc_info.get("description", "").strip().replace("\n", " ")
            sc_meaning = f"{sc_label}: {sc_desc}" if sc_desc else sc_label

            peer_pr = None
            peer_irr = None
            peer_count = 0
            if base_df is not None and not base_df.empty:
                b_match = (base_df["block_name"] == blk) if "block_name" in base_df.columns else (base_df["block_id"] == blk)
                d_match = base_df["log_date"].astype(str) == dt_val
                i_match = base_df["inverter_id"] != inv_id
                peers = base_df[b_match & d_match & i_match]
                peer_count = len(peers)
                if peer_count > 0:
                    if "performance_ratio" in peers.columns:
                        p_mean = peers["performance_ratio"].mean()
                        peer_pr = round(float(p_mean), 4) if pd.notna(p_mean) else None
                    if "total_solar_radiation_kwh_m2" in peers.columns:
                        i_mean = peers["total_solar_radiation_kwh_m2"].mean()
                        peer_irr = round(float(i_mean), 2) if pd.notna(i_mean) else None

                # Consistency check: peer irradiance 0 while the plant's irradiance that day is above 0 produces a warning and no claim
                day_df = base_df[base_df["log_date"].astype(str) == dt_val]
                if "total_solar_radiation_kwh_m2" in day_df.columns:
                    day_irr = day_df["total_solar_radiation_kwh_m2"].dropna()
                    plant_irr = float(day_irr.max()) if not day_irr.empty else 0.0
                    if peer_irr is not None and peer_irr == 0 and plant_irr > 0:
                        msg = f"Inconsistency detected on {dt_val}: peer irradiance is 0.0 while plant irradiance is {plant_irr:.2f} kWh/m²."
                        if warnings is not None and msg not in warnings:
                            warnings.append(msg)
                        peer_irr = None

            item = {
                "inverter_id": inv_id,
                "block": blk,
                "date": dt_val,
                "status_code": sc_val,
                "status_meaning": sc_meaning,
                "pr": pr_val,
                "peer_pr": peer_pr,
                "peer_irradiance": peer_irr,
                "peer_count": peer_count,
                "flagging_tool": tool_name,
            }
            if item not in flagged_items and len(flagged_items) < 10:
                flagged_items.append(item)
    except Exception as e:
        logger.debug("Could not collect flagged items: %s", e)


class BaseSpecialistAgent:
    """
    ReAct loop agent. Each run() may use up to MAX_ITERATIONS LLM calls.
    Subclasses define: name, system_prompt, tools.
    """

    name: str = "base"
    system_prompt: str = ""
    tools: list[BaseTool] = []

    def __init__(self, llm):
        if not self.tools:
            raise ValueError(f"Agent '{self.name}' must define at least one tool.")
        self._raw_llm = llm
        self._llm = llm.bind_tools(self.tools)
        self._tool_map: dict[str, BaseTool] = {t.name: t for t in self.tools}

    def run(self, task_id: str, description: str, inputs: dict[str, Any], run_id: str = "__default__") -> TaskResult:
        t_start = time.monotonic()
        tool_calls_made: list[str] = []

        human_text = self._format_human_message(description, inputs)
        messages: list = [
            SystemMessage(content=self.system_prompt),
            HumanMessage(content=human_text),
        ]

        try:
            flagged_items: list[dict] = []
            anomaly_sentence: str = "Anomalies found: none."
            extra_warnings: list[str] = []
            for _iteration in range(MAX_ITERATIONS):
                from solar_agent.state import get_cancel_token
                token = get_cancel_token(run_id)
                if token:
                    token.check()

                # On the last iteration call the LLM with tools unbound (never use tool_choice="none")
                if _iteration == MAX_ITERATIONS - 1:
                    active_llm = self._raw_llm
                    messages.append(HumanMessage(content="You have reached the maximum tool iterations. Summarize your findings and conclusions now in plain text without calling any tools."))
                else:
                    active_llm = self._llm

                try:
                    response: AIMessage = active_llm.invoke(messages, run_id=run_id)
                except (BudgetExceeded, Cancelled):
                    raise
                except Exception as exc:
                    err_str = str(exc)
                    if "tool_use_failed" in err_str or "Tool choice is none" in err_str:
                        logger.warning("Caught tool_use_failed (400) on agent '%s'. Retrying once with tools unbound...", self.name)
                        try:
                            fallback_msgs = list(messages) + [
                                HumanMessage(content="Tool call failed. Provide your final answer and summary now in plain text without calling any tools.")
                            ]
                            response = self._raw_llm.invoke(fallback_msgs, run_id=run_id)
                        except BudgetExceeded:
                            raise
                        except Exception as retry_exc:
                            return TaskResult(
                                task_id=task_id,
                                status=TaskStatus.FAILED,
                                error=f"Tool use failed (400) and unbound fallback failed: {retry_exc}",
                                tool_calls_made=tool_calls_made,
                                latency_ms=(time.monotonic() - t_start) * 1000,
                            )
                    elif isinstance(exc, CacheMissError):
                        logger.info("Agent '%s' cache miss in replay mode; breaking to execute tools directly", self.name)
                        break
                    else:
                        raise

                messages.append(response)

                if not getattr(response, "tool_calls", None):
                    break

                for tc in response.tool_calls:
                    tool_name = tc["name"]
                    tool_args = dict(tc["args"]) if isinstance(tc["args"], dict) else tc["args"]
                    from solar_agent.graph.data_store import DataStore
                    if isinstance(tool_args, dict) and inputs.get("data_ref"):
                        if (
                            not tool_args.get("data_ref")
                            or tool_args.get("data_ref") in ("<data_ref>", "df_ref")
                            or DataStore.get(tool_args.get("data_ref")) is None
                        ):
                            tool_args["data_ref"] = inputs["data_ref"]
                    tool_calls_made.append(tool_name)
                    logger.debug(
                        "Agent '%s' calling tool '%s' with %s", self.name, tool_name, tool_args
                    )
                    tool_output = self._call_tool(tool_name, tool_args)
                    messages.append(
                        ToolMessage(
                            content=json.dumps(tool_output, default=str),
                            tool_call_id=tc["id"],
                        )
                    )

                    if self.name == "fault" and isinstance(tool_output, dict):
                        if "anomalies" in tool_name:
                            tot_anom = (tool_output.get("metrics") or {}).get("total_anomalies", 0)
                            if tot_anom > 0:
                                anomaly_sentence = f"Anomalies found: {tot_anom} statistical anomaly datapoints detected."
                            else:
                                anomaly_sentence = "Anomalies found: none."

                        if tool_output.get("data_ref"):
                            named_invs = inputs.get("named_inverter_ids") or []
                            base_ref = inputs.get("data_ref")
                            from solar_agent.graph.data_store import DataStore
                            base_df = DataStore.get(base_ref) if base_ref else None
                            _collect_flagged_items_for_tool(
                                tool_name=tool_name,
                                tool_output=tool_output,
                                base_df=base_df,
                                flagged_items=flagged_items,
                                named_inverter_ids=named_invs,
                                warnings=extra_warnings,
                            )

            # Ensure fault agent always runs all four fault tools
            if self.name == "fault":
                required_fault_tools = [
                    "detect_zero_generation_tool",
                    "detect_status_faults_tool",
                    "detect_peer_underperformance_tool",
                    "detect_anomalies_tool",
                ]
                missing_tools = [t for t in required_fault_tools if t not in tool_calls_made]
                if missing_tools:
                    logger.info("Fault agent missing required tools %s; invoking them now.", missing_tools)
                    base_ref = inputs.get("data_ref")
                    named_invs = inputs.get("named_inverter_ids") or []
                    from solar_agent.graph.data_store import DataStore
                    base_df = DataStore.get(base_ref) if base_ref else None
                    for m_tool in missing_tools:
                        tool_calls_made.append(m_tool)
                        m_args = {"data_ref": base_ref} if base_ref else {}
                        if named_invs:
                            m_args["inverter_ids"] = named_invs
                        tool_output = self._call_tool(m_tool, m_args)
                        messages.append(
                            ToolMessage(
                                content=json.dumps(tool_output, default=str),
                                tool_call_id=f"auto_{m_tool}",
                            )
                        )
                        if isinstance(tool_output, dict):
                            if "anomalies" in m_tool:
                                tot_anom = (tool_output.get("metrics") or {}).get("total_anomalies", 0)
                                if tot_anom > 0:
                                    anomaly_sentence = f"Anomalies found: {tot_anom} statistical anomaly datapoints detected."
                                else:
                                    anomaly_sentence = "Anomalies found: none."
                            if tool_output.get("data_ref"):
                                _collect_flagged_items_for_tool(
                                    tool_name=m_tool,
                                    tool_output=tool_output,
                                    base_df=base_df,
                                    flagged_items=flagged_items,
                                    named_inverter_ids=named_invs,
                                    warnings=extra_warnings,
                                )

            result = self._parse_result(
                task_id,
                messages,
                tool_calls_made,
                flagged_items=flagged_items,
                anomaly_sentence=anomaly_sentence,
                extra_warnings=extra_warnings,
            )

        except (BudgetExceeded, Cancelled):
            raise
        except Exception as exc:
            logger.exception("Agent '%s' failed on task '%s': %s", self.name, task_id, exc)
            result = TaskResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                error=str(exc),
                tool_calls_made=tool_calls_made,
            )

        result.latency_ms = (time.monotonic() - t_start) * 1000
        return result

    def _format_human_message(self, description: str, inputs: dict[str, Any]) -> str:
        parts = [f"Task: {description}"]
        if inputs:
            parts.append("\nInputs:")
            for k, v in inputs.items():
                parts.append(f"  {k}: {v}")
        parts.append(
            "\nComplete this task using only your tools. "
            "Return a summary of findings and include any data_ref values from tool outputs."
        )
        return "\n".join(parts)

    def _call_tool(self, name: str, args: dict) -> Any:
        tool = self._tool_map.get(name)
        if tool is None:
            raise ValueError(f"Tool '{name}' is not available to agent '{self.name}'.")
        return tool.invoke(args)

    def _parse_result(
        self,
        task_id: str,
        messages: list,
        tool_calls_made: list[str],
        flagged_items: list[dict] | None = None,
        anomaly_sentence: str | None = None,
        extra_warnings: list[str] | None = None,
    ) -> TaskResult:
        final_ai_msg = next(
            (m for m in reversed(messages) if isinstance(m, AIMessage)), None
        )
        summary = (
            final_ai_msg.content
            if final_ai_msg and isinstance(final_ai_msg.content, str)
            else ""
        )

        metrics: dict[str, Any] = {}
        data_ref: str | None = None
        warnings: list[str] = []

        if extra_warnings:
            for ew in extra_warnings:
                if ew not in warnings:
                    warnings.append(ew)

        for msg in messages:
            if isinstance(msg, ToolMessage):
                try:
                    payload = json.loads(msg.content)
                    if isinstance(payload, dict):
                        if "data_ref" in payload and payload["data_ref"]:
                            data_ref = payload["data_ref"]
                        if "metrics" in payload and isinstance(payload["metrics"], dict):
                            metrics.update(payload["metrics"])
                        if "warnings" in payload and isinstance(payload["warnings"], list):
                            warnings.extend(payload["warnings"])
                except (json.JSONDecodeError, TypeError):
                    pass

        if flagged_items:
            metrics["flagged_items"] = flagged_items[:10]
        if self.name == "fault":
            metrics["anomaly_sentence"] = anomaly_sentence or "Anomalies found: none."

        from solar_agent.graph.synthesizer import clean_task_summary
        return TaskResult(
            task_id=task_id,
            status=TaskStatus.DONE,
            summary=clean_task_summary(summary),
            data_ref=data_ref,
            metrics=metrics if metrics else None,
            warnings=warnings,
            tool_calls_made=tool_calls_made,
        )


# ---------------------------------------------------------------------------
# 2. Single-call structured agent (PerformanceAgent, EnvironmentAgent)
# ---------------------------------------------------------------------------

_TOOL_SELECT_SYSTEM = """\
You are a specialist tool selector. Given the task description and inputs,
respond with a JSON object that selects EXACTLY ONE tool to call and its arguments.

Response format (JSON only, no markdown fences):
{{
  "tool": "<tool_name>",
  "args": {{<argument key-value pairs>}}
}}

Available tools:
{tool_descriptions}

Rules:
1. Pick exactly one tool — the most appropriate one for the task.
2. Fill args from the task inputs. Use the data_ref from inputs if available.
3. Never fabricate data or compute answers yourself.
4. Respond with valid JSON only.
"""


class SingleCallAgent:
    """
    One-LLM-call agent for deterministic, single-tool tasks.

    Flow per run():
      1. One LLM call → structured JSON {tool, args}
      2. Parse the JSON, call the tool directly (no further LLM calls)
      3. Build TaskResult from the tool output deterministically

    Maximum LLM calls per task: 1
    """

    name: str = "single_call"
    tools: list[BaseTool] = []

    def __init__(self, llm):
        if not self.tools:
            raise ValueError(f"Agent '{self.name}' must define at least one tool.")
        self._tool_map: dict[str, BaseTool] = {t.name: t for t in self.tools}
        # Build tool descriptions for the system prompt
        descs = []
        for t in self.tools:
            arg_names = list(t.args.keys()) if hasattr(t, "args") and t.args else []
            args_str = f"({', '.join(arg_names)})" if arg_names else ""
            summary = (t.description or "").split(chr(10))[0]
            descs.append(f"  - {t.name}{args_str}: {summary}")
        tool_descs = "\n".join(descs)
        system_content = _TOOL_SELECT_SYSTEM.format(tool_descriptions=tool_descs)
        self._system_msg = SystemMessage(content=system_content)
        # Bind WITHOUT tools so the LLM returns plain JSON text (not tool_calls)
        self._llm = llm

    def run(self, task_id: str, description: str, inputs: dict[str, Any], run_id: str = "__default__") -> TaskResult:
        t_start = time.monotonic()
        tool_calls_made: list[str] = []

        from solar_agent.state import get_cancel_token
        token = get_cancel_token(run_id)
        if token:
            token.check()

        # Build human message
        human_parts = [f"Task: {description}", "\nInputs:"]
        for k, v in inputs.items():
            human_parts.append(f"  {k}: {v}")
        human_text = "\n".join(human_parts)

        try:
            selection = None
            try:
                # --- LLM call: ask it to pick a tool ---
                response = self._llm.invoke(
                    [self._system_msg, HumanMessage(content=human_text)],
                    run_id=run_id,
                )
            except CacheMissError:
                logger.info("SingleCallAgent[%s] CacheMiss in replay; inferring tool from description", task_id)
                desc_l = description.lower()
                tool_names = list(self._tool_map.keys())
                if any(w in desc_l for w in ["soiling", "dust", "dirt", "wash"]):
                    chosen_tool = "estimate_soiling_loss_tool"
                elif any(w in desc_l for w in ["weather", "correlation", "irradiance vs"]):
                    chosen_tool = "analyze_weather_correlation_tool"
                elif any(w in desc_l for w in ["rank", "best", "worst", "highest", "lowest"]) and "rank_entities_tool" in tool_names:
                    chosen_tool = "rank_entities_tool"
                elif "compare" in desc_l and "compare_periods_tool" in tool_names:
                    chosen_tool = "compare_periods_tool"
                elif any(w in desc_l for w in ["trend", "rolling", "moving average"]) and "trend_analysis_tool" in tool_names:
                    chosen_tool = "trend_analysis_tool"
                elif "compute_kpis_tool" in tool_names:
                    chosen_tool = "compute_kpis_tool"
                else:
                    chosen_tool = tool_names[0]

                if chosen_tool in ("estimate_soiling_loss_tool", "analyze_weather_correlation_tool"):
                    t_args = {"data_ref": inputs.get("data_ref")}
                elif chosen_tool == "rank_entities_tool":
                    metric = "performance_ratio"
                    if "yield" in desc_l:
                        metric = "total_daily_yield_kwh"
                    t_args = {"data_ref": inputs.get("data_ref"), "metric": metric}
                elif chosen_tool == "trend_analysis_tool":
                    metric = "performance_ratio"
                    if "yield" in desc_l:
                        metric = "total_daily_yield_kwh"
                    t_args = {"data_ref": inputs.get("data_ref"), "metric": metric}
                else:
                    lvl = "inverter" if "inverter" in desc_l else "block" if "block" in desc_l else "plant"
                    t_args = {"data_ref": inputs.get("data_ref"), "level": lvl, "period": "total"}

                selection = {
                    "tool": chosen_tool,
                    "args": t_args,
                }
                response = None

            def _extract_selection(resp: Any) -> dict:
                # 1. Check if tool_calls were emitted by ChatGroq
                if hasattr(resp, "tool_calls") and resp.tool_calls:
                    tc = resp.tool_calls[0]
                    t_name = tc.get("name", "")
                    t_args = tc.get("args", {})
                    if isinstance(t_args, str):
                        try:
                            t_args = json.loads(t_args)
                        except Exception:
                            t_args = {}
                    if t_name:
                        return {"tool": t_name, "args": t_args}

                raw = getattr(resp, "content", "")
                if isinstance(raw, list):
                    raw = "".join(b.get("text", "") if isinstance(b, dict) else str(b) for b in raw)
                raw = str(raw).strip()

                if "```" in raw:
                    parts = raw.split("```")
                    if len(parts) >= 2:
                        raw = parts[1]
                        if raw.startswith("json"):
                            raw = raw[4:]
                        raw = raw.strip()

                try:
                    res = json.loads(raw)
                    if isinstance(res, dict):
                        return res
                    raise ValueError(f"Expected JSON object, got {type(res)}")
                except Exception:
                    start = raw.find("{")
                    end = raw.rfind("}")
                    if start != -1 and end != -1 and end > start:
                        res = json.loads(raw[start:end+1])
                        if isinstance(res, dict):
                            return res
                    raise

            if selection is None:
                try:
                    selection = _extract_selection(response)
                except Exception as parse_err:
                    logger.warning(
                        "SingleCallAgent[%s] malformed JSON from LLM: %s. Retrying with reminder...",
                        task_id, parse_err,
                    )
                    retry_messages = [
                        self._system_msg,
                        HumanMessage(content=human_text),
                        AIMessage(content=getattr(response, "content", "") if response is not None else ""),
                        HumanMessage(
                            content="Your previous response was not valid JSON. Please return valid JSON only matching the schema: {\"tool\": \"<tool_name>\", \"args\": {<args>}}"
                        ),
                    ]
                    try:
                        retry_resp = self._llm.invoke(retry_messages, run_id=run_id)
                        selection = _extract_selection(retry_resp)
                    except Exception as retry_err:
                        logger.error(
                            "SingleCallAgent[%s] malformed JSON from LLM after retry: %s",
                            task_id, retry_err,
                        )
                        return TaskResult(
                            task_id=task_id,
                            status=TaskStatus.FAILED,
                            error=f"Malformed JSON from LLM: {retry_err}",
                            tool_calls_made=tool_calls_made,
                            latency_ms=(time.monotonic() - t_start) * 1000,
                        )

            tool_name = selection.get("tool", "")
            tool_args = selection.get("args", {})
            if isinstance(tool_args, dict):
                if "aggregation_level" in tool_args and "level" not in tool_args:
                    tool_args["level"] = tool_args.pop("aggregation_level")
                if "time_period" in tool_args and "period" not in tool_args:
                    tool_args["period"] = tool_args.pop("time_period")
                from solar_agent.graph.data_store import DataStore
                if inputs.get("data_ref") and (
                    not tool_args.get("data_ref")
                    or tool_args.get("data_ref") in ("<data_ref>", "df_ref")
                    or DataStore.get(tool_args.get("data_ref")) is None
                ):
                    tool_args["data_ref"] = inputs["data_ref"]
                if tool_name == "estimate_soiling_loss_tool":
                    tool_args = {"data_ref": tool_args.get("data_ref", inputs.get("data_ref"))}
                elif tool_name == "rank_entities_tool":
                    tool_args = {
                        k: v for k, v in tool_args.items()
                        if k in ("data_ref", "metric", "ascending", "top_n")
                    }

            # ── unknown tool → return FAILED immediately (item 5) ──
            tool = self._tool_map.get(tool_name)
            if tool is None:
                logger.error(
                    "SingleCallAgent[%s] unknown tool '%s'. Available: %s",
                    task_id, tool_name, list(self._tool_map.keys()),
                )
                return TaskResult(
                    task_id=task_id,
                    status=TaskStatus.FAILED,
                    error=(
                        f"LLM selected unknown tool '{tool_name}'. "
                        f"Available: {list(self._tool_map.keys())}"
                    ),
                    tool_calls_made=tool_calls_made,
                    latency_ms=(time.monotonic() - t_start) * 1000,
                )

            logger.info(
                "SingleCallAgent[%s]: executing tool '%s' with args=%s",
                task_id, tool_name, tool_args,
            )
            tool_calls_made.append(tool_name)
            tool_output = tool.invoke(tool_args)

            # --- Deterministic result summary ---
            data_ref = tool_output.get("data_ref")
            metrics = tool_output.get("metrics") or {}
            warnings = tool_output.get("warnings") or []
            summary_text = tool_output.get("summary", f"Tool '{tool_name}' completed.")

            from solar_agent.graph.synthesizer import clean_task_summary
            return TaskResult(
                task_id=task_id,
                status=TaskStatus.DONE,
                summary=clean_task_summary(summary_text),
                data_ref=data_ref,
                metrics=metrics if metrics else None,
                warnings=warnings,
                tool_calls_made=tool_calls_made,
                latency_ms=(time.monotonic() - t_start) * 1000,
            )

        except (BudgetExceeded, Cancelled):
            raise
        except Exception as exc:
            logger.exception(
                "SingleCallAgent[%s] failed: %s", task_id, exc
            )
            return TaskResult(
                task_id=task_id,
                status=TaskStatus.FAILED,
                error=str(exc),
                tool_calls_made=tool_calls_made,
                latency_ms=(time.monotonic() - t_start) * 1000,
            )
