# Solar Power Plant Analysis — Comprehensive Technical Documentation

## Executive Summary

The **Solar Power Plant Analysis Multi-Agent System** is an enterprise-grade, deterministic, and LLM-assisted intelligence platform designed to analyze operational telemetry from commercial and utility-scale solar photovoltaic (PV) power plants.

Built on **LangGraph**, **SQLAlchemy**, and modern Python 3.12, the system solves the primary vulnerability of standard LLM applications in engineering domains—**hallucination of quantitative metrics**—by strictly adhering to the architectural axiom:

> **"The LLM plans, coordinates, and explains; pure Python and SQL tools compute. No numerical value in any final response originates from LLM fabrication."**

The platform features an end-to-end directed acyclic graph (DAG) execution engine, rigorous SQL security guardrails, thread-safe in-memory data management with disk spillover, a multi-role LLM provider factory, run-isolated query budget management, number-grounding response verification, and a comprehensive test suite of **232 passing automated tests**.

---

## 1. System Architecture

### 1.1 High-Level Multi-Agent Flow

The system coordinates specialized autonomous nodes using LangGraph's `StateGraph`:

```
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
| `is_out_of_scope`| `bool` | Flag indicating query is non-solar or cannot be fulfilled. |

---

## 2. Database Schema & Semantic Foundation

The database models a hierarchical PV portfolio: **Plant → Block → Inverter**.

### 2.1 Entity Relationship Model

```
 ┌──────────────────────────┐
 │          plants          │
 ├──────────────────────────┤
 │ PK  plant_id             │
 │     plant_name           │
 │     latitude, longitude  │
 │     capacity_mw          │
 └─────────────┬────────────┘
               │ 1:N
               ▼
 ┌──────────────────────────┐         ┌──────────────────────────┐
 │          blocks          │         │ daily_weather_telemetry  │
 ├──────────────────────────┤         ├──────────────────────────┤
 │ PK  block_id             │         │ PK  log_date             │
 │ FK  plant_id             │         │ PK  plant_id             │
 │     block_name           │         │     total_solar_rad_kwh_m2│ (Insolation H)
 └─────────────┬────────────┘         │     ambient_temp_c       │
               │ 1:N                  │     module_temp_c        │
               ▼                      │     soiling_ratio        │
 ┌──────────────────────────┐         └──────────────────────────┘
 │        inverters         │
 ├──────────────────────────┤
 │ PK  inverter_id          │
 │ FK  block_id             │
 │     rated_dc_kw          │
 │     rated_ac_kw          │
 └─────────────┬────────────┘
               │ 1:N
               ▼
 ┌──────────────────────────────────────────┐
 │         daily_inverter_telemetry         │
 ├──────────────────────────────────────────┤
 │ PK  log_date                             │
 │ PK  inverter_id                          │
 │ FK  plant_id                             │
 │     total_daily_yield_kwh                │
 │     peak_dc_power_kw, peak_ac_power_kw   │
 │     inverter_status_code                 │
 └──────────────────────────────────────────┘
```

### 2.2 Status Codes (`solar_agent/config/status_codes.yaml`)

Inverter operational health is classified using integer status codes:

- **Code 0 (Normal)**: Generating without faults.
- **Code 1 (Standby)**: Low solar irradiance or grid curtailment. **Excluded from zero-generation yield fault detection**.
- **Code 2 (Warning)**: Minor operational alerts; continues power generation.
- **Code 3 (Fault / Tripped)**: Severe hardware fault; generation halted.
- **Code 4 (Offline)**: Inverter disconnected or non-responsive.

---

## 3. Mathematical Metrics & Physics Engine

All physics equations are implemented in `solar_agent/tools/metrics.py` as pure numpy/pandas calculations with zero external dependencies and zero LLM intervention.

### 3.1 Performance Ratio ($PR$)
$$PR = \frac{\sum Y_i}{\sum (P_{dc, i} \cdot H)}$$
- **Insolation Threshold (BR-01)**: Days with solar insolation $H \le 0.5 \text{ kWh/m}^2$ are excluded to prevent asymptotic division anomalies during overcast/rainy conditions.
- **Aggregation Invariant**: Block and Plant $PR$ are computed via pooled yield and capacity-weighted insolation sums—**never by taking the arithmetic average of child inverter $PR$ values**.

### 3.2 Specific Yield ($Y_f$)
$$Y_f = \frac{\text{Yield (kWh)}}{P_{dc} \text{ (kW)}}$$
Normalizes energy production across varying inverter and block capacities.

### 3.3 Inverter Efficiency ($\eta$)
$$\eta = \frac{P_{ac}}{P_{dc}}$$
- Clamped between $0.0$ and $1.0$. Excludes sensor artifacts when $P_{dc} < 10 \text{ kW}$.

### 3.4 Soiling Loss ($L_s$)
$$L_s = 1.0 - \text{soiling\_ratio}$$
$$\text{Energy Lost (kWh)} = \text{Yield} \cdot \left(\frac{1}{\text{soiling\_ratio}} - 1\right)$$
- Detects cleaning events when day-over-day soiling ratio increases by $\ge 2\%$.

### 3.5 Peer Deviation (Weather-Agnostic Fault Detection)
Compares each inverter's performance against its block peers on the exact same date using the **Median Absolute Deviation (MAD)** modified Z-score:
$$M_i = \frac{0.6745 \cdot (PR_i - \text{median}(PR))}{\text{MAD}}$$
$$\text{MAD} = \text{median}(|PR_i - \text{median}(PR)|)$$
Because all inverters in a block experience identical irradiance, temperature, and cloud cover, weather variations cancel out completely, isolating true hardware degradation.

---

## 4. Security & Guardrails

### 4.1 SQL Security Guardrail (`solar_agent/tools/data_tools.py`)

Every database query generated by agents passes through an AST-based parser (`sqlglot`) before hitting PostgreSQL:

1. **Strict SELECT-Only Enforcement**: Rejects `INSERT`, `UPDATE`, `DELETE`, `DROP`, `TRUNCATE`, `ALTER`, `CREATE`, `GRANT`, `REVOKE`, and `COPY`.
2. **Table Allowlisting**: Restricts table access solely to `plants`, `blocks`, `inverters`, `daily_weather_telemetry`, and `daily_inverter_telemetry`. Prevents access to system schemas (`pg_user`, `pg_shadow`, `information_schema`).
3. **CTE Injection Prevention**: Inspects Common Table Expressions to ensure malicious DML operations are not hidden within `WITH` statements.
4. **Multi-Statement Blocking**: Rejects semicolon-delimited compound queries.
5. **Runtime Bounds**: Enforces statement-level timeouts (15s) and row limits (50,000 rows max).
6. **Defense-in-Depth**: `solar_agent/db/connection.py` includes a secondary SQLAlchemy `@event.listens_for("before_cursor_execute")` hook that rejects any non-SELECT SQL at the connection driver level.

### 4.2 Dynamic Temporal Clamping
To prevent querying non-existent future data, temporal filters automatically clamp "today" to:
$$\text{Current Date} = \max(\text{log\_date})$$
queried directly from the active telemetry database.

---

## 5. Multi-Agent System & Graph Pipeline

### 5.1 Specialized Agent Roles

| Agent | Architecture | Primary Tools | Role & Responsibility |
|---|---|---|---|
| **Data Agent** | **Deterministic (Zero-LLM)** | `execute_sql`, `fetch_plant_overview`, `fetch_inverter_data` | Directly executes data extraction without LLM overhead or latency. |
| **Performance Agent**| **SingleCallAgent** | `compute_kpis_tool`, `rank_entities_tool`, `compare_periods_tool`, `trend_analysis_tool` | Computes PR, specific yields, rankings, and multi-period comparisons in a single structured call. |
| **Environment Agent**| **SingleCallAgent** | `estimate_soiling_loss_tool`, `analyze_weather_correlation_tool` | Evaluates dust/soiling penalties, cleaning events, and temperature regression slopes. |
| **Fault Agent** | **ReAct Loop Agent** | `detect_zero_generation_tool`, `detect_status_faults_tool`, `detect_peer_underperformance_tool`, `detect_anomalies_tool` | Multi-turn diagnostic agent that correlates fault streaks, peer deviations, and anomalies. |

### 5.2 Orchestrator & DAG Execution
- **DAG Generation**: The Orchestrator LLM translates natural queries into a structured Pydantic `Plan` composed of tasks with explicit dependencies (`dependencies: ["t1"]`).
- **Cycle & Dependency Validation**: Uses Kahn's topological sort algorithm to validate the graph and split tasks into parallel execution layers.
- **Parallel Dispatch**: The `Executor` runs all independent tasks in parallel using `ThreadPoolExecutor`.
- **Reference Resolution**: Resolves upstream references dynamically (e.g., passing `$t1.data_ref` into downstream analysis tasks).

### 5.3 In-Memory DataStore & Spillover (`solar_agent/graph/data_store.py`)
- Telemetry DataFrames are kept in an in-memory key-value store using opaque handles (`df_xxxxxxxx`).
- Tools take and return data handles, passing only lightweight summary dictionaries to LLM contexts, keeping context window usage minimal.
- **Thread Safety**: Protected with `threading.RLock`.
- **Parquet Disk Spillover**: Transparently writes large datasets to disk (`spill_to_parquet`) and reloads them on demand.

### 5.4 Validator & Re-plan Routing
The Validator inspects all completed `TaskResult` entries:
- If all tasks succeed $\rightarrow$ routes to **Synthesizer**.
- If any task fails and `replan_count < max_replan_loops` $\rightarrow$ routes to **Orchestrator** with failure context for plan revision.
- If replan limit is exceeded $\rightarrow$ forces failover to Synthesizer to construct a best-effort partial answer.

### 5.5 Synthesizer & Number-Grounding Verification
The Synthesizer translates verified tool metrics into natural language:
- **Grounding Check**: Parses every numerical quantity in the generated response and compares it against the tool metric outputs.
- **2% Tolerance**: Permits acceptable rounding variations (e.g., `0.7842` $\rightarrow$ `78.4%` or `0.78`).
- **Retry & Deterministic Fallback**: If ungrounded numbers are detected, the LLM is prompted to re-synthesize. If grounding fails again, the system falls back to a deterministic markdown table of verified metrics.

---

## 6. Infrastructure & LLM Budget Engine

### 6.1 Multi-Provider LLM Factory (`solar_agent/config/llm.py`)
Provides `get_llm(role)` supporting:
- **Roles**: `planner`, `agent`, `synthesizer`.
- **Supported Providers**: Google Gemini (`gemini-3.8-flash`), Anthropic Claude (`claude-3-5-sonnet`), OpenAI (`gpt-4o`), and local Ollama (`llama3`).
- **Lazy Imports**: Provider libraries (`google-genai`, `anthropic`, `openai`) are imported on-demand, preventing missing dependency crashes.

### 6.2 Thread-Safe Budget Isolation (`solar_agent/graph/budgeted_llm.py`)
- **Query Budget**: Hard limit of 12 LLM calls per query (`settings.max_llm_calls_per_query`).
- **Run ID Isolation**: Budget counters are keyed strictly by unique `run_id` (UUID generated per invocation), ensuring that sequential queries on the same thread or concurrent queries in parallel threads each receive a fresh, independent budget.
- **Fail-Safe Partial Response**: If the budget is exhausted, `BudgetExceeded` is caught gracefully, and a partial summary of all completed tasks is returned without crashing.

---

## 7. Verification & Test Suite Summary

The repository maintains an automated test suite of **232 passing tests** across 12 test modules:

| Test Module | Test Count | Key Features Covered |
|---|---|---|
| `test_metrics.py` | **39** | PR formulas, low-irradiance exclusion, aggregation invariants, soiling, MAD peer deviation. |
| `test_orchestrator_executor.py`| **36** | DAG parsing, layer scheduling, ref resolution, replan routing, number grounding. |
| `test_sql_guard.py` | **31** | DML/DDL rejection, table allowlists, CTE injection, semicolon compound injection. |
| `test_dag_validation.py` | **30** | Kahn's algorithm, cycle detection, invalid dependencies, layer grouping. |
| `test_agents.py` | **21** | SingleCallAgent handling, malformed JSON recovery, ReAct loops, zero-LLM data execution. |
| `test_llm_factory.py` | **16** | Multi-provider dispatch, role configuration, fallback defaults, lazy import isolation. |
| `test_performance_tools.py` | **14** | KPI aggregation, multi-plant keying, ranking, trend analysis, period comparisons. |
| `test_environment_tools.py` | **14** | Soiling losses, cleaning event detection, module temp vs PR regression. |
| `test_fault_tools.py` | **12** | Zero-gen detection, standby exclusions, hardware fault streaks, peer anomalies. |
| `test_budget.py` | **9** | Run ID isolation, thread safety, budget exhaustion partial recovery, worst-case limits. |
| `test_golden.py` | **5** | End-to-end golden evaluation queries with cached replay responses. |
| `test_executor.py` | **5** | Layer scheduling, transient retry, fail-fast on HTTP 429 quota exhaustion. |
| **Total Passing Tests** | **232** | **100% Passing (0 failures, 0 errors)** |

---

## 8. Directory & File Reference

```
solar Power Plant analysis/
├── PROJECT_DOCUMENTATION.md            ← Master comprehensive technical document
├── README.md                           ← Getting started & architecture overview
├── conftest.py                         ← Pytest configuration & path registration
├── pyproject.toml                      ← Test markers, asyncio, and linting settings
├── requirements.txt                    ← Pinned project dependencies
├── scripts/
│   ├── derive_expected_values.py       ← Ground-truth SQL derivation script
│   ├── run_eval.py                     ← Evaluation harness (accuracy, latency, tokens)
│   └── rerecord.py                     ← LLM cache recording utility
└── solar_agent/
    ├── eval_set.json                   ← Golden evaluation query benchmark set
    ├── state.py                        ← Pydantic schemas, Task, Plan, AgentState
    ├── config/
    │   ├── llm.py                      ← Multi-provider LLM role factory (get_llm)
    │   ├── settings.py                 ← Configuration thresholds & environment variables
    │   ├── semantic_layer.yaml         ← Database schema metadata & business rules
    │   └── status_codes.yaml           ← Inverter operational status definitions
    ├── db/
    │   ├── connection.py               ← Sync/async engine factories with query guards
    │   └── queries.py                  ← Parameterized SQL query templates
    ├── tools/
    │   ├── metrics.py                  ← Pure mathematical KPI calculation engine
    │   ├── data_tools.py               ← AST SQL guardrail & 6 LangChain data tools
    │   ├── performance_tools.py        ← KPI aggregation, ranking, period comparison
    │   ├── fault_tools.py              ← Zero-gen, peer deviation, hardware streaks
    │   └── environment_tools.py        ← Soiling calculations & regression analysis
    ├── agents/
    │   ├── base_agent.py               ← BaseAgent, ReActAgent, SingleCallAgent
    │   ├── data_agent.py               ← Zero-LLM deterministic SQL execution agent
    │   ├── performance_agent.py        ← SingleCallAgent for performance analytics
    │   ├── environment_agent.py        ← SingleCallAgent for environmental diagnostics
    │   └── fault_agent.py              ← ReAct loop agent for exploratory fault diagnosis
    ├── graph/
    │   ├── graph.py                    ← LangGraph StateGraph pipeline assembly
    │   ├── orchestrator.py             ← DAG planning & replan reasoning
    │   ├── executor.py                 ← Parallel topological layer scheduler
    │   ├── validator.py                ← Task completion & integrity validation
    │   ├── synthesizer.py              ← Synthesis with number-grounding verification
    │   ├── budgeted_llm.py             ← Thread-safe, run-isolated call budget wrapper
    │   ├── data_store.py               ← In-memory thread-safe DataFrame repository
    │   └── llm_recorder.py             ← ReplayLLM test recording and playback cache
    └── tests/                          ← 232 automated tests
```

---

## 9. Recent Development Milestones

1. **Deterministic Single-Call & Zero-LLM Architecture**: Migrated `DataAgent` to zero-LLM execution and `PerformanceAgent` / `EnvironmentAgent` to `SingleCallAgent`, slashing API token consumption and eliminating non-deterministic errors.
2. **Multi-Plant Metrics Disambiguation**: Updated `compute_kpis_tool` to key metrics by distinct plant IDs, ensuring multi-plant comparative queries produce isolated, accurate data streams.
3. **Thread-Safe Run ID Budget Isolation**: Refactored `BudgetedLLM` to key call counts by per-invocation UUID `run_id` with shared `threading.Lock`, guaranteeing isolation across concurrent queries.
4. **SQL Ground Truth Alignment**: Implemented `scripts/derive_expected_values.py` to establish reproducible mathematical ground truths in `eval_set.json` referencing `status_codes.yaml`.
5. **Full Test Suite Restoration & Pass**: Validated complete 232-test suite with zero regressions.
6. **Git History & Contribution Alignment**: Corrected commit author credentials to match GitHub user profile across all historical commits and synchronized with `origin/main`.
