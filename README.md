# ☀️ Solar Power Plant Analysis Platform

[![Python 3.12](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![React 18](https://img.shields.io/badge/React-18-61DAFB.svg)](https://react.dev/)
[![Vite](https://img.shields.io/badge/Vite-6.0+-646CFF.svg)](https://vitejs.dev/)
[![LangGraph](https://img.shields.io/badge/LangGraph-Multi--Agent-orange.svg)](https://www.langchain.com/langgraph)
[![TailwindCSS](https://img.shields.io/badge/TailwindCSS-v4-38B2AC.svg)](https://tailwindcss.com/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-14+-336791.svg)](https://www.postgresql.org/)
[![Tests](https://img.shields.io/badge/Tests-276%20Passing-brightgreen.svg)]()

An enterprise-grade operational intelligence and conversational analytics platform for commercial and utility-scale solar photovoltaic (PV) facilities. 

The platform pairs a modern **React 18 analytics dashboard** with a **deterministic, multi-agent AI copilot** powered by **LangGraph**, **FastAPI**, and **PostgreSQL**.

> 🔒 **The Core Architectural Axiom**:
> *"The LLM plans, coordinates, and explains; pure Python and SQL tools compute. All numbers originate from deterministic tool outputs, and an automated grounding validator verifies that every number in the narrative matches upstream data before it reaches the user."*

---

## 📑 Table of Contents
- [Architecture Overview](#-architecture-overview)
- [Key Features](#-key-features)
- [Solar Engineering Domain Concepts](#-solar-engineering-domain-concepts)
- [Database Schema & Dataset](#-database-schema--dataset)
- [Quickstart & Setup](#-quickstart--setup)
- [Running Automated Tests & Benchmarks](#-running-automated-tests--benchmarks)
- [REST API Reference](#-rest-api-reference)
- [Comprehensive Documentation](#-comprehensive-documentation)

---

## 🏗 Architecture Overview

```text
┌────────────────────────────────────────────────────────────────────────┐
│                        React 18 SPA (Frontend)                         │
│  - Modern light theme (#F8FAFC, #2563EB, #0F172A)                      │
│  - Interactive Dashboard: Overview, Plant Detail, Inverter Drawer      │
│  - AI Copilot: SSE streaming, multi-turn sessions, DAG plan inspector  │
└───────────────────────────────────┬────────────────────────────────────┘
                                    │ HTTP / REST & SSE Streaming
                                    ▼
┌────────────────────────────────────────────────────────────────────────┐
│                         FastAPI Server (Backend)                       │
│  - /api/dashboard/* (Deterministic REST endpoints, 60s TTL cache)      │
│  - /api/meta (Asset topology, date bounds, model provider settings)    │
│  - /api/chat (LangGraph multi-agent execution with token streaming)    │
│  - /api/sessions (Persistent conversation memory in SQLite/PostgreSQL) │
└──────────────────┬─────────────────────────────────┬───────────────────┘
                   │                                 │
                   ▼                                 ▼
┌──────────────────────────────────────┐  ┌──────────────────────────────┐
│       LangGraph Multi-Agent DAG      │  │     PostgreSQL Database      │
│  - Orchestrator (DAG Planner)        │  │  - plants, blocks, inverters │
│  - DAG Executor (Parallel Layers)    │  │  - daily_weather_telemetry   │
│  - Data / Fault / Perf / Env Agents  │  │  - daily_inverter_telemetry  │
│  - Grounding Validator & Synthesizer │  │  (113 days: July–Oct 2026)   │
└──────────────────────────────────────┘  └──────────────────────────────┘
```

### Multi-Agent Pipeline Execution Flow
```text
[ User Query ]
      │
      ▼
[ Orchestrator ] ──(Generates DAG Plan: Task dependencies & agent assignments)
      │
      ▼
[ DAG Executor ] ──(Concurrent Topological Layers)
      │
      ├──► 📊 Data Agent (Zero-LLM deterministic SQL execution with AST guards)
      ├──► ⚡ Performance Agent (PR, Specific Yield, Capacity Factor, Rankings)
      ├──► 🌡 Environmental Agent (Soiling loss modeling, cleaning detection)
      └──► 🛠 Fault Agent (Zero-gen triage, status code faults, peer MAD anomalies)
      │
      ▼
[ Validator ] ──(Verifies tool execution; triggers replan loop if tasks fail)
      │
      ▼
[ Synthesizer LLM ] ──(Drafts explanation + Automated Number-Grounding Verification)
      │
      ▼
[ Grounded Answer + Data Citations ]
```

---

## ✨ Key Features

### 1. Interactive Analytics Dashboard (`/dashboard`)
- **Global Sticky Filter Bar**:
  - Fleet selector ("All Plants" or focus on Plant 1–5).
  - Quick date presets: **7D**, **28D** (default), **30D**, **July**, **August**, **September**, **October**, and **All**.
  - Dynamic dual date picker automatically bounded by database coverage (`/api/meta`).
  - "Compare with previous period" toggle with single-line historical data coverage notes.
  - Granularity switch: **Day**, **Week**, **Month**.

- **Fleet Overview Tab**:
  - **7 KPI Cards**: Energy (`Zap`), Performance Ratio (`Gauge`), Specific Yield (`Sun`), Capacity Factor (`Percent`), Soiling Loss (`Droplets`), Availability (`CheckCircle2`), and Open Alerts (`AlertTriangle`). Labels wrap cleanly over 2 lines with inline glossary `(?)` tooltips that never collide with top-right icons.
  - **PR Benchmark Bar Chart**: Fleet rankings bounded to $[\min - 2, \max + 1]\%$ with bar-end percentage labels and fleet-average reference line.
  - **Generation vs. Irradiance Timeline**: Overlaying daily MWh generation against solar insolation ($\text{kWh/m}^2$).
  - **Plant Health Matrix**: Sortable table with color-coded health badges based on fleet median delta thresholds (`Optimal`, `Warning`, `Critical`).

- **Plant Detail Tab**:
  - **Block PR Bars**: Identifies physical block performance, highlighting blocks $> 3.0\ \text{pp}$ below plant median in danger color.
  - **Inverter Leaderboards**: Best 5 and Worst 5 inverters with deterministic tie-breaking by ID.
  - **Inverter × Date Heatmap**: CSS grid with sticky inverter column and toggle:
    - *Absolute PR Mode*: Standard red ($<70\%$) through green ($\ge 91\%$).
    - *Difference from Peers Mode*: Highlights deficits (amber/red) and surpluses (blue) relative to that day's plant median.
  - **Topology Treemap**: Hierarchical `Plant → Block → Inverter` treemap with bordered block headers and luminance-contrast text (dark text on light tiles, white text on dark tiles).
  - **Thermal & Weather Panels**: Ambient vs. module surface temperature line chart, and solar radiation vs. violet soiling ratio line chart.

- **Slide-Out Inverter Drawer**:
  - Full viewport height (`fixed inset-y-0 right-0 h-screen`), closing on `Esc` or backdrop click.
  - Summary block: installed DC rating, AC capacity, flagged days count.
  - Operating status timeline micro-strip with hover tooltips.
  - Daily PR vs. block peer median dashed baseline (`#0F172A`, stroke width 2.5) with data points and legend.
  - Daily Yield (kWh) chart with unclipped Y-axis values.
  - DC Electrical Characteristics (voltage & current dual axes).
  - One-click **"Ask AI about this inverter"** deep-linking directly into chat.

### 2. Conversational AI Copilot (`/chat`)
- **Real-Time Streaming**: Server-Sent Events (SSE) token streaming for conversational analysis.
- **Multi-Turn Session Memory**: SQLite/PostgreSQL conversation persistence with session renaming and deletion.
- **Execution Plan & Trace Inspector Drawer**: Inspect the generated DAG plan, sub-agent task arguments, raw SQL query results, and number-grounding verification logs.
- **Model Flexibility**: Supports Groq (Llama-3.3-70b, Llama-3.1-8b), OpenAI, Gemini, Anthropic, Ollama, and an offline deterministic replay mode.

---

## ⚡ Solar Engineering Domain Concepts

The platform implements industry-standard photovoltaic engineering metrics (IEC 61724):

| Metric | Formula / Concept | Description |
|---|---|---|
| **Performance Ratio ($PR$)** | $$PR = \frac{\sum \text{Yield}_{\text{actual}}}{\sum (P_{dc} \times H_{poa})}$$ | Weather-normalized efficiency ratio. Separates poor weather from equipment defects. Typical benchmark: $\ge 82\%$ optimal, $< 75\%$ critical. |
| **Specific Yield ($Y_f$)** | $$Y_f = \frac{\text{Yield (kWh)}}{P_{dc} \text{ (kWp)}}$$ | Generation normalized by installed DC peak capacity ($\text{kWh/kWp}$). Allows fair comparison between plants of different sizes. |
| **Capacity Factor ($CF$)** | $$CF = \frac{\text{Yield (kWh)}}{P_{ac} \text{ (kW)} \times 24\text{h}}$$ | Continuous 24h utilization ratio. Typical solar values sit between $18\% - 28\%$ due to daylight availability. |
| **Soiling Loss ($SR$)** | $$\text{Loss} = \text{Yield} \times \left(\frac{1}{SR} - 1\right)$$ | Optical transmission loss from dust/dirt. A jump in Soiling Ratio ($\Delta SR > +0.02$) detects module cleaning or rain events. |
| **Thermal Derating** | $$\Delta P = \gamma \times (T_{\text{mod}} - 25^\circ\text{C})$$ | PV cell voltage drops as panels heat up (typical temperature coefficient $\gamma \approx -0.4\%/^\circ\text{C}$). |
| **Zero-Generation Day** | $\text{Yield} \le 5\ \text{kWh}$ while $H > 0.5\ \text{kWh/m}^2$ | Detects inverters that produced zero power despite adequate sunlight. |
| **Peer Anomaly (MAD)** | $$M_i = \frac{0.6745 \times (PR_i - \text{median})}{\text{MAD}}$$ | Modified Z-Score using Median Absolute Deviation to detect statistical underperformance relative to block peers. |

---

## 🗄 Database Schema & Dataset

The PostgreSQL database stores portfolio topology and daily time-series telemetry across **5 plants, 21 blocks, and 116 inverters**:

```mermaid
erDiagram
    plants ||--o{ blocks : "contains"
    plants ||--o{ inverters : "owns"
    plants ||--o{ daily_weather_telemetry : "logs"
    blocks ||--o{ inverters : "groups"
    inverters ||--o{ daily_inverter_telemetry : "generates"

    plants {
        int plant_id PK
        varchar plant_name
        numeric area_acres
        numeric capacity_mwp
    }
    blocks {
        int block_id PK
        int plant_id FK
        varchar block_name
    }
    inverters {
        varchar inverter_id PK
        int block_id FK
        int plant_id FK
        numeric rated_dc_kw
        numeric rated_ac_kw
    }
    daily_weather_telemetry {
        date log_date PK
        int plant_id PK, FK
        numeric peak_poa_irradiance_w_m2
        numeric total_solar_radiation_kwh_m2
        numeric avg_ambient_temp_c
        numeric max_module_temp_c
        numeric soiling_ratio
    }
    daily_inverter_telemetry {
        date log_date PK
        varchar inverter_id PK, FK
        int plant_id FK
        numeric peak_dc_power_kw
        numeric peak_ac_power_kw
        numeric total_daily_yield_kwh
        numeric avg_dc_voltage_v
        numeric avg_dc_current_a
        int inverter_status_code
    }
```

### Continuous Operational Dataset (113 Days)
- **Timeframe**: July 1, 2026 to October 21, 2026 ($13,108$ inverter records, $565$ weather records).
- **Embedded Benchmark Scenarios**:
  - **2026-09-16**: Plant 1, Inverter `P1_Block_A_INV_02` complete trip (0 kWh yield under $6.66\ \text{kWh/m}^2$ irradiance; peers at $85\%$).
  - **2026-09-21**: Plant 2, Inverter `P2_Block_B_INV_04` localized string loss ($35\%$ output, status 3).
  - **2026-09-23**: Plant 5 thermal efficiency degradation (module temp $64^\circ\text{C}$, $15\%$ PR drop).
  - **2026-09-19 to 2026-09-26**: Plant 3 prolonged dust accumulation ($SR = 0.78$).
  - **2026-08-05 to 2026-08-15**: Plant 4 dust accumulation followed by cleaning restoration on Aug 15 ($SR \to 0.98$).

---

## 🚀 Quickstart & Setup

### 1. Prerequisites
- **Python**: 3.11 or 3.12
- **Node.js**: 18+ and `npm`
- **PostgreSQL**: 14+ running locally (or remote connection)
- **Groq API Key**: Free API key from [Groq Console](https://console.groq.com)

### 2. Clone Repository & Setup Backend
```bash
# Clone the repository
git clone https://github.com/Rutvik995/Solar-Power-Plant-Analysis.git
cd Solar-Power-Plant-Analysis

# Create and activate Python virtual environment
python -m venv .venv
# On Windows:
.venv\Scripts\activate
# On Linux/macOS:
source .venv/bin/activate

# Install backend dependencies
pip install -r requirements.txt
```

### 3. Environment Configuration
Create `.env` in the project root:
```env
# Database connection string (psycopg v3 driver)
DATABASE_URL=postgresql+psycopg://postgres:123456789@localhost:5432/solardata
CHAT_DATABASE_URL=postgresql+psycopg://postgres:123456789@localhost:5432/solardata

# LLM Provider Configuration
LLM_PROVIDER=groq
GROQ_API_KEY=your_groq_api_key_here
LLM_PLANNER_MODEL=openai/gpt-oss-120b
LLM_AGENT_MODEL=openai/gpt-oss-20b
LLM_SYNTH_MODEL=openai/gpt-oss-120b
LLM_MAX_RPM=20
```

### 4. Database Ingestion (July 1 to October 21, 2026)
Run the automated ingestion script:
```bash
python generate_solar_data.py --start-date 2026-07-01 --end-date 2026-10-21
```

### 5. Launch the Application

#### Start the FastAPI Backend:
```bash
uvicorn api.main:app --port 8000 --reload
```
*API running at `http://localhost:8000` (docs at `http://localhost:8000/docs`).*

#### Start the React Frontend:
```bash
cd frontend
npm install
npm run dev
```
*Frontend running at `http://localhost:5173`.*

---

## 🧪 Running Automated Tests & Benchmarks

### 1. Dashboard Endpoint & Metrics Unit Tests
```bash
# Run all dashboard endpoint tests:
pytest solar_agent/tests/test_dashboard_endpoints.py -v

# Run fault and peer metrics tests:
pytest solar_agent/tests/test_fault_peer_metrics.py -v
```

### 2. Frontend Production Bundle Check
```bash
cd frontend
npm run build
```

### 3. Benchmark Evaluation (25-Question Test Suite)
```bash
# Offline replay mode (zero API consumption, uses checked-in cache)
python scripts/run_eval.py --replay

# Live mode (uses configured Groq LLM)
python scripts/run_eval.py --live
```
**Benchmark Score**: **86.30% PASS** (exceeds the 70% threshold with 99% ungrounded number rejection).

---

## 🔌 REST API Reference

| Endpoint | Method | Query Parameters | Description |
|---|:---:|---|---|
| `/api/meta` | `GET` | — | Returns portfolio metadata (plants, blocks, min/max log dates, LLM settings). |
| `/api/dashboard/overview` | `GET` | `start`, `end`, `agg` | Fleet KPIs, daily generation series, plant comparison table, data quality. |
| `/api/dashboard/plant/{id}` | `GET` | `plant_id`, `start`, `end`, `agg` | Block PRs, best/worst inverters, weather telemetry, treemap hierarchy. |
| `/api/dashboard/plant/{id}/heatmap` | `GET` | `plant_id`, `start`, `end` | Inverter × date PR matrix for heatmap rendering. |
| `/api/dashboard/inverter/{id}` | `GET` | `inverter_id`, `start`, `end`, `agg` | Inverter summary, status timeline, daily PR vs peer median, yield, DC characteristics. |
| `/api/chat` | `POST` | `session_id`, `message`, `model` | Streaming SSE endpoint executing the LangGraph multi-agent pipeline. |
| `/api/sessions` | `GET` | — | Retrieves all stored chat sessions. |
| `/api/sessions` | `POST` | `title` | Creates a new chat session. |
| `/api/sessions/{id}` | `DELETE` | `id` | Deletes a chat session and its message history. |

---

## 📖 Comprehensive Documentation

For exhaustive technical documentation covering mathematical derivations, AST SQL guardrails, Pydantic schemas, and in-depth multi-agent state machines, refer to:
👉 **[PROJECT_DOCUMENTATION.md](./PROJECT_DOCUMENTATION.md)**
