# Solar Power Plant Analysis — Multi-Agent System

A LangGraph-powered multi-agent system that answers natural-language questions about solar power plant performance using **only** SQL and Python tools for all numerical outputs — the LLM plans and explains, never fabricates numbers.

---

## Architecture

```
User Query
   │
   ▼
[1] Orchestrator / Planner    ← Gemini 2.5 Pro
   │  (emits a DAG Plan)
   ▼
[2] DAG Executor (LangGraph)  ← runs tasks layer-by-layer, parallel within each layer
   ├── Data Agent             ← Gemini 2.5 Flash
   ├── Performance Agent      ← deterministic tools
   ├── Fault Diagnosis Agent  ← deterministic tools + LLM reasoning
   └── Environmental Agent    ← deterministic tools + LLM reasoning
   ▼
[3] Validator                 ← Gemini 2.5 Flash (number grounding check)
   │
   ▼
[4] Synthesizer               ← Gemini 2.5 Pro (final answer from verified results only)
```

## Database Schema

| Table | Primary Key |
|---|---|
| `plants` | `plant_id` |
| `blocks` | `block_id` |
| `inverters` | `inverter_id` |
| `daily_weather_telemetry` | `(log_date, plant_id)` |
| `daily_inverter_telemetry` | `(log_date, inverter_id)` |

Hierarchy: **plant → block → inverter**. Weather is plant-level.

## Setup

### 1. Prerequisites
- Python 3.11+
- PostgreSQL 14+ with a read-only user

### 2. Environment
```bash
# Clone or copy to your working directory
cp .env.example .env
# Fill in DATABASE_URL and GOOGLE_API_KEY in .env
```

### 3. Install dependencies
```bash
python -m venv .venv
# Windows (MSYS2/Git Bash):
.venv/bin/pip install -r requirements.txt
# Windows (cmd/PowerShell with Scripts folder):
.venv\Scripts\pip install -r requirements.txt
```

### 4. Create DB tables and seed test data
```bash
# (Schema DDL will be added in Phase 6)
# For now, create tables matching the schema above and populate test data.
```

### 5. Run tests
```bash
.venv/bin/pytest solar_agent/tests/ -v
```

## Configuration

Key thresholds (all adjustable in `config/settings.py` or via `.env`):

| Setting | Default | Description |
|---|---|---|
| `MIN_IRRADIANCE_KWH_M2` | 0.5 | Minimum daily insolation to include in PR calculation |
| `SQL_ROW_LIMIT` | 50,000 | Max rows any SQL query may return |
| `SQL_TIMEOUT_SECONDS` | 15 | Statement-level query timeout |
| `PEER_UNDERPERFORMANCE_Z_THRESHOLD` | 2.0 | MAD-based z-score threshold for flagging underperformance |
| `MAX_REPLAN_LOOPS` | 2 | Max Validator → Orchestrator re-plan cycles |

## Status Codes (`config/status_codes.yaml`)

| Code | Label | Is Fault |
|---|---|---|
| 0 | Normal | No |
| 1 | Standby | No (excluded from yield checks) |
| 2 | Warning | No |
| 3 | Fault / Tripped | **Yes** |
| 4 | Offline | **Yes** |

## Build Phases

- [x] **Phase 1**: Foundation — DB connection, semantic layer, metrics, data tools, tests
- [ ] **Phase 2**: Analysis tools — performance, fault, environmental
- [ ] **Phase 3**: Agents — LangGraph agent nodes
- [ ] **Phase 4**: Orchestrator + DAG executor
- [ ] **Phase 5**: Validator, Synthesizer, re-plan loop
- [ ] **Phase 6**: Persistence, evaluation, CLI/Streamlit interface

## Key Design Principles

1. **LLM plans; tools compute.** No number in the final answer can originate from LLM reasoning.
2. **SQL guardrails.** Only SELECT statements, only on allowlisted tables, with row limits and timeouts.
3. **Correct aggregation.** Block/plant PR = Σ(yield) / Σ(capacity × H) — never averaged.
4. **"Today" = MAX(log_date) from DB**, not the system clock.
5. **Peer deviation cancels weather.** The primary fault signal compares inverters to block-median on the same day, making it weather-agnostic.
