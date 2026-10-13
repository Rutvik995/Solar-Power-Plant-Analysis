# Solar Power Plant Analysis — Comprehensive Technical Documentation

## Executive Summary

The **Solar Power Plant Analysis Multi-Agent System** is a deterministic, LLM-assisted intelligence platform designed to analyze operational telemetry from commercial and utility-scale solar photovoltaic (PV) power plants.

Built on **LangGraph**, **SQLAlchemy**, and Python 3.12, the system addresses the primary vulnerability of standard LLM applications in engineering domains—**hallucination of quantitative metrics**—by strictly adhering to the architectural axiom:

> **"The LLM plans, coordinates, and explains; pure Python and SQL tools compute. Numbers come directly from tool outputs, and an automated grounding check flags mismatches."**

The platform features an end-to-end directed acyclic graph (DAG) execution engine, AST-based SQL security guardrails, thread-safe in-memory data management with disk spillover, a multi-role LLM provider factory, run-isolated query budget management, number-grounding response verification, and a comprehensive test suite of **276 passing automated tests**.

---

## 1. System Architecture

### 1.1 High-Level Multi-Agent Flow

The system coordinates specialized autonomous nodes using LangGraph's `StateGraph`:

```text
                           ┌────────────────────────┐
                           │      User Query        │
                           └───────────┬────────────┘
                                       │
                                       ▼
                           ┌────────────────────────┐
                           │      start_node        │  (Assigns unique UUID run_id)
                           └───────────┬────────────┘
                                       │
                                       ▼
                           ┌────────────────────────┐
                 ┌────────►│      Orchestrator      │  (Planner LLM emits DAG Plan)
                 │         └───────────┬────────────┘
                 │                     │
                 │                     ▼
                 │         ┌────────────────────────┐
                 │         │      DAG Executor      │◄─┐
                 │         │  (Topological Layers)  │──┘ (Executes parallel tasks)
                 │         └───────────┬────────────┘
                 │                     │ All tasks complete
                 │                     ▼
                 │         ┌────────────────────────┐
          REPLAN │         │       Validator        │  (Status, deps, & metric checks)
          (≤ 2x) │         └───────────┬────────────┘
                 │                     │
                 ├─────────────────────┴────────────┐
                 │                                  │ PASS / FAIL CAP
                 ▼                                  ▼
      [Replan Context]                     ┌─────────────────┐
                                           │   Synthesizer   │  (Drafts answer +
                                           └────────┬────────┘   Number-Grounding Check)
                                                    │
                                                    ▼
                                           ┌─────────────────┐
                                           │       END       │  (Verified answer)
                                           └─────────────────┘
```

### 1.2 Pipeline Lifecycle & State Management

Execution state is encapsulated in `AgentState` (defined in `solar_agent/state.py`):

| State Attribute | Type | Description |
|---|---|---|
| `run_id` | `str (UUID)` | Unique invocation identifier used for budget isolation and concurrency safety. |
| `user_query` | `str` | Raw natural-language question posed by the user. |
| `plan` | `Optional[Plan]` | Validated Pydantic DAG plan comprising structured `Task` objects. |
| `results` | `dict[str, TaskResult]` | Map of task ID (`t1`, `t2`, ...) to execution outcome and data references. |
| `validation` | `Optional[ValidationResult]`| Validation status (`OK`, `REPLAN`, `FAIL`) and diagnostics. |
| `replan_count` | `int` | Number of replanning loops executed (capped at `settings.max_replan_loops = 2`). |
| `final_answer` | `Optional[str]` | Grounded narrative explanation presented to the user. |
| `llm_call_count`| `int` | Real-time count of LLM API invocations consumed during the query. |
| `is_out_of_scope`| `bool` | Flag indicating query is non-solar, predictive forecasting, or cannot be fulfilled. |

---

## 2. Database Schema (from `information_schema`)

The database models a hierarchical PV portfolio: **Plant → Block → Inverter**, with plant-level daily weather telemetry.

### 2.1 Table Definitions & Column Types

#### `plants`
Primary entity representing a solar power plant installation.
| Column Name | Data Type | Key / Constraint | Description |
|---|---|---|---|
| `plant_id` | `integer` | Primary Key | Unique numeric plant identifier (1 to 5). |
| `plant_name` | `character varying` | Not Null | Name of the solar facility (e.g., "Plant 1"). |
| `area_acres` | `numeric` | | Physical land area in acres. |
| `capacity_mwp` | `numeric` | | Total DC peak capacity in MWp. |

#### `blocks`
Sub-division of a plant grouping inverters under common balance-of-system equipment.
| Column Name | Data Type | Key / Constraint | Description |
|---|---|---|---|
| `block_id` | `integer` | Primary Key | Unique numeric block identifier. |
| `plant_id` | `integer` | Foreign Key → `plants(plant_id)` | Owning plant ID. |
| `block_name` | `character varying` | Not Null | Block designation (e.g., "Block A"). |

#### `inverters`
Individual DC-to-AC conversion units.
| Column Name | Data Type | Key / Constraint | Description |
|---|---|---|---|
| `inverter_id` | `character varying` | Primary Key | Alphanumeric inverter tag (e.g., "P1_Block_A_INV_01"). |
| `block_id` | `integer` | Foreign Key → `blocks(block_id)` | Owning block ID. |
| `plant_id` | `integer` | Foreign Key → `plants(plant_id)` | Owning plant ID. |
| `rated_dc_kw` | `numeric` | | Inverter rated DC capacity in kW. |
| `rated_ac_kw` | `numeric` | | Inverter rated AC output capacity in kW. |

#### `daily_inverter_telemetry`
Daily generation metrics and health status aggregated per inverter.
| Column Name | Data Type | Key / Constraint | Description |
|---|---|---|---|
| `log_date` | `date` | Composite PK | Date of operation. |
| `inverter_id` | `character varying` | Composite PK, FK → `inverters` | Inverter identifier. |
| `plant_id` | `integer` | FK → `plants(plant_id)` | Owning plant identifier. |
| `peak_dc_power_kw` | `numeric` | | Maximum DC power logged during the day. |
| `peak_ac_power_kw` | `numeric` | | Maximum AC power logged during the day. |
| `total_daily_yield_kwh` | `numeric` | | Total daily energy produced in kilowatt-hours (kWh). |
| `avg_dc_voltage_v` | `numeric` | | Average operating DC bus voltage. |
| `avg_dc_current_a` | `numeric` | | Average operating DC current. |
| `inverter_status_code` | `integer` | | Operational health code (0 = Normal, 1 = Standby, etc.). |

#### `daily_weather_telemetry`
Meteorological and environmental conditions logged at plant level.
| Column Name | Data Type | Key / Constraint | Description |
|---|---|---|---|
| `log_date` | `date` | Composite PK | Date of measurement. |
| `plant_id` | `integer` | Composite PK, FK → `plants` | Plant identifier. |
| `peak_poa_irradiance_w_m2` | `numeric` | | Peak Plane-of-Array irradiance in W/m². |
| `total_solar_radiation_kwh_m2` | `numeric` | | Daily cumulative insolation ($H$) in kWh/m². |
| `avg_ambient_temp_c` | `numeric` | | Daily average ambient air temperature in °C. |
| `max_module_temp_c` | `numeric` | | Daily maximum PV module temperature in °C. |
| `soiling_ratio` | `numeric` | | Transmission factor (1.0 = pristine clean, <1.0 = soiled). |

### 2.2 Operational Status Codes (`solar_agent/config/status_codes.yaml`)

- **Code 0 (Normal)**: Operating normally without faults.
- **Code 1 (Standby)**: Low solar irradiance or curtailment. Excluded from zero-generation fault counts.
- **Code 2 (Warning)**: Minor operational alert (e.g. ambient warning); continues generating.
- **Code 3 (Fault / Tripped)**: Inverter tripped on hardware fault; generation halted.
- **Code 4 (Offline)**: Disconnected or non-communicating.

---

## 3. Mathematical Metrics Engine

Implemented in `solar_agent/tools/metrics.py` as pure numerical calculations:

### 3.1 Performance Ratio ($PR$)
$$PR = \frac{\sum \text{Yield}_i}{\sum (P_{dc, i} \cdot H)}$$
- **Insolation Threshold**: Days with daily insolation $H \le 0.5 \text{ kWh/m}^2$ are excluded to prevent division distortions on dark/stormy days.
- **Aggregation Invariant**: Block and Plant $PR$ are always computed via pooled yield and capacity-weighted insolation sums—never as an unweighted average of inverter PRs.

### 3.2 Specific Yield ($Y_f$)
$$Y_f = \frac{\text{Yield (kWh)}}{P_{dc} \text{ (kW)}}$$

### 3.3 Soiling Loss & Clean Yield
$$\text{Soiling Energy Loss (kWh)} = \text{Yield} \cdot \left(\frac{1}{\text{soiling\_ratio}} - 1\right)$$
$$\text{Clean Yield without Soiling (kWh)} = \text{Yield} + \text{Soiling Energy Loss}$$
- Cleaning events are detected when day-over-day soiling ratio increases by $> 0.02$ ($2\%$).

### 3.4 Peer Deviation (Modified Z-Score MAD)
$$M_i = \frac{0.6745 \cdot (PR_i - \text{median}(PR))}{\text{MAD}}$$
$$\text{MAD} = \text{median}(|PR_i - \text{median}(PR)|)$$

---

## 4. Security & Guardrails

### 4.1 SQL Security Guardrail (`solar_agent/tools/data_tools.py`)
1. **Strict SELECT-Only Enforcement**: Rejects `INSERT`, `UPDATE`, `DELETE`, `DROP`, `TRUNCATE`, `ALTER`, `CREATE`, `GRANT`, `REVOKE`, `COPY`.
2. **Table Allowlisting**: Access restricted strictly to `plants`, `blocks`, `inverters`, `daily_weather_telemetry`, `daily_inverter_telemetry`.
3. **CTE Injection Prevention**: Validates Common Table Expressions (`WITH ...`).
4. **Defense-in-Depth**: SQLAlchemy `@event.listens_for("before_cursor_execute")` hook blocks non-SELECT queries at driver level.

### 4.2 Number-Grounding Verification (`solar_agent/graph/synthesizer.py` & `validator.py`)
- The Synthesizer receives structured JSON metrics and data tables from tool outputs.
- An automated validator extracts all numbers from the narrative response and tests that each number matches an upstream tool output within a 2% tolerance.
- If ungrounded numbers are found, the synthesizer is re-prompted with explicit correction context. If grounding fails a second time, the system falls back to a deterministic markdown table of raw verified metrics.

---

## 5. Multi-Agent Pipeline & Provider Configuration

### 5.1 Specialized Agent Architecture
- **Data Agent**: Zero-LLM deterministic SQL execution.
- **Performance Agent**: SingleCallAgent for KPIs, rankings, and comparisons.
- **Environment Agent**: SingleCallAgent for soiling loss and weather regressions.
- **Fault Agent**: ReAct agent for multi-step fault triage and anomaly correlation.

### 5.2 Provider Configuration & Free-Tier Setup (Groq)
Configured via `solar_agent/config/settings.py` and `.env`:
- `LLM_PROVIDER`: Provider selector (`groq`, `gemini`, `anthropic`, `openai`, `ollama`).
- `GROQ_API_KEY`: API key for Groq Cloud.
- `LLM_PLANNER_MODEL`: Model for planning (default: `llama-3.3-70b-versatile`).
- `LLM_AGENT_MODEL`: Model for agent tool selection (default: `llama-3.1-8b-instant`).
- `LLM_SYNTH_MODEL`: Model for final narrative synthesis (default: `llama-3.3-70b-versatile`).
- `LLM_MAX_RPM`: Rate limiter threshold (default: 20 RPM).

---

## 6. Evaluation Benchmark Results

Evaluated across the 25-question comprehensive benchmark set (`scripts/run_eval.py`):

| Evaluation Dimension | Score | Description |
|:---|:---:|:---|
| **Intent Match** | **1.00 (100%)** | Correct intent classification across all 25 queries |
| **Tools Match** | **0.78 (78%)** | Fraction of expected domain tools scheduled in DAG plan |
| **Numbers Grounded** | **0.68 (68%)** | Fraction of ground-truth target numbers found in answer |
| **Unexpected Numbers** | **0.99 (99%)** | Answers free of hallucinated or fabricated metrics |
| **Overall Score** | **86.30%** | **PASS (Benchmark Threshold: 70%)** |

---

## 7. Known Limitations

1. **Temporal Coverage**: The telemetry database currently covers **2026-08-01 through 2026-09-26** (57 operational days). Queries requesting dates outside this range will receive data bounded to this window with an explicit assumption disclosure.
2. **Synthetic Telemetry**: The dataset is synthetically generated using physical PV yield formulas, realistic irradiance profiles, and simulated inverter fault events for reproducible benchmarking.
3. **No Predictive Forecasting**: The system evaluates historical operational telemetry. Future date forecasts (e.g., "predict tomorrow's yield") are explicitly declared out of scope by the planner.
4. **Free-Tier LLM Rate Limits**: When operating on free Groq Cloud API tiers (e.g. 6,000 TPM limit), bursts of consecutive queries can hit HTTP 429 rate limits. The system uses exponential backoff and provides an offline replay cache (`llm_cache.json`) for instant, offline local demonstration and testing.
5. **Unsupported Query Domains**:
   - Power quality, harmonics, or sub-second grid frequency transient analysis.
   - PPA financial billing, dynamic feed-in tariffs, and revenue forecasting.
   - Sub-daily / real-time SCADA sensor streams (telemetry is daily-aggregated).
   - Maintenance work order ticketing system integration.
