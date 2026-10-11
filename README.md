# ☀️ Solar Power Plant Analysis — Multi-Agent System

<p center>
  <img src="https://readme-typing-svg.demolab.com?font=Fira+Code&weight=600&size=24&pause=1000&color=F7B731&center=true&vCenter=true&width=600&lines=Multi-Agent+Solar+Analytics;Powered+by+LangGraph+%26+Gemini+2.5;Deterministic+Numerical+Precision" alt="Typing SVG" />
</p>

---

## 📌 Overview

A **LangGraph-powered multi-agent system** that evaluates solar energy generation efficiency, detects anomalies, and answers natural-language questions about solar power plant performance. 

> 🔒 **Core Design Principle:** *LLMs plan and explain; deterministic tools compute.* Every numerical metric is calculated via SQL and Python execution—zero halluncinated numbers.

---

## 🛠 Tech & Frameworks

![Python](https://img.shields.io/badge/Python-3.11+-3776AB?style=for-the-badge&logo=python&logoColor=white)
![LangChain](https://img.shields.io/badge/LangGraph-Multi--Agent-000000?style=for-the-badge&logo=langchain&logoColor=white)
![Gemini AI](https://img.shields.io/badge/Gemini_2.5-Pro%2FFlash-8E7CC3?style=for-the-badge&logo=google&logoColor=white)
![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-4169E1?style=for-the-badge&logo=postgresql&logoColor=white)
![Pytest](https://img.shields.io/badge/Testing-Pytest-0A9EDC?style=for-the-badge&logo=pytest&logoColor=white)

---

## 🏗 System Architecture

```text
 User Query
   │
   ▼
[1] Orchestrator / Planner ───► Gemini 2.5 Pro (Generates DAG Plan)
   │
   ▼
[2] DAG Executor (LangGraph) ── Runs parallel/layered execution nodes
   ├── 📊 Data Agent            ─── Deterministic Data Tools (Zero-LLM)
   ├── ⚡ Performance Agent     ─── Deterministic Calculation Tools
   ├── 🛠 Fault Diagnosis Agent ─── Deterministic Tools + Reasoning
   └── 🌡 Environmental Agent   ─── Weather & Irradiance Analysis
   │
   ▼
[3] Validator ───────────────► Deterministic Verification & Routing
   │
   ▼
[4] Synthesizer ─────────────► Grounded Synthesis & Number Verification
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

## Documentation

For an exhaustive, in-depth technical report covering all mathematics, security controls, agent architectures, and testing benchmarks, refer to:
👉 **[PROJECT_DOCUMENTATION.md](file:///c:/Users/rutvik/OneDrive/Desktop/solar%20Power%20Plant%20analysis/PROJECT_DOCUMENTATION.md)**

## Build Phases

- [x] **Phase 1**: Foundation — DB connection, semantic layer, metrics, data tools, tests (39 tests)
- [x] **Phase 2**: Analysis tools — performance, fault, environmental, DataStore (40 tests)
- [x] **Phase 3**: Agents — LangGraph agent nodes, ReAct + SingleCall architectures
- [x] **Phase 4**: Orchestrator + DAG parallel executor, dynamic reference resolution
- [x] **Phase 5**: Validator, Synthesizer with number-grounding check, re-plan loop
- [x] **Phase 6**: Persistence, multi-provider LLM factory, run-isolated budget, evaluation harness (232 tests total)

## Key Design Principles

1. **LLM plans; tools compute.** No number in the final answer can originate from LLM reasoning.
2. **SQL guardrails.** Only SELECT statements, only on allowlisted tables, with row limits and timeouts.
3. **Correct aggregation.** Block/plant PR = Σ(yield) / Σ(capacity × H) — never averaged.
4. **"Today" = MAX(log_date) from DB**, not the system clock.
5. **Peer deviation cancels weather.** The primary fault signal compares inverters to block-median on the same day, making it weather-agnostic.
